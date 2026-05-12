"""
Step 10: build-application 应用层测试

验收目标（严格对齐实施步骤）：
1. build 成功：仅允许 extracted -> building -> ready，生成 output。
2. 非法状态 build 必须被拒绝（JOB_STATUS_CONFLICT）。
3. building 期间其他写操作（保存 output）必须拒绝。
4. save_output_document 仅在 ready 允许。
5. 构建失败后状态回滚到构建前稳定状态，且不留下半成品。
6. build/save output 过程中不得回写 pages/ 内容。
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, mock

from backend.build.application.service import BuildApplication
from backend.infra.fs import FsArtifactRepository, FsJobRepository, FsPageRepository, WorkspaceManager
from backend.shared_kernel.contracts import (
    BuildMergeMode,
    JobAggregate,
    JobStatus,
    PageDocument,
    PageStatus,
)
from backend.shared_kernel.errors import AppError, ErrorCode


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        now = self._current
        self._current = self._current + timedelta(seconds=1)
        return now


class _FsHarness:
    def __init__(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.data_root = Path(self.tmp_dir) / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)

        self.workspace = WorkspaceManager(data_root=self.data_root)
        self.job_repo = FsJobRepository(workspace=self.workspace)
        self.page_repo = FsPageRepository(workspace=self.workspace)
        self.artifact_repo = FsArtifactRepository(workspace=self.workspace)
        self.clock = _FakeClock(start=datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc))
        self.app = BuildApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            artifact_repository=self.artifact_repo,
            clock=self.clock,
        )

    def save_job(
        self,
        *,
        job_id: uuid.UUID,
        status: JobStatus,
        total_pages: int,
        source_pdf_name: str = "lecture.pdf",
        succeeded_pages: list[int] | None = None,
        failed_pages: list[int] | None = None,
        version: int = 1,
    ) -> JobAggregate:
        now = _utc_now()
        job = JobAggregate(
            job_id=job_id,
            source_pdf_name=source_pdf_name,
            total_pages=total_pages,
            status=status,
            succeeded_pages=list(succeeded_pages or []),
            failed_pages=list(failed_pages or []),
            created_at=now,
            updated_at=now,
            version=version,
            last_error=None,
        )
        self.job_repo.save(job)
        return job

    def save_page(
        self,
        *,
        job_id: uuid.UUID,
        page_num: int,
        status: PageStatus,
        content: str | None,
        error_message: str | None = None,
    ) -> None:
        self.page_repo.save(
            PageDocument(
                job_id=job_id,
                page_num=page_num,
                status=status,
                content=content,
                error_message=error_message,
                updated_at=_utc_now(),
            )
        )

    def snapshot_pages(self, job_id: uuid.UUID) -> dict[str, str]:
        pages_dir = self.workspace.pages_dir(job_id)
        if not pages_dir.exists():
            return {}
        result: dict[str, str] = {}
        for path in sorted(pages_dir.glob("*")):
            result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result


class TestBuildApplication(TestCase):
    def setUp(self) -> None:
        self.fs = _FsHarness()

    def test_build_output_success_sets_ready_and_does_not_modify_pages(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=2,
            succeeded_pages=[1, 2],
            failed_pages=[],
            version=10,
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="## 第一节\n内容 A",
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=2,
            status=PageStatus.DONE,
            content="## 第二节\n内容 B",
        )
        page_snapshot_before = self.fs.snapshot_pages(job_id)

        artifact = self.fs.app.build_output(job_id)

        saved_job = self.fs.job_repo.get(job_id)
        output_doc = self.fs.app.get_output_document(job_id)
        output_artifact = self.fs.app.get_output_artifact(job_id)
        page_snapshot_after = self.fs.snapshot_pages(job_id)

        self.assertEqual(JobStatus.READY, saved_job.status)
        self.assertEqual(artifact, output_artifact)
        self.assertEqual(str(job_id), str(artifact.job_id))
        self.assertEqual("output.md", artifact.filename)
        self.assertEqual("## 第一节\n内容 A\n\n## 第二节\n内容 B", output_doc.content)
        self.assertEqual(page_snapshot_before, page_snapshot_after)

    def test_build_output_supports_page_separator_modes(self) -> None:
        cases = [
            (
                BuildMergeMode.SEPARATOR,
                "## 第一节\n内容 A\n\n---\n\n## 第二节\n内容 B",
            ),
            (
                BuildMergeMode.SEPARATOR_WITH_PAGE_NUMBER,
                "--- 第 1 页 ---\n\n## 第一节\n内容 A\n\n--- 第 2 页 ---\n\n## 第二节\n内容 B",
            ),
        ]
        for merge_mode, expected in cases:
            with self.subTest(merge_mode=merge_mode.value):
                job_id = uuid.uuid4()
                self.fs.save_job(
                    job_id=job_id,
                    status=JobStatus.EXTRACTED,
                    total_pages=2,
                    succeeded_pages=[1, 2],
                    failed_pages=[],
                )
                self.fs.save_page(
                    job_id=job_id,
                    page_num=1,
                    status=PageStatus.DONE,
                    content="## 第一节\n内容 A",
                )
                self.fs.save_page(
                    job_id=job_id,
                    page_num=2,
                    status=PageStatus.DONE,
                    content="## 第二节\n内容 B",
                )

                self.fs.app.build_output(job_id, merge_mode)

                self.assertEqual(expected, self.fs.app.get_output_document(job_id).content)

    def test_build_output_rejects_illegal_status(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTING,
            total_pages=1,
            succeeded_pages=[],
            failed_pages=[],
            version=3,
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.EXTRACTING,
            content=None,
        )

        with self.assertRaises(AppError) as ctx:
            self.fs.app.build_output(job_id)
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, ctx.exception.code)
        self.assertEqual(JobStatus.EXTRACTING, self.fs.job_repo.get(job_id).status)

    def test_build_output_rejects_extracted_with_failed_pages(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=2,
            succeeded_pages=[1],
            failed_pages=[2],
            version=3,
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="ok",
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=2,
            status=PageStatus.FAILED,
            content=None,
            error_message="boom",
        )

        with self.assertRaises(AppError) as ctx:
            self.fs.app.build_output(job_id)
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, ctx.exception.code)
        self.assertEqual(JobStatus.EXTRACTED, self.fs.job_repo.get(job_id).status)

    def test_building_status_rejects_save_output_write(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.BUILDING,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="page",
        )

        with self.assertRaises(AppError) as ctx:
            self.fs.app.save_output_document(job_id, "# edited")
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, ctx.exception.code)

    def test_save_output_document_only_allowed_in_ready(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="page",
        )

        with self.assertRaises(AppError) as extracted_ctx:
            self.fs.app.save_output_document(job_id, "# should fail")
        self.assertEqual(ErrorCode.OUTPUT_EDIT_FORBIDDEN, extracted_ctx.exception.code)

        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.READY,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
            version=20,
        )
        saved = self.fs.app.save_output_document(job_id, "# final output")

        self.assertEqual(job_id, saved.job_id)
        self.assertEqual("# final output", saved.content)
        self.assertEqual(JobStatus.READY, self.fs.job_repo.get(job_id).status)
        self.assertEqual("# final output", self.fs.artifact_repo.get_output_document(job_id).content)

    def test_discard_output_returns_ready_job_to_extracted_and_deletes_output(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.READY,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
            version=20,
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="page",
        )
        output_path = self.fs.workspace.artifacts_dir(job_id) / "output.md"
        self.fs.artifact_repo.save_output(job_id, "# final output")

        reopened = self.fs.app.discard_output(job_id)

        self.assertEqual(JobStatus.EXTRACTED, reopened.status)
        self.assertEqual(JobStatus.EXTRACTED, self.fs.job_repo.get(job_id).status)
        self.assertFalse(output_path.exists())

        with self.assertRaises(AppError) as ctx:
            self.fs.app.get_output_document(job_id)
        self.assertEqual(ErrorCode.OUTPUT_NOT_READY, ctx.exception.code)

    def test_build_failure_rolls_back_status_and_does_not_leave_half_output(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
            version=5,
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="## 正文\nabc",
        )
        page_snapshot_before = self.fs.snapshot_pages(job_id)
        output_path = self.fs.workspace.artifacts_dir(job_id) / "output.md"

        with mock.patch(
            "backend.infra.fs.artifact_repository._atomic_write_text",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(AppError) as ctx:
                self.fs.app.build_output(job_id)
        self.assertEqual(ErrorCode.PERSISTENCE_ERROR, ctx.exception.code)

        rolled_back = self.fs.job_repo.get(job_id)
        self.assertEqual(JobStatus.EXTRACTED, rolled_back.status)
        self.assertFalse(output_path.exists(), "构建失败后不应留下 output.md 半成品")
        self.assertEqual(page_snapshot_before, self.fs.snapshot_pages(job_id))

    def test_get_output_queries_require_ready_status(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
        )
        self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="page",
        )
        # 即使 artifacts 下已有旧文件，也不应在非 ready 状态对外暴露
        self.fs.artifact_repo.save_output(job_id, "# stale output")

        with self.assertRaises(AppError) as doc_ctx:
            self.fs.app.get_output_document(job_id)
        self.assertEqual(ErrorCode.OUTPUT_NOT_READY, doc_ctx.exception.code)

        with self.assertRaises(AppError) as artifact_ctx:
            self.fs.app.get_output_artifact(job_id)
        self.assertEqual(ErrorCode.OUTPUT_NOT_READY, artifact_ctx.exception.code)

