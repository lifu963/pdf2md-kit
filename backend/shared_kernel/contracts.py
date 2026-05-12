"""Cross-module domain contracts shared via shared-kernel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class JobStatus(str, Enum):
    IDLE = "idle"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class PageStatus(str, Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    DONE = "done"
    FAILED = "failed"


class BuildMergeMode(str, Enum):
    DIRECT = "direct"
    SEPARATOR = "separator"
    SEPARATOR_WITH_PAGE_NUMBER = "separator_with_page_number"


class ArtifactType(str, Enum):
    OUTPUT_MD = "output_md"
    KNOWLEDGE_POINTS_MD = "knowledge_points_md"
    FLASHCARDS_MD = "flashcards_md"
    STEP1_CHAT_JSON = "step1_chat_json"
    STEP2_CHAT_JSON = "step2_chat_json"


class EventType(str, Enum):
    PAGE_PROCESSED = "page_processed"
    EXTRACTION_COMPLETED = "extraction_completed"
    BUILD_COMPLETED = "build_completed"
    STATUS_CHANGED = "status_changed"
    JOB_FAILED = "job_failed"


@dataclass(frozen=True, slots=True)
class ModelConfig:
    name: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ExtractConfig:
    dpi: int
    concurrency: int
    max_retries: int
    prompt: str


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    model: ModelConfig
    extract: ExtractConfig
    has_api_key: bool


@dataclass(slots=True)
class PageDocument:
    job_id: UUID
    page_num: int
    status: PageStatus
    content: str | None
    error_message: str | None
    updated_at: datetime


@dataclass(slots=True)
class JobAggregate:
    job_id: UUID
    source_pdf_name: str
    total_pages: int
    status: JobStatus
    succeeded_pages: list[int]
    failed_pages: list[int]
    created_at: datetime
    updated_at: datetime
    version: int
    last_error: str | None

    @property
    def processed_count(self) -> int:
        return len(self.succeeded_pages) + len(self.failed_pages)


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    job_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    content_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class SourceDocumentRef:
    job_id: UUID
    relative_path: str
    content_type: str
    filename: str
    size_bytes: int


@dataclass(slots=True)
class OutputDocument:
    job_id: UUID
    content: str
    updated_at: datetime


@dataclass(slots=True)
class JobEvent:
    job_id: UUID
    seq: int
    event_type: EventType
    payload: dict[str, Any]
    created_at: datetime


__all__ = [
    "ArtifactRef",
    "ArtifactType",
    "BuildMergeMode",
    "EventType",
    "ExtractConfig",
    "JobAggregate",
    "JobEvent",
    "JobStatus",
    "ModelConfig",
    "OutputDocument",
    "PageDocument",
    "PageStatus",
    "RuntimeConfig",
    "SourceDocumentRef",
]
