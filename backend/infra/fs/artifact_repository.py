"""
FsArtifactRepository: 将 output.md 持久化到 data/{job_id}/artifacts/。

设计要点：
- 当前步骤仅实现 OUTPUT_MD（artifacts/output.md）。
- 写入采用 tmp + os.replace 原子替换。
- 缺失 output.md 映射为 OUTPUT_NOT_READY。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from backend.infra.fs.error_mapping import raise_persistence_error
from backend.infra.fs.workspace import WorkspaceManager
from backend.shared_kernel.contracts import ArtifactRef, ArtifactType, OutputDocument
from backend.shared_kernel.errors import AppError, ErrorCode


class FsArtifactRepository:
    """基于文件系统的 ArtifactRepository 实现。"""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._workspace = workspace

    def save_output(self, job_id: UUID, content: str) -> ArtifactRef:
        try:
            self._workspace.ensure_job_dir(job_id)
        except OSError as exc:
            raise_persistence_error("failed to create job directory for artifacts", exc)
        artifacts_dir = self._workspace.artifacts_dir(job_id)
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise_persistence_error("failed to create artifacts directory", exc)
        output_path = self._output_path(job_id)

        lock = self._workspace.get_lock(job_id)
        with lock:
            try:
                _atomic_write_text(output_path, content)
            except OSError as exc:
                raise_persistence_error("failed to write output.md", exc)

        return self._build_output_ref(job_id)

    def get_output_document(self, job_id: UUID) -> OutputDocument:
        output_path = self._output_path(job_id)
        if not output_path.exists():
            raise AppError(code=ErrorCode.OUTPUT_NOT_READY)

        try:
            content = output_path.read_text(encoding="utf-8")
            stat = output_path.stat()
        except OSError as exc:
            raise AppError(
                code=ErrorCode.PERSISTENCE_ERROR,
                message=f"failed to read output.md: {exc}",
            ) from exc

        return OutputDocument(
            job_id=job_id,
            content=content,
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    def get_output_artifact(self, job_id: UUID) -> ArtifactRef:
        output_path = self._output_path(job_id)
        if not output_path.exists():
            raise AppError(code=ErrorCode.OUTPUT_NOT_READY)
        return self._build_output_ref(job_id)

    def delete_output(self, job_id: UUID) -> None:
        output_path = self._output_path(job_id)
        lock = self._workspace.get_lock(job_id)
        with lock:
            if not output_path.exists():
                return
            try:
                output_path.unlink()
            except OSError as exc:
                raise_persistence_error("failed to delete output.md", exc)

    def _output_path(self, job_id: UUID) -> Path:
        return self._workspace.artifacts_dir(job_id) / "output.md"

    def _build_output_ref(self, job_id: UUID) -> ArtifactRef:
        return ArtifactRef(
            job_id=job_id,
            artifact_type=ArtifactType.OUTPUT_MD,
            relative_path=f"{job_id}/artifacts/output.md",
            content_type="text/markdown; charset=utf-8",
            filename="output.md",
        )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


__all__ = ["FsArtifactRepository"]
