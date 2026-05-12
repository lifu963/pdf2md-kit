"""Stream application command contracts."""

from __future__ import annotations

from dataclasses import dataclass

from backend.shared_kernel.contracts import JobEvent


@dataclass(frozen=True, slots=True)
class PublishEventCommand:
    event: JobEvent


__all__ = ["PublishEventCommand"]
