"""Stream application DTO contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.shared_kernel.contracts import EventType


@dataclass(frozen=True, slots=True)
class StreamEventView:
    job_id: UUID
    seq: int
    event_type: EventType
    payload: dict[str, object]
    created_at: datetime


__all__ = ["StreamEventView"]
