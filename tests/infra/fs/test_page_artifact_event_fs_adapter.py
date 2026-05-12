"""
Step 05: 页面、产物与事件日志持久化（workspace-fs-adapter）集成测试

验收目标：
1. PageRepository：页面保存、失败信息保存、覆盖保存、页面不存在。
2. ArtifactRepository：output.md 保存与读取；缺失时返回 OUTPUT_NOT_READY。
3. EventLogRepository：事件顺序追加、重复 seq 拒绝、损坏 JSONL 映射 STATE_CORRUPTED。
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, mock

from backend.infra.fs import (
    FsArtifactRepository,
    FsEventLogRepository,
    FsPageRepository,
    WorkspaceManager,
)
from backend.shared_kernel.contracts import (
    EventType,
    JobEvent,
    PageDocument,
    PageStatus,
)
from backend.shared_kernel.errors import AppError, ErrorCode


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _page(
    *,
    job_id: uuid.UUID,
    page_num: int,
    status: PageStatus,
    content: str | None,
    error_message: str | None,
) -> PageDocument:
    return PageDocument(
        job_id=job_id,
        page_num=page_num,
        status=status,
        content=content,
        error_message=error_message,
        updated_at=_now(),
    )


def _event(job_id: uuid.UUID, seq: int, event_type: EventType) -> JobEvent:
    return JobEvent(
        job_id=job_id,
        seq=seq,
        event_type=event_type,
        payload={"seq": seq},
        created_at=_now(),
    )


class TestFsPageRepository(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._workspace = WorkspaceManager(data_root=Path(self._tmp))
        self._repo = FsPageRepository(workspace=self._workspace)
        self._job_id = uuid.uuid4()

    def test_save_and_get_done_page_roundtrip(self) -> None:
        page = _page(
            job_id=self._job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="# 第 1 页",
            error_message=None,
        )
        self._repo.save(page)

        loaded = self._repo.get(self._job_id, 1)
        self.assertEqual(loaded.job_id, self._job_id)
        self.assertEqual(loaded.page_num, 1)
        self.assertEqual(loaded.status, PageStatus.DONE)
        self.assertEqual(loaded.content, "# 第 1 页")
        self.assertIsNone(loaded.error_message)

    def test_save_failed_page_keeps_error_message(self) -> None:
        page = _page(
            job_id=self._job_id,
            page_num=2,
            status=PageStatus.FAILED,
            content=None,
            error_message="vision timeout",
        )
        self._repo.save(page)

        loaded = self._repo.get(self._job_id, 2)
        self.assertEqual(loaded.status, PageStatus.FAILED)
        self.assertIsNone(loaded.content)
        self.assertEqual(loaded.error_message, "vision timeout")

    def test_save_overwrites_existing_page_content(self) -> None:
        self._repo.save(
            _page(
                job_id=self._job_id,
                page_num=3,
                status=PageStatus.DONE,
                content="old content",
                error_message=None,
            )
        )
        self._repo.save(
            _page(
                job_id=self._job_id,
                page_num=3,
                status=PageStatus.DONE,
                content="new content",
                error_message=None,
            )
        )

        loaded = self._repo.get(self._job_id, 3)
        self.assertEqual(loaded.content, "new content")

    def test_get_raises_page_not_found_when_missing(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self._repo.get(self._job_id, 99)
        self.assertEqual(ctx.exception.code, ErrorCode.PAGE_NOT_FOUND)

    def test_list_summaries_by_job_does_not_read_markdown_bodies(self) -> None:
        self._repo.save(
            _page(
                job_id=self._job_id,
                page_num=2,
                status=PageStatus.FAILED,
                content="stale body",
                error_message="timeout",
            )
        )
        self._repo.save(
            _page(
                job_id=self._job_id,
                page_num=1,
                status=PageStatus.DONE,
                content="# 第 1 页",
                error_message=None,
            )
        )

        original_read_text = Path.read_text

        def _guard_markdown_reads(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if path.suffix == ".md":
                raise AssertionError("summary listing should not read markdown body files")
            return original_read_text(path, *args, **kwargs)

        with mock.patch(
            "pathlib.Path.read_text",
            autospec=True,
            side_effect=_guard_markdown_reads,
        ):
            list_summaries = getattr(self._repo, "list_summaries_by_job")
            summaries = list_summaries(self._job_id)

        self.assertEqual(
            [(1, PageStatus.DONE), (2, PageStatus.FAILED)],
            [(item.page_num, item.status) for item in summaries],
        )


class TestFsArtifactRepository(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._workspace = WorkspaceManager(data_root=Path(self._tmp))
        self._repo = FsArtifactRepository(workspace=self._workspace)
        self._job_id = uuid.uuid4()

    def test_save_and_get_output_document(self) -> None:
        ref = self._repo.save_output(self._job_id, "# output v1")
        self.assertEqual(str(self._job_id), ref.relative_path.split("/")[0])

        loaded = self._repo.get_output_document(self._job_id)
        self.assertEqual(loaded.job_id, self._job_id)
        self.assertEqual(loaded.content, "# output v1")

    def test_get_output_artifact_returns_output_ref(self) -> None:
        self._repo.save_output(self._job_id, "hello")
        ref = self._repo.get_output_artifact(self._job_id)
        self.assertEqual(ref.job_id, self._job_id)
        self.assertEqual(ref.relative_path, f"{self._job_id}/artifacts/output.md")

    def test_get_output_document_raises_output_not_ready_when_missing(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self._repo.get_output_document(self._job_id)
        self.assertEqual(ctx.exception.code, ErrorCode.OUTPUT_NOT_READY)

    def test_get_output_artifact_raises_output_not_ready_when_missing(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self._repo.get_output_artifact(self._job_id)
        self.assertEqual(ctx.exception.code, ErrorCode.OUTPUT_NOT_READY)


class TestFsEventLogRepository(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._workspace = WorkspaceManager(data_root=Path(self._tmp))
        self._repo = FsEventLogRepository(workspace=self._workspace)
        self._job_id = uuid.uuid4()

    def test_append_and_list_by_job_returns_events_in_seq_order(self) -> None:
        self._repo.append(_event(self._job_id, 1, EventType.PAGE_PROCESSED))
        self._repo.append(_event(self._job_id, 2, EventType.EXTRACTION_COMPLETED))

        events = self._repo.list_by_job(self._job_id)
        self.assertEqual([1, 2], [event.seq for event in events])
        self.assertEqual(
            [EventType.PAGE_PROCESSED, EventType.EXTRACTION_COMPLETED],
            [event.event_type for event in events],
        )

    def test_append_rejects_duplicate_seq(self) -> None:
        self._repo.append(_event(self._job_id, 1, EventType.PAGE_PROCESSED))

        with self.assertRaises(AppError) as ctx:
            self._repo.append(_event(self._job_id, 1, EventType.STATUS_CHANGED))
        self.assertEqual(ctx.exception.code, ErrorCode.PERSISTENCE_ERROR)

    def test_append_rejects_non_increasing_seq(self) -> None:
        self._repo.append(_event(self._job_id, 3, EventType.PAGE_PROCESSED))

        with self.assertRaises(AppError) as ctx:
            self._repo.append(_event(self._job_id, 2, EventType.STATUS_CHANGED))
        self.assertEqual(ctx.exception.code, ErrorCode.PERSISTENCE_ERROR)

    def test_list_by_job_raises_state_corrupted_when_jsonl_broken(self) -> None:
        self._workspace.ensure_job_dir(self._job_id)
        self._workspace.events_path(self._job_id).write_text("not-json-line\n", encoding="utf-8")

        with self.assertRaises(AppError) as ctx:
            self._repo.list_by_job(self._job_id)
        self.assertEqual(ctx.exception.code, ErrorCode.STATE_CORRUPTED)

    def test_append_does_not_read_all_lines_for_steady_state_appends(self) -> None:
        self._repo.append(_event(self._job_id, 1, EventType.PAGE_PROCESSED))

        with mock.patch.object(
            self._repo,
            "_read_all_lines",
            side_effect=AssertionError("append should not read the whole events.jsonl in steady state"),
        ):
            self._repo.append(_event(self._job_id, 2, EventType.STATUS_CHANGED))

        events = self._repo.list_by_job(self._job_id)
        self.assertEqual([1, 2], [event.seq for event in events])

    def test_append_after_repository_recreated_still_avoids_full_file_read(self) -> None:
        self._repo.append(_event(self._job_id, 1, EventType.PAGE_PROCESSED))
        self._repo.append(_event(self._job_id, 2, EventType.STATUS_CHANGED))

        recreated_repo = FsEventLogRepository(workspace=self._workspace)
        with mock.patch.object(
            recreated_repo,
            "_read_all_lines",
            side_effect=AssertionError("append should initialize from file tail instead of full read"),
        ):
            recreated_repo.append(_event(self._job_id, 3, EventType.EXTRACTION_COMPLETED))

        events = recreated_repo.list_by_job(self._job_id)
        self.assertEqual([1, 2, 3], [event.seq for event in events])
