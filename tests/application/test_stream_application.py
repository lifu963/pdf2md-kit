"""
Step 15: stream-application 应用层测试

验收目标（严格对齐实施步骤）：
1. 空历史：订阅时无历史事件，也应能切换到 live 并收到新事件。
2. 有历史：先按 seq 升序 replay，再切换 live。
3. 终态历史：replay 完成后立即关闭连接，不挂空订阅。
4. 实时广播：多个订阅者都能收到同一条 live 事件。
5. 损坏日志：历史读取损坏必须抛出 STATE_CORRUPTED。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from backend.infra.stream.live_subscriber_hub import InMemoryLiveSubscriberHub
from backend.shared_kernel.contracts import EventType, JobEvent, JobStatus
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.stream.application.service import StreamApplication


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    *,
    job_id: uuid.UUID,
    seq: int,
    event_type: EventType = EventType.PAGE_PROCESSED,
    payload: dict[str, object] | None = None,
) -> JobEvent:
    return JobEvent(
        job_id=job_id,
        seq=seq,
        event_type=event_type,
        payload=payload or {"seq": seq, "type": event_type.value},
        created_at=_now(),
    )


async def _next_event(stream, *, timeout_seconds: float = 0.3) -> JobEvent:  # type: ignore[no-untyped-def]
    return await asyncio.wait_for(anext(stream), timeout=timeout_seconds)


async def _collect_events(stream, total: int) -> list[JobEvent]:  # type: ignore[no-untyped-def]
    events: list[JobEvent] = []
    for _ in range(total):
        events.append(await _next_event(stream))
    return events


async def _await_live_event(
    *,
    stream,
    app: StreamApplication,
    event: JobEvent,
) -> JobEvent:  # type: ignore[no-untyped-def]
    waiter = asyncio.create_task(_next_event(stream, timeout_seconds=0.5))
    await asyncio.sleep(0)
    app.publish(event)
    return await waiter


class _InMemoryJobRepository:
    def __init__(self) -> None:
        self._job_statuses: dict[uuid.UUID, JobStatus] = {}

    def add(self, job_id: uuid.UUID, *, status: JobStatus = JobStatus.EXTRACTING) -> None:
        self._job_statuses[job_id] = status

    def set_status(self, job_id: uuid.UUID, status: JobStatus) -> None:
        if job_id not in self._job_statuses:
            raise KeyError(f"job {job_id} not found")
        self._job_statuses[job_id] = status

    def exists(self, job_id: uuid.UUID) -> bool:
        return job_id in self._job_statuses

    def get(self, job_id: uuid.UUID):  # type: ignore[no-untyped-def]
        if job_id not in self._job_statuses:
            raise KeyError(f"job {job_id} not found")
        return SimpleNamespace(status=self._job_statuses[job_id], job_id=job_id)

    def save(self, job):  # type: ignore[no-untyped-def]
        self._job_statuses[job.job_id] = job.status


class _InMemoryEventLogRepository:
    def __init__(self) -> None:
        self._events: dict[uuid.UUID, list[JobEvent]] = {}

    def seed(self, job_id: uuid.UUID, events: list[JobEvent]) -> None:
        self._events[job_id] = list(events)

    def append(self, event: JobEvent) -> None:
        bucket = self._events.setdefault(event.job_id, [])
        bucket.append(event)

    def list_by_job(self, job_id: uuid.UUID) -> list[JobEvent]:
        return list(self._events.get(job_id, []))


class _CorruptedEventLogRepository(_InMemoryEventLogRepository):
    def list_by_job(self, job_id: uuid.UUID) -> list[JobEvent]:
        del job_id
        raise AppError(code=ErrorCode.STATE_CORRUPTED, message="events.jsonl corrupted")


class TestStreamApplication(TestCase):
    def setUp(self) -> None:
        self.job_id = uuid.uuid4()
        self.jobs = _InMemoryJobRepository()
        self.jobs.add(self.job_id)
        self.events = _InMemoryEventLogRepository()
        self.hub = InMemoryLiveSubscriberHub()
        self.app = StreamApplication(
            event_log_repository=self.events,
            live_subscriber_hub=self.hub,
            job_repository=self.jobs,
        )

    def test_empty_history_switches_to_live_and_receives_event(self) -> None:
        stream = self.app.subscribe_job_events(job_id=self.job_id, replay=True)
        live_event = _event(job_id=self.job_id, seq=1)

        async def _scenario() -> JobEvent:
            received_event = await _await_live_event(stream=stream, app=self.app, event=live_event)
            await stream.aclose()
            return received_event

        received = asyncio.run(_scenario())

        self.assertEqual(1, received.seq)
        self.assertEqual(EventType.PAGE_PROCESSED, received.event_type)
        self.assertEqual([1], [item.seq for item in self.events.list_by_job(self.job_id)])

    def test_history_replays_in_seq_ascending_before_live(self) -> None:
        self.events.seed(
            self.job_id,
            [
                _event(job_id=self.job_id, seq=3),
                _event(job_id=self.job_id, seq=1),
                _event(job_id=self.job_id, seq=2),
            ],
        )
        stream = self.app.subscribe_job_events(job_id=self.job_id, replay=True)

        live_event = _event(job_id=self.job_id, seq=4)

        async def _scenario() -> tuple[list[JobEvent], JobEvent]:
            replayed_events = await _collect_events(stream, total=3)
            live_received_event = await _await_live_event(stream=stream, app=self.app, event=live_event)
            await stream.aclose()
            return replayed_events, live_received_event

        replayed, live_received = asyncio.run(_scenario())

        self.assertEqual([1, 2, 3], [item.seq for item in replayed])
        self.assertEqual(4, live_received.seq)

    def test_terminal_history_replays_then_closes_immediately(self) -> None:
        self.jobs.set_status(self.job_id, JobStatus.EXTRACTED)
        self.events.seed(
            self.job_id,
            [
                _event(job_id=self.job_id, seq=1),
                _event(
                    job_id=self.job_id,
                    seq=2,
                    event_type=EventType.EXTRACTION_COMPLETED,
                    payload={"type": "complete"},
                ),
            ],
        )
        stream = self.app.subscribe_job_events(job_id=self.job_id, replay=True)

        async def _scenario() -> tuple[list[JobEvent], bool]:
            replayed_events = await _collect_events(stream, total=2)
            try:
                await _next_event(stream, timeout_seconds=0.1)
            except StopAsyncIteration:
                return replayed_events, True
            return replayed_events, False

        replayed, closed = asyncio.run(_scenario())

        self.assertEqual([1, 2], [item.seq for item in replayed])
        self.assertTrue(closed, "终态历史 replay 后应立即关闭订阅")

    def test_active_job_skips_stale_terminal_replay_and_waits_for_live(self) -> None:
        self.jobs.set_status(self.job_id, JobStatus.EXTRACTING)
        self.events.seed(
            self.job_id,
            [
                _event(job_id=self.job_id, seq=1),
                _event(
                    job_id=self.job_id,
                    seq=2,
                    event_type=EventType.EXTRACTION_COMPLETED,
                    payload={"type": "complete"},
                ),
            ],
        )
        trimmed_replay = self.app.load_replay_events(
            self.job_id,
            for_live_follow=True,
        )
        self.assertEqual([], trimmed_replay, "活跃任务应忽略上一轮终态之前的历史事件")
        self.assertFalse(
            self.app.has_terminal_event(job_id=self.job_id, events=trimmed_replay),
            "活跃任务在空 replay 窗口下不应被判定为终态关闭",
        )

        stream = self.app.subscribe_job_events(job_id=self.job_id, replay=True)
        live_event = _event(job_id=self.job_id, seq=3, payload={"type": "page", "seq": 3})

        async def _scenario() -> JobEvent:
            received_event = await _await_live_event(
                stream=stream,
                app=self.app,
                event=live_event,
            )
            await stream.aclose()
            return received_event

        received = asyncio.run(_scenario())

        self.assertEqual(3, received.seq)
        self.assertEqual(EventType.PAGE_PROCESSED, received.event_type)

    def test_live_broadcast_reaches_multiple_subscribers(self) -> None:
        stream_a = self.app.subscribe_job_events(job_id=self.job_id, replay=False)
        stream_b = self.app.subscribe_job_events(job_id=self.job_id, replay=False)
        event = _event(job_id=self.job_id, seq=1, event_type=EventType.JOB_FAILED, payload={"type": "failed"})

        async def _collect_two() -> tuple[JobEvent, JobEvent]:
            task_a = asyncio.create_task(_next_event(stream_a, timeout_seconds=0.5))
            task_b = asyncio.create_task(_next_event(stream_b, timeout_seconds=0.5))
            await asyncio.sleep(0)
            self.app.publish(event)
            received_events = await asyncio.gather(task_a, task_b)
            await stream_a.aclose()
            await stream_b.aclose()
            return received_events[0], received_events[1]

        received_a, received_b = asyncio.run(_collect_two())

        self.assertEqual(1, received_a.seq)
        self.assertEqual(1, received_b.seq)
        self.assertEqual(EventType.JOB_FAILED, received_a.event_type)
        self.assertEqual(EventType.JOB_FAILED, received_b.event_type)

    def test_corrupted_event_log_raises_state_corrupted(self) -> None:
        app = StreamApplication(
            event_log_repository=_CorruptedEventLogRepository(),
            live_subscriber_hub=InMemoryLiveSubscriberHub(),
            job_repository=self.jobs,
        )
        stream = app.subscribe_job_events(job_id=self.job_id, replay=True)

        with self.assertRaises(AppError) as ctx:
            asyncio.run(_next_event(stream))
        self.assertEqual(ErrorCode.STATE_CORRUPTED, ctx.exception.code)
