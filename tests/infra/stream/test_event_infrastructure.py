"""
Step 14: 事件基础设施测试（LiveSubscriberHub + EventPublisher）

验收目标（严格对齐实施步骤）：
1. 单订阅者可以收到发布事件。
2. 多订阅者都能收到同一条 live 事件。
3. 订阅断开后应释放，不再继续接收事件。
4. 事件落盘失败时禁止广播。
5. 同一 job 下事件 seq 必须严格单调递增。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest import TestCase

from backend.infra.stream.event_publisher import EventLogBackedEventPublisher
from backend.infra.stream.live_subscriber_hub import InMemoryLiveSubscriberHub
from backend.shared_kernel.contracts import EventType, JobEvent
from backend.shared_kernel.errors import AppError, ErrorCode


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(*, job_id: uuid.UUID, seq: int, event_type: EventType = EventType.PAGE_PROCESSED) -> JobEvent:
    return JobEvent(
        job_id=job_id,
        seq=seq,
        event_type=event_type,
        payload={"seq": seq, "type": event_type.value},
        created_at=_now(),
    )


async def _next_event(subscriber, *, timeout_seconds: float = 0.2) -> JobEvent:  # type: ignore[no-untyped-def]
    return await asyncio.wait_for(anext(subscriber.stream()), timeout=timeout_seconds)


class _InMemoryEventLogRepository:
    def __init__(self) -> None:
        self._events: dict[uuid.UUID, list[JobEvent]] = {}

    def append(self, event: JobEvent) -> None:
        bucket = self._events.setdefault(event.job_id, [])
        if bucket and event.seq <= bucket[-1].seq:
            raise AppError(
                code=ErrorCode.PERSISTENCE_ERROR,
                message=f"event seq must increase, got {event.seq} <= {bucket[-1].seq}",
            )
        bucket.append(event)

    def list_by_job(self, job_id: uuid.UUID) -> list[JobEvent]:
        return list(self._events.get(job_id, []))


class _FailingEventLogRepository:
    def append(self, event: JobEvent) -> None:
        del event
        raise AppError(code=ErrorCode.PERSISTENCE_ERROR, message="append failed")

    def list_by_job(self, job_id: uuid.UUID) -> list[JobEvent]:
        del job_id
        return []


class TestEventInfrastructure(TestCase):
    def setUp(self) -> None:
        self.job_id = uuid.uuid4()
        self.log_repo = _InMemoryEventLogRepository()
        self.hub = InMemoryLiveSubscriberHub()
        self.publisher = EventLogBackedEventPublisher(
            event_log_repository=self.log_repo,
            live_subscriber_hub=self.hub,
        )

    def test_single_subscriber_receives_live_event(self) -> None:
        subscriber = self.hub.attach(self.job_id)
        event = _event(job_id=self.job_id, seq=1)

        self.publisher.publish(event)
        received = asyncio.run(_next_event(subscriber))

        self.assertEqual(event.seq, received.seq)
        self.assertEqual(event.event_type, received.event_type)
        self.assertEqual([1], [item.seq for item in self.log_repo.list_by_job(self.job_id)])

    def test_multiple_subscribers_receive_same_live_event(self) -> None:
        sub_a = self.hub.attach(self.job_id)
        sub_b = self.hub.attach(self.job_id)
        event = _event(job_id=self.job_id, seq=1, event_type=EventType.EXTRACTION_COMPLETED)

        self.publisher.publish(event)
        received_a = asyncio.run(_next_event(sub_a))
        received_b = asyncio.run(_next_event(sub_b))

        self.assertEqual(event.seq, received_a.seq)
        self.assertEqual(event.seq, received_b.seq)
        self.assertEqual(event.event_type, received_a.event_type)
        self.assertEqual(event.event_type, received_b.event_type)

    def test_detach_releases_subscriber_and_stops_stream(self) -> None:
        subscriber = self.hub.attach(self.job_id)
        first = _event(job_id=self.job_id, seq=1)
        second = _event(job_id=self.job_id, seq=2)

        self.publisher.publish(first)
        _ = asyncio.run(_next_event(subscriber))

        self.hub.detach(self.job_id, subscriber)
        self.publisher.publish(second)

        with self.assertRaises(StopAsyncIteration):
            asyncio.run(_next_event(subscriber))

    def test_publish_never_broadcasts_when_append_fails(self) -> None:
        subscriber = self.hub.attach(self.job_id)
        publisher = EventLogBackedEventPublisher(
            event_log_repository=_FailingEventLogRepository(),
            live_subscriber_hub=self.hub,
        )

        with self.assertRaises(AppError) as ctx:
            publisher.publish(_event(job_id=self.job_id, seq=1))
        self.assertEqual(ErrorCode.PERSISTENCE_ERROR, ctx.exception.code)

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(_next_event(subscriber, timeout_seconds=0.1))

    def test_seq_must_be_strictly_increasing_for_same_job(self) -> None:
        subscriber = self.hub.attach(self.job_id)
        first = _event(job_id=self.job_id, seq=10)
        duplicate = _event(job_id=self.job_id, seq=10)

        self.publisher.publish(first)
        received = asyncio.run(_next_event(subscriber))
        self.assertEqual(10, received.seq)

        with self.assertRaises(AppError) as ctx:
            self.publisher.publish(duplicate)
        self.assertEqual(ErrorCode.PERSISTENCE_ERROR, ctx.exception.code)

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(_next_event(subscriber, timeout_seconds=0.1))
