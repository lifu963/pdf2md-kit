"""Flashcard (v2 placeholder) command contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StartStep1Command:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class ChatStep1Command:
    job_id: UUID
    message: str


@dataclass(frozen=True, slots=True)
class StartStep2Command:
    job_id: UUID
    card_limit: int | None = None


@dataclass(frozen=True, slots=True)
class ChatStep2Command:
    job_id: UUID
    message: str


__all__ = [
    "ChatStep1Command",
    "ChatStep2Command",
    "StartStep1Command",
    "StartStep2Command",
]
