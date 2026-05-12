"""
FsEventLogRepository: 将 JobEvent 以 JSONL 方式持久化到 events.jsonl。

设计要点：
- append 仅允许 seq 严格递增，重复或回退 seq 会被拒绝。
- list_by_job 保证按 seq 升序返回。
- JSONL 损坏映射为 STATE_CORRUPTED。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.infra.fs.error_mapping import raise_persistence_error
from backend.infra.fs.workspace import WorkspaceManager
from backend.shared_kernel.contracts import EventType, JobEvent
from backend.shared_kernel.errors import AppError, ErrorCode


def _serialize_event(event: JobEvent) -> dict[str, object]:
    return {
        "job_id": str(event.job_id),
        "seq": event.seq,
        "event_type": event.event_type.value,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def _deserialize_event(data: dict[str, object]) -> JobEvent:
    return JobEvent(
        job_id=UUID(str(data["job_id"])),
        seq=int(data["seq"]),
        event_type=EventType(str(data["event_type"])),
        payload=dict(data["payload"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
    )


class FsEventLogRepository:
    """基于文件系统的 EventLogRepository 实现。"""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._workspace = workspace
        self._last_seq_by_job: dict[UUID, int] = {}

    def append(self, event: JobEvent) -> None:
        try:
            self._workspace.ensure_job_dir(event.job_id)
        except OSError as exc:
            raise_persistence_error("failed to create job directory for events", exc)
        events_path = self._workspace.events_path(event.job_id)
        lock = self._workspace.get_lock(event.job_id)

        with lock:
            last_seq = self._load_last_seq_for_append(event.job_id, events_path)

            if event.seq <= last_seq:
                raise AppError(
                    code=ErrorCode.PERSISTENCE_ERROR,
                    message=f"event seq must be increasing, got {event.seq} <= {last_seq}",
                )

            payload = json.dumps(_serialize_event(event), ensure_ascii=False)
            try:
                with open(events_path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(payload + "\n")
            except OSError as exc:
                raise_persistence_error("failed to append events.jsonl", exc)
            self._last_seq_by_job[event.job_id] = event.seq

    def list_by_job(self, job_id: UUID) -> list[JobEvent]:
        events_path = self._workspace.events_path(job_id)
        if not events_path.exists():
            return []

        lock = self._workspace.get_lock(job_id)
        with lock:
            lines = self._read_all_lines(events_path)

        events: list[JobEvent] = []
        prev_seq = 0
        for line in lines:
            try:
                event = _deserialize_event(json.loads(line))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                raise AppError(
                    code=ErrorCode.STATE_CORRUPTED,
                    message=f"events.jsonl corrupted: {exc}",
                ) from exc

            if event.job_id != job_id:
                raise AppError(
                    code=ErrorCode.STATE_CORRUPTED,
                    message="events.jsonl contains unexpected job_id",
                )
            if event.seq <= prev_seq:
                raise AppError(
                    code=ErrorCode.STATE_CORRUPTED,
                    message="events.jsonl seq is not strictly increasing",
                )
            prev_seq = event.seq
            events.append(event)

        return events

    def _read_all_lines(self, events_path: Path) -> list[str]:
        if not events_path.exists():
            return []
        try:
            text = events_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise_persistence_error("failed to read events.jsonl", exc)
        return [line for line in text.splitlines() if line.strip()]

    def _load_last_seq_for_append(self, job_id: UUID, events_path: Path) -> int:
        cached = self._last_seq_by_job.get(job_id)
        if cached is not None:
            return cached

        last_seq = self._read_last_seq_from_tail(job_id, events_path)
        self._last_seq_by_job[job_id] = last_seq
        return last_seq

    def _read_last_seq_from_tail(self, job_id: UUID, events_path: Path) -> int:
        last_line = self._read_last_non_empty_line(events_path)
        if last_line is None:
            return 0

        try:
            last_event = _deserialize_event(json.loads(last_line))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message=f"events.jsonl corrupted: {exc}",
            ) from exc

        if last_event.job_id != job_id:
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message="events.jsonl contains unexpected job_id",
            )
        return last_event.seq

    def _read_last_non_empty_line(self, events_path: Path) -> str | None:
        if not events_path.exists():
            return None

        try:
            with open(events_path, "rb") as file_obj:
                file_obj.seek(0, 2)
                cursor = file_obj.tell() - 1
                if cursor < 0:
                    return None

                while cursor >= 0:
                    file_obj.seek(cursor)
                    byte = file_obj.read(1)
                    if byte not in b" \t\r\n":
                        break
                    cursor -= 1

                if cursor < 0:
                    return None

                end = cursor
                while cursor >= 0:
                    file_obj.seek(cursor)
                    byte = file_obj.read(1)
                    if byte in b"\r\n":
                        break
                    cursor -= 1

                start = cursor + 1
                file_obj.seek(start)
                line_bytes = file_obj.read(end - start + 1)
            return line_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message=f"events.jsonl corrupted: {exc}",
            ) from exc
        except OSError as exc:
            raise_persistence_error("failed to read events.jsonl tail", exc)


__all__ = ["FsEventLogRepository"]
