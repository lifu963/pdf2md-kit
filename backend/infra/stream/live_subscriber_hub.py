"""In-process live subscriber hub for job event broadcasting."""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator
from uuid import UUID

from backend.shared_kernel.contracts import JobEvent
from backend.stream.ports import LiveEventSubscriber

_END = object()


class _QueueLiveEventSubscriber:
    """Thread-safe subscriber backed by a blocking queue."""

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[JobEvent | object] = queue.SimpleQueue()
        self._closed = threading.Event()

    def stream(self) -> AsyncIterator[JobEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[JobEvent]:
        while True:
            try:
                item = await asyncio.to_thread(self._queue.get, True, 0.05)
            except queue.Empty:
                if self._closed.is_set():
                    return
                continue
            if item is _END:
                return
            assert isinstance(item, JobEvent)
            yield item

    def push(self, event: JobEvent) -> None:
        if self._closed.is_set():
            return
        self._queue.put(event)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put(_END)


class InMemoryLiveSubscriberHub:
    """Manage in-process live subscribers per job."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[UUID, set[_QueueLiveEventSubscriber]] = {}

    def attach(self, job_id: UUID) -> LiveEventSubscriber:
        subscriber = _QueueLiveEventSubscriber()
        with self._lock:
            bucket = self._subscribers.setdefault(job_id, set())
            bucket.add(subscriber)
        return subscriber

    def detach(self, job_id: UUID, subscriber: LiveEventSubscriber) -> None:
        if not isinstance(subscriber, _QueueLiveEventSubscriber):
            return

        with self._lock:
            bucket = self._subscribers.get(job_id)
            if not bucket:
                return
            bucket.discard(subscriber)
            if not bucket:
                self._subscribers.pop(job_id, None)
        subscriber.close()

    def broadcast(self, event: JobEvent) -> None:
        with self._lock:
            bucket = list(self._subscribers.get(event.job_id, set()))
        for subscriber in bucket:
            subscriber.push(event)


__all__ = ["InMemoryLiveSubscriberHub"]
