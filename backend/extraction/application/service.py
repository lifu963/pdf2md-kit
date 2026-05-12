"""Extraction application use-cases."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from dataclasses import dataclass, replace
from functools import partial
from uuid import UUID

from backend.extraction.ports import (
    Clock,
    ConfigRepository,
    EventPublisher,
    JobRepository,
    PageRepository,
    PdfDocumentGateway,
    SecretStore,
    SourceDocumentStore,
    TaskScheduler,
    VisionExtractionGateway,
    VisionExtractionSession,
)
from backend.shared_kernel.contracts import (
    EventType,
    JobAggregate,
    JobEvent,
    JobStatus,
    PageDocument,
    PageStatus,
)
from backend.shared_kernel.errors import AppError, ErrorCode


@dataclass(slots=True)
class _VisionWorker:
    executor: ThreadPoolExecutor
    session: VisionExtractionSession


class ExtractionApplication:
    """Application service for whole-job extraction and page retry extraction."""

    def __init__(
        self,
        *,
        job_repository: JobRepository,
        page_repository: PageRepository,
        source_store: SourceDocumentStore,
        config_repository: ConfigRepository,
        secret_store: SecretStore,
        pdf_gateway: PdfDocumentGateway,
        vision_gateway: VisionExtractionGateway,
        event_publisher: EventPublisher,
        task_scheduler: TaskScheduler,
        clock: Clock,
    ) -> None:
        self._job_repository = job_repository
        self._page_repository = page_repository
        self._source_store = source_store
        self._config_repository = config_repository
        self._secret_store = secret_store
        self._pdf_gateway = pdf_gateway
        self._vision_gateway = vision_gateway
        self._event_publisher = event_publisher
        self._task_scheduler = task_scheduler
        self._clock = clock
        self._job_state_locks: dict[UUID, threading.Lock] = {}
        self._job_state_locks_guard = threading.Lock()

    def start_job_extraction(self, job_id: UUID) -> None:
        job = self._job_repository.get(job_id)
        _ensure_job_extracting(job)

        # Fail fast for missing API key before dispatching background work.
        self._secret_store.require_api_key()

        self._task_scheduler.schedule(
            job_id=job_id,
            task_name="extract-all",
            task_factory=lambda: self._run_extraction_task(job_id=job_id, target_page_nums=None),
        )

    def retry_page_extraction(self, job_id: UUID, page_num: int) -> None:
        lock = self._get_job_state_lock(job_id)
        with lock:
            job = self._job_repository.get(job_id)
            page = self._page_repository.get(job_id, page_num)
            next_job, next_page = _apply_retry_transition(
                job=job,
                page=page,
                now=self._clock.now(),
            )
            self._page_repository.save(next_page)
            self._job_repository.save(next_job)
            self._publish_page_status_changed(
                job=next_job,
                page=next_page,
                seq_offset=page_num,
            )

        self._task_scheduler.schedule(
            job_id=job_id,
            task_name=f"extract-page-{page_num}",
            task_factory=lambda: self._run_extraction_task(job_id=job_id, target_page_nums=[page_num]),
        )

    async def _run_extraction_task(
        self,
        *,
        job_id: UUID,
        target_page_nums: list[int] | None,
    ) -> None:
        await self._extract_pages(job_id=job_id, target_page_nums=target_page_nums)

    async def _extract_pages(self, *, job_id: UUID, target_page_nums: list[int] | None) -> None:
        job = self._job_repository.get(job_id)
        if job.status != JobStatus.EXTRACTING:
            return

        all_pages = self._page_repository.list_by_job(job_id)
        page_nums = _resolve_target_pages(all_pages=all_pages, target_page_nums=target_page_nums)
        if not page_nums:
            self._promote_to_extracted_if_complete(job_id)
            return

        runtime = self._config_repository.load()
        api_key = self._secret_store.require_api_key()
        pdf_bytes = self._read_source_pdf_bytes(job_id)

        worker_count = max(1, min(runtime.extract.concurrency, len(page_nums)))
        queue: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue(
            maxsize=max(1, worker_count * 2)
        )
        cancel_event = threading.Event()
        auth_errors: list[AppError] = []
        auth_failed_job: JobAggregate | None = None
        fatal_errors: list[Exception] = []
        loop = asyncio.get_running_loop()
        render_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"pdf-render-{job_id.hex[:8]}",
        )
        pdf_session = self._pdf_gateway.open_render_session(pdf_bytes)
        vision_workers = [
            _VisionWorker(
                executor=ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix=f"llm-extract-{index + 1}-{job_id.hex[:8]}",
                ),
                session=self._vision_gateway.open_session(model=runtime.model, api_key=api_key),
            )
            for index in range(worker_count)
        ]

        async def render_producer() -> None:
            try:
                for page_num in page_nums:
                    if cancel_event.is_set():
                        break

                    try:
                        should_render = await asyncio.to_thread(
                            self._mark_page_extracting,
                            job_id,
                            page_num,
                        )
                    except Exception as exc:
                        if not cancel_event.is_set():
                            cancel_event.set()
                            fatal_errors.append(exc)
                        break
                    if not should_render:
                        continue
                    if cancel_event.is_set():
                        break

                    try:
                        image_bytes = await loop.run_in_executor(
                            render_executor,
                            partial(
                                pdf_session.render_page,
                                page_num=page_num,
                                dpi=runtime.extract.dpi,
                            ),
                        )
                    except Exception as exc:
                        if cancel_event.is_set():
                            break
                        try:
                            await asyncio.to_thread(
                                self._apply_page_failure,
                                job_id=job_id,
                                page_num=page_num,
                                error_message=_error_message(exc),
                            )
                        except Exception as state_exc:
                            if not cancel_event.is_set():
                                cancel_event.set()
                                fatal_errors.append(state_exc)
                            break
                        continue

                    await queue.put((page_num, image_bytes))
            finally:
                for _ in range(worker_count):
                    await queue.put(None)

        async def api_consumer(worker: _VisionWorker) -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return

                page_num, image_bytes = item
                if cancel_event.is_set():
                    continue

                try:
                    markdown = await loop.run_in_executor(
                        worker.executor,
                        partial(
                            worker.session.extract_markdown,
                            image_bytes=image_bytes,
                            prompt=runtime.extract.prompt,
                            max_retries=runtime.extract.max_retries,
                            page_num=page_num,
                        ),
                    )
                except AppError as exc:
                    if exc.code == ErrorCode.LLM_AUTH_FAILED:
                        if not auth_errors:
                            auth_errors.append(exc)
                        if not cancel_event.is_set():
                            cancel_event.set()
                            auth_failed_job = await asyncio.to_thread(
                                self._transition_job_to_failed,
                                job_id=job_id,
                                detail=_error_message(exc),
                            )
                        continue

                    if cancel_event.is_set():
                        continue

                    try:
                        await asyncio.to_thread(
                            self._apply_page_failure,
                            job_id=job_id,
                            page_num=page_num,
                            error_message=_error_message(exc),
                        )
                    except AppError as state_exc:
                        if cancel_event.is_set() and state_exc.code == ErrorCode.JOB_STATUS_CONFLICT:
                            continue
                        if not cancel_event.is_set():
                            cancel_event.set()
                            fatal_errors.append(state_exc)
                        continue
                except Exception as exc:
                    if cancel_event.is_set():
                        continue

                    try:
                        await asyncio.to_thread(
                            self._apply_page_failure,
                            job_id=job_id,
                            page_num=page_num,
                            error_message=_error_message(exc),
                        )
                    except AppError as state_exc:
                        if cancel_event.is_set() and state_exc.code == ErrorCode.JOB_STATUS_CONFLICT:
                            continue
                        if not cancel_event.is_set():
                            cancel_event.set()
                            fatal_errors.append(state_exc)
                        continue
                else:
                    if cancel_event.is_set():
                        continue

                    try:
                        await asyncio.to_thread(
                            self._apply_page_success,
                            job_id=job_id,
                            page_num=page_num,
                            content=markdown,
                        )
                    except AppError as state_exc:
                        if cancel_event.is_set() and state_exc.code == ErrorCode.JOB_STATUS_CONFLICT:
                            continue
                        if not cancel_event.is_set():
                            cancel_event.set()
                            fatal_errors.append(state_exc)
                        continue

        try:
            results = await asyncio.gather(
                render_producer(),
                *(api_consumer(worker) for worker in vision_workers),
                return_exceptions=True,
            )
        finally:
            await _close_on_executor(render_executor, pdf_session.close)
            render_executor.shutdown(wait=True)
            for worker in vision_workers:
                await _close_on_executor(worker.executor, worker.session.close)
                worker.executor.shutdown(wait=True)

        if auth_errors:
            if auth_failed_job is None:
                auth_failed_job = self._job_repository.get(job_id)
            reset_event_count = await asyncio.to_thread(
                self._reset_extracting_pages_to_pending,
                job_id,
                auth_failed_job,
            )
            await asyncio.to_thread(
                self._publish_job_failed,
                job=auth_failed_job,
                detail=_error_message(auth_errors[0]),
                seq_offset=reset_event_count + 1,
            )
            raise auth_errors[0]

        if fatal_errors:
            raise fatal_errors[0]

        for result in results:
            if isinstance(result, Exception):
                raise result

        self._promote_to_extracted_if_complete(job_id)

    def _mark_page_extracting(self, job_id: UUID, page_num: int) -> bool:
        lock = self._get_job_state_lock(job_id)
        with lock:
            current_page = self._page_repository.get(job_id, page_num)
            if current_page.status not in {PageStatus.PENDING, PageStatus.EXTRACTING}:
                return False
            if (
                current_page.status == PageStatus.EXTRACTING
                and current_page.content is None
                and current_page.error_message is None
            ):
                return True
            extracting_page = replace(
                current_page,
                status=PageStatus.EXTRACTING,
                content=None,
                error_message=None,
                updated_at=self._clock.now(),
            )
            self._page_repository.save(extracting_page)
            self._publish_page_status_changed(
                job=self._job_repository.get(job_id),
                page=extracting_page,
                seq_offset=page_num,
            )
            return True

    def _apply_page_success(self, *, job_id: UUID, page_num: int, content: str) -> None:
        lock = self._get_job_state_lock(job_id)
        with lock:
            page = self._page_repository.get(job_id, page_num)
            if page.status != PageStatus.EXTRACTING:
                return
            job = self._job_repository.get(job_id)
            if job.status != JobStatus.EXTRACTING:
                return

            now = self._clock.now()
            next_page = replace(
                page,
                status=PageStatus.DONE,
                content=content,
                error_message=None,
                updated_at=now,
            )
            next_succeeded = sorted(set(job.succeeded_pages + [page_num]))
            next_failed = [num for num in job.failed_pages if num != page_num]
            next_status = _next_job_status_after_page(
                total_pages=job.total_pages,
                succeeded_pages=next_succeeded,
                failed_pages=next_failed,
            )
            next_job = replace(
                job,
                status=next_status,
                succeeded_pages=next_succeeded,
                failed_pages=next_failed,
                updated_at=now,
                version=job.version + 1,
            )

            self._page_repository.save(next_page)
            self._job_repository.save(next_job)
            self._publish_page_processed(
                job=next_job,
                page=next_page,
                error_message=None,
                seq_offset=0,
            )
            if next_job.status == JobStatus.EXTRACTED:
                self._publish_extraction_completed(job=next_job, seq_offset=1)

    def _apply_page_failure(
        self,
        *,
        job_id: UUID,
        page_num: int,
        error_message: str,
    ) -> None:
        lock = self._get_job_state_lock(job_id)
        with lock:
            page = self._page_repository.get(job_id, page_num)
            if page.status != PageStatus.EXTRACTING:
                return
            job = self._job_repository.get(job_id)
            if job.status != JobStatus.EXTRACTING:
                return

            now = self._clock.now()
            next_page = replace(
                page,
                status=PageStatus.FAILED,
                content=None,
                error_message=error_message,
                updated_at=now,
            )
            next_succeeded = [num for num in job.succeeded_pages if num != page_num]
            next_failed = sorted(set(job.failed_pages + [page_num]))
            next_status = _next_job_status_after_page(
                total_pages=job.total_pages,
                succeeded_pages=next_succeeded,
                failed_pages=next_failed,
            )
            next_job = replace(
                job,
                status=next_status,
                succeeded_pages=next_succeeded,
                failed_pages=next_failed,
                updated_at=now,
                version=job.version + 1,
            )

            self._page_repository.save(next_page)
            self._job_repository.save(next_job)
            self._publish_page_processed(
                job=next_job,
                page=next_page,
                error_message=error_message,
                seq_offset=0,
            )
            if next_job.status == JobStatus.EXTRACTED:
                self._publish_extraction_completed(job=next_job, seq_offset=1)

    def _promote_to_extracted_if_complete(self, job_id: UUID) -> None:
        lock = self._get_job_state_lock(job_id)
        with lock:
            job = self._job_repository.get(job_id)
            if job.status != JobStatus.EXTRACTING:
                return
            if job.processed_count != job.total_pages:
                return
            if job.failed_pages:
                return

            extracted_job = replace(
                job,
                status=JobStatus.EXTRACTED,
                updated_at=self._clock.now(),
                version=job.version + 1,
                last_error=None,
            )
            self._job_repository.save(extracted_job)
            self._publish_extraction_completed(job=extracted_job, seq_offset=0)

    def _transition_job_to_failed(self, *, job_id: UUID, detail: str) -> JobAggregate:
        lock = self._get_job_state_lock(job_id)
        with lock:
            job = self._job_repository.get(job_id)
            if job.status == JobStatus.FAILED:
                return job

            failed_job = replace(
                job,
                status=JobStatus.FAILED,
                updated_at=self._clock.now(),
                version=job.version + 1,
                last_error=detail,
            )
            self._job_repository.save(failed_job)
            return failed_job

    def _reset_extracting_pages_to_pending(self, job_id: UUID, event_job: JobAggregate) -> int:
        lock = self._get_job_state_lock(job_id)
        with lock:
            pages = self._page_repository.list_by_job(job_id)
            reset_count = 0
            for page in pages:
                if page.status != PageStatus.EXTRACTING:
                    continue
                reset_page = replace(
                    page,
                    status=PageStatus.PENDING,
                    content=None,
                    error_message=None,
                    updated_at=self._clock.now(),
                )
                self._page_repository.save(reset_page)
                reset_count += 1
                self._publish_page_status_changed(
                    job=event_job,
                    page=reset_page,
                    seq_offset=reset_count,
                )
            return reset_count

    def _publish_page_processed(
        self,
        *,
        job: JobAggregate,
        page: PageDocument,
        error_message: str | None,
        seq_offset: int,
    ) -> None:
        payload: dict[str, object] = {
            "type": "page",
            "page_num": page.page_num,
            "status": page.status.value,
            "processed_count": job.processed_count,
            "total_pages": job.total_pages,
        }
        if error_message:
            payload["error"] = error_message
        self._publish_event(
            job=job,
            event_type=EventType.PAGE_PROCESSED,
            payload=payload,
            seq_offset=seq_offset,
        )

    def _publish_page_status_changed(
        self,
        *,
        job: JobAggregate,
        page: PageDocument,
        seq_offset: int,
    ) -> None:
        payload: dict[str, object] = {
            "type": "page",
            "page_num": page.page_num,
            "status": page.status.value,
            "processed_count": job.processed_count,
            "total_pages": job.total_pages,
        }
        self._publish_event(
            job=job,
            event_type=EventType.STATUS_CHANGED,
            payload=payload,
            seq_offset=seq_offset,
        )

    def _publish_extraction_completed(self, *, job: JobAggregate, seq_offset: int) -> None:
        payload: dict[str, object] = {
            "type": "complete",
            "processed_count": job.processed_count,
            "total_pages": job.total_pages,
            "succeeded_pages": list(job.succeeded_pages),
            "failed_pages": list(job.failed_pages),
        }
        self._publish_event(
            job=job,
            event_type=EventType.EXTRACTION_COMPLETED,
            payload=payload,
            seq_offset=seq_offset,
        )

    def _publish_job_failed(self, *, job: JobAggregate, detail: str, seq_offset: int) -> None:
        payload: dict[str, object] = {"type": "failed", "detail": detail}
        self._publish_event(
            job=job,
            event_type=EventType.JOB_FAILED,
            payload=payload,
            seq_offset=seq_offset,
        )

    def _publish_event(
        self,
        *,
        job: JobAggregate,
        event_type: EventType,
        payload: dict[str, object],
        seq_offset: int,
    ) -> None:
        event = JobEvent(
            job_id=job.job_id,
            seq=_event_seq(job=job, seq_offset=seq_offset),
            event_type=event_type,
            payload=payload,
            created_at=self._clock.now(),
        )
        self._event_publisher.publish(event)

    def _read_source_pdf_bytes(self, job_id: UUID) -> bytes:
        with self._source_store.open_read(job_id) as source:
            payload = source.read()
        if not payload:
            raise AppError(
                code=ErrorCode.PDF_OPEN_FAILED,
                message="source pdf is empty",
                details={"job_id": str(job_id)},
            )
        return payload

    def _get_job_state_lock(self, job_id: UUID) -> threading.Lock:
        with self._job_state_locks_guard:
            existing = self._job_state_locks.get(job_id)
            if existing is not None:
                return existing
            lock = threading.Lock()
            self._job_state_locks[job_id] = lock
            return lock


async def _close_on_executor(executor: ThreadPoolExecutor, close_callback) -> None:  # type: ignore[no-untyped-def]
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(executor, close_callback)
    except Exception:
        return


def _resolve_target_pages(
    *,
    all_pages: list[PageDocument],
    target_page_nums: list[int] | None,
) -> list[int]:
    if target_page_nums is None:
        return sorted(
            page.page_num
            for page in all_pages
            if page.status in {PageStatus.PENDING, PageStatus.EXTRACTING}
        )

    available = {page.page_num for page in all_pages}
    resolved: list[int] = []
    for page_num in sorted(set(target_page_nums)):
        if page_num not in available:
            raise AppError(
                code=ErrorCode.PAGE_NOT_FOUND,
                message="target retry page does not exist",
                details={"page_num": page_num},
            )
        resolved.append(page_num)
    return resolved


def _next_job_status_after_page(
    *,
    total_pages: int,
    succeeded_pages: list[int],
    failed_pages: list[int],
) -> JobStatus:
    processed_count = len(succeeded_pages) + len(failed_pages)
    if processed_count == total_pages and not failed_pages:
        return JobStatus.EXTRACTED
    return JobStatus.EXTRACTING


def _apply_retry_transition(
    *,
    job: JobAggregate,
    page: PageDocument,
    now,
) -> tuple[JobAggregate, PageDocument]:
    if job.status == JobStatus.BUILDING:
        raise AppError(
            code=ErrorCode.JOB_STATUS_CONFLICT,
            message="writes are not allowed while job is building",
            details={"status": job.status.value},
        )
    if job.status == JobStatus.READY:
        raise AppError(
            code=ErrorCode.PAGE_RETRY_FORBIDDEN,
            message="pages are frozen in ready state",
            details={"status": job.status.value},
        )
    if job.status not in {JobStatus.EXTRACTING, JobStatus.EXTRACTED}:
        raise AppError(
            code=ErrorCode.PAGE_RETRY_FORBIDDEN,
            message="page retry is only allowed in extracting/extracted",
            details={"status": job.status.value},
        )
    if page.status not in {PageStatus.DONE, PageStatus.FAILED}:
        raise AppError(
            code=ErrorCode.PAGE_RETRY_FORBIDDEN,
            message="only done/failed pages can be retried",
            details={"page_num": page.page_num, "page_status": page.status.value},
        )

    next_page = replace(
        page,
        status=PageStatus.EXTRACTING,
        content=None,
        error_message=None,
        updated_at=now,
    )
    next_job = replace(
        job,
        status=JobStatus.EXTRACTING,
        succeeded_pages=[num for num in job.succeeded_pages if num != page.page_num],
        failed_pages=[num for num in job.failed_pages if num != page.page_num],
        updated_at=now,
        version=job.version + 1,
    )
    return next_job, next_page


def _ensure_job_extracting(job: JobAggregate) -> None:
    if job.status != JobStatus.EXTRACTING:
        raise AppError(
            code=ErrorCode.JOB_STATUS_CONFLICT,
            message="job must be extracting",
            details={"status": job.status.value},
        )


def _event_seq(*, job: JobAggregate, seq_offset: int) -> int:
    return (job.version * 1_000_000) + seq_offset


def _error_message(exc: Exception) -> str:
    if isinstance(exc, AppError):
        if exc.message:
            return exc.message
        return exc.code.value
    text = str(exc).strip()
    if text:
        return text
    return exc.__class__.__name__


__all__ = ["ExtractionApplication"]
