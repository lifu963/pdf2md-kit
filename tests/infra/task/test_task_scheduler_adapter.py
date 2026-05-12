"""
Step 13: task-scheduler-adapter 测试

验收目标（严格对齐实施步骤）：
1. 正常调度：后台任务可以启动并执行。
2. 去重：同一 job_id + task_name 的重复调度会被拒绝。
3. 异常可见：后台任务异常不能被静默吞掉。
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from unittest import TestCase

from backend.infra.task import ThreadedTaskScheduler


class TestThreadedTaskScheduler(TestCase):
    def setUp(self) -> None:
        self.scheduler = ThreadedTaskScheduler()
        self.job_id = uuid.uuid4()

    def test_schedule_starts_background_task(self) -> None:
        finished = threading.Event()

        async def task() -> None:
            finished.set()

        scheduled = self.scheduler.schedule(
            job_id=self.job_id,
            task_name="extract-all",
            task_factory=lambda: task(),
        )

        self.assertTrue(scheduled)
        self.assertTrue(finished.wait(timeout=1.0), "后台任务未在预期时间内执行")

    def test_schedule_deduplicates_running_task_and_allows_reschedule_after_finish(self) -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        rerun_finished = threading.Event()

        async def blocking_task() -> None:
            started.set()
            await asyncio.to_thread(release.wait, 2.0)
            finished.set()

        first = self.scheduler.schedule(
            job_id=self.job_id,
            task_name="extract-all",
            task_factory=lambda: blocking_task(),
        )
        self.assertTrue(first)
        self.assertTrue(started.wait(timeout=1.0), "首次调度未真正启动")

        duplicate = self.scheduler.schedule(
            job_id=self.job_id,
            task_name="extract-all",
            task_factory=lambda: blocking_task(),
        )
        self.assertFalse(duplicate, "运行中的相同任务应被去重拒绝")

        release.set()
        self.assertTrue(finished.wait(timeout=1.0), "首次任务未按预期结束")

        async def rerun_task() -> None:
            rerun_finished.set()

        second = self.scheduler.schedule(
            job_id=self.job_id,
            task_name="extract-all",
            task_factory=lambda: rerun_task(),
        )
        self.assertTrue(second, "已结束任务应允许重新调度")
        self.assertTrue(rerun_finished.wait(timeout=1.0))

    def test_task_exception_is_not_silently_swallowed(self) -> None:
        exception_seen = threading.Event()
        captured_messages: list[str] = []
        original_excepthook = threading.excepthook

        def _hook(args: threading.ExceptHookArgs) -> None:
            captured_messages.append(str(args.exc_value))
            exception_seen.set()

        threading.excepthook = _hook
        try:
            async def crash_task() -> None:
                raise RuntimeError("boom from background task")

            scheduled = self.scheduler.schedule(
                job_id=self.job_id,
                task_name="extract-all",
                task_factory=lambda: crash_task(),
            )
            self.assertTrue(scheduled)
            self.assertTrue(
                exception_seen.wait(timeout=1.0),
                "后台任务异常未通过 threading.excepthook 暴露",
            )
            self.assertTrue(
                any("boom from background task" in message for message in captured_messages),
                "异常信息未被正确暴露",
            )
        finally:
            threading.excepthook = original_excepthook
