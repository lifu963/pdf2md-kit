"""Build application query contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GetOutputDocumentQuery:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class GetOutputArtifactQuery:
    job_id: UUID


__all__ = ["GetOutputArtifactQuery", "GetOutputDocumentQuery"]
