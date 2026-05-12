"""
Step 16: job-application 查询用例测试

验收目标（严格对齐实施步骤）：
1. job 不存在时，所有查询都返回 JOB_NOT_FOUND。
2. page 不存在时返回 PAGE_NOT_FOUND。
3. output 未就绪时，两个 output 查询都返回 OUTPUT_NOT_READY。
4. list_pages 只返回轻量摘要，不泄露正文或错误详情。
5. get_page 按状态返回正文或错误详情；pending/extracting 不暴露正文。
6. source 查询返回稳定元数据。
7. get_job 将领域对象映射为稳定 DTO。
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, mock

from backend.build.application.service import BuildApplication
from backend.infra.fs import (
    FsArtifactRepository,
    FsJobRepository,
    FsPageRepository,
    FsSourceDocumentStore,
    WorkspaceManager,
)
from backend.job.application import JobApplication
from backend.job.ports import ExtractionApplication, PdfDocumentGateway
from backend.shared_kernel.contracts import JobAggregate, JobStatus, PageDocument, PageStatus
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.shared_kernel.time import Uuid4Generator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _QueryOnlyPdf(PdfDocumentGateway):
    def count_pages(self, pdf_bytes: bytes) -> int:
        raise AssertionError("step16 query harness must not open PDF")

    def render_page(self, pdf_bytes: bytes, page_num: int, dpi: int) -> bytes:
        raise AssertionError("step16 query harness must not render PDF")


class _QueryOnlySecret:
    def has_api_key(self) -> bool:
        return True

    def get_api_key(self) -> str | None:
        return "unused"

    def require_api_key(self) -> str:
        return "unused"

    def set_api_key(self, api_key: str) -> None:
        del api_key


class _QueryOnlyExtraction(ExtractionApplication):
    def start_job_extraction(self, job_id: uuid.UUID) -> None:
        raise AssertionError("step16 query harness must not start extraction")

    def retry_page_extraction(self, job_id: uuid.UUID, page_num: int) -> None:
        raise AssertionError("step16 query harness must not retry extraction")


class _FsHarness:
    def __init__(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.data_root = Path(self.tmp_dir) / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)

        self.workspace = WorkspaceManager(data_root=self.data_root)
        self.job_repo = FsJobRepository(workspace=self.workspace)
        self.page_repo = FsPageRepository(workspace=self.workspace)
        self.source_store = FsSourceDocumentStore(workspace=self.workspace)
        self.artifact_repo = FsArtifactRepository(workspace=self.workspace)
        self._shared_clock = _FixedClock(datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc))
        self.build_app = BuildApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            artifact_repository=self.artifact_repo,
            clock=self._shared_clock,
        )
        self.app = JobApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            source_store=self.source_store,
            build_application=self.build_app,
            extraction_application=_QueryOnlyExtraction(),
            pdf_gateway=_QueryOnlyPdf(),
            secret_store=_QueryOnlySecret(),
            clock=self._shared_clock,
            id_generator=Uuid4Generator(),
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
        updated_at: datetime | None = None,
    ) -> PageDocument:
        page = PageDocument(
            job_id=job_id,
            page_num=page_num,
            status=status,
            content=content,
            error_message=error_message,
            updated_at=updated_at or _utc_now(),
        )
        self.page_repo.save(page)
        return page


class TestJobQueryApplication(TestCase):
    def setUp(self) -> None:
        self.fs = _FsHarness()

    def test_all_queries_raise_job_not_found_when_job_is_missing(self) -> None:
        missing_job_id = uuid.uuid4()
        self.fs.source_store.save_source(
            job_id=missing_job_id,
            pdf_filename="orphan.pdf",
            pdf_bytes=b"%PDF-1.7 orphan",
        )
        self.fs.save_page(
            job_id=missing_job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="orphan page body",
        )
        self.fs.artifact_repo.save_output(missing_job_id, "# orphan output")

        queries = {
            "get_job": lambda: self.fs.app.get_job(missing_job_id),
            "get_source_document": lambda: self.fs.app.get_source_document(missing_job_id),
            "list_pages": lambda: self.fs.app.list_pages(missing_job_id),
            "get_page": lambda: self.fs.app.get_page(missing_job_id, 1),
            "get_output_document": lambda: self.fs.app.get_output_document(missing_job_id),
            "get_output_artifact": lambda: self.fs.app.get_output_artifact(missing_job_id),
        }

        for name, call in queries.items():
            with self.subTest(query=name):
                with self.assertRaises(AppError) as ctx:
                    call()
                self.assertEqual(ErrorCode.JOB_NOT_FOUND, ctx.exception.code)

    def test_get_job_maps_domain_to_stable_dto(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=4,
            succeeded_pages=[3, 1],
            failed_pages=[4, 2],
            version=7,
        )

        view = self.fs.app.get_job(job_id)

        self.assertEqual(job_id, view.job_id)
        self.assertEqual(JobStatus.EXTRACTED, view.status)
        self.assertEqual(4, view.total_pages)
        self.assertEqual([1, 3], view.succeeded_pages)
        self.assertEqual([2, 4], view.failed_pages)
        self.assertEqual(4, view.processed_count)
        self.assertFalse(hasattr(view, "created_at"))
        self.assertFalse(hasattr(view, "updated_at"))

    def test_list_pages_returns_only_lightweight_summaries(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=3)
        self.fs.save_page(job_id=job_id, page_num=3, status=PageStatus.EXTRACTING, content="partial")
        self.fs.save_page(job_id=job_id, page_num=1, status=PageStatus.DONE, content="## page 1")
        self.fs.save_page(
            job_id=job_id,
            page_num=2,
            status=PageStatus.FAILED,
            content="stale body",
            error_message="timeout",
        )

        summaries = self.fs.app.list_pages(job_id)

        self.assertEqual(
            [(1, PageStatus.DONE), (2, PageStatus.FAILED), (3, PageStatus.EXTRACTING)],
            [(item.page_num, item.status) for item in summaries],
        )
        for summary in summaries:
            self.assertFalse(hasattr(summary, "content"))
            self.assertFalse(hasattr(summary, "error_message"))
            self.assertFalse(hasattr(summary, "updated_at"))

    def test_list_pages_summary_query_does_not_read_markdown_bodies(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=2)
        self.fs.save_page(job_id=job_id, page_num=1, status=PageStatus.DONE, content="## page 1")
        self.fs.save_page(
            job_id=job_id,
            page_num=2,
            status=PageStatus.FAILED,
            content="stale body",
            error_message="timeout",
        )

        original_read_text = Path.read_text

        def _guard_markdown_reads(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if path.suffix == ".md":
                raise AssertionError("list_pages summary query should not read markdown body files")
            return original_read_text(path, *args, **kwargs)

        with mock.patch(
            "pathlib.Path.read_text",
            autospec=True,
            side_effect=_guard_markdown_reads,
        ):
            summaries = self.fs.app.list_pages(job_id)

        self.assertEqual(
            [(1, PageStatus.DONE), (2, PageStatus.FAILED)],
            [(item.page_num, item.status) for item in summaries],
        )

    def test_get_page_returns_body_or_error_detail_by_status(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=4)
        pending_page = self.fs.save_page(
            job_id=job_id,
            page_num=1,
            status=PageStatus.PENDING,
            content="hidden pending body",
        )
        extracting_page = self.fs.save_page(
            job_id=job_id,
            page_num=2,
            status=PageStatus.EXTRACTING,
            content="hidden extracting body",
        )
        done_page = self.fs.save_page(
            job_id=job_id,
            page_num=3,
            status=PageStatus.DONE,
            content="## done body",
        )
        failed_page = self.fs.save_page(
            job_id=job_id,
            page_num=4,
            status=PageStatus.FAILED,
            content="hidden failed body",
            error_message="llm timeout",
        )

        pending_view = self.fs.app.get_page(job_id, pending_page.page_num)
        extracting_view = self.fs.app.get_page(job_id, extracting_page.page_num)
        done_view = self.fs.app.get_page(job_id, done_page.page_num)
        failed_view = self.fs.app.get_page(job_id, failed_page.page_num)

        self.assertEqual(PageStatus.PENDING, pending_view.status)
        self.assertIsNone(pending_view.content)
        self.assertIsNone(pending_view.error_message)
        self.assertIsNone(pending_view.updated_at)

        self.assertEqual(PageStatus.EXTRACTING, extracting_view.status)
        self.assertIsNone(extracting_view.content)
        self.assertIsNone(extracting_view.error_message)
        self.assertIsNone(extracting_view.updated_at)

        self.assertEqual(PageStatus.DONE, done_view.status)
        self.assertEqual("## done body", done_view.content)
        self.assertIsNone(done_view.error_message)
        self.assertEqual(done_page.updated_at, done_view.updated_at)

        self.assertEqual(PageStatus.FAILED, failed_view.status)
        self.assertIsNone(failed_view.content)
        self.assertEqual("llm timeout", failed_view.error_message)
        self.assertEqual(failed_page.updated_at, failed_view.updated_at)

    def test_get_page_raises_page_not_found_when_page_is_missing(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=1)

        with self.assertRaises(AppError) as ctx:
            self.fs.app.get_page(job_id, 99)
        self.assertEqual(ErrorCode.PAGE_NOT_FOUND, ctx.exception.code)

    def test_get_source_document_returns_stable_metadata(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(job_id=job_id, status=JobStatus.EXTRACTING, total_pages=1)
        source_ref = self.fs.source_store.save_source(
            job_id=job_id,
            pdf_filename="电力系统.pdf",
            pdf_bytes=b"%PDF-1.7\nmock pdf\n",
        )

        view = self.fs.app.get_source_document(job_id)

        self.assertEqual(source_ref, view)
        self.assertEqual(job_id, view.job_id)
        self.assertEqual(f"{job_id}/source.pdf", view.relative_path)
        self.assertEqual("application/pdf", view.content_type)
        self.assertEqual("电力系统.pdf", view.filename)
        self.assertEqual(len(b"%PDF-1.7\nmock pdf\n"), view.size_bytes)

    def test_output_queries_reject_non_ready_job_even_if_output_file_exists(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
        )
        self.fs.artifact_repo.save_output(job_id, "# stale output")

        with self.assertRaises(AppError) as document_ctx:
            self.fs.app.get_output_document(job_id)
        self.assertEqual(ErrorCode.OUTPUT_NOT_READY, document_ctx.exception.code)

        with self.assertRaises(AppError) as artifact_ctx:
            self.fs.app.get_output_artifact(job_id)
        self.assertEqual(ErrorCode.OUTPUT_NOT_READY, artifact_ctx.exception.code)

    def test_output_queries_return_ready_document_and_artifact(self) -> None:
        job_id = uuid.uuid4()
        self.fs.save_job(
            job_id=job_id,
            status=JobStatus.READY,
            total_pages=1,
            succeeded_pages=[1],
            failed_pages=[],
        )
        self.fs.artifact_repo.save_output(job_id, "# final output")

        document = self.fs.app.get_output_document(job_id)
        artifact = self.fs.app.get_output_artifact(job_id)

        self.assertEqual(job_id, document.job_id)
        self.assertEqual("# final output", document.content)
        self.assertIsNotNone(document.updated_at)
        self.assertEqual(job_id, artifact.job_id)
        self.assertEqual("output.md", artifact.filename)
        self.assertEqual("text/markdown; charset=utf-8", artifact.content_type)
