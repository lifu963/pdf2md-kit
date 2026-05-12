"""Build application command contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class BuildOutputCommand:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class SaveOutputDocumentCommand:
    job_id: UUID
    content: str


__all__ = ["BuildOutputCommand", "SaveOutputDocumentCommand"]
