"""Flashcard (v2 placeholder) DTO contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from backend.shared_kernel.contracts import ArtifactType


class FlashcardPhase(str, Enum):
    STEP1 = "step1"
    STEP2 = "step2"


@dataclass(frozen=True, slots=True)
class FlashcardAcceptedResult:
    job_id: UUID
    phase: FlashcardPhase


@dataclass(frozen=True, slots=True)
class FlashcardArtifactView:
    job_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    content_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class FlashcardChatTurn:
    role: str
    content: str
    created_at: datetime


__all__ = [
    "FlashcardAcceptedResult",
    "FlashcardArtifactView",
    "FlashcardChatTurn",
    "FlashcardPhase",
]
