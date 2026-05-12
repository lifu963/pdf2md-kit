"""EventPublisher adapter: append event log first, then broadcast live."""

from __future__ import annotations

from backend.shared_kernel.contracts import JobEvent
from backend.stream.ports import EventLogRepository, LiveSubscriberHub


class EventLogBackedEventPublisher:
    """Publish events with durable-first ordering."""

    def __init__(
        self,
        *,
        event_log_repository: EventLogRepository,
        live_subscriber_hub: LiveSubscriberHub,
    ) -> None:
        self._event_log_repository = event_log_repository
        self._live_subscriber_hub = live_subscriber_hub

    def publish(self, event: JobEvent) -> None:
        self._event_log_repository.append(event)
        self._live_subscriber_hub.broadcast(event)


__all__ = ["EventLogBackedEventPublisher"]
