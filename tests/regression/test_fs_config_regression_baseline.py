"""
Step 08: 文件系统与配置回归基线（跨仓储集成测试）

验收目标（严格对齐实施步骤）：
1. 组合 state/source/pages/artifacts/events/config/secrets，验证同一 job 工作区可完整创建、读取、覆盖。
2. 验证跨仓储损坏检测：state/source_meta/page_meta/events/config/secrets 的异常能映射为契约错误。
3. 验证同一 job_id 共享锁保护：不同仓储写操作会被同一把锁串行化。
4. 验证所有文件系统读写故障不会泄漏原生异常，而是映射为契约错误。
"""

from __future__ import annotations

import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, mock

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
from backend.shared_kernel.contracts import (
    EventType,
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _template_yaml() -> str:
    return (
        "model:\n"
        '  name: "doubao-seed-2-0-lite-260215"\n'
        "  timeout_seconds: 60\n"
        "\n"
        "extract:\n"
        "  dpi: 150\n"
        "  concurrency: 10\n"
        "  max_retries: 3\n"
        "  prompt: |\n"
        "    从课件图片中提取文字内容，以 Markdown 格式输出。\n"
    )


def _job(
    *,
    job_id: uuid.UUID,
    status: JobStatus = JobStatus.EXTRACTING,
    version: int = 1,
) -> JobAggregate:
    now = _now()
    return JobAggregate(
        job_id=job_id,
        source_pdf_name="lecture.pdf",
        total_pages=2,
        status=status,
        succeeded_pages=[],
        failed_pages=[],
        created_at=now,
        updated_at=now,
        version=version,
        last_error=None,
    )


def _page(
    *,
    job_id: uuid.UUID,
    page_num: int,
    status: PageStatus,
    content: str | None,
    error_message: str | None = None,
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


class _FsHarness:
    def __init__(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.tmp_dir)
        self.data_root = self.project_root / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.project_root / "config.yaml").write_text(_template_yaml(), encoding="utf-8")

        self.workspace = WorkspaceManager(data_root=self.data_root)
        self.job_repo = FsJobRepository(workspace=self.workspace)
        self.source_store = FsSourceDocumentStore(workspace=self.workspace)
        self.page_repo = FsPageRepository(workspace=self.workspace)
        self.artifact_repo = FsArtifactRepository(workspace=self.workspace)
        self.event_repo = FsEventLogRepository(workspace=self.workspace)
        self.secret_store = FsSecretStore(data_root=self.data_root)
        self.config_repo = FsConfigRepository(
            data_root=self.data_root,
            project_root=self.project_root,
            secret_store=self.secret_store,
        )


class TestFsConfigRegressionBaseline(TestCase):
    def setUp(self) -> None:
        self.fs = _FsHarness()

    def _assert_error_code(self, expected: ErrorCode, fn) -> None:
        with self.assertRaises(AppError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, expected)

    def test_full_workspace_roundtrip_create_read_overwrite(self) -> None:
        job_id = uuid.uuid4()

        initial_config = self.fs.config_repo.load()
        self.assertEqual(initial_config.model.name, "doubao-seed-2-0-lite-260215")
        self.assertFalse(initial_config.has_api_key)

        self.fs.job_repo.save(_job(job_id=job_id, status=JobStatus.EXTRACTING, version=1))
        self.fs.source_store.save_source(job_id, "lecture-v1.pdf", b"%PDF-v1")
        self.fs.page_repo.save(
            _page(
                job_id=job_id,
                page_num=1,
                status=PageStatus.DONE,
                content="# 第1页 v1",
            )
        )
        self.fs.artifact_repo.save_output(job_id, "# output v1")
        self.fs.event_repo.append(_event(job_id, 1, EventType.PAGE_PROCESSED))
        self.fs.secret_store.set_api_key("secret-v1")

        loaded_job = self.fs.job_repo.get(job_id)
        loaded_source = self.fs.source_store.get_source(job_id)
        loaded_page = self.fs.page_repo.get(job_id, 1)
        loaded_output = self.fs.artifact_repo.get_output_document(job_id)
        loaded_events = self.fs.event_repo.list_by_job(job_id)

        self.assertEqual(loaded_job.version, 1)
        self.assertEqual(loaded_source.filename, "lecture-v1.pdf")
        self.assertEqual(loaded_page.content, "# 第1页 v1")
        self.assertEqual(loaded_output.content, "# output v1")
        self.assertEqual([1], [e.seq for e in loaded_events])
        self.assertEqual(self.fs.secret_store.require_api_key(), "secret-v1")

        self.fs.job_repo.save(_job(job_id=job_id, status=JobStatus.EXTRACTED, version=2))
        self.fs.source_store.save_source(job_id, "lecture-v2.pdf", b"%PDF-v2")
        self.fs.page_repo.save(
            _page(
                job_id=job_id,
                page_num=1,
                status=PageStatus.DONE,
                content="# 第1页 v2",
            )
        )
        self.fs.artifact_repo.save_output(job_id, "# output v2")
        self.fs.event_repo.append(_event(job_id, 2, EventType.EXTRACTION_COMPLETED))
        self.fs.secret_store.set_api_key("secret-v2")

        saved_config = self.fs.config_repo.save(
            RuntimeConfig(
                model=ModelConfig(
                    name="doubao-seed-2-0-pro",
                    timeout_seconds=90,
                ),
                extract=ExtractConfig(
                    dpi=220,
                    concurrency=6,
                    max_retries=5,
                    prompt="请输出结构化 Markdown。",
                ),
                has_api_key=True,
            )
        )
        self.assertTrue(saved_config.has_api_key)

        loaded_job_v2 = self.fs.job_repo.get(job_id)
        loaded_source_v2 = self.fs.source_store.get_source(job_id)
        loaded_page_v2 = self.fs.page_repo.get(job_id, 1)
        loaded_output_v2 = self.fs.artifact_repo.get_output_document(job_id)
        loaded_events_v2 = self.fs.event_repo.list_by_job(job_id)
        loaded_config_v2 = self.fs.config_repo.load()

        self.assertEqual(loaded_job_v2.status, JobStatus.EXTRACTED)
        self.assertEqual(loaded_job_v2.version, 2)
        self.assertEqual(loaded_source_v2.filename, "lecture-v2.pdf")
        self.assertEqual(loaded_page_v2.content, "# 第1页 v2")
        self.assertEqual(loaded_output_v2.content, "# output v2")
        self.assertEqual([1, 2], [e.seq for e in loaded_events_v2])
        self.assertEqual(self.fs.secret_store.require_api_key(), "secret-v2")
        self.assertEqual(loaded_config_v2.model.name, "doubao-seed-2-0-pro")
        self.assertEqual(loaded_config_v2.extract.dpi, 220)

    def test_reset_to_template_restores_project_config(self) -> None:
        template = self.fs.config_repo.load()
        self.fs.secret_store.set_api_key("secret-reset")

        self.fs.config_repo.save(
            RuntimeConfig(
                model=ModelConfig(
                    name="doubao-seed-2-0-pro",
                    timeout_seconds=90,
                ),
                extract=ExtractConfig(
                    dpi=220,
                    concurrency=6,
                    max_retries=5,
                    prompt="请输出结构化 Markdown。",
                ),
                has_api_key=True,
            )
        )

        restored = self.fs.config_repo.reset_to_template()

        self.assertEqual(template.model.name, restored.model.name)
        self.assertEqual(template.model.timeout_seconds, restored.model.timeout_seconds)
        self.assertEqual(template.extract.dpi, restored.extract.dpi)
        self.assertEqual(template.extract.prompt, restored.extract.prompt)
        self.assertTrue(restored.has_api_key)
        self.assertIn(
            'name: "doubao-seed-2-0-lite-260215"',
            (self.fs.data_root / "config.yaml").read_text(encoding="utf-8"),
        )

    def test_corruption_detection_across_repositories(self) -> None:
        state_job = uuid.uuid4()
        self.fs.job_repo.save(_job(job_id=state_job))
        self.fs.workspace.state_path(state_job).write_text("{bad json", encoding="utf-8")
        self._assert_error_code(ErrorCode.STATE_CORRUPTED, lambda: self.fs.job_repo.get(state_job))

        source_job = uuid.uuid4()
        self.fs.source_store.save_source(source_job, "source.pdf", b"pdf")
        self.fs.workspace.source_meta_path(source_job).write_text("{bad json", encoding="utf-8")
        self._assert_error_code(
            ErrorCode.STATE_CORRUPTED, lambda: self.fs.source_store.get_source(source_job)
        )

        page_job = uuid.uuid4()
        self.fs.page_repo.save(
            _page(
                job_id=page_job,
                page_num=1,
                status=PageStatus.DONE,
                content="ok",
            )
        )
        bad_page_meta = self.fs.workspace.pages_dir(page_job) / "page_001.meta.json"
        bad_page_meta.write_text("{bad json", encoding="utf-8")
        self._assert_error_code(ErrorCode.STATE_CORRUPTED, lambda: self.fs.page_repo.get(page_job, 1))

        event_job = uuid.uuid4()
        self.fs.event_repo.append(_event(event_job, 1, EventType.PAGE_PROCESSED))
        self.fs.workspace.events_path(event_job).write_text("not-json-line\n", encoding="utf-8")
        self._assert_error_code(
            ErrorCode.STATE_CORRUPTED, lambda: self.fs.event_repo.list_by_job(event_job)
        )

        self.fs.secret_store.set_api_key("normal-secret")
        (self.fs.data_root / "secrets.json").write_text("{bad json", encoding="utf-8")
        self._assert_error_code(ErrorCode.STATE_CORRUPTED, self.fs.secret_store.get_api_key)

        (self.fs.data_root / "config.yaml").write_text(
            (
                "model:\n"
                '  name: "model"\n'
                "  timeout_seconds: 60\n"
                "\n"
                "extract:\n"
                "  dpi: 150\n"
                "  concurrency: 0\n"
                "  max_retries: 3\n"
                '  prompt: "x"\n'
            ),
            encoding="utf-8",
        )
        self._assert_error_code(ErrorCode.CONFIG_INVALID, self.fs.config_repo.load)

    def test_shared_lock_blocks_cross_repository_writes(self) -> None:
        job_id = uuid.uuid4()
        lock = self.fs.workspace.get_lock(job_id)
        done: list[str] = []
        errors: list[Exception] = []

        def write_state() -> None:
            try:
                self.fs.job_repo.save(_job(job_id=job_id))
                done.append("state")
            except Exception as exc:  # pragma: no cover - 失败时由断言检查
                errors.append(exc)

        def write_output() -> None:
            try:
                self.fs.artifact_repo.save_output(job_id, "locked output")
                done.append("output")
            except Exception as exc:  # pragma: no cover - 失败时由断言检查
                errors.append(exc)

        lock.acquire()
        t1 = threading.Thread(target=write_state)
        t2 = threading.Thread(target=write_output)
        t1.start()
        t2.start()
        time.sleep(0.15)
        self.assertEqual(done, [])

        lock.release()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        self.assertEqual(errors, [])
        self.assertCountEqual(done, ["state", "output"])
        self.assertTrue(self.fs.workspace.state_path(job_id).exists())
        self.assertEqual(self.fs.artifact_repo.get_output_document(job_id).content, "locked output")

    def test_write_failures_must_map_to_persistence_error(self) -> None:
        job_id = uuid.uuid4()
        runtime_config = self.fs.config_repo.load()

        with self.subTest("job_repository.save"):
            with mock.patch(
                "backend.infra.fs.job_repository.os.replace",
                side_effect=OSError("disk full"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR, lambda: self.fs.job_repo.save(_job(job_id=job_id))
                )

        with self.subTest("source_store.save_source"):
            with mock.patch(
                "backend.infra.fs.source_store.os.replace",
                side_effect=OSError("disk full"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.source_store.save_source(job_id, "a.pdf", b"123"),
                )

        with self.subTest("page_repository.save"):
            with mock.patch(
                "backend.infra.fs.page_repository._atomic_write_text",
                side_effect=OSError("disk full"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.page_repo.save(
                        _page(
                            job_id=job_id,
                            page_num=1,
                            status=PageStatus.DONE,
                            content="abc",
                        )
                    ),
                )

        with self.subTest("artifact_repository.save_output"):
            with mock.patch(
                "backend.infra.fs.artifact_repository._atomic_write_text",
                side_effect=OSError("disk full"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.artifact_repo.save_output(job_id, "out"),
                )

        with self.subTest("event_repository.append"):
            with mock.patch(
                "backend.infra.fs.event_log_repository.open",
                side_effect=OSError("disk full"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.event_repo.append(_event(job_id, 1, EventType.PAGE_PROCESSED)),
                )

        with self.subTest("config_repository.save"):
            with mock.patch(
                "backend.infra.fs.config_repository._atomic_write_text",
                side_effect=OSError("disk full"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.config_repo.save(runtime_config),
                )

        with self.subTest("secret_store.set_api_key"):
            with mock.patch(
                "backend.infra.fs.secret_store._atomic_write_text",
                side_effect=OSError("disk full"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.secret_store.set_api_key("secret"),
                )

    def test_read_failures_must_map_to_persistence_error(self) -> None:
        job_id = uuid.uuid4()
        self.fs.job_repo.save(_job(job_id=job_id))
        self.fs.source_store.save_source(job_id, "src.pdf", b"pdf")
        self.fs.page_repo.save(
            _page(
                job_id=job_id,
                page_num=1,
                status=PageStatus.DONE,
                content="content",
            )
        )
        self.fs.artifact_repo.save_output(job_id, "output")
        self.fs.event_repo.append(_event(job_id, 1, EventType.PAGE_PROCESSED))
        self.fs.config_repo.load()
        self.fs.secret_store.set_api_key("secret")

        with self.subTest("job_repository.get"):
            with mock.patch(
                "backend.infra.fs.job_repository.Path.read_text",
                side_effect=OSError("io failed"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR, lambda: self.fs.job_repo.get(job_id)
                )

        with self.subTest("source_store.get_source"):
            with mock.patch(
                "backend.infra.fs.source_store.Path.read_text",
                side_effect=OSError("io failed"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR, lambda: self.fs.source_store.get_source(job_id)
                )

        with self.subTest("source_store.open_read"):
            with mock.patch("backend.infra.fs.source_store.open", side_effect=OSError("io failed")):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR, lambda: self.fs.source_store.open_read(job_id)
                )

        with self.subTest("page_repository.get"):
            with mock.patch(
                "backend.infra.fs.page_repository.Path.read_text",
                side_effect=OSError("io failed"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR, lambda: self.fs.page_repo.get(job_id, 1)
                )

        with self.subTest("artifact_repository.get_output_document"):
            with mock.patch(
                "backend.infra.fs.artifact_repository.Path.read_text",
                side_effect=OSError("io failed"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.artifact_repo.get_output_document(job_id),
                )

        with self.subTest("event_repository.list_by_job"):
            with mock.patch(
                "backend.infra.fs.event_log_repository.Path.read_text",
                side_effect=OSError("io failed"),
            ):
                self._assert_error_code(
                    ErrorCode.PERSISTENCE_ERROR,
                    lambda: self.fs.event_repo.list_by_job(job_id),
                )

        with self.subTest("config_repository.load"):
            with mock.patch(
                "backend.infra.fs.config_repository.Path.read_text",
                side_effect=OSError("io failed"),
            ):
                self._assert_error_code(ErrorCode.PERSISTENCE_ERROR, self.fs.config_repo.load)

        with self.subTest("secret_store.get_api_key"):
            with mock.patch(
                "backend.infra.fs.secret_store.Path.read_text",
                side_effect=OSError("io failed"),
            ):
                self._assert_error_code(ErrorCode.PERSISTENCE_ERROR, self.fs.secret_store.get_api_key)
