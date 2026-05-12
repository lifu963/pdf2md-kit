"""
FsJobRepository: 将 JobAggregate 以 state.json 原子写方式持久化到文件系统。

设计要点：
- 写入采用 tmp 文件 + os.replace 原子替换，杜绝半文件。
- 读取时 JSON 解析失败映射为 STATE_CORRUPTED。
- 所有写操作在 WorkspaceManager 提供的 per-job 锁内执行。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.infra.fs.error_mapping import raise_persistence_error
from backend.infra.fs.workspace import WorkspaceManager
from backend.shared_kernel.contracts import (
    JobAggregate,
    JobStatus,
)
from backend.shared_kernel.errors import AppError, ErrorCode


@dataclass(frozen=True)
class _StateCacheEntry:
    fingerprint: tuple[int, int]
    job: JobAggregate


def _serialize_job(job: JobAggregate) -> dict:
    return {
        "job_id": str(job.job_id),
        "source_pdf_name": job.source_pdf_name,
        "total_pages": job.total_pages,
        "status": job.status.value,
        "succeeded_pages": job.succeeded_pages,
        "failed_pages": job.failed_pages,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "version": job.version,
        "last_error": job.last_error,
    }


def _deserialize_job(data: dict) -> JobAggregate:
    return JobAggregate(
        job_id=UUID(data["job_id"]),
        source_pdf_name=data["source_pdf_name"],
        total_pages=data["total_pages"],
        status=JobStatus(data["status"]),
        succeeded_pages=data["succeeded_pages"],
        failed_pages=data["failed_pages"],
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        version=data["version"],
        last_error=data.get("last_error"),
    )


def _deserialize_job_from_raw(raw: str) -> JobAggregate:
    try:
        data = json.loads(raw)
        return _deserialize_job(data)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise AppError(
            code=ErrorCode.STATE_CORRUPTED,
            message=f"state.json corrupted: {exc}",
        ) from exc


class FsJobRepository:
    """基于文件系统的 JobRepository 实现。"""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._workspace = workspace
        self._state_cache: dict[UUID, _StateCacheEntry] = {}

    def exists(self, job_id: UUID) -> bool:
        return self._workspace.state_path(job_id).exists()

    def get(self, job_id: UUID) -> JobAggregate:
        state_path = self._workspace.state_path(job_id)
        if not state_path.exists():
            raise AppError(code=ErrorCode.JOB_NOT_FOUND)
        return self._load_job_from_state_path(state_path)

    def list_all(self) -> list[JobAggregate]:
        jobs: list[JobAggregate] = []
        seen_job_ids: set[UUID] = set()
        for job_dir in self._workspace.iter_job_dirs():
            try:
                job_id = UUID(job_dir.name)
            except ValueError:
                continue
            seen_job_ids.add(job_id)

            lock = self._workspace.get_lock(job_id)
            with lock:
                state_path = self._workspace.state_path(job_id)
                if not state_path.exists():
                    self._state_cache.pop(job_id, None)
                    continue
                fingerprint = self._read_state_fingerprint(state_path)
                if fingerprint is None:
                    self._state_cache.pop(job_id, None)
                    continue

                cached = self._state_cache.get(job_id)
                if cached is not None and cached.fingerprint == fingerprint:
                    jobs.append(cached.job)
                    continue

                job = self._load_job_from_state_path(state_path)
                updated_fingerprint = self._read_state_fingerprint(state_path)
                if updated_fingerprint is None:
                    self._state_cache.pop(job_id, None)
                    continue
                self._state_cache[job_id] = _StateCacheEntry(
                    fingerprint=updated_fingerprint,
                    job=job,
                )
                jobs.append(job)

        for cached_job_id in list(self._state_cache):
            if cached_job_id not in seen_job_ids:
                self._state_cache.pop(cached_job_id, None)

        jobs.sort(key=lambda item: item.updated_at, reverse=True)
        return jobs

    def delete(self, job_id: UUID) -> None:
        if not self.exists(job_id):
            raise AppError(code=ErrorCode.JOB_NOT_FOUND)
        self._workspace.delete_job_dir(job_id)
        self._state_cache.pop(job_id, None)

    def _load_job_from_state_path(self, state_path: Path) -> JobAggregate:
        try:
            raw = state_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise_persistence_error("failed to read state.json", exc)
        return _deserialize_job_from_raw(raw)

    def save(self, job: JobAggregate) -> None:
        """原子写 state.json，锁保护并发写。"""
        try:
            job_dir = self._workspace.ensure_job_dir(job.job_id)
        except OSError as exc:
            raise_persistence_error("failed to create job directory for state", exc)
        state_path = self._workspace.state_path(job.job_id)
        lock = self._workspace.get_lock(job.job_id)

        payload = json.dumps(_serialize_job(job), ensure_ascii=False, indent=2)

        with lock:
            # 写临时文件后原子替换
            try:
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(job_dir), prefix=".state_", suffix=".tmp"
                )
            except OSError as exc:
                raise_persistence_error("failed to create state temp file", exc)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp_path, str(state_path))
            except OSError as exc:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise_persistence_error("failed to write state.json", exc)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            fingerprint = self._read_state_fingerprint(state_path)
            if fingerprint is not None:
                self._state_cache[job.job_id] = _StateCacheEntry(
                    fingerprint=fingerprint,
                    job=_deserialize_job(_serialize_job(job)),
                )

    def _read_state_fingerprint(self, state_path: Path) -> tuple[int, int] | None:
        try:
            stat = state_path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise_persistence_error("failed to stat state.json", exc)
        return stat.st_mtime_ns, stat.st_size


__all__ = ["FsJobRepository"]
