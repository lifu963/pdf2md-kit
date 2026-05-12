"""Port contracts used by job application."""

from __future__ import annotations

from typing import BinaryIO, Protocol
from uuid import UUID

from backend.job.domain.models import (
    ArtifactRef,
    BuildMergeMode,
    JobAggregate,
    JobEvent,
    OutputDocument,
    PageDocument,
    SourceDocumentRef,
)


class JobRepository(Protocol):
    def exists(self, job_id: UUID) -> bool:
        """Check whether a job exists."""

    def get(self, job_id: UUID) -> JobAggregate:
        """Load a job aggregate."""

    def list_all(self) -> list[JobAggregate]:
        """List all persisted jobs."""

    def save(self, job: JobAggregate) -> None:
        """Persist a full job aggregate."""

    def delete(self, job_id: UUID) -> None:
        """Delete one job and its persisted artifacts."""


class PageRepository(Protocol):
    def list_summaries_by_job(self, job_id: UUID) -> list[PageDocument]:
        """List page summaries without reading markdown bodies."""

    def list_by_job(self, job_id: UUID) -> list[PageDocument]:
        """List all pages for a job."""

    def get(self, job_id: UUID, page_num: int) -> PageDocument:
        """Load one page document."""

    def save(self, page: PageDocument) -> None:
        """Persist one page document."""


class SourceDocumentStore(Protocol):
    def save_source(self, job_id: UUID, pdf_filename: str, pdf_bytes: bytes) -> SourceDocumentRef:
        """Persist source PDF and return metadata."""

    def get_source(self, job_id: UUID) -> SourceDocumentRef:
        """Return source PDF metadata."""

    def open_read(self, job_id: UUID) -> BinaryIO:
        """Return a readable binary stream for source.pdf."""


class ArtifactRepository(Protocol):
    def save_output(self, job_id: UUID, content: str) -> ArtifactRef:
        """Persist output.md and return artifact metadata."""

    def get_output_document(self, job_id: UUID) -> OutputDocument:
        """Read output document content."""

    def get_output_artifact(self, job_id: UUID) -> ArtifactRef:
        """Read output artifact metadata."""

    def delete_output(self, job_id: UUID) -> None:
        """Delete output.md when abandoning the built artifact."""


class EventPublisher(Protocol):
    def publish(self, event: JobEvent) -> None:
        """Persist and publish one domain event."""


class ExtractionApplication(Protocol):
    def start_job_extraction(self, job_id: UUID) -> None:
        """Start extracting a whole job."""

    def retry_page_extraction(self, job_id: UUID, page_num: int) -> None:
        """Retry extraction for one page."""


class BuildApplication(Protocol):
    def build_output(
        self,
        job_id: UUID,
        merge_mode: BuildMergeMode = BuildMergeMode.DIRECT,
    ) -> ArtifactRef:
        """Build output artifact from pages."""

    def get_output_document(self, job_id: UUID) -> OutputDocument:
        """Read output document."""

    def save_output_document(self, job_id: UUID, content: str) -> OutputDocument:
        """Persist edited output document."""

    def get_output_artifact(self, job_id: UUID) -> ArtifactRef:
        """Read output artifact metadata."""

    def discard_output(self, job_id: UUID) -> JobAggregate:
        """Discard built output and reopen the job in extracted state."""


class PdfRenderSession(Protocol):
    @property
    def page_count(self) -> int:
        """Total page count of the opened document, without re-opening it."""

    def render_page(self, page_num: int, dpi: int) -> bytes:
        """Render one page as PNG bytes with a reused opened document."""

    def close(self) -> None:
        """Release any underlying PDF document resources."""


class PdfDocumentGateway(Protocol):
    def count_pages(self, pdf_bytes: bytes) -> int:
        """Count PDF pages for create_job command."""

    def render_page(self, pdf_bytes: bytes, page_num: int, dpi: int) -> bytes:
        """Render one page as PNG bytes for extraction."""

    def open_render_session(self, pdf_bytes: bytes) -> PdfRenderSession:
        """Open one reusable render session for multiple page renders."""


__all__ = [
    "ArtifactRepository",
    "BuildApplication",
    "EventPublisher",
    "ExtractionApplication",
    "JobRepository",
    "PageRepository",
    "PdfDocumentGateway",
    "PdfRenderSession",
    "SourceDocumentStore",
]
