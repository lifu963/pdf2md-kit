"""Flashcard (v2 placeholder) query contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GetStep1ArtifactQuery:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class GetStep2ArtifactQuery:
    job_id: UUID


__all__ = ["GetStep1ArtifactQuery", "GetStep2ArtifactQuery"]
