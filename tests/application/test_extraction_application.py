"""
Step 13: extraction-application 应用层测试

验收目标（严格对齐实施步骤）：
1. 成功路径：单页、多页提取成功并推进到 extracted。
2. 单页失败继续：可恢复错误只标记该页 failed，job 保持在 extracting。
3. 鉴权失败：job 转 failed，并停止后续页面处理。
4. retry_page_extraction：支持 done/failed 页面重试；extracting（含失败页批量收尾）下先回退计数再恢复。
5. 事件发布：页面事件与完成事件（或失败事件）按预期发布。
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable
from unittest import TestCase

from backend.extraction.application.service import ExtractionApplication
from backend.shared_kernel.contracts import (
    EventType,
    ExtractConfig,
    JobAggregate,
    JobEvent,
    JobStatus,
    ModelConfig,
    PageDocument,
    PageStatus,
    RuntimeConfig,
    SourceDocumentRef,
)
from backend.shared_kernel.errors import AppError, ErrorCode


def _utc(value: datetime | None = None) -> datetime:
    if value is not None:
        return value
    return datetime.now(timezone.utc)


def _clone_job(job: JobAggregate) -> JobAggregate:
    return replace(
        job,
        succeeded_pages=list(job.succeeded_pages),
        failed_pages=list(job.failed_pages),
    )


def _clone_page(page: PageDocument) -> PageDocument:
    return replace(page)


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        now = self._current
        self._current = self._current + timedelta(seconds=1)
        return now


class _InMemoryJobRepository:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, JobAggregate] = {}

    def exists(self, job_id: uuid.UUID) -> bool:
        return job_id in self._store

    def get(self, job_id: uuid.UUID) -> JobAggregate:
        if job_id not in self._store:
            raise AppError(ErrorCode.JOB_NOT_FOUND)
        return _clone_job(self._store[job_id])

    def save(self, job: JobAggregate) -> None:
        self._store[job.job_id] = _clone_job(job)


class _InMemoryPageRepository:
    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, int], PageDocument] = {}

    def list_by_job(self, job_id: uuid.UUID) -> list[PageDocument]:
        pages = [page for (stored_job_id, _), page in self._store.items() if stored_job_id == job_id]
        return sorted((_clone_page(page) for page in pages), key=lambda item: item.page_num)

    def get(self, job_id: uuid.UUID, page_num: int) -> PageDocument:
        key = (job_id, page_num)
        if key not in self._store:
            raise AppError(ErrorCode.PAGE_NOT_FOUND)
        return _clone_page(self._store[key])

    def save(self, page: PageDocument) -> None:
        self._store[(page.job_id, page.page_num)] = _clone_page(page)


class _FakeSourceStore:
    def __init__(self) -> None:
        self._sources: dict[uuid.UUID, bytes] = {}

    def set_source(self, job_id: uuid.UUID, payload: bytes) -> None:
        self._sources[job_id] = payload

    def save_source(self, job_id: uuid.UUID, pdf_filename: str, pdf_bytes: bytes) -> SourceDocumentRef:
        self._sources[job_id] = pdf_bytes
        return SourceDocumentRef(
            job_id=job_id,
            relative_path=f"{job_id}/source.pdf",
            content_type="application/pdf",
            filename=pdf_filename,
            size_bytes=len(pdf_bytes),
        )

    def get_source(self, job_id: uuid.UUID) -> SourceDocumentRef:
        payload = self._sources.get(job_id)
        if payload is None:
            raise AppError(ErrorCode.JOB_NOT_FOUND)
        return SourceDocumentRef(
            job_id=job_id,
            relative_path=f"{job_id}/source.pdf",
            content_type="application/pdf",
            filename="source.pdf",
            size_bytes=len(payload),
        )

    def open_read(self, job_id: uuid.UUID) -> io.BytesIO:
        payload = self._sources.get(job_id)
        if payload is None:
            raise AppError(ErrorCode.JOB_NOT_FOUND)
        return io.BytesIO(payload)


class _FakeConfigRepository:
    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config

    def load(self) -> RuntimeConfig:
        return self._config

    def save(self, config: RuntimeConfig) -> RuntimeConfig:
        self._config = config
        return config


class _FakeSecretStore:
    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def get_api_key(self) -> str | None:
        return self._api_key

    def require_api_key(self) -> str:
        if not self._api_key:
            raise AppError(ErrorCode.CONFIG_MISSING_API_KEY)
        return self._api_key

    def set_api_key(self, api_key: str) -> None:
        self._api_key = api_key


class _FakePdfGateway:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.open_session_calls = 0
        self.closed_session_calls = 0

    def count_pages(self, pdf_bytes: bytes) -> int:
        del pdf_bytes
        return 1

    def render_page(self, pdf_bytes: bytes, page_num: int, dpi: int) -> bytes:
        del pdf_bytes, dpi
        self.calls.append(page_num)
        return f"page:{page_num}".encode("utf-8")

    def open_render_session(self, pdf_bytes: bytes) -> "_FakePdfRenderSession":
        self.open_session_calls += 1
        return _FakePdfRenderSession(gateway=self, pdf_bytes=pdf_bytes)


class _FakePdfRenderSession:
    def __init__(self, *, gateway: _FakePdfGateway, pdf_bytes: bytes) -> None:
        self._gateway = gateway
        self._pdf_bytes = pdf_bytes

    def render_page(self, page_num: int, dpi: int) -> bytes:
        return self._gateway.render_page(self._pdf_bytes, page_num, dpi)

    def close(self) -> None:
        self._gateway.closed_session_calls += 1


class _FakeVisionGateway:
    def __init__(self) -> None:
        self.outcomes: dict[int, str | Exception] = {}
        self.calls: list[int] = []
        self.delay_seconds = 0.0
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()
        self.open_session_calls = 0
        self.closed_session_calls = 0

    def extract_markdown(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        model: ModelConfig,
        api_key: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        del prompt, model, api_key, max_retries
        resolved_page_num = page_num or int(image_bytes.decode("utf-8").split(":")[1])
        with self._lock:
            self.calls.append(resolved_page_num)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            outcome = self.outcomes.get(resolved_page_num, f"# page {resolved_page_num}")
        try:
            if self.delay_seconds > 0:
                time.sleep(self.delay_seconds)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            with self._lock:
                self._active -= 1

    def open_session(
        self,
        *,
        model: ModelConfig,
        api_key: str,
    ) -> "_FakeVisionSession":
        self.open_session_calls += 1
        return _FakeVisionSession(
            gateway=self,
            model=model,
            api_key=api_key,
        )


class _FakeVisionSession:
    def __init__(
        self,
        *,
        gateway: _FakeVisionGateway,
        model: ModelConfig,
        api_key: str,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._api_key = api_key

    def extract_markdown(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        return self._gateway.extract_markdown(
            image_bytes=image_bytes,
            prompt=prompt,
            model=self._model,
            api_key=self._api_key,
            max_retries=max_retries,
            page_num=page_num,
        )

    def close(self) -> None:
        self._gateway.closed_session_calls += 1


class _FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[JobEvent] = []

    def publish(self, event: JobEvent) -> None:
        self.events.append(event)


class _ImmediateTaskScheduler:
    def __init__(
        self,
        *,
        before_run: Callable[[uuid.UUID, str], None] | None = None,
    ) -> None:
        self._before_run = before_run
        self._running: set[tuple[uuid.UUID, str]] = set()
        self.calls: list[tuple[uuid.UUID, str]] = []

    def schedule(self, *, job_id: uuid.UUID, task_name: str, task_factory) -> bool:  # type: ignore[no-untyped-def]
        key = (job_id, task_name)
        if key in self._running:
            return False
        self.calls.append(key)
        self._running.add(key)
        if self._before_run is not None:
            self._before_run(job_id, task_name)
        try:
            asyncio.run(task_factory())
        finally:
            self._running.remove(key)
        return True


class TestExtractionApplication(TestCase):
    def setUp(self) -> None:
        self.clock = _FakeClock(start=datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc))
        self.jobs = _InMemoryJobRepository()
        self.pages = _InMemoryPageRepository()
        self.source_store = _FakeSourceStore()
        self.config_repo = _FakeConfigRepository(
            RuntimeConfig(
                model=ModelConfig(
                    name="vision-model",
                    timeout_seconds=30,
                ),
                extract=ExtractConfig(
                    dpi=150,
                    concurrency=2,
                    max_retries=1,
                    prompt="提取成 Markdown",
                ),
                has_api_key=True,
            )
        )
        self.secret_store = _FakeSecretStore(api_key="test-key")
        self.pdf_gateway = _FakePdfGateway()
        self.vision_gateway = _FakeVisionGateway()
        self.events = _FakeEventPublisher()
        self.scheduler = _ImmediateTaskScheduler()
        self.app = self._build_app(self.scheduler)

    def _build_app(self, scheduler: _ImmediateTaskScheduler) -> ExtractionApplication:
        return ExtractionApplication(
            job_repository=self.jobs,
            page_repository=self.pages,
            source_store=self.source_store,
            config_repository=self.config_repo,
            secret_store=self.secret_store,
            pdf_gateway=self.pdf_gateway,
            vision_gateway=self.vision_gateway,
            event_publisher=self.events,
            task_scheduler=scheduler,
            clock=self.clock,
        )

    def _seed_job(
        self,
        *,
        job_id: uuid.UUID,
        status: JobStatus,
        total_pages: int,
        succeeded_pages: list[int] | None = None,
        failed_pages: list[int] | None = None,
        version: int = 1,
        last_error: str | None = None,
    ) -> None:
        now = _utc()
        self.jobs.save(
            JobAggregate(
                job_id=job_id,
                source_pdf_name="lecture.pdf",
                total_pages=total_pages,
                status=status,
                succeeded_pages=list(succeeded_pages or []),
                failed_pages=list(failed_pages or []),
                created_at=now,
                updated_at=now,
                version=version,
                last_error=last_error,
            )
        )

    def _seed_page(
        self,
        *,
        job_id: uuid.UUID,
        page_num: int,
        status: PageStatus,
        content: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.pages.save(
            PageDocument(
                job_id=job_id,
                page_num=page_num,
                status=status,
                content=content,
                error_message=error_message,
                updated_at=_utc(),
            )
        )

    def _call_mark_page_extracting(self, job_id: uuid.UUID, page_num: int) -> bool:
        return getattr(self.app, "_mark_page_extracting")(job_id, page_num)

    def _call_apply_page_success(self, *, job_id: uuid.UUID, page_num: int, content: str) -> None:
        getattr(self.app, "_apply_page_success")(
            job_id=job_id,
            page_num=page_num,
            content=content,
        )

    def _call_apply_page_failure(
        self,
        *,
        job_id: uuid.UUID,
        page_num: int,
        error_message: str,
    ) -> None:
        getattr(self.app, "_apply_page_failure")(
            job_id=job_id,
            page_num=page_num,
            error_message=error_message,
        )

    def test_start_job_extraction_single_page_success(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=1)
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.PENDING)
        self.source_store.set_source(job_id, b"%PDF-1.7 fake")
        self.vision_gateway.outcomes[1] = "## 第一页"

        self.app.start_job_extraction(job_id)

        job = self.jobs.get(job_id)
        page = self.pages.get(job_id, 1)
        self.assertEqual(JobStatus.EXTRACTED, job.status)
        self.assertEqual([1], job.succeeded_pages)
        self.assertEqual([], job.failed_pages)
        self.assertEqual(PageStatus.DONE, page.status)
        self.assertEqual("## 第一页", page.content)

        self.assertEqual(
            [EventType.STATUS_CHANGED, EventType.PAGE_PROCESSED, EventType.EXTRACTION_COMPLETED],
            [event.event_type for event in self.events.events],
        )
        extracting_event = self.events.events[0]
        page_event = self.events.events[1]
        complete_event = self.events.events[2]
        self.assertEqual("page", extracting_event.payload["type"])
        self.assertEqual("extracting", extracting_event.payload["status"])
        self.assertEqual(0, extracting_event.payload["processed_count"])
        self.assertEqual("page", page_event.payload["type"])
        self.assertEqual("done", page_event.payload["status"])
        self.assertEqual(1, page_event.payload["processed_count"])
        self.assertEqual("complete", complete_event.payload["type"])
        self.assertEqual([1], complete_event.payload["succeeded_pages"])
        self.assertEqual([], complete_event.payload["failed_pages"])

    def test_start_job_extraction_multiple_pages_success(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=2)
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.PENDING)
        self._seed_page(job_id=job_id, page_num=2, status=PageStatus.PENDING)
        self.source_store.set_source(job_id, b"%PDF-1.7 fake")
        self.vision_gateway.outcomes[1] = "page-1 ok"
        self.vision_gateway.outcomes[2] = "page-2 ok"

        self.app.start_job_extraction(job_id)

        job = self.jobs.get(job_id)
        self.assertEqual(JobStatus.EXTRACTED, job.status)
        self.assertEqual([1, 2], job.succeeded_pages)
        self.assertEqual([], job.failed_pages)
        self.assertEqual([1, 2], sorted(self.vision_gateway.calls))
        self.assertEqual(
            [
                EventType.STATUS_CHANGED,
                EventType.STATUS_CHANGED,
                EventType.PAGE_PROCESSED,
                EventType.PAGE_PROCESSED,
                EventType.EXTRACTION_COMPLETED,
            ],
            [event.event_type for event in self.events.events],
        )

    def test_start_job_extraction_uses_configured_concurrency(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=4)
        for page_num in range(1, 5):
            self._seed_page(job_id=job_id, page_num=page_num, status=PageStatus.PENDING)
        self.source_store.set_source(job_id, b"%PDF-1.7 fake")
        current = self.config_repo.load()
        self.config_repo.save(
            replace(
                current,
                extract=replace(current.extract, concurrency=3),
            )
        )
        self.vision_gateway.delay_seconds = 0.05

        self.app.start_job_extraction(job_id)

        job = self.jobs.get(job_id)
        self.assertEqual(JobStatus.EXTRACTED, job.status)
        self.assertEqual([1, 2, 3, 4], job.succeeded_pages)
        self.assertGreaterEqual(self.vision_gateway.max_active, 2)
        self.assertLessEqual(self.vision_gateway.max_active, 3)
        self.assertEqual(1, self.pdf_gateway.open_session_calls)
        self.assertEqual(1, self.pdf_gateway.closed_session_calls)
        self.assertEqual(3, self.vision_gateway.open_session_calls)
        self.assertEqual(3, self.vision_gateway.closed_session_calls)

    def test_start_job_extraction_timeout_marks_page_failed_and_job_continues(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=2)
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.PENDING)
        self._seed_page(job_id=job_id, page_num=2, status=PageStatus.PENDING)
        self.source_store.set_source(job_id, b"%PDF-1.7 fake")
        self.vision_gateway.outcomes[1] = AppError(ErrorCode.LLM_TIMEOUT, "timeout on page 1")
        self.vision_gateway.outcomes[2] = "page-2 recovered"

        self.app.start_job_extraction(job_id)

        job = self.jobs.get(job_id)
        failed_page = self.pages.get(job_id, 1)
        done_page = self.pages.get(job_id, 2)
        self.assertEqual(JobStatus.EXTRACTING, job.status)
        self.assertEqual([2], job.succeeded_pages)
        self.assertEqual([1], job.failed_pages)
        self.assertEqual(PageStatus.FAILED, failed_page.status)
        self.assertIn("timeout on page 1", failed_page.error_message or "")
        self.assertEqual(PageStatus.DONE, done_page.status)
        self.assertEqual("page-2 recovered", done_page.content)
        self.assertEqual(
            [
                EventType.STATUS_CHANGED,
                EventType.STATUS_CHANGED,
                EventType.PAGE_PROCESSED,
                EventType.PAGE_PROCESSED,
            ],
            [event.event_type for event in self.events.events],
        )
        failed_event = next(
            event for event in self.events.events if event.event_type == EventType.PAGE_PROCESSED and event.payload["page_num"] == 1
        )
        self.assertEqual("failed", failed_event.payload["status"])
        self.assertIn("timeout on page 1", str(failed_event.payload.get("error")))

    def test_start_job_extraction_auth_failure_marks_job_failed_and_stops_remaining_pages(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=3)
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.PENDING)
        self._seed_page(job_id=job_id, page_num=2, status=PageStatus.PENDING)
        self._seed_page(job_id=job_id, page_num=3, status=PageStatus.PENDING)
        self.source_store.set_source(job_id, b"%PDF-1.7 fake")
        current = self.config_repo.load()
        self.config_repo.save(
            replace(
                current,
                extract=replace(current.extract, concurrency=1),
            )
        )
        self.vision_gateway.outcomes[1] = AppError(ErrorCode.LLM_AUTH_FAILED, "invalid api key")
        self.vision_gateway.outcomes[2] = "should-not-run-2"
        self.vision_gateway.outcomes[3] = "should-not-run-3"

        with self.assertRaises(AppError) as ctx:
            self.app.start_job_extraction(job_id)
        self.assertEqual(ErrorCode.LLM_AUTH_FAILED, ctx.exception.code)

        job = self.jobs.get(job_id)
        self.assertEqual(JobStatus.FAILED, job.status)
        self.assertIn("invalid api key", job.last_error or "")
        self.assertIn(1, self.vision_gateway.calls)
        self.assertGreaterEqual(len(self.vision_gateway.calls), 1)
        self.assertEqual(PageStatus.PENDING, self.pages.get(job_id, 2).status)
        self.assertEqual(PageStatus.PENDING, self.pages.get(job_id, 3).status)
        self.assertEqual(PageStatus.PENDING, self.pages.get(job_id, 1).status)
        event_types = [event.event_type for event in self.events.events]
        self.assertEqual(EventType.JOB_FAILED, event_types[-1])
        self.assertTrue(all(event_type == EventType.STATUS_CHANGED for event_type in event_types[:-1]))
        self.assertGreaterEqual(len(event_types[:-1]), 1)
        page_events = self.events.events[:-1]
        self.assertTrue(any(event.payload["status"] == "pending" for event in page_events))
        self.assertEqual("failed", self.events.events[-1].payload["type"])

    def test_retry_page_extraction_from_extracted_rolls_back_before_recover(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(
            job_id=job_id,
            status=JobStatus.EXTRACTING,
            total_pages=2,
            succeeded_pages=[1],
            failed_pages=[2],
            version=9,
        )
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.DONE, content="old content")
        self._seed_page(job_id=job_id, page_num=2, status=PageStatus.FAILED, error_message="old err")
        self.source_store.set_source(job_id, b"%PDF-1.7 fake")
        self.vision_gateway.outcomes[1] = "new content"

        snapshot: dict[str, object] = {}

        def _before_run(captured_job_id: uuid.UUID, task_name: str) -> None:
            snapshot["task_name"] = task_name
            snapshot["job"] = self.jobs.get(captured_job_id)
            snapshot["page"] = self.pages.get(captured_job_id, 1)

        scheduler = _ImmediateTaskScheduler(before_run=_before_run)
        self.app = self._build_app(scheduler)

        self.app.retry_page_extraction(job_id, 1)

        intermediate_job = snapshot["job"]
        intermediate_page = snapshot["page"]
        assert isinstance(intermediate_job, JobAggregate)
        assert isinstance(intermediate_page, PageDocument)
        self.assertEqual("extract-page-1", snapshot["task_name"])
        self.assertEqual(JobStatus.EXTRACTING, intermediate_job.status)
        self.assertEqual(1, intermediate_job.processed_count, "含失败页时重试_done 页必须先回退 processed_count")
        self.assertEqual(PageStatus.EXTRACTING, intermediate_page.status)

        final_job = self.jobs.get(job_id)
        final_page = self.pages.get(job_id, 1)
        self.assertEqual(JobStatus.EXTRACTING, final_job.status)
        self.assertEqual(2, final_job.processed_count)
        self.assertEqual(PageStatus.DONE, final_page.status)
        self.assertEqual("new content", final_page.content)
        self.assertEqual(
            [EventType.STATUS_CHANGED, EventType.PAGE_PROCESSED],
            [event.event_type for event in self.events.events],
        )
        self.assertEqual("extracting", self.events.events[0].payload["status"])
        self.assertEqual(1, self.events.events[0].payload["processed_count"])

    def test_retry_page_extraction_allows_failed_page_and_recovers(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(
            job_id=job_id,
            status=JobStatus.EXTRACTING,
            total_pages=2,
            succeeded_pages=[1],
            failed_pages=[2],
        )
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.DONE, content="ok")
        self._seed_page(job_id=job_id, page_num=2, status=PageStatus.FAILED, error_message="timeout")
        self.source_store.set_source(job_id, b"%PDF-1.7 fake")
        self.vision_gateway.outcomes[2] = "page-2 fixed"

        self.app.retry_page_extraction(job_id, 2)

        job = self.jobs.get(job_id)
        page = self.pages.get(job_id, 2)
        self.assertEqual(JobStatus.EXTRACTED, job.status)
        self.assertEqual([1, 2], job.succeeded_pages)
        self.assertEqual([], job.failed_pages)
        self.assertEqual(PageStatus.DONE, page.status)
        self.assertEqual("page-2 fixed", page.content)
        self.assertEqual(
            [EventType.STATUS_CHANGED, EventType.PAGE_PROCESSED, EventType.EXTRACTION_COMPLETED],
            [event.event_type for event in self.events.events],
        )
        self.assertEqual("extracting", self.events.events[0].payload["status"])
        self.assertEqual(1, self.events.events[0].payload["processed_count"])

    def test_mark_page_extracting_skips_page_already_saved_manually(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(
            job_id=job_id,
            status=JobStatus.EXTRACTING,
            total_pages=2,
            succeeded_pages=[1],
        )
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.DONE, content="manual content")
        self._seed_page(job_id=job_id, page_num=2, status=PageStatus.PENDING)

        marked = self._call_mark_page_extracting(job_id, 1)

        page = self.pages.get(job_id, 1)
        self.assertFalse(marked)
        self.assertEqual(PageStatus.DONE, page.status)
        self.assertEqual("manual content", page.content)

    def test_apply_page_success_ignores_stale_result_after_manual_save(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
            version=5,
        )
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.DONE, content="manual content")

        self._call_apply_page_success(
            job_id=job_id,
            page_num=1,
            content="llm content",
        )

        job = self.jobs.get(job_id)
        page = self.pages.get(job_id, 1)
        self.assertEqual(JobStatus.EXTRACTED, job.status)
        self.assertEqual([1], job.succeeded_pages)
        self.assertEqual("manual content", page.content)
        self.assertEqual([], self.events.events)

    def test_apply_page_failure_ignores_stale_result_after_manual_save(self) -> None:
        job_id = uuid.uuid4()
        self._seed_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
            version=5,
        )
        self._seed_page(job_id=job_id, page_num=1, status=PageStatus.DONE, content="manual content")

        self._call_apply_page_failure(
            job_id=job_id,
            page_num=1,
            error_message="timeout",
        )

        job = self.jobs.get(job_id)
        page = self.pages.get(job_id, 1)
        self.assertEqual(JobStatus.EXTRACTED, job.status)
        self.assertEqual([1], job.succeeded_pages)
        self.assertEqual("manual content", page.content)
        self.assertEqual([], self.events.events)
