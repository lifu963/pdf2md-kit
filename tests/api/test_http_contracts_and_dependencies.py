"""
Step 19: 固定 HTTP 契约并完成依赖装配

验收目标（严格对齐实施步骤）：
1. 固定 HTTP 请求体、响应体、错误响应和 SSE payload 映射。
2. 契约快照测试逐项锁定字段名与缺省行为，避免后续路由实现时漂移。
3. 验证错误码到 HTTP 状态码的映射与架构文档一致。
4. 验证 `backend/api/dependencies.py` 统一装配应用服务与适配器，且测试环境可注入 fake。
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from unittest import TestCase, mock
from uuid import uuid4

from backend.build.application import BuildApplication, SimpleMarkdownBuildPipeline
from backend.config.application import (
    ConfigApplication,
    ExtractConfigView,
    ModelConfigView,
    PublicConfigView,
    TestConnectionResultView,
)
from backend.extraction.application import ExtractionApplication, SinglePagePreviewApplication
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
from backend.infra.llm import OpenAIVisionExtractionGateway
from backend.infra.pdf import PymupdfPdfDocumentGateway
from backend.infra.stream import EventLogBackedEventPublisher, InMemoryLiveSubscriberHub
from backend.infra.task import ThreadedTaskScheduler
from backend.job.application import BuildResult, JobApplication, PageView
from backend.shared_kernel.contracts import BuildMergeMode, EventType, JobEvent, JobStatus, PageStatus
from backend.shared_kernel.errors import ERROR_HTTP_STATUS, AppError
from backend.stream.application import StreamApplication

from backend.api import create_api_app, dependencies as api_dependencies
from backend.api.schemas import common as schema_common
from backend.api.schemas import config as config_schemas
from backend.api.schemas import errors as error_schemas
from backend.api.schemas import extraction as extraction_schemas
from backend.api.schemas import job as job_schemas
from backend.api.schemas import stream as stream_schemas
from backend.extraction.application import SinglePagePreviewResult


def _field_names(cls: type) -> list[str]:
    if not is_dataclass(cls):
        raise AssertionError(f"{cls.__name__} 不是 dataclass")
    return [field.name for field in fields(cls)]


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


class HttpContractSnapshotTests(TestCase):
    def test_job_http_contract_snapshot(self) -> None:
        expected = {
            job_schemas.JobResponse: [
                "job_id",
                "status",
                "total_pages",
                "succeeded_pages",
                "failed_pages",
                "processed_count",
            ],
            job_schemas.JobHistoryItemResponse: [
                "job_id",
                "pdf_name",
                "status",
                "total_pages",
                "processed_count",
                "created_at",
                "updated_at",
            ],
            job_schemas.CreateJobResponse: ["job_id", "total_pages", "status"],
            job_schemas.PageSummaryResponse: ["page_num", "status"],
            job_schemas.PageResponse: ["page_num", "status", "content", "error"],
            job_schemas.RetryPageAcceptedResponse: ["job_id", "page_num"],
            job_schemas.BuildResponse: ["status", "output_url", "download_url"],
            job_schemas.BuildOutputRequest: ["merge_mode"],
            job_schemas.OutputDocumentResponse: ["content", "updated_at"],
            job_schemas.SavePageRequest: ["content"],
            job_schemas.SaveOutputRequest: ["content"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_extraction_http_contract_snapshot(self) -> None:
        expected = {
            extraction_schemas.SinglePagePreviewResponse: ["page_num", "content"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_config_http_contract_snapshot(self) -> None:
        expected = {
            config_schemas.ConfigModelPayload: ["name", "timeout"],
            config_schemas.ExtractConfigPayload: ["dpi", "concurrency", "max_retries", "prompt"],
            config_schemas.PublicConfigResponse: ["model", "extract", "has_api_key"],
            config_schemas.TestConnectionResponse: ["ok", "message", "reply_preview"],
            config_schemas.UpdateConfigRequest: ["model", "extract", "api_key"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_error_and_stream_contract_snapshot(self) -> None:
        expected = {
            error_schemas.ApiErrorDetail: ["code", "message", "details"],
            error_schemas.ApiErrorResponse: ["detail"],
            stream_schemas.PageEventPayload: [
                "type",
                "page_num",
                "status",
                "processed_count",
                "total_pages",
                "error",
            ],
            stream_schemas.CompleteEventPayload: [
                "type",
                "processed_count",
                "total_pages",
                "succeeded_pages",
                "failed_pages",
            ],
            stream_schemas.FailedEventPayload: ["type", "detail"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")


class HttpMappingTests(TestCase):
    def test_config_http_mapping_uses_timeout_and_omits_missing_api_key(self) -> None:
        view = PublicConfigView(
            model=ModelConfigView(
                name="vision-http",
                timeout_seconds=60,
            ),
            extract=ExtractConfigView(
                dpi=180,
                concurrency=4,
                max_retries=2,
                prompt="请严格提取 Markdown",
            ),
            has_api_key=True,
        )
        response = config_schemas.to_public_config_response(view)
        request = config_schemas.UpdateConfigRequest(
            model=config_schemas.ConfigModelPayload(
                name="vision-http",
                timeout=60,
            ),
            extract=config_schemas.ExtractConfigPayload(
                dpi=180,
                concurrency=4,
                max_retries=2,
                prompt="请严格提取 Markdown",
            ),
        )
        command = config_schemas.to_update_config_command(request)

        self.assertEqual(
            {
                "model": {
                    "name": "vision-http",
                    "timeout": 60,
                },
                "extract": {
                    "dpi": 180,
                    "concurrency": 4,
                    "max_retries": 2,
                    "prompt": "请严格提取 Markdown",
                },
                "has_api_key": True,
            },
            schema_common.dump_http_model(response),
        )
        self.assertEqual(
            {
                "model": {
                    "name": "vision-http",
                    "timeout": 60,
                },
                "extract": {
                    "dpi": 180,
                    "concurrency": 4,
                    "max_retries": 2,
                    "prompt": "请严格提取 Markdown",
                },
            },
            schema_common.dump_http_model(request, exclude_none=True),
        )
        self.assertIsNone(command.api_key)
        self.assertEqual(60, command.model.timeout_seconds)

    def test_test_connection_http_mapping_omits_missing_reply_preview(self) -> None:
        view = TestConnectionResultView(
            ok=True,
            message="LLM API 响应正常",
            reply_preview=None,
        )
        response = config_schemas.to_test_connection_response(view)
        self.assertEqual(
            {
                "ok": True,
                "message": "LLM API 响应正常",
            },
            schema_common.dump_http_model(response, exclude_none=True),
        )

    def test_page_response_omits_optional_fields_and_hides_internal_names(self) -> None:
        now = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)
        page_id = uuid4()
        pending = PageView(
            job_id=page_id,
            page_num=1,
            status=PageStatus.PENDING,
            content=None,
            error_message=None,
            updated_at=None,
        )
        done = PageView(
            job_id=page_id,
            page_num=2,
            status=PageStatus.DONE,
            content="## 标题\n内容",
            error_message=None,
            updated_at=now,
        )
        failed = PageView(
            job_id=page_id,
            page_num=3,
            status=PageStatus.FAILED,
            content=None,
            error_message="LLM timeout",
            updated_at=now,
        )

        self.assertEqual(
            {"page_num": 1, "status": "pending"},
            schema_common.dump_http_model(job_schemas.to_page_response(pending), exclude_none=True),
        )
        self.assertEqual(
            {"page_num": 2, "status": "done", "content": "## 标题\n内容"},
            schema_common.dump_http_model(job_schemas.to_page_response(done), exclude_none=True),
        )
        self.assertEqual(
            {"page_num": 3, "status": "failed", "error": "LLM timeout"},
            schema_common.dump_http_model(job_schemas.to_page_response(failed), exclude_none=True),
        )

    def test_build_response_and_download_filename_contract_are_stable(self) -> None:
        job_id = uuid4()
        result = BuildResult(
            status=JobStatus.READY,
            output_url="/api/jobs/demo/output",
            download_url="/api/jobs/demo/output/download",
        )
        request = job_schemas.BuildOutputRequest(
            merge_mode=BuildMergeMode.SEPARATOR_WITH_PAGE_NUMBER,
        )
        default_command = job_schemas.to_build_job_command(job_id=job_id)
        selected_command = job_schemas.to_build_job_command(job_id=job_id, request=request)
        self.assertEqual(
            {
                "status": "ready",
                "output_url": "/api/jobs/demo/output",
                "download_url": "/api/jobs/demo/output/download",
            },
            schema_common.dump_http_model(job_schemas.to_build_response(result)),
        )
        self.assertEqual(
            {"merge_mode": "separator_with_page_number"},
            schema_common.dump_http_model(request),
        )
        self.assertEqual(BuildMergeMode.DIRECT, default_command.merge_mode)
        self.assertEqual(BuildMergeMode.SEPARATOR_WITH_PAGE_NUMBER, selected_command.merge_mode)
        self.assertEqual("高压课程-整理.md", job_schemas.output_download_filename_for("高压课程.pdf"))
        self.assertEqual("未命名-整理.md", job_schemas.output_download_filename_for(""))

    def test_single_page_preview_response_mapping_is_stable(self) -> None:
        result = SinglePagePreviewResult(page_num=7, content="## 第 7 页\n正文")
        response = extraction_schemas.to_single_page_preview_response(result)
        self.assertIsInstance(response, extraction_schemas.SinglePagePreviewResponse)
        self.assertEqual(7, response.page_num)
        self.assertEqual("## 第 7 页\n正文", response.content)
        self.assertEqual(
            {"page_num": 7, "content": "## 第 7 页\n正文"},
            schema_common.dump_http_model(response, exclude_none=True),
        )

    def test_error_mapping_wraps_fastapi_detail_and_preserves_http_status(self) -> None:
        for code, expected_status in ERROR_HTTP_STATUS.items():
            status_code, response = error_schemas.map_error_to_http_response(AppError(code))
            self.assertEqual(expected_status, status_code, f"{code.value} 状态码漂移")
            self.assertEqual(
                {
                    "detail": {
                        "code": code.value,
                        "message": code.value.lower().replace("_", " "),
                        "details": None,
                    }
                },
                schema_common.dump_http_model(response),
            )

    def test_sse_payload_mapping_drops_build_completed_and_keeps_optional_error_omitted(self) -> None:
        job_id = uuid4()
        created_at = datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc)
        extracting_event = JobEvent(
            job_id=job_id,
            seq=1,
            event_type=EventType.STATUS_CHANGED,
            payload={
                "type": "page",
                "page_num": 2,
                "status": "extracting",
                "processed_count": 1,
                "total_pages": 5,
            },
            created_at=created_at,
        )
        page_event = JobEvent(
            job_id=job_id,
            seq=2,
            event_type=EventType.PAGE_PROCESSED,
            payload={
                "type": "page",
                "page_num": 2,
                "status": "done",
                "processed_count": 2,
                "total_pages": 5,
            },
            created_at=created_at,
        )
        failed_event = JobEvent(
            job_id=job_id,
            seq=3,
            event_type=EventType.JOB_FAILED,
            payload={"type": "failed", "detail": "invalid api key"},
            created_at=created_at,
        )
        build_event = JobEvent(
            job_id=job_id,
            seq=4,
            event_type=EventType.BUILD_COMPLETED,
            payload={"artifact": "ignored"},
            created_at=created_at,
        )

        self.assertEqual(
            {
                "type": "page",
                "page_num": 2,
                "status": "extracting",
                "processed_count": 1,
                "total_pages": 5,
            },
            schema_common.dump_http_model(stream_schemas.to_sse_payload(extracting_event), exclude_none=True),
        )
        self.assertEqual(
            {
                "type": "page",
                "page_num": 2,
                "status": "done",
                "processed_count": 2,
                "total_pages": 5,
            },
            schema_common.dump_http_model(stream_schemas.to_sse_payload(page_event), exclude_none=True),
        )
        self.assertEqual(
            {"type": "failed", "detail": "invalid api key"},
            schema_common.dump_http_model(stream_schemas.to_sse_payload(failed_event)),
        )
        self.assertIsNone(stream_schemas.to_sse_payload(build_event))


class DependencyAssemblyTests(TestCase):
    def test_api_container_accepts_fake_dependencies_and_wires_services_through_interfaces(self) -> None:
        fake_clock = mock.Mock(spec=["now"])
        fake_id_generator = mock.Mock(spec=["new"])
        fake_job_repository = mock.Mock(spec=["exists", "get", "list_all", "save", "delete"])
        fake_page_repository = mock.Mock(spec=["list_summaries_by_job", "list_by_job", "get", "save"])
        fake_source_store = mock.Mock(spec=["save_source", "get_source", "open_read"])
        fake_artifact_repository = mock.Mock(
            spec=["save_output", "get_output_document", "get_output_artifact", "delete_output"]
        )
        fake_event_log_repository = mock.Mock(spec=["append", "list_by_job"])
        fake_config_repository = mock.Mock(spec=["load", "save"])
        fake_secret_store = mock.Mock(spec=["has_api_key", "get_api_key", "require_api_key", "set_api_key"])
        fake_pdf_gateway = mock.Mock(spec=["count_pages", "render_page"])
        fake_vision_gateway = mock.Mock(spec=["extract_markdown", "test_connection"])
        fake_task_scheduler = mock.Mock(spec=["schedule"])
        fake_live_subscriber_hub = mock.Mock(spec=["attach", "detach", "broadcast"])
        fake_event_publisher = mock.Mock(spec=["publish"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")
            data_root = root / "data"
            data_root.mkdir(parents=True, exist_ok=True)

            container = api_dependencies.ApiContainer(
                project_root=root,
                data_root=data_root,
                clock=fake_clock,
                id_generator=fake_id_generator,
                job_repository=fake_job_repository,
                page_repository=fake_page_repository,
                source_store=fake_source_store,
                artifact_repository=fake_artifact_repository,
                event_log_repository=fake_event_log_repository,
                config_repository=fake_config_repository,
                secret_store=fake_secret_store,
                pdf_gateway=fake_pdf_gateway,
                vision_gateway=fake_vision_gateway,
                task_scheduler=fake_task_scheduler,
                live_subscriber_hub=fake_live_subscriber_hub,
                event_publisher=fake_event_publisher,
            )

            build_app = container.build_application
            extraction_app = container.extraction_application
            job_app = container.job_application
            config_app = container.config_application
            stream_app = container.stream_application
            single_page_preview_app = container.single_page_preview_application

            self.assertIs(fake_job_repository, build_app._job_repository)
            self.assertIs(fake_page_repository, build_app._page_repository)
            self.assertIs(fake_artifact_repository, build_app._artifact_repository)
            self.assertIs(fake_clock, build_app._clock)
            self.assertIsInstance(build_app._pipeline, SimpleMarkdownBuildPipeline)
            self.assertFalse(hasattr(build_app._pipeline, "_text_gateway"))

            self.assertIs(fake_job_repository, extraction_app._job_repository)
            self.assertIs(fake_page_repository, extraction_app._page_repository)
            self.assertIs(fake_source_store, extraction_app._source_store)
            self.assertIs(fake_config_repository, extraction_app._config_repository)
            self.assertIs(fake_secret_store, extraction_app._secret_store)
            self.assertIs(fake_pdf_gateway, extraction_app._pdf_gateway)
            self.assertIs(fake_vision_gateway, extraction_app._vision_gateway)
            self.assertIs(fake_event_publisher, extraction_app._event_publisher)
            self.assertIs(fake_task_scheduler, extraction_app._task_scheduler)
            self.assertIs(fake_clock, extraction_app._clock)

            self.assertIs(fake_job_repository, job_app._job_repository)
            self.assertIs(fake_page_repository, job_app._page_repository)
            self.assertIs(fake_source_store, job_app._source_store)
            self.assertIs(build_app, job_app._build_application)
            self.assertIs(extraction_app, job_app._extraction_application)
            self.assertIs(fake_pdf_gateway, job_app._pdf_gateway)
            self.assertIs(fake_secret_store, job_app._secret_store)
            self.assertIs(fake_clock, job_app._clock)
            self.assertIs(fake_id_generator, job_app._id_generator)

            self.assertIs(fake_config_repository, config_app._config_repository)
            self.assertIs(fake_secret_store, config_app._secret_store)
            self.assertIs(fake_vision_gateway, config_app._vision_gateway)

            self.assertIs(fake_event_log_repository, stream_app._event_log_repository)
            self.assertIs(fake_live_subscriber_hub, stream_app._live_subscriber_hub)
            self.assertIs(fake_job_repository, stream_app._job_repository)

            self.assertIsInstance(single_page_preview_app, SinglePagePreviewApplication)
            self.assertIs(fake_config_repository, single_page_preview_app._config_repository)
            self.assertIs(fake_secret_store, single_page_preview_app._secret_store)
            self.assertIs(fake_pdf_gateway, single_page_preview_app._pdf_gateway)
            self.assertIs(fake_vision_gateway, single_page_preview_app._vision_gateway)
            self.assertFalse(hasattr(single_page_preview_app, "_clock"))
            self.assertFalse(hasattr(single_page_preview_app, "_job_repository"))
            self.assertFalse(hasattr(single_page_preview_app, "_page_repository"))
            self.assertFalse(hasattr(single_page_preview_app, "_source_store"))
            self.assertFalse(hasattr(single_page_preview_app, "_event_publisher"))
            self.assertFalse(hasattr(single_page_preview_app, "_task_scheduler"))
            self.assertIs(single_page_preview_app, container.single_page_preview_application)

    def test_api_container_builds_real_default_stack_from_roots_once(self) -> None:
        fake_clock = mock.Mock(spec=["now"])
        fake_id_generator = mock.Mock(spec=["new"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_root = root / "data"
            data_root.mkdir(parents=True, exist_ok=True)
            (root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")

            container = api_dependencies.ApiContainer(
                project_root=root,
                data_root=data_root,
                clock=fake_clock,
                id_generator=fake_id_generator,
            )

            self.assertIsInstance(container.workspace, WorkspaceManager)
            self.assertIsInstance(container.job_repository, FsJobRepository)
            self.assertIsInstance(container.page_repository, FsPageRepository)
            self.assertIsInstance(container.source_store, FsSourceDocumentStore)
            self.assertIsInstance(container.artifact_repository, FsArtifactRepository)
            self.assertIsInstance(container.event_log_repository, FsEventLogRepository)
            self.assertIsInstance(container.secret_store, FsSecretStore)
            self.assertIsInstance(container.config_repository, FsConfigRepository)
            self.assertIsInstance(container.pdf_gateway, PymupdfPdfDocumentGateway)
            self.assertIsInstance(container.vision_gateway, OpenAIVisionExtractionGateway)
            self.assertIsInstance(container.task_scheduler, ThreadedTaskScheduler)
            self.assertIsInstance(container.live_subscriber_hub, InMemoryLiveSubscriberHub)
            self.assertIsInstance(container.event_publisher, EventLogBackedEventPublisher)
            self.assertIsInstance(container.build_application, BuildApplication)
            self.assertIsInstance(container.build_application._pipeline, SimpleMarkdownBuildPipeline)
            self.assertIsInstance(container.extraction_application, ExtractionApplication)
            self.assertIsInstance(container.job_application, JobApplication)
            self.assertIsInstance(container.config_application, ConfigApplication)
            self.assertIsInstance(container.stream_application, StreamApplication)
            self.assertIsInstance(
                container.single_page_preview_application, SinglePagePreviewApplication
            )

            self.assertIs(container.job_repository, container.build_application._job_repository)
            self.assertIs(container.job_repository, container.extraction_application._job_repository)
            self.assertIs(container.job_repository, container.job_application._job_repository)
            self.assertIs(container.job_repository, container.stream_application._job_repository)
            self.assertIs(container.page_repository, container.build_application._page_repository)
            self.assertIs(container.page_repository, container.extraction_application._page_repository)
            self.assertIs(container.page_repository, container.job_application._page_repository)
            self.assertIs(container.secret_store, container.config_application._secret_store)
            self.assertIs(container.secret_store, container.extraction_application._secret_store)
            self.assertIs(container.secret_store, container.job_application._secret_store)
            self.assertIs(fake_clock, container.build_application._clock)
            self.assertIs(fake_clock, container.extraction_application._clock)
            self.assertIs(fake_clock, container.job_application._clock)
            self.assertIs(fake_id_generator, container.job_application._id_generator)
            self.assertIs(
                container.config_repository,
                container.single_page_preview_application._config_repository,
            )
            self.assertIs(
                container.secret_store,
                container.single_page_preview_application._secret_store,
            )
            self.assertIs(
                container.pdf_gateway,
                container.single_page_preview_application._pdf_gateway,
            )
            self.assertIs(
                container.vision_gateway,
                container.single_page_preview_application._vision_gateway,
            )
            self.assertFalse(
                hasattr(container.single_page_preview_application, "_clock")
            )

    def test_dependency_provider_helpers_return_services_from_same_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_root = root / "data"
            data_root.mkdir(parents=True, exist_ok=True)
            (root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")
            container = api_dependencies.ApiContainer(project_root=root, data_root=data_root)

            self.assertIs(container.build_application, api_dependencies.get_build_application(container))
            self.assertIs(container.extraction_application, api_dependencies.get_extraction_application(container))
            self.assertIs(container.job_application, api_dependencies.get_job_application(container))
            self.assertIs(container.config_application, api_dependencies.get_config_application(container))
            self.assertIs(container.stream_application, api_dependencies.get_stream_application(container))
            self.assertIs(
                container.single_page_preview_application,
                api_dependencies.get_single_page_preview_application(container),
            )
            self.assertIn(
                "get_single_page_preview_application", api_dependencies.__all__
            )


class RouteRegistrationTests(TestCase):
    def test_single_page_preview_route_is_mounted_under_api_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_root = root / "data"
            data_root.mkdir(parents=True, exist_ok=True)
            (root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")
            container = api_dependencies.ApiContainer(project_root=root, data_root=data_root)

            app = create_api_app(container=container)

            routes = {
                (route.path, tuple(sorted(route.methods or ())))
                for route in app.routes
                if hasattr(route, "methods")
            }
            self.assertIn(
                ("/api/extraction/single-page-preview", ("POST",)),
                routes,
                f"新路由未按预期挂载; 现有路由: {sorted(routes)}",
            )

            openapi = app.openapi()
            self.assertIn("/api/extraction/single-page-preview", openapi["paths"])
            self.assertIn("post", openapi["paths"]["/api/extraction/single-page-preview"])
