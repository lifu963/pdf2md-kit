"""
WorkspaceManager: per-job 目录路径解析与共享锁管理。

职责：
- 解析 data/{job_id}/ 下所有标准路径
- 为同一 job_id 返回同一把 threading.Lock
- 提供目录创建工具
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from uuid import UUID

from backend.infra.fs.error_mapping import raise_persistence_error


class WorkspaceManager:
    """管理 data/ 下所有 per-job 路径，并持有进程级锁表。"""

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root.resolve()
        self._locks: dict[UUID, threading.Lock] = {}
        self._locks_meta_lock = threading.Lock()

    # ---- Path helpers ----

    def job_dir(self, job_id: UUID) -> Path:
        """返回 data/{job_id}/ 的绝对路径（不创建目录）。"""
        return self._data_root / str(job_id)

    def state_path(self, job_id: UUID) -> Path:
        return self.job_dir(job_id) / "state.json"

    def source_path(self, job_id: UUID) -> Path:
        return self.job_dir(job_id) / "source.pdf"

    def source_meta_path(self, job_id: UUID) -> Path:
        return self.job_dir(job_id) / "source_meta.json"

    def pages_dir(self, job_id: UUID) -> Path:
        return self.job_dir(job_id) / "pages"

    def artifacts_dir(self, job_id: UUID) -> Path:
        return self.job_dir(job_id) / "artifacts"

    def events_path(self, job_id: UUID) -> Path:
        return self.job_dir(job_id) / "events.jsonl"

    def iter_job_dirs(self) -> list[Path]:
        """返回 data/ 下所有一级 job 目录候选。"""
        try:
            return [path for path in self._data_root.iterdir() if path.is_dir()]
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise_persistence_error("failed to list job directories", exc)

    # ---- Directory creation ----

    def ensure_job_dir(self, job_id: UUID) -> Path:
        """确保 data/{job_id}/ 目录存在，并返回路径。"""
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def delete_job_dir(self, job_id: UUID) -> None:
        """删除 data/{job_id}/ 整个目录。"""
        job_dir = self.job_dir(job_id)
        lock = self.get_lock(job_id)
        with lock:
            if not job_dir.exists():
                return
            try:
                shutil.rmtree(job_dir)
            except OSError as exc:
                raise_persistence_error("failed to delete job directory", exc)

    # ---- Lock management ----

    def get_lock(self, job_id: UUID) -> threading.Lock:
        """返回该 job_id 专属的共享锁（同一进程内唯一）。"""
        with self._locks_meta_lock:
            if job_id not in self._locks:
                self._locks[job_id] = threading.Lock()
            return self._locks[job_id]


__all__ = ["WorkspaceManager"]
