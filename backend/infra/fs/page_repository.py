"""
FsPageRepository: 将 PageDocument 持久化到 data/{job_id}/pages/。

设计要点：
- 页面正文保存为 page_{num:03d}.md（UTF-8）。
- 页面状态元数据保存为 page_{num:03d}.meta.json。
- 所有写操作在 WorkspaceManager 的同一 job 锁内执行。
- 元数据损坏映射为 STATE_CORRUPTED；页面缺失映射为 PAGE_NOT_FOUND。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.infra.fs.error_mapping import raise_persistence_error
from backend.infra.fs.workspace import WorkspaceManager
from backend.shared_kernel.contracts import PageDocument, PageStatus
from backend.shared_kernel.errors import AppError, ErrorCode


def _page_stem(page_num: int) -> str:
    return f"page_{page_num:03d}"


def _serialize_meta(page: PageDocument) -> dict[str, object]:
    return {
        "job_id": str(page.job_id),
        "page_num": page.page_num,
        "status": page.status.value,
        "error_message": page.error_message,
        "updated_at": page.updated_at.isoformat(),
    }


class FsPageRepository:
    """基于文件系统的 PageRepository 实现。"""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._workspace = workspace

    def list_by_job(self, job_id: UUID) -> list[PageDocument]:
        pages_dir = self._workspace.pages_dir(job_id)
        if not pages_dir.exists():
            return []

        lock = self._workspace.get_lock(job_id)
        with lock:
            docs: list[PageDocument] = []
            for meta_path in sorted(pages_dir.glob("page_*.meta.json")):
                docs.append(self._read_one(job_id=job_id, meta_path=meta_path))
            docs.sort(key=lambda item: item.page_num)
            return docs

    def list_summaries_by_job(self, job_id: UUID) -> list[PageDocument]:
        pages_dir = self._workspace.pages_dir(job_id)
        if not pages_dir.exists():
            return []

        lock = self._workspace.get_lock(job_id)
        with lock:
            docs: list[PageDocument] = []
            for meta_path in sorted(pages_dir.glob("page_*.meta.json")):
                docs.append(self._read_one(job_id=job_id, meta_path=meta_path, include_content=False))
            docs.sort(key=lambda item: item.page_num)
            return docs

    def get(self, job_id: UUID, page_num: int) -> PageDocument:
        pages_dir = self._workspace.pages_dir(job_id)
        meta_path = pages_dir / f"{_page_stem(page_num)}.meta.json"
        if not meta_path.exists():
            raise AppError(code=ErrorCode.PAGE_NOT_FOUND)

        lock = self._workspace.get_lock(job_id)
        with lock:
            return self._read_one(job_id=job_id, meta_path=meta_path)

    def save(self, page: PageDocument) -> None:
        try:
            self._workspace.ensure_job_dir(page.job_id)
        except OSError as exc:
            raise_persistence_error("failed to create job directory for pages", exc)
        pages_dir = self._workspace.pages_dir(page.job_id)
        try:
            pages_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise_persistence_error("failed to create pages directory", exc)

        stem = _page_stem(page.page_num)
        content_path = pages_dir / f"{stem}.md"
        meta_path = pages_dir / f"{stem}.meta.json"

        lock = self._workspace.get_lock(page.job_id)
        with lock:
            try:
                if page.content is not None:
                    _atomic_write_text(content_path, page.content)
                elif content_path.exists():
                    content_path.unlink()

                meta_payload = json.dumps(_serialize_meta(page), ensure_ascii=False, indent=2)
                _atomic_write_text(meta_path, meta_payload)
            except OSError as exc:
                raise_persistence_error("failed to save page document", exc)

    def _read_one(
        self,
        *,
        job_id: UUID,
        meta_path: Path,
        include_content: bool = True,
    ) -> PageDocument:
        try:
            meta_raw = meta_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise_persistence_error("failed to read page metadata", exc)
        try:
            meta_data = json.loads(meta_raw)
            doc_job_id = UUID(meta_data["job_id"])
            if doc_job_id != job_id:
                raise ValueError("job_id mismatch in page meta")
            page_num = int(meta_data["page_num"])
            status = PageStatus(meta_data["status"])
            error_message = meta_data.get("error_message")
            updated_at = datetime.fromisoformat(meta_data["updated_at"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message=f"page meta corrupted: {exc}",
            ) from exc

        content: str | None = None
        if include_content:
            content_path = meta_path.with_suffix("").with_suffix(".md")
            if content_path.exists():
                try:
                    content = content_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise_persistence_error("failed to read page content", exc)

        return PageDocument(
            job_id=job_id,
            page_num=page_num,
            status=status,
            content=content,
            error_message=error_message,
            updated_at=updated_at,
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


__all__ = ["FsPageRepository"]
