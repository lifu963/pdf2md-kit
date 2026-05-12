"""Stream application use-cases."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from backend.shared_kernel.contracts import EventType, JobEvent, JobStatus
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.stream.ports import EventLogRepository, JobRepository, LiveEventSubscriber, LiveSubscriberHub

_TERMINAL_EVENT_TYPES = {
    EventType.EXTRACTION_COMPLETED,
    EventType.JOB_FAILED,
    EventType.BUILD_COMPLETED,
}
_ACTIVE_JOB_STATUSES = {
    JobStatus.EXTRACTING,
    JobStatus.BUILDING,
}


class StreamApplication:
    """Application service for event publish and replay+live subscription."""

    def __init__(
        self,
        *,
        event_log_repository: EventLogRepository,
        live_subscriber_hub: LiveSubscriberHub,
        job_repository: JobRepository,
    ) -> None:
        self._event_log_repository = event_log_repository
        self._live_subscriber_hub = live_subscriber_hub
        self._job_repository = job_repository

    def publish(self, event: JobEvent) -> None:
        self._event_log_repository.append(event)
        self._live_subscriber_hub.broadcast(event)

    def load_replay_events(
        self,
        job_id: UUID,
        *,
        for_live_follow: bool = False,
    ) -> list[JobEvent]:
        _ensure_job_exists(self._job_repository, job_id)
        events = sorted(
            self._event_log_repository.list_by_job(job_id),
            key=lambda item: item.seq,
        )
        if not for_live_follow:
            return events
        job = self._job_repository.get(job_id)
        return _replay_window_for_job_status(events=events, job_status=job.status)

    def has_terminal_event(self, *, job_id: UUID, events: list[JobEvent]) -> bool:
        job = self._job_repository.get(job_id)
        return _should_close_after_replay(events=events, job_status=job.status)

    async def subscribe_job_events(
        self,
        job_id: UUID,
        replay: bool = True,
    ) -> AsyncIterator[JobEvent]:
        historical_events: list[JobEvent] = []
        if replay:
            historical_events = self.load_replay_events(
                job_id,
                for_live_follow=True,
            )
            for event in historical_events:
                yield event

            if self.has_terminal_event(job_id=job_id, events=historical_events):
                return
        else:
            _ensure_job_exists(self._job_repository, job_id)

        subscriber: LiveEventSubscriber | None = None
        try:
            subscriber = self._live_subscriber_hub.attach(job_id)
            async for event in subscriber.stream():
                yield event
                if _is_terminal_event(event):
                    return
        finally:
            if subscriber is not None:
                self._live_subscriber_hub.detach(job_id, subscriber)


def _has_terminal_event(events: list[JobEvent]) -> bool:
    return any(_is_terminal_event(event) for event in events)


def _replay_window_for_job_status(
    *,
    events: list[JobEvent],
    job_status: JobStatus,
) -> list[JobEvent]:
    if job_status not in _ACTIVE_JOB_STATUSES:
        return events
    return _events_after_latest_terminal(events)


def _events_after_latest_terminal(events: list[JobEvent]) -> list[JobEvent]:
    last_terminal_index = -1
    for index, event in enumerate(events):
        if _is_terminal_event(event):
            last_terminal_index = index
    if last_terminal_index < 0:
        return events
    return events[last_terminal_index + 1 :]


def _should_close_after_replay(
    *,
    events: list[JobEvent],
    job_status: JobStatus,
) -> bool:
    if job_status in _ACTIVE_JOB_STATUSES:
        return False
    return _has_terminal_event(events)


def _is_terminal_event(event: JobEvent) -> bool:
    return event.event_type in _TERMINAL_EVENT_TYPES


def _ensure_job_exists(job_repository: JobRepository, job_id: UUID) -> None:
    if job_repository.exists(job_id):
        return
    raise AppError(
        code=ErrorCode.JOB_NOT_FOUND,
        message="job does not exist",
        details={"job_id": str(job_id)},
    )


__all__ = ["StreamApplication"]
