"""Port contracts used by stream application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID

from backend.job.ports import JobRepository
from backend.shared_kernel.contracts import JobEvent


class EventLogRepository(Protocol):
    def append(self, event: JobEvent) -> None:
        """Append one event to persistent event log."""

    def list_by_job(self, job_id: UUID) -> list[JobEvent]:
        """Read all events for one job ordered by seq."""


class LiveEventSubscriber(Protocol):
    def stream(self) -> AsyncIterator[JobEvent]:
        """Yield live events for one subscription."""


class LiveSubscriberHub(Protocol):
    def attach(self, job_id: UUID) -> LiveEventSubscriber:
        """Attach a live subscriber to a job channel."""

    def detach(self, job_id: UUID, subscriber: LiveEventSubscriber) -> None:
        """Detach a live subscriber from a job channel."""

    def broadcast(self, event: JobEvent) -> None:
        """Broadcast one live event to all subscribers."""


__all__ = ["EventLogRepository", "JobRepository", "LiveEventSubscriber", "LiveSubscriberHub"]
