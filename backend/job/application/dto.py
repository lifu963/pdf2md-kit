"""Job application DTO contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.job.domain.models import JobStatus, PageStatus


@dataclass(frozen=True, slots=True)
class JobView:
    job_id: UUID
    status: JobStatus
    total_pages: int
    succeeded_pages: list[int]
    failed_pages: list[int]
    processed_count: int


@dataclass(frozen=True, slots=True)
class JobHistoryItemView:
    job_id: UUID
    pdf_name: str
    status: JobStatus
    total_pages: int
    processed_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PageSummary:
    page_num: int
    status: PageStatus


@dataclass(frozen=True, slots=True)
class PageView:
    job_id: UUID
    page_num: int
    status: PageStatus
    content: str | None
    error_message: str | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class OutputDocumentView:
    job_id: UUID
    content: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CreateJobResult:
    job_id: UUID
    total_pages: int
    status: JobStatus


@dataclass(frozen=True, slots=True)
class AcceptedResult:
    job_id: UUID
    page_num: int | None = None


@dataclass(frozen=True, slots=True)
class BuildResult:
    status: JobStatus
    output_url: str
    download_url: str


__all__ = [
    "AcceptedResult",
    "BuildResult",
    "CreateJobResult",
    "JobHistoryItemView",
    "JobView",
    "OutputDocumentView",
    "PageSummary",
    "PageView",
]
