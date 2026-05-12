"""
Step 18: 应用层回归基线

验收目标（严格对齐实施步骤）：
1. 以应用服务真实装配形态回归 `config` / `build` / `extraction` / `stream` / `job`。
2. 正向路径覆盖：配置 -> 创建 job -> 调度提取 -> 历史事件 replay -> build -> 保存 output。
3. 反向路径覆盖：非法配置、缺失 API Key、鉴权失败、ready 后页面写操作拒绝。
4. 边界路径覆盖：live stream、损坏 events.jsonl、build 失败回滚且不留半成品。
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, mock

import fitz

from backend.build.application import BuildApplication, SimpleMarkdownBuildPipeline
from backend.config.application import (
    ConfigApplication,
    ExtractConfigInput,
    GetPublicConfigQuery,
    ModelConfigInput,
    UpdateConfigCommand,
)
from backend.extraction.application import ExtractionApplication
from backend.infra.fs import (
    FsArtifactRepository,
    FsConfigRepository,
    FsEventLogRepository,
    FsJobRepository,
    FsPageRepository,
    FsSecretStore,
    FsSourceDocumentStore,
    WorkspaceManager,
)
from backend.infra.pdf import PymupdfPdfDocumentGateway
from backend.infra.stream import EventLogBackedEventPublisher, InMemoryLiveSubscriberHub
from backend.job.application import (
    BuildJobCommand,
    CreateJobCommand,
    JobApplication,
    RetryPageCommand,
    SaveOutputCommand,
    SavePageCommand,
)
from backend.shared_kernel.contracts import (
    BuildMergeMode,
    EventType,
    JobAggregate,
    JobEvent,
    JobStatus,
    ModelConfig,
    PageStatus,
)
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.stream.application import StreamApplication


def _build_pdf_bytes(*, total_pages: int) -> bytes:
    doc = fitz.open()
    try:
        for _ in range(total_pages):
            doc.new_page()
        return doc.tobytes()
    finally:
        doc.close()


def _template_config_text() -> str:
    return (
        "model:\n"
        '  name: "vision-template"\n'
        "  timeout_seconds: 30\n"
        "\n"
        "extract:\n"
        "  dpi: 150\n"
        "  concurrency: 2\n"
        "  max_retries: 1\n"
        "  prompt: |\n"
        "    请提取成 Markdown\n"
    )


def _update_config_command(
    *,
    name: str = "vision-template",
    timeout_seconds: int = 30,
    dpi: int = 150,
    concurrency: int = 2,
    max_retries: int = 1,
    prompt: str = "请提取成 Markdown",
    api_key: str | None = None,
) -> UpdateConfigCommand:
    return UpdateConfigCommand(
        model=ModelConfigInput(
            name=name,
            timeout_seconds=timeout_seconds,
        ),
        extract=ExtractConfigInput(
            dpi=dpi,
            concurrency=concurrency,
            max_retries=max_retries,
            prompt=prompt,
        ),
        api_key=api_key,
    )


async def _next_event(stream, *, timeout_seconds: float = 0.5) -> JobEvent:  # type: ignore[no-untyped-def]
    return await asyncio.wait_for(anext(stream), timeout=timeout_seconds)


async def _collect_until_closed(stream, *, max_events: int = 10) -> list[JobEvent]:  # type: ignore[no-untyped-def]
    events: list[JobEvent] = []
    try:
        for _ in range(max_events):
            try:
                events.append(await _next_event(stream))
            except StopAsyncIteration:
                return events
    finally:
        await stream.aclose()
    raise AssertionError("stream did not close within expected event budget")


class _StepClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        now = self._current
        self._current = self._current + timedelta(seconds=1)
        return now


class _FixedIdGenerator:
    def __init__(self, value: uuid.UUID) -> None:
        self._value = value

    def new(self) -> uuid.UUID:
        return self._value


class _ControlledTaskScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str]] = []
        self._pending: dict[tuple[uuid.UUID, str], object] = {}

    def schedule(
        self,
        *,
        job_id: uuid.UUID,
        task_name: str,
        task_factory,
    ) -> bool:
        key = (job_id, task_name)
        if key in self._pending:
            return False
        self.calls.append(key)
        self._pending[key] = task_factory
        return True

    def run(self, *, job_id: uuid.UUID, task_name: str) -> None:
        key = (job_id, task_name)
        if key not in self._pending:
            raise AssertionError(f"scheduled task not found: {key}")
        task_factory = self._pending.pop(key)
        asyncio.run(task_factory())


class _InspectableVisionGateway:
    def __init__(self) -> None:
        self.outcomes: list[str | Exception] = []
        self.calls: list[dict[str, object]] = []
        self._lock = threading.Lock()

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
        resolved_page_num = page_num or 1
        with self._lock:
            self.calls.append(
                {
                    "prompt": prompt,
                    "model_name": model.name,
                    "api_key": api_key,
                    "max_retries": max_retries,
                    "image_size": len(image_bytes),
                    "page_num": resolved_page_num,
                }
            )
            if 1 <= resolved_page_num <= len(self.outcomes):
                outcome = self.outcomes[resolved_page_num - 1]
            else:
                outcome = f"# page {resolved_page_num}"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def open_session(
        self,
        *,
        model: ModelConfig,
        api_key: str,
    ) -> "_InspectableVisionSession":
        return _InspectableVisionSession(
            gateway=self,
            model=model,
            api_key=api_key,
        )

    def test_connection(self, *, model: ModelConfig, api_key: str) -> str:
        return self.extract_markdown(
            image_bytes=b"probe",
            prompt="Hello, please reply with OK.",
            model=model,
            api_key=api_key,
            max_retries=0,
            page_num=0,
        )


class _InspectableVisionSession:
    def __init__(
        self,
        *,
        gateway: _InspectableVisionGateway,
        model: ModelConfig,
        api_key: str,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._api_key = api_key

    def extract_markdown(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        return self._gateway.extract_markdown(
            image_bytes=image_bytes,
            prompt=prompt,
            model=self._model,
            api_key=self._api_key,
            max_retries=max_retries,
            page_num=page_num,
        )

    def close(self) -> None:
        return


class _ApplicationHarness:
    def __init__(self, *, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        self.tmp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.tmp_dir)
        self.data_root = self.project_root / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.project_root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")

        self.workspace = WorkspaceManager(data_root=self.data_root)
        self.job_repo = FsJobRepository(workspace=self.workspace)
        self.page_repo = FsPageRepository(workspace=self.workspace)
        self.source_store = FsSourceDocumentStore(workspace=self.workspace)
        self.artifact_repo = FsArtifactRepository(workspace=self.workspace)
        self.event_log_repo = FsEventLogRepository(workspace=self.workspace)
        self.secret_store = FsSecretStore(data_root=self.data_root)
        self.config_repo = FsConfigRepository(
            data_root=self.data_root,
            project_root=self.project_root,
            secret_store=self.secret_store,
        )
        self.live_hub = InMemoryLiveSubscriberHub()
        self.event_publisher = EventLogBackedEventPublisher(
            event_log_repository=self.event_log_repo,
            live_subscriber_hub=self.live_hub,
        )
        self.clock = _StepClock(start=datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc))
        self.scheduler = _ControlledTaskScheduler()
        self.vision_gateway = _InspectableVisionGateway()
        self.pdf_gateway = PymupdfPdfDocumentGateway()

        self.config_app = ConfigApplication(
            config_repository=self.config_repo,
            secret_store=self.secret_store,
            vision_gateway=self.vision_gateway,
        )
        self.build_app = BuildApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            artifact_repository=self.artifact_repo,
            clock=self.clock,
            pipeline=SimpleMarkdownBuildPipeline(),
        )
        self.extraction_app = ExtractionApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            source_store=self.source_store,
            config_repository=self.config_repo,
            secret_store=self.secret_store,
            pdf_gateway=self.pdf_gateway,
            vision_gateway=self.vision_gateway,
            event_publisher=self.event_publisher,
            task_scheduler=self.scheduler,
            clock=self.clock,
        )
        self.job_app = JobApplication(
            job_repository=self.job_repo,
            page_repository=self.page_repo,
            source_store=self.source_store,
            build_application=self.build_app,
            extraction_application=self.extraction_app,
            pdf_gateway=self.pdf_gateway,
            secret_store=self.secret_store,
            clock=self.clock,
            id_generator=_FixedIdGenerator(job_id),
        )
        self.stream_app = StreamApplication(
            event_log_repository=self.event_log_repo,
            live_subscriber_hub=self.live_hub,
            job_repository=self.job_repo,
        )

    @property
    def config_path(self) -> Path:
        return self.data_root / "config.yaml"

    @property
    def secrets_path(self) -> Path:
        return self.data_root / "secrets.json"

    @property
    def output_path(self) -> Path:
        return self.workspace.artifacts_dir(self.job_id) / "output.md"

    def update_config(self, **kwargs) -> object:  # type: ignore[no-untyped-def]
        return self.config_app.update_config(_update_config_command(**kwargs))

    def create_job(
        self,
        *,
        total_pages: int,
        pdf_filename: str = "course.pdf",
    ):
        return self.job_app.create_job(
            CreateJobCommand(
                pdf_filename=pdf_filename,
                pdf_bytes=_build_pdf_bytes(total_pages=total_pages),
            )
        )

    def run_scheduled(self, *, task_name: str = "extract-all") -> None:
        self.scheduler.run(job_id=self.job_id, task_name=task_name)

    def replay_events(self) -> list[JobEvent]:
        stream = self.stream_app.subscribe_job_events(job_id=self.job_id, replay=True)
        return asyncio.run(_collect_until_closed(stream))

    def seed_job(
        self,
        *,
        status: JobStatus,
        total_pages: int,
        source_pdf_name: str = "seed.pdf",
        succeeded_pages: list[int] | None = None,
        failed_pages: list[int] | None = None,
        version: int = 1,
        last_error: str | None = None,
    ) -> JobAggregate:
        now = self.clock.now()
        job = JobAggregate(
            job_id=self.job_id,
            source_pdf_name=source_pdf_name,
            total_pages=total_pages,
            status=status,
            succeeded_pages=list(succeeded_pages or []),
            failed_pages=list(failed_pages or []),
            created_at=now,
            updated_at=now,
            version=version,
            last_error=last_error,
        )
        self.job_repo.save(job)
        return job


class TestApplicationRegressionBaseline(TestCase):
    def test_config_template_initialization_is_write_only_and_rejects_invalid_update(self) -> None:
        harness = _ApplicationHarness(job_id=uuid.uuid4())

        view = harness.config_app.get_public_config(GetPublicConfigQuery())
        original_text = harness.config_path.read_text(encoding="utf-8")

        self.assertTrue(harness.config_path.exists())
        self.assertEqual("vision-template", view.model.name)
        self.assertEqual(2, view.extract.concurrency)
        self.assertFalse(view.has_api_key)
        self.assertFalse(hasattr(view, "api_key"))

        with self.assertRaises(AppError) as ctx:
            harness.update_config(concurrency=0)
        self.assertEqual(ErrorCode.CONFIG_INVALID, ctx.exception.code)
        self.assertEqual(original_text, harness.config_path.read_text(encoding="utf-8"))
        self.assertFalse(harness.secrets_path.exists())

    def test_create_job_without_api_key_rejects_before_workspace_write(self) -> None:
        job_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        harness = _ApplicationHarness(job_id=job_id)

        with self.assertRaises(AppError) as ctx:
            harness.create_job(total_pages=1, pdf_filename="no-key.pdf")
        self.assertEqual(ErrorCode.CONFIG_MISSING_API_KEY, ctx.exception.code)
        self.assertFalse(harness.job_repo.exists(job_id))
        self.assertFalse(harness.workspace.job_dir(job_id).exists())
        self.assertEqual([], harness.scheduler.calls)

    def test_application_stack_happy_path_covers_create_extract_replay_build_and_ready_guards(self) -> None:
        job_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
        harness = _ApplicationHarness(job_id=job_id)

        initial = harness.config_app.get_public_config(GetPublicConfigQuery())
        updated = harness.update_config(
            name="vision-regression-model",
            concurrency=4,
            max_retries=2,
            prompt="请严格提取 Markdown 并保留标题层级",
            api_key="step18-secret",
        )
        harness.vision_gateway.outcomes = [
            "# 第一页\n内容 1",
            "# 第二页\n内容 2",
        ]

        created = harness.create_job(total_pages=2, pdf_filename="高压课程.pdf")

        self.assertFalse(initial.has_api_key)
        self.assertTrue(updated.has_api_key)
        self.assertEqual(job_id, created.job_id)
        self.assertEqual(JobStatus.EXTRACTING, created.status)
        self.assertEqual(2, created.total_pages)
        self.assertEqual([(job_id, "extract-all")], harness.scheduler.calls)

        job_before = harness.job_app.get_job(job_id)
        source_ref = harness.job_app.get_source_document(job_id)
        page_summaries = harness.job_app.list_pages(job_id)
        self.assertEqual(JobStatus.EXTRACTING, job_before.status)
        self.assertEqual("高压课程.pdf", source_ref.filename)
        self.assertEqual(
            [(1, PageStatus.PENDING), (2, PageStatus.PENDING)],
            [(item.page_num, item.status) for item in page_summaries],
        )

        harness.run_scheduled()

        self.assertEqual(2, len(harness.vision_gateway.calls))
        self.assertEqual("vision-regression-model", harness.vision_gateway.calls[0]["model_name"])
        self.assertEqual(
            "请严格提取 Markdown 并保留标题层级",
            harness.vision_gateway.calls[0]["prompt"],
        )
        self.assertEqual("step18-secret", harness.vision_gateway.calls[0]["api_key"])
        self.assertEqual(2, harness.vision_gateway.calls[0]["max_retries"])
        self.assertGreater(int(harness.vision_gateway.calls[0]["image_size"]), 0)

        job_after = harness.job_app.get_job(job_id)
        second_page = harness.job_app.get_page(job_id, 2)
        self.assertEqual(JobStatus.EXTRACTED, job_after.status)
        self.assertEqual([1, 2], job_after.succeeded_pages)
        self.assertEqual([], job_after.failed_pages)
        self.assertEqual("# 第二页\n内容 2", second_page.content)

        history = harness.replay_events()
        self.assertEqual(
            [
                EventType.STATUS_CHANGED,
                EventType.STATUS_CHANGED,
                EventType.PAGE_PROCESSED,
                EventType.PAGE_PROCESSED,
                EventType.EXTRACTION_COMPLETED,
            ],
            [event.event_type for event in history],
        )
        self.assertEqual(sorted(event.seq for event in history), [event.seq for event in history])

        with self.assertRaises(AppError) as output_ctx:
            harness.job_app.get_output_document(job_id)
        self.assertEqual(ErrorCode.OUTPUT_NOT_READY, output_ctx.exception.code)

        build_result = harness.job_app.build_output(
            BuildJobCommand(
                job_id=job_id,
                merge_mode=BuildMergeMode.SEPARATOR_WITH_PAGE_NUMBER,
            )
        )
        artifact = harness.job_app.get_output_artifact(job_id)
        built_output = harness.job_app.get_output_document(job_id)
        saved_output = harness.job_app.save_output(
            SaveOutputCommand(job_id=job_id, content="# 人工修订\n")
        )

        self.assertEqual(JobStatus.READY, build_result.status)
        self.assertEqual(f"/api/jobs/{job_id}/output", build_result.output_url)
        self.assertEqual(f"/api/jobs/{job_id}/output/download", build_result.download_url)
        self.assertEqual("output.md", artifact.filename)
        self.assertEqual(
            "--- 第 1 页 ---\n\n# 第一页\n内容 1\n\n--- 第 2 页 ---\n\n# 第二页\n内容 2",
            built_output.content,
        )
        self.assertEqual("# 人工修订\n", saved_output.content)

        with self.assertRaises(AppError) as save_page_ctx:
            harness.job_app.save_page(
                SavePageCommand(job_id=job_id, page_num=1, content="# forbidden")
            )
        self.assertEqual(ErrorCode.PAGE_EDIT_FORBIDDEN, save_page_ctx.exception.code)

        with self.assertRaises(AppError) as retry_page_ctx:
            harness.job_app.retry_page(RetryPageCommand(job_id=job_id, page_num=1))
        self.assertEqual(ErrorCode.PAGE_RETRY_FORBIDDEN, retry_page_ctx.exception.code)

    def test_auth_failure_produces_failed_job_and_terminal_replay(self) -> None:
        job_id = uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
        harness = _ApplicationHarness(job_id=job_id)
        harness.update_config(api_key="broken-secret", concurrency=1)
        harness.vision_gateway.outcomes = [
            AppError(ErrorCode.LLM_AUTH_FAILED, "invalid api key"),
            "# should not run",
        ]

        created = harness.create_job(total_pages=2, pdf_filename="auth-fail.pdf")
        self.assertEqual(JobStatus.EXTRACTING, created.status)

        with self.assertRaises(AppError) as ctx:
            harness.run_scheduled()
        self.assertEqual(ErrorCode.LLM_AUTH_FAILED, ctx.exception.code)

        job = harness.job_app.get_job(job_id)
        history = harness.replay_events()
        pages = harness.job_app.list_pages(job_id)
        self.assertEqual(JobStatus.FAILED, job.status)
        self.assertGreaterEqual(len(harness.vision_gateway.calls), 1)
        self.assertIn(1, [int(call["page_num"]) for call in harness.vision_gateway.calls])
        self.assertEqual(EventType.JOB_FAILED, history[-1].event_type)
        self.assertEqual("failed", history[-1].payload["type"])
        self.assertIn("invalid api key", str(history[-1].payload["detail"]))
        self.assertTrue(all(event.event_type == EventType.STATUS_CHANGED for event in history[:-1]))
        self.assertTrue(any(event.payload["status"] == "pending" for event in history[:-1]))
        final_status_by_page = {
            int(event.payload["page_num"]): str(event.payload["status"])
            for event in history[:-1]
        }
        self.assertEqual(
            {1: "pending", 2: "pending"},
            final_status_by_page,
        )
        self.assertEqual(sorted(event.seq for event in history), [event.seq for event in history])
        self.assertEqual(PageStatus.PENDING, pages[1].status)
        self.assertEqual(PageStatus.PENDING, pages[0].status)

    def test_stream_live_publish_reaches_subscriber_without_replay(self) -> None:
        harness = _ApplicationHarness(job_id=uuid.uuid4())
        harness.seed_job(status=JobStatus.EXTRACTING, total_pages=1)
        stream = harness.stream_app.subscribe_job_events(job_id=harness.job_id, replay=False)
        live_event = JobEvent(
            job_id=harness.job_id,
            seq=1,
            event_type=EventType.PAGE_PROCESSED,
            payload={
                "type": "page",
                "page_num": 1,
                "status": "done",
                "processed_count": 1,
                "total_pages": 1,
            },
            created_at=harness.clock.now(),
        )

        async def _scenario() -> JobEvent:
            waiter = asyncio.create_task(_next_event(stream))
            await asyncio.sleep(0)
            harness.stream_app.publish(live_event)
            received = await waiter
            await stream.aclose()
            return received

        received = asyncio.run(_scenario())

        self.assertEqual(live_event.seq, received.seq)
        self.assertEqual(live_event.event_type, received.event_type)
        self.assertEqual([1], [event.seq for event in harness.event_log_repo.list_by_job(harness.job_id)])

    def test_stream_corrupted_event_log_raises_state_corrupted(self) -> None:
        harness = _ApplicationHarness(job_id=uuid.uuid4())
        harness.seed_job(status=JobStatus.EXTRACTING, total_pages=1)
        harness.workspace.ensure_job_dir(harness.job_id)
        harness.workspace.events_path(harness.job_id).write_text("{broken json", encoding="utf-8")
        stream = harness.stream_app.subscribe_job_events(job_id=harness.job_id, replay=True)

        with self.assertRaises(AppError) as ctx:
            asyncio.run(_next_event(stream))
        self.assertEqual(ErrorCode.STATE_CORRUPTED, ctx.exception.code)

    def test_build_failure_rolls_back_to_extracted_and_leaves_no_output(self) -> None:
        job_id = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        harness = _ApplicationHarness(job_id=job_id)
        harness.update_config(api_key="build-secret")
        harness.vision_gateway.outcomes = ["# 唯一页面\n内容"]
        harness.create_job(total_pages=1, pdf_filename="rollback.pdf")
        harness.run_scheduled()

        with mock.patch(
            "backend.infra.fs.artifact_repository._atomic_write_text",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(AppError) as ctx:
                harness.job_app.build_output(BuildJobCommand(job_id=job_id))
        self.assertEqual(ErrorCode.PERSISTENCE_ERROR, ctx.exception.code)

        job = harness.job_app.get_job(job_id)
        page = harness.job_app.get_page(job_id, 1)
        self.assertEqual(JobStatus.EXTRACTED, job.status)
        self.assertEqual("# 唯一页面\n内容", page.content)
        self.assertFalse(harness.output_path.exists())

        with self.assertRaises(AppError) as output_ctx:
            harness.job_app.get_output_document(job_id)
        self.assertEqual(ErrorCode.OUTPUT_NOT_READY, output_ctx.exception.code)
