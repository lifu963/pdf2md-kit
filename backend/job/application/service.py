"""Job application query and command use-cases."""

from __future__ import annotations

from uuid import UUID

from backend.config.ports import SecretStore
from backend.job.application.commands import (
    BuildJobCommand,
    CreateJobCommand,
    DiscardOutputCommand,
    RetryPageCommand,
    SaveOutputCommand,
    SavePageCommand,
)
from backend.job.application.dto import (
    AcceptedResult,
    BuildResult,
    CreateJobResult,
    JobHistoryItemView,
    JobView,
    OutputDocumentView,
    PageSummary,
    PageView,
)
from backend.job.domain import rules as job_rules
from backend.job.domain.models import (
    ArtifactRef,
    JobAggregate,
    JobStatus,
    PageDocument,
    PageStatus,
    SourceDocumentRef,
)
from backend.job.ports import (
    BuildApplication,
    ExtractionApplication,
    JobRepository,
    PageRepository,
    PdfDocumentGateway,
    SourceDocumentStore,
)
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.shared_kernel.time import Clock, IdGenerator


class JobApplication:
    """Job facade: stable query/command DTOs and orchestration."""

    def __init__(
        self,
        *,
        job_repository: JobRepository,
        page_repository: PageRepository,
        source_store: SourceDocumentStore,
        build_application: BuildApplication,
        extraction_application: ExtractionApplication,
        pdf_gateway: PdfDocumentGateway,
        secret_store: SecretStore,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._job_repository = job_repository
        self._page_repository = page_repository
        self._source_store = source_store
        self._build_application = build_application
        self._extraction_application = extraction_application
        self._pdf_gateway = pdf_gateway
        self._secret_store = secret_store
        self._clock = clock
        self._id_generator = id_generator

    def create_job(self, cmd: CreateJobCommand) -> CreateJobResult:
        total_pages = self._pdf_gateway.count_pages(cmd.pdf_bytes)
        self._secret_store.require_api_key()
        job_id = self._id_generator.new()
        job, pages = job_rules.create_job(
            job_id=job_id,
            source_pdf_name=cmd.pdf_filename,
            total_pages=total_pages,
            now=self._clock.now(),
        )
        self._job_repository.save(job)
        for page in pages:
            self._page_repository.save(page)
        self._source_store.save_source(job_id, cmd.pdf_filename, cmd.pdf_bytes)
        self._extraction_application.start_job_extraction(job_id)
        return CreateJobResult(job_id=job_id, total_pages=total_pages, status=job.status)

    def save_page(self, cmd: SavePageCommand) -> PageView:
        job = self._job_repository.get(cmd.job_id)
        page = self._page_repository.get(cmd.job_id, cmd.page_num)
        next_job, next_page = job_rules.save_page(job, page, cmd.content, self._clock.now())
        self._page_repository.save(next_page)
        self._job_repository.save(next_job)
        return _to_page_view(next_page)

    def retry_page(self, cmd: RetryPageCommand) -> AcceptedResult:
        self._extraction_application.retry_page_extraction(cmd.job_id, cmd.page_num)
        return AcceptedResult(job_id=cmd.job_id, page_num=cmd.page_num)

    def build_output(self, cmd: BuildJobCommand) -> BuildResult:
        self._build_application.build_output(cmd.job_id, cmd.merge_mode)
        return BuildResult(
            status=JobStatus.READY,
            output_url=f"/api/jobs/{cmd.job_id}/output",
            download_url=f"/api/jobs/{cmd.job_id}/output/download",
        )

    def save_output(self, cmd: SaveOutputCommand) -> OutputDocumentView:
        doc = self._build_application.save_output_document(cmd.job_id, cmd.content)
        return OutputDocumentView(
            job_id=doc.job_id,
            content=doc.content,
            updated_at=doc.updated_at,
        )

    def discard_output(self, cmd: DiscardOutputCommand) -> JobView:
        job = self._build_application.discard_output(cmd.job_id)
        return _to_job_view(job)

    def list_jobs(self) -> list[JobHistoryItemView]:
        jobs = self._job_repository.list_all()
        return [_to_job_history_item_view(job) for job in jobs]

    def delete_job(self, job_id: UUID) -> None:
        job = self._load_job(job_id)
        if job.status in (JobStatus.EXTRACTING, JobStatus.BUILDING):
            raise AppError(
                code=ErrorCode.JOB_STATUS_CONFLICT,
                message="history deletion is only allowed for inactive jobs",
                details={"status": job.status.value},
            )
        self._job_repository.delete(job_id)

    def get_job(self, job_id: UUID) -> JobView:
        job = self._load_job(job_id)
        return _to_job_view(job)

    def get_source_document(self, job_id: UUID) -> SourceDocumentRef:
        self._load_job(job_id)
        return self._source_store.get_source(job_id)

    def list_pages(self, job_id: UUID) -> list[PageSummary]:
        self._load_job(job_id)
        pages = sorted(
            self._page_repository.list_summaries_by_job(job_id),
            key=lambda item: item.page_num,
        )
        return [_to_page_summary(page) for page in pages]

    def get_page(self, job_id: UUID, page_num: int) -> PageView:
        self._load_job(job_id)
        page = self._page_repository.get(job_id, page_num)
        return _to_page_view(page)

    def get_output_document(self, job_id: UUID) -> OutputDocumentView:
        self._load_job(job_id)
        output = self._build_application.get_output_document(job_id)
        return OutputDocumentView(
            job_id=output.job_id,
            content=output.content,
            updated_at=output.updated_at,
        )

    def get_output_artifact(self, job_id: UUID) -> ArtifactRef:
        self._load_job(job_id)
        return self._build_application.get_output_artifact(job_id)

    def _load_job(self, job_id: UUID) -> JobAggregate:
        return self._job_repository.get(job_id)


def _to_job_view(job: JobAggregate) -> JobView:
    return JobView(
        job_id=job.job_id,
        status=job.status,
        total_pages=job.total_pages,
        succeeded_pages=sorted(job.succeeded_pages),
        failed_pages=sorted(job.failed_pages),
        processed_count=job.processed_count,
    )


def _to_job_history_item_view(job: JobAggregate) -> JobHistoryItemView:
    return JobHistoryItemView(
        job_id=job.job_id,
        pdf_name=job.source_pdf_name,
        status=job.status,
        total_pages=job.total_pages,
        processed_count=job.processed_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _to_page_summary(page: PageDocument) -> PageSummary:
    return PageSummary(
        page_num=page.page_num,
        status=page.status,
    )


def _to_page_view(page: PageDocument) -> PageView:
    should_expose_body = page.status == PageStatus.DONE
    should_expose_error = page.status == PageStatus.FAILED
    should_expose_details = should_expose_body or should_expose_error

    return PageView(
        job_id=page.job_id,
        page_num=page.page_num,
        status=page.status,
        content=page.content if should_expose_body else None,
        error_message=page.error_message if should_expose_error else None,
        updated_at=page.updated_at if should_expose_details else None,
    )


__all__ = ["JobApplication"]
