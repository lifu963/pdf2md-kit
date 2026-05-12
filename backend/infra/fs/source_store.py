"""
FsSourceDocumentStore: 将 source.pdf 与其元数据持久化到文件系统。

设计要点：
- source.pdf 直接写入 data/{job_id}/source.pdf。
- 元数据（filename、size_bytes）写入 data/{job_id}/source_meta.json。
- open_read 返回只读二进制文件句柄，不暴露底层路径。
- 文件不存在时映射为 JOB_NOT_FOUND。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from backend.infra.fs.error_mapping import raise_persistence_error
from backend.infra.fs.workspace import WorkspaceManager
from backend.shared_kernel.contracts import SourceDocumentRef
from backend.shared_kernel.errors import AppError, ErrorCode


class FsSourceDocumentStore:
    """基于文件系统的 SourceDocumentStore 实现。"""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._workspace = workspace

    def save_source(
        self, job_id: UUID, pdf_filename: str, pdf_bytes: bytes
    ) -> SourceDocumentRef:
        """保存源 PDF 文件与元数据，返回 SourceDocumentRef。"""
        try:
            job_dir = self._workspace.ensure_job_dir(job_id)
        except OSError as exc:
            raise_persistence_error("failed to create job directory for source", exc)
        source_path = self._workspace.source_path(job_id)
        meta_path = self._workspace.source_meta_path(job_id)
        lock = self._workspace.get_lock(job_id)

        with lock:
            # 原子写 source.pdf
            try:
                fd, tmp_pdf = tempfile.mkstemp(
                    dir=str(job_dir), prefix=".src_", suffix=".tmp"
                )
            except OSError as exc:
                raise_persistence_error("failed to create source temp file", exc)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(pdf_bytes)
                os.replace(tmp_pdf, str(source_path))
            except OSError as exc:
                try:
                    os.unlink(tmp_pdf)
                except OSError:
                    pass
                raise_persistence_error("failed to write source.pdf", exc)
            except Exception:
                try:
                    os.unlink(tmp_pdf)
                except OSError:
                    pass
                raise

            # 原子写元数据
            meta = {
                "job_id": str(job_id),
                "filename": pdf_filename,
                "size_bytes": len(pdf_bytes),
                "content_type": "application/pdf",
            }
            meta_payload = json.dumps(meta, ensure_ascii=False)
            try:
                fd2, tmp_meta = tempfile.mkstemp(
                    dir=str(job_dir), prefix=".meta_", suffix=".tmp"
                )
            except OSError as exc:
                raise_persistence_error("failed to create source_meta temp file", exc)
            try:
                with os.fdopen(fd2, "w", encoding="utf-8") as f:
                    f.write(meta_payload)
                os.replace(tmp_meta, str(meta_path))
            except OSError as exc:
                try:
                    os.unlink(tmp_meta)
                except OSError:
                    pass
                raise_persistence_error("failed to write source_meta.json", exc)
            except Exception:
                try:
                    os.unlink(tmp_meta)
                except OSError:
                    pass
                raise

        return self._build_ref(job_id, pdf_filename, len(pdf_bytes))

    def get_source(self, job_id: UUID) -> SourceDocumentRef:
        meta_path = self._workspace.source_meta_path(job_id)
        if not meta_path.exists():
            raise AppError(code=ErrorCode.JOB_NOT_FOUND)
        try:
            raw = meta_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise_persistence_error("failed to read source_meta.json", exc)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message=f"source_meta.json corrupted: {exc}",
            ) from exc
        return self._build_ref(
            job_id=UUID(data["job_id"]),
            filename=data["filename"],
            size_bytes=data["size_bytes"],
        )

    def open_read(self, job_id: UUID) -> BinaryIO:
        source_path = self._workspace.source_path(job_id)
        if not source_path.exists():
            raise AppError(code=ErrorCode.JOB_NOT_FOUND)
        try:
            return open(source_path, "rb")
        except OSError as exc:
            raise_persistence_error("failed to open source.pdf", exc)

    def _build_ref(self, job_id: UUID, filename: str, size_bytes: int) -> SourceDocumentRef:
        relative_path = f"{job_id}/source.pdf"
        return SourceDocumentRef(
            job_id=job_id,
            relative_path=relative_path,
            content_type="application/pdf",
            filename=filename,
            size_bytes=size_bytes,
        )


__all__ = ["FsSourceDocumentStore"]
