"""HTTP job/page/output request and response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

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
from backend.shared_kernel.contracts import BuildMergeMode, JobStatus, PageStatus


@dataclass(frozen=True, slots=True)
class JobResponse:
    job_id: UUID
    status: JobStatus
    total_pages: int
    succeeded_pages: list[int]
    failed_pages: list[int]
    processed_count: int


@dataclass(frozen=True, slots=True)
class JobHistoryItemResponse:
    job_id: UUID
    pdf_name: str
    status: JobStatus
    total_pages: int
    processed_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CreateJobResponse:
    job_id: UUID
    total_pages: int
    status: JobStatus


@dataclass(frozen=True, slots=True)
class PageSummaryResponse:
    page_num: int
    status: PageStatus


@dataclass(frozen=True, slots=True)
class PageResponse:
    page_num: int
    status: PageStatus
    content: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RetryPageAcceptedResponse:
    job_id: UUID
    page_num: int


@dataclass(frozen=True, slots=True)
class BuildResponse:
    status: JobStatus
    output_url: str
    download_url: str


@dataclass(frozen=True, slots=True)
class BuildOutputRequest:
    merge_mode: BuildMergeMode = BuildMergeMode.DIRECT


@dataclass(frozen=True, slots=True)
class OutputDocumentResponse:
    content: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SavePageRequest:
    content: str


@dataclass(frozen=True, slots=True)
class SaveOutputRequest:
    content: str


def to_job_response(view: JobView) -> JobResponse:
    return JobResponse(
        job_id=view.job_id,
        status=view.status,
        total_pages=view.total_pages,
        succeeded_pages=list(view.succeeded_pages),
        failed_pages=list(view.failed_pages),
        processed_count=view.processed_count,
    )


def to_job_history_item_response(view: JobHistoryItemView) -> JobHistoryItemResponse:
    return JobHistoryItemResponse(
        job_id=view.job_id,
        pdf_name=view.pdf_name,
        status=view.status,
        total_pages=view.total_pages,
        processed_count=view.processed_count,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def to_job_history_list_response(items: list[JobHistoryItemView]) -> list[JobHistoryItemResponse]:
    return [to_job_history_item_response(item) for item in items]


def to_create_job_response(result: CreateJobResult) -> CreateJobResponse:
    return CreateJobResponse(
        job_id=result.job_id,
        total_pages=result.total_pages,
        status=result.status,
    )


def to_page_summary_response(summary: PageSummary) -> PageSummaryResponse:
    return PageSummaryResponse(page_num=summary.page_num, status=summary.status)


def to_page_summaries_response(summaries: list[PageSummary]) -> list[PageSummaryResponse]:
    return [to_page_summary_response(item) for item in summaries]


def to_page_response(view: PageView) -> PageResponse:
    return PageResponse(
        page_num=view.page_num,
        status=view.status,
        content=view.content,
        error=view.error_message,
    )


def to_retry_page_response(result: AcceptedResult) -> RetryPageAcceptedResponse:
    if result.page_num is None:
        raise ValueError("retry page response requires page_num")
    return RetryPageAcceptedResponse(job_id=result.job_id, page_num=result.page_num)


def to_build_response(result: BuildResult) -> BuildResponse:
    return BuildResponse(
        status=result.status,
        output_url=result.output_url,
        download_url=result.download_url,
    )


def to_output_document_response(view: OutputDocumentView) -> OutputDocumentResponse:
    return OutputDocumentResponse(content=view.content, updated_at=view.updated_at)


def to_create_job_command(*, pdf_filename: str, pdf_bytes: bytes) -> CreateJobCommand:
    return CreateJobCommand(pdf_filename=pdf_filename, pdf_bytes=pdf_bytes)


def to_save_page_command(*, job_id: UUID, page_num: int, request: SavePageRequest) -> SavePageCommand:
    return SavePageCommand(job_id=job_id, page_num=page_num, content=request.content)


def to_retry_page_command(*, job_id: UUID, page_num: int) -> RetryPageCommand:
    return RetryPageCommand(job_id=job_id, page_num=page_num)


def to_build_job_command(
    *,
    job_id: UUID,
    request: BuildOutputRequest | None = None,
) -> BuildJobCommand:
    merge_mode = request.merge_mode if request is not None else BuildMergeMode.DIRECT
    return BuildJobCommand(job_id=job_id, merge_mode=merge_mode)


def to_save_output_command(*, job_id: UUID, request: SaveOutputRequest) -> SaveOutputCommand:
    return SaveOutputCommand(job_id=job_id, content=request.content)


def to_discard_output_command(*, job_id: UUID) -> DiscardOutputCommand:
    return DiscardOutputCommand(job_id=job_id)


def output_download_filename_for(source_pdf_name: str) -> str:
    normalized = source_pdf_name.replace("\\", "/").split("/")[-1].strip()
    stem, dot, _suffix = normalized.rpartition(".")
    if dot and stem:
        base = stem
    else:
        base = normalized
    if not base:
        base = "未命名"
    return f"{base}-整理.md"


__all__ = [
    "BuildOutputRequest",
    "BuildResponse",
    "CreateJobResponse",
    "JobHistoryItemResponse",
    "JobResponse",
    "OutputDocumentResponse",
    "PageResponse",
    "PageSummaryResponse",
    "RetryPageAcceptedResponse",
    "SaveOutputRequest",
    "SavePageRequest",
    "output_download_filename_for",
    "to_build_job_command",
    "to_build_response",
    "to_create_job_command",
    "to_create_job_response",
    "to_discard_output_command",
    "to_job_history_item_response",
    "to_job_history_list_response",
    "to_job_response",
    "to_output_document_response",
    "to_page_response",
    "to_page_summaries_response",
    "to_page_summary_response",
    "to_retry_page_command",
    "to_retry_page_response",
    "to_save_output_command",
    "to_save_page_command",
]
