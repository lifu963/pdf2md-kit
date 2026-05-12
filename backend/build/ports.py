"""Port contracts used by build application."""

from __future__ import annotations

from typing import Protocol

from backend.job.ports import ArtifactRepository, JobRepository, PageRepository
from backend.shared_kernel.contracts import BuildMergeMode
from backend.shared_kernel.time import Clock


class MarkdownBuildPipeline(Protocol):
    def build_output_content(
        self,
        *,
        page_contents: list[str],
        merge_mode: BuildMergeMode,
    ) -> str:
        """Build output markdown content from extracted pages."""


__all__ = [
    "ArtifactRepository",
    "Clock",
    "JobRepository",
    "MarkdownBuildPipeline",
    "PageRepository",
]
