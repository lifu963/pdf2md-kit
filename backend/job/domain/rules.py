"""Job domain state machine and guard rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from backend.job.domain.models import JobAggregate, JobStatus, PageDocument, PageStatus
from backend.shared_kernel.errors import AppError, ErrorCode


def create_job(
    *,
    job_id: UUID,
    source_pdf_name: str,
    total_pages: int,
    now: datetime,
) -> tuple[JobAggregate, list[PageDocument]]:
    """Create a new job and initialize all pages as pending."""
    if total_pages <= 0:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "total_pages must be positive",
            details={"total_pages": total_pages},
        )

    job = JobAggregate(
        job_id=job_id,
        source_pdf_name=source_pdf_name,
        total_pages=total_pages,
        status=JobStatus.EXTRACTING,
        succeeded_pages=[],
        failed_pages=[],
        created_at=now,
        updated_at=now,
        version=1,
        last_error=None,
    )
    pages = [
        PageDocument(
            job_id=job_id,
            page_num=page_num,
            status=PageStatus.PENDING,
            content=None,
            error_message=None,
            updated_at=now,
        )
        for page_num in range(1, total_pages + 1)
    ]
    return job, pages


def mark_page_done(
    job: JobAggregate,
    page: PageDocument,
    content: str,
    now: datetime,
) -> tuple[JobAggregate, PageDocument]:
    """Apply extraction success for one page."""
    _assert_page_belongs_to_job(job, page)
    _require_job_status(
        job,
        allowed={JobStatus.EXTRACTING},
        error_code=ErrorCode.JOB_STATUS_CONFLICT,
        message="cannot mark page done when job is not extracting",
    )

    next_page = replace(
        page,
        status=PageStatus.DONE,
        content=content,
        error_message=None,
        updated_at=now,
    )
    next_succeeded = _add_page_num(job.succeeded_pages, page.page_num)
    next_failed = _remove_page_num(job.failed_pages, page.page_num)
    next_job = _advance_after_page_processed(
        job=job,
        succeeded_pages=next_succeeded,
        failed_pages=next_failed,
        now=now,
    )
    return next_job, next_page


def mark_page_failed(
    job: JobAggregate,
    page: PageDocument,
    error_message: str,
    now: datetime,
) -> tuple[JobAggregate, PageDocument]:
    """Apply extraction failure for one page while allowing job to continue."""
    _assert_page_belongs_to_job(job, page)
    _require_job_status(
        job,
        allowed={JobStatus.EXTRACTING},
        error_code=ErrorCode.JOB_STATUS_CONFLICT,
        message="cannot mark page failed when job is not extracting",
    )

    next_page = replace(
        page,
        status=PageStatus.FAILED,
        content=None,
        error_message=error_message,
        updated_at=now,
    )
    next_succeeded = _remove_page_num(job.succeeded_pages, page.page_num)
    next_failed = _add_page_num(job.failed_pages, page.page_num)
    next_job = _advance_after_page_processed(
        job=job,
        succeeded_pages=next_succeeded,
        failed_pages=next_failed,
        now=now,
    )
    return next_job, next_page


def save_page(
    job: JobAggregate,
    page: PageDocument,
    content: str,
    now: datetime,
) -> tuple[JobAggregate, PageDocument]:
    """Save one page content with state guards."""
    _assert_page_belongs_to_job(job, page)
    _assert_page_write_allowed(job, for_retry=False)

    next_page = replace(
        page,
        status=PageStatus.DONE,
        content=content,
        error_message=None,
        updated_at=now,
    )

    next_succeeded = _add_page_num(job.succeeded_pages, page.page_num)
    next_failed = _remove_page_num(job.failed_pages, page.page_num)

    if job.status == JobStatus.EXTRACTED:
        next_status = JobStatus.EXTRACTED
    else:
        processed_count = len(next_succeeded) + len(next_failed)
        next_status = (
            JobStatus.EXTRACTED
            if processed_count == job.total_pages and not next_failed
            else JobStatus.EXTRACTING
        )

    next_job = _evolve_job(
        job,
        now=now,
        status=next_status,
        succeeded_pages=next_succeeded,
        failed_pages=next_failed,
    )
    return next_job, next_page


def retry_page(
    job: JobAggregate,
    page: PageDocument,
    now: datetime,
) -> tuple[JobAggregate, PageDocument]:
    """Retry one terminal page and roll back processed counters."""
    _assert_page_belongs_to_job(job, page)
    _assert_page_write_allowed(job, for_retry=True)
    if page.status not in {PageStatus.DONE, PageStatus.FAILED}:
        raise AppError(
            ErrorCode.PAGE_RETRY_FORBIDDEN,
            "only done/failed pages can be retried",
            details={"page_num": page.page_num, "page_status": page.status.value},
        )

    next_page = replace(
        page,
        status=PageStatus.EXTRACTING,
        content=None,
        error_message=None,
        updated_at=now,
    )
    next_succeeded = _remove_page_num(job.succeeded_pages, page.page_num)
    next_failed = _remove_page_num(job.failed_pages, page.page_num)
    next_job = _evolve_job(
        job,
        now=now,
        status=JobStatus.EXTRACTING,
        succeeded_pages=next_succeeded,
        failed_pages=next_failed,
    )
    return next_job, next_page


def start_build(job: JobAggregate, now: datetime) -> JobAggregate:
    """Enter building state from extracted."""
    if job.status != JobStatus.EXTRACTED:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "build is only allowed in extracted state",
            details={"status": job.status.value},
        )
    if job.failed_pages:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "build is blocked while failed pages exist",
            details={"failed_pages": job.failed_pages},
        )
    return _evolve_job(job, now=now, status=JobStatus.BUILDING)


def finish_build(job: JobAggregate, now: datetime) -> JobAggregate:
    """Mark build success and enter ready state."""
    if job.status != JobStatus.BUILDING:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "build can only be finished from building state",
            details={"status": job.status.value},
        )
    return _evolve_job(job, now=now, status=JobStatus.READY)


def discard_output(job: JobAggregate, now: datetime) -> JobAggregate:
    """Discard built output and return to extracted state."""
    if job.status != JobStatus.READY:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "output discard is only allowed in ready state",
            details={"status": job.status.value},
        )
    return _evolve_job(
        job,
        now=now,
        status=JobStatus.EXTRACTED,
        last_error=None,
    )


def fail_build(
    job: JobAggregate,
    now: datetime,
    *,
    error_message: str | None = None,
) -> JobAggregate:
    """Rollback a failed build to extracted."""
    if job.status != JobStatus.BUILDING:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "build can only fail from building state",
            details={"status": job.status.value},
        )
    return _evolve_job(
        job,
        now=now,
        status=JobStatus.EXTRACTED,
        last_error=error_message,
    )


def save_output(job: JobAggregate, now: datetime) -> JobAggregate:
    """Guard output editing and keep ready state stable."""
    if job.status == JobStatus.BUILDING:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "writes are not allowed while job is building",
            details={"status": job.status.value},
        )
    if job.status != JobStatus.READY:
        raise AppError(
            ErrorCode.OUTPUT_EDIT_FORBIDDEN,
            "output can only be edited in ready state",
            details={"status": job.status.value},
        )
    return _evolve_job(job, now=now, status=JobStatus.READY)


def fail_job(job: JobAggregate, error_message: str, now: datetime) -> JobAggregate:
    """Mark an unrecoverable job-level failure."""
    return _evolve_job(
        job,
        now=now,
        status=JobStatus.FAILED,
        last_error=error_message,
    )


def _assert_page_belongs_to_job(job: JobAggregate, page: PageDocument) -> None:
    if page.job_id != job.job_id:
        raise AppError(
            ErrorCode.PAGE_NOT_FOUND,
            "page does not belong to job",
            details={"job_id": str(job.job_id), "page_job_id": str(page.job_id)},
        )
    if page.page_num < 1 or page.page_num > job.total_pages:
        raise AppError(
            ErrorCode.PAGE_NOT_FOUND,
            "page number is out of range",
            details={"page_num": page.page_num, "total_pages": job.total_pages},
        )


def _require_job_status(
    job: JobAggregate,
    *,
    allowed: set[JobStatus],
    error_code: ErrorCode,
    message: str,
) -> None:
    if job.status not in allowed:
        raise AppError(
            error_code,
            message,
            details={"status": job.status.value},
        )


def _assert_page_write_allowed(job: JobAggregate, *, for_retry: bool) -> None:
    if job.status == JobStatus.BUILDING:
        raise AppError(
            ErrorCode.JOB_STATUS_CONFLICT,
            "writes are not allowed while job is building",
            details={"status": job.status.value},
        )
    if job.status == JobStatus.READY:
        code = ErrorCode.PAGE_RETRY_FORBIDDEN if for_retry else ErrorCode.PAGE_EDIT_FORBIDDEN
        raise AppError(
            code,
            "pages are frozen in ready state",
            details={"status": job.status.value},
        )
    if job.status not in {JobStatus.EXTRACTING, JobStatus.EXTRACTED}:
        code = ErrorCode.PAGE_RETRY_FORBIDDEN if for_retry else ErrorCode.PAGE_EDIT_FORBIDDEN
        raise AppError(
            code,
            "page write is only allowed in extracting/extracted",
            details={"status": job.status.value},
        )


def _advance_after_page_processed(
    *,
    job: JobAggregate,
    succeeded_pages: list[int],
    failed_pages: list[int],
    now: datetime,
) -> JobAggregate:
    processed_count = len(succeeded_pages) + len(failed_pages)
    next_status = (
        JobStatus.EXTRACTED
        if processed_count == job.total_pages and not failed_pages
        else JobStatus.EXTRACTING
    )
    return _evolve_job(
        job,
        now=now,
        status=next_status,
        succeeded_pages=succeeded_pages,
        failed_pages=failed_pages,
    )


def _add_page_num(page_nums: list[int], page_num: int) -> list[int]:
    return sorted(set(page_nums + [page_num]))


def _remove_page_num(page_nums: list[int], page_num: int) -> list[int]:
    return [item for item in page_nums if item != page_num]


def _evolve_job(
    job: JobAggregate,
    *,
    now: datetime,
    status: JobStatus,
    succeeded_pages: list[int] | None = None,
    failed_pages: list[int] | None = None,
    last_error: str | None | object = ...,
) -> JobAggregate:
    if last_error is ...:
        next_last_error = job.last_error
    else:
        next_last_error = last_error

    return replace(
        job,
        status=status,
        succeeded_pages=list(job.succeeded_pages if succeeded_pages is None else succeeded_pages),
        failed_pages=list(job.failed_pages if failed_pages is None else failed_pages),
        updated_at=now,
        version=job.version + 1,
        last_error=next_last_error,
    )


__all__ = [
    "create_job",
    "discard_output",
    "fail_build",
    "fail_job",
    "finish_build",
    "mark_page_done",
    "mark_page_failed",
    "retry_page",
    "save_output",
    "save_page",
    "start_build",
]
