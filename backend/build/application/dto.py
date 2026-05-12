"""Build application DTO contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.shared_kernel.contracts import ArtifactRef, ArtifactType, JobStatus


@dataclass(frozen=True, slots=True)
class BuildOutputResult:
    job_id: UUID
    status: JobStatus
    artifact: ArtifactRef


@dataclass(frozen=True, slots=True)
class OutputDocumentView:
    job_id: UUID
    content: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OutputArtifactView:
    job_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    content_type: str
    filename: str


__all__ = ["BuildOutputResult", "OutputArtifactView", "OutputDocumentView"]
