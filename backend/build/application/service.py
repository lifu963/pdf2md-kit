"""Build application use-cases."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from backend.build.application.simple_pipeline import SimpleMarkdownBuildPipeline
from backend.build.ports import (
    ArtifactRepository,
    Clock,
    JobRepository,
    MarkdownBuildPipeline,
    PageRepository,
)
from backend.shared_kernel.contracts import (
    ArtifactRef,
    BuildMergeMode,
    JobAggregate,
    JobStatus,
    OutputDocument,
    PageDocument,
)
from backend.shared_kernel.errors import AppError, ErrorCode


_UNSET = object()


class BuildApplication:
    """Application service for output build/read/save flows."""

    def __init__(
        self,
        *,
        job_repository: JobRepository,
        page_repository: PageRepository,
        artifact_repository: ArtifactRepository,
        clock: Clock,
        pipeline: MarkdownBuildPipeline | None = None,
    ) -> None:
        self._job_repository = job_repository
        self._page_repository = page_repository
        self._artifact_repository = artifact_repository
        self._clock = clock
        self._pipeline = pipeline or SimpleMarkdownBuildPipeline()

    def build_output(
        self,
        job_id: UUID,
        merge_mode: BuildMergeMode = BuildMergeMode.DIRECT,
    ) -> ArtifactRef:
        job = self._job_repository.get(job_id)
        if job.status != JobStatus.EXTRACTED:
            raise AppError(
                code=ErrorCode.JOB_STATUS_CONFLICT,
                message="build is only allowed in extracted state",
                details={"status": job.status.value},
            )
        if job.failed_pages:
            raise AppError(
                code=ErrorCode.JOB_STATUS_CONFLICT,
                message="build is blocked while failed pages exist",
                details={"failed_pages": job.failed_pages},
            )

        building_job = _evolve_job(job, now=self._clock.now(), status=JobStatus.BUILDING, last_error=None)
        self._job_repository.save(building_job)

        try:
            pages = self._page_repository.list_by_job(job_id)
            page_contents = _normalize_pages_for_build(
                pages=pages,
                expected_total_pages=job.total_pages,
            )
            output_content = self._pipeline.build_output_content(
                page_contents=page_contents,
                merge_mode=merge_mode,
            )

            artifact = self._artifact_repository.save_output(job_id, output_content)
            ready_job = _evolve_job(
                building_job,
                now=self._clock.now(),
                status=JobStatus.READY,
                last_error=None,
            )
            self._job_repository.save(ready_job)
            return artifact
        except Exception as exc:
            rolled_back = _evolve_job(
                building_job,
                now=self._clock.now(),
                status=job.status,
                last_error=str(exc),
            )
            self._job_repository.save(rolled_back)
            raise

    def get_output_document(self, job_id: UUID) -> OutputDocument:
        job = self._job_repository.get(job_id)
        _require_output_ready(job)
        return self._artifact_repository.get_output_document(job_id)

    def save_output_document(self, job_id: UUID, content: str) -> OutputDocument:
        job = self._job_repository.get(job_id)
        if job.status == JobStatus.BUILDING:
            raise AppError(
                code=ErrorCode.JOB_STATUS_CONFLICT,
                message="writes are not allowed while job is building",
                details={"status": job.status.value},
            )
        if job.status != JobStatus.READY:
            raise AppError(
                code=ErrorCode.OUTPUT_EDIT_FORBIDDEN,
                message="output can only be edited in ready state",
                details={"status": job.status.value},
            )

        self._artifact_repository.save_output(job_id, content)
        return self._artifact_repository.get_output_document(job_id)

    def get_output_artifact(self, job_id: UUID) -> ArtifactRef:
        job = self._job_repository.get(job_id)
        _require_output_ready(job)
        return self._artifact_repository.get_output_artifact(job_id)

    def discard_output(self, job_id: UUID) -> JobAggregate:
        job = self._job_repository.get(job_id)
        if job.status != JobStatus.READY:
            raise AppError(
                code=ErrorCode.JOB_STATUS_CONFLICT,
                message="output discard is only allowed in ready state",
                details={"status": job.status.value},
            )

        discarded_job = _evolve_job(
            job,
            now=self._clock.now(),
            status=JobStatus.EXTRACTED,
            last_error=None,
        )
        self._job_repository.save(discarded_job)
        self._artifact_repository.delete_output(job_id)
        return discarded_job


def _normalize_pages_for_build(*, pages: list[PageDocument], expected_total_pages: int) -> list[str]:
    if len(pages) != expected_total_pages:
        raise AppError(
            code=ErrorCode.JOB_STATUS_CONFLICT,
            message="pages are incomplete for build",
            details={"expected_total_pages": expected_total_pages, "actual_pages": len(pages)},
        )

    sorted_pages = sorted(pages, key=lambda item: item.page_num)
    expected_page_nums = list(range(1, expected_total_pages + 1))
    actual_page_nums = [page.page_num for page in sorted_pages]
    if actual_page_nums != expected_page_nums:
        raise AppError(
            code=ErrorCode.JOB_STATUS_CONFLICT,
            message="page numbers are not continuous for build",
            details={"expected_page_nums": expected_page_nums, "actual_page_nums": actual_page_nums},
        )

    page_contents: list[str] = []
    for page in sorted_pages:
        page_contents.append(page.content or "")
    return page_contents


def _require_output_ready(job: JobAggregate) -> None:
    if job.status != JobStatus.READY:
        raise AppError(
            code=ErrorCode.OUTPUT_NOT_READY,
            message="output is not ready in current job state",
            details={"status": job.status.value},
        )


def _evolve_job(
    job: JobAggregate,
    *,
    now,
    status: JobStatus,
    last_error: str | None | object = _UNSET,
) -> JobAggregate:
    # Mirror backend.job.domain.rules._evolve_job semantics exactly:
    # defensive shallow copies of page-number lists prevent aliasing with the
    # previous aggregate instance.
    if last_error is _UNSET:
        next_last_error = job.last_error
    else:
        next_last_error = last_error

    return replace(
        job,
        status=status,
        succeeded_pages=list(job.succeeded_pages),
        failed_pages=list(job.failed_pages),
        updated_at=now,
        version=job.version + 1,
        last_error=next_last_error,
    )


__all__ = ["BuildApplication"]

