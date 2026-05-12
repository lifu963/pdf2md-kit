"""Port contracts used by extraction application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from backend.config.ports import ConfigRepository, SecretStore
from backend.job.ports import (
    EventPublisher,
    JobRepository,
    PageRepository,
    PdfDocumentGateway,
    SourceDocumentStore,
)
from backend.shared_kernel.contracts import ModelConfig
from backend.shared_kernel.time import Clock


class VisionExtractionSession(Protocol):
    def extract_markdown(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        """Extract markdown using a reused client/session."""

    def close(self) -> None:
        """Release any underlying client resources."""


class VisionExtractionGateway(Protocol):
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
        """Extract markdown for one rendered page image."""

    def open_session(self, *, model: ModelConfig, api_key: str) -> VisionExtractionSession:
        """Open one reusable extraction session for a worker."""

    def test_connection(self, *, model: ModelConfig, api_key: str) -> str:
        """Run one minimal probe against the configured vision endpoint."""


class TaskScheduler(Protocol):
    def schedule(
        self,
        *,
        job_id: UUID,
        task_name: str,
        task_factory: Callable[[], Awaitable[None]],
    ) -> bool:
        """Schedule a deduplicated background task."""


__all__ = [
    "Clock",
    "ConfigRepository",
    "EventPublisher",
    "JobRepository",
    "PageRepository",
    "PdfDocumentGateway",
    "SecretStore",
    "SourceDocumentStore",
    "TaskScheduler",
    "VisionExtractionGateway",
    "VisionExtractionSession",
]
