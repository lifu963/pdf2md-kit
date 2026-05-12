"""Thread-based task scheduler adapter with per-job deduplication."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class _TaskHandle:
    thread: threading.Thread
    completed: threading.Event


class ThreadedTaskScheduler:
    """Run async tasks in background threads and deduplicate by job/task key."""

    def __init__(self, *, thread_name_prefix: str = "task") -> None:
        self._thread_name_prefix = thread_name_prefix
        self._lock = threading.Lock()
        self._running: dict[tuple[UUID, str], _TaskHandle] = {}

    def schedule(
        self,
        *,
        job_id: UUID,
        task_name: str,
        task_factory: Callable[[], Awaitable[None]],
    ) -> bool:
        key = (job_id, task_name)
        with self._lock:
            existing = self._running.get(key)
            if existing is not None:
                if existing.completed.is_set():
                    self._running.pop(key, None)
                else:
                    if existing.thread.is_alive():
                        existing.thread.join(timeout=0.01)
                    if existing.completed.is_set() or not existing.thread.is_alive():
                        self._running.pop(key, None)
                    else:
                        return False

            completed = threading.Event()

            worker = threading.Thread(
                target=self._run_task,
                args=(key, task_factory, completed),
                daemon=True,
                name=f"{self._thread_name_prefix}-{job_id}-{task_name}",
            )
            self._running[key] = _TaskHandle(thread=worker, completed=completed)
            worker.start()
            return True

    def _run_task(
        self,
        key: tuple[UUID, str],
        task_factory: Callable[[], Awaitable[None]],
        completed: threading.Event,
    ) -> None:
        try:
            asyncio.run(task_factory())
        finally:
            completed.set()
            with self._lock:
                current = self._running.get(key)
                if current is not None and current.thread is threading.current_thread():
                    self._running.pop(key, None)


__all__ = ["ThreadedTaskScheduler"]
