"""Stream application query contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SubscribeJobEventsQuery:
    job_id: UUID
    replay: bool = True


__all__ = ["SubscribeJobEventsQuery"]
