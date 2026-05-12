"""Extraction application DTO contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.shared_kernel.contracts import JobStatus


@dataclass(frozen=True, slots=True)
class ExtractionProgressView:
    job_id: UUID
    status: JobStatus
    total_pages: int
    processed_count: int


@dataclass(frozen=True, slots=True)
class SinglePagePreviewResult:
    page_num: int
    content: str


__all__ = ["ExtractionProgressView", "SinglePagePreviewResult"]
