"""
Step 17: job-application 命令用例测试

验收目标（严格对齐实施步骤）：
1. create_job：正常创建、损坏 PDF、缺失 API Key、源文件与页面初始化落盘、调度提取。
2. save_page / retry_page：守卫拒绝非法状态；成功路径字段稳定。
3. build_output / save_output：结果字段稳定且与契约一致。
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

import fitz

from backend.build.application.service import BuildApplication
from backend.config.ports import SecretStore
from backend.extraction.application.service import ExtractionApplication
from backend.infra.fs import (
    FsArtifactRepository,
    FsJobRepository,
    FsPageRepository,
    FsSourceDocumentStore,
    WorkspaceManager,
)
from backend.infra.pdf import PymupdfPdfDocumentGateway
from backend.job.application import (
    AcceptedResult,
    BuildJobCommand,
    CreateJobCommand,
    CreateJobResult,
    DiscardOutputCommand,
    JobApplication,
    RetryPageCommand,
    SaveOutputCommand,
    SavePageCommand,
)
from backend.shared_kernel.contracts import (
    ExtractConfig,
    JobAggregate,
    JobEvent,
    JobStatus,
    ModelConfig,
    PageDocument,
    PageStatus,
    RuntimeConfig,
)
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.shared_kernel.time import IdGenerator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_pdf_bytes(*, total_pages: int) -> bytes:
    doc = fitz.open()
    try:
        for _ in range(total_pages):
            doc.new_page()
        return doc.tobytes()
    finally:
        doc.close()


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _FixedId(IdGenerator):
    def __init__(self, value: uuid.UUID) -> None:
        self._value = value

    def new(self) -> uuid.UUID:
        return self._value


class _NoopTaskScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str]] = []

    def schedule(
        self,
        *,
        job_id: uuid.UUID,
        task_name: str,
        task_factory,
    ) -> bool:
        self.calls.append((job_id, task_name))
        return True


class _FakeConfigRepository:
    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config

    def load(self) -> RuntimeConfig:
        return self._config

    def save(self, config: RuntimeConfig) -> RuntimeConfig:
        self._config = config
        return config


class _FakeSecretStore:
    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    def has_api_key(self) -> bool:
        return self._api_key is not None and bool(self._api_key.strip())

    def get_api_key(self) -> str | None:
        return self._api_key

    def require_api_key(self) -> str:
        if not self._api_key:
            raise AppError(code=ErrorCode.CONFIG_MISSING_API_KEY)
        return self._api_key

    def set_api_key(self, api_key: str) -> None:
        self._api_key = api_key


class _FakeEventPublisher:
    def publish(self, event: JobEvent) -> None:
        del event


class _CommandHarness:
    """FS + Build + Extraction + JobApplication（命令路径）。"""

    def __init__(
        self,
        *,
        secret: SecretStore,
        fixed_job_id: uuid.UUID | None = None,
    ) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.data_root = Path(self.tmp_dir) / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)

        self.workspace = WorkspaceManager(data_root=self.data_root)
        self.job_repo = FsJobRepository(workspace=self.workspace)
        self.page_repo = FsPageRepository(workspace=self.workspace)
        self.source_store = FsSourceDocumentStore(workspace=self.workspace)
        self.artifact_repo = FsArtifactRepository(workspace=self.workspace)

        self.clock = _FixedClock(datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc))
        self.build_app = BuildApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            artifact_repository=self.artifact_repo,
            clock=self.clock,
        )

        self.pdf_gateway = PymupdfPdfDocumentGateway()
        self._secret = secret
        self._id_gen: IdGenerator = (
            _FixedId(fixed_job_id) if fixed_job_id is not None else _FixedId(uuid.uuid4())
        )

        self.scheduler = _NoopTaskScheduler()
        runtime = RuntimeConfig(
            model=ModelConfig(
                name="vision-model",
                timeout_seconds=30,
            ),
            extract=ExtractConfig(
                dpi=150,
                concurrency=2,
                max_retries=1,
                prompt="提取成 Markdown",
            ),
            has_api_key=True,
        )
        self.extraction_app = ExtractionApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            source_store=self.source_store,
            config_repository=_FakeConfigRepository(runtime),
            secret_store=secret,
            pdf_gateway=self.pdf_gateway,
            vision_gateway=_UnusedVisionGateway(),
            event_publisher=_FakeEventPublisher(),
            task_scheduler=self.scheduler,
            clock=self.clock,
        )

        self.app = JobApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            source_store=self.source_store,
            build_application=self.build_app,
            extraction_application=self.extraction_app,
            pdf_gateway=self.pdf_gateway,
            secret_store=secret,
            clock=self.clock,
            id_generator=self._id_gen,
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

    def save_page_doc(
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


class _UnusedVisionGateway:
    def extract_markdown(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        model: ModelConfig,
        api_key: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        del image_bytes, prompt, model, api_key, max_retries, page_num
        raise AssertionError("vision gateway must not run in step17 command tests")


class TestJobCommandApplication(TestCase):
    def test_create_job_rejects_corrupt_pdf_before_any_workspace_writes(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        before = {p.name for p in harness.data_root.iterdir()}

        with self.assertRaises(AppError) as ctx:
            harness.app.create_job(
                CreateJobCommand(pdf_filename="bad.pdf", pdf_bytes=b"not a pdf")
            )
        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)
        after = {p.name for p in harness.data_root.iterdir()}
        self.assertEqual(before, after)

    def test_create_job_rejects_missing_api_key_before_persistence(self) -> None:
        job_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        harness = _CommandHarness(secret=_FakeSecretStore(None), fixed_job_id=job_id)
        pdf = _build_pdf_bytes(total_pages=1)
        before = {p.name for p in harness.data_root.iterdir()}

        with self.assertRaises(AppError) as ctx:
            harness.app.create_job(CreateJobCommand(pdf_filename="ok.pdf", pdf_bytes=pdf))
        self.assertEqual(ErrorCode.CONFIG_MISSING_API_KEY, ctx.exception.code)
        self.assertFalse(harness.job_repo.exists(job_id))
        after = {p.name for p in harness.data_root.iterdir()}
        self.assertEqual(before, after)

    def test_create_job_persists_source_pages_and_schedules_extraction(self) -> None:
        job_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
        harness = _CommandHarness(secret=_FakeSecretStore("unit-test-key"), fixed_job_id=job_id)
        pdf = _build_pdf_bytes(total_pages=2)
        cmd = CreateJobCommand(pdf_filename="course.pdf", pdf_bytes=pdf)

        result = harness.app.create_job(cmd)

        self.assertIsInstance(result, CreateJobResult)
        self.assertEqual(job_id, result.job_id)
        self.assertEqual(2, result.total_pages)
        self.assertEqual(JobStatus.EXTRACTING, result.status)

        job = harness.job_repo.get(job_id)
        self.assertEqual(JobStatus.EXTRACTING, job.status)
        self.assertEqual(2, job.total_pages)
        self.assertEqual("course.pdf", job.source_pdf_name)

        source_path = harness.workspace.source_path(job_id)
        self.assertTrue(source_path.exists())
        self.assertEqual(pdf, source_path.read_bytes())

        pages = sorted(harness.page_repo.list_by_job(job_id), key=lambda p: p.page_num)
        self.assertEqual(2, len(pages))
        self.assertEqual([1, 2], [p.page_num for p in pages])
        self.assertTrue(all(p.status == PageStatus.PENDING for p in pages))

        self.assertEqual([(job_id, "extract-all")], harness.scheduler.calls)

    def test_save_page_rejects_when_job_ready(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        job_id = uuid.uuid4()
        harness.save_job(
            job_id=job_id,
            status=JobStatus.READY,
            total_pages=1,
            succeeded_pages=[1],
        )
        harness.save_page_doc(
            job_id=job_id,
            page_num=1,
            status=PageStatus.DONE,
            content="## old",
        )

        with self.assertRaises(AppError) as ctx:
            harness.app.save_page(SavePageCommand(job_id=job_id, page_num=1, content="## new"))
        self.assertEqual(ErrorCode.PAGE_EDIT_FORBIDDEN, ctx.exception.code)

    def test_retry_page_rejects_when_job_ready(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        job_id = uuid.uuid4()
        harness.save_job(
            job_id=job_id,
            status=JobStatus.READY,
            total_pages=1,
            succeeded_pages=[1],
        )
        harness.save_page_doc(job_id=job_id, page_num=1, status=PageStatus.DONE, content="x")

        with self.assertRaises(AppError) as ctx:
            harness.app.retry_page(RetryPageCommand(job_id=job_id, page_num=1))
        self.assertEqual(ErrorCode.PAGE_RETRY_FORBIDDEN, ctx.exception.code)

    def test_save_page_success_returns_stable_page_view(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        job_id = uuid.uuid4()
        harness.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
        )
        harness.save_page_doc(job_id=job_id, page_num=1, status=PageStatus.DONE, content="old")

        view = harness.app.save_page(
            SavePageCommand(job_id=job_id, page_num=1, content="## edited")
        )

        self.assertEqual(job_id, view.job_id)
        self.assertEqual(1, view.page_num)
        self.assertEqual(PageStatus.DONE, view.status)
        self.assertEqual("## edited", view.content)
        self.assertIsNone(view.error_message)
        self.assertIsNotNone(view.updated_at)

        stored = harness.page_repo.get(job_id, 1)
        self.assertEqual("## edited", stored.content)

    def test_build_output_returns_stable_result_fields(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        job_id = uuid.uuid4()
        harness.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
        )
        harness.save_page_doc(job_id=job_id, page_num=1, status=PageStatus.DONE, content="# p1")

        result = harness.app.build_output(BuildJobCommand(job_id=job_id))

        self.assertEqual(JobStatus.READY, result.status)
        self.assertEqual(f"/api/jobs/{job_id}/output", result.output_url)
        self.assertEqual(f"/api/jobs/{job_id}/output/download", result.download_url)

        job = harness.job_repo.get(job_id)
        self.assertEqual(JobStatus.READY, job.status)

    def test_save_output_returns_stable_output_document_view(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        job_id = uuid.uuid4()
        harness.save_job(
            job_id=job_id,
            status=JobStatus.READY,
            total_pages=1,
            succeeded_pages=[1],
        )
        harness.save_page_doc(job_id=job_id, page_num=1, status=PageStatus.DONE, content="# p1")
        harness.artifact_repo.save_output(job_id, "# built")

        view = harness.app.save_output(SaveOutputCommand(job_id=job_id, content="# user edit\n"))

        self.assertEqual(job_id, view.job_id)
        self.assertEqual("# user edit\n", view.content)
        self.assertIsNotNone(view.updated_at)

        disk = harness.artifact_repo.get_output_document(job_id)
        self.assertEqual("# user edit\n", disk.content)

    def test_discard_output_returns_updated_job_view_and_reopens_pages(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        job_id = uuid.uuid4()
        harness.save_job(
            job_id=job_id,
            status=JobStatus.READY,
            total_pages=1,
            succeeded_pages=[1],
        )
        harness.save_page_doc(job_id=job_id, page_num=1, status=PageStatus.DONE, content="# p1")
        harness.artifact_repo.save_output(job_id, "# built")

        view = harness.app.discard_output(DiscardOutputCommand(job_id=job_id))

        self.assertEqual(job_id, view.job_id)
        self.assertEqual(JobStatus.EXTRACTED, view.status)
        self.assertEqual([1], view.succeeded_pages)
        self.assertEqual([], view.failed_pages)
        self.assertEqual(1, view.processed_count)

    def test_retry_page_success_returns_accepted_result_and_schedules(self) -> None:
        harness = _CommandHarness(secret=_FakeSecretStore("k"))
        job_id = uuid.uuid4()
        harness.save_job(
            job_id=job_id,
            status=JobStatus.EXTRACTED,
            total_pages=1,
            succeeded_pages=[1],
        )
        harness.save_page_doc(job_id=job_id, page_num=1, status=PageStatus.DONE, content="# ok")
        harness.source_store.save_source(job_id, "s.pdf", _build_pdf_bytes(total_pages=1))

        accepted = harness.app.retry_page(RetryPageCommand(job_id=job_id, page_num=1))

        self.assertEqual(AcceptedResult(job_id=job_id, page_num=1), accepted)
        self.assertIn((job_id, "extract-page-1"), harness.scheduler.calls)

        page = harness.page_repo.get(job_id, 1)
        self.assertEqual(PageStatus.EXTRACTING, page.status)
        job = harness.job_repo.get(job_id)
        self.assertEqual(JobStatus.EXTRACTING, job.status)

