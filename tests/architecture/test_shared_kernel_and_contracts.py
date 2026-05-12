from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
from pathlib import Path
import unittest
from uuid import UUID

from backend.build.application import commands as build_commands
from backend.build.application import dto as build_dto
from backend.build.application import queries as build_queries
from backend.build import ports as build_ports
from backend.config.application import commands as config_commands
from backend.config.application import dto as config_dto
from backend.config.application import queries as config_queries
from backend.config import ports as config_ports
from backend.extraction.application import commands as extraction_commands
from backend.extraction.application import dto as extraction_dto
from backend.extraction.application import queries as extraction_queries
from backend.extraction import ports as extraction_ports
from backend.flashcard.application import commands as flashcard_commands
from backend.flashcard.application import dto as flashcard_dto
from backend.flashcard.application import queries as flashcard_queries
from backend.flashcard import ports as flashcard_ports
from backend.job.application import commands as job_commands
from backend.job.application import dto as job_dto
from backend.job.application import queries as job_queries
from backend.job.domain.models import (
    ArtifactRef,
    ArtifactType,
    BuildMergeMode,
    EventType,
    ExtractConfig,
    JobAggregate,
    JobEvent,
    JobStatus,
    ModelConfig,
    OutputDocument,
    PageDocument,
    PageStatus,
    RuntimeConfig,
    SourceDocumentRef,
)
from backend.job import ports as job_ports
from backend.shared_kernel.errors import ERROR_HTTP_STATUS, AppError, ErrorCode
from backend.shared_kernel.time import Clock, IdGenerator, SystemClock, Uuid4Generator
from backend.shared_kernel.types import Result
from backend.stream.application import commands as stream_commands
from backend.stream.application import dto as stream_dto
from backend.stream.application import queries as stream_queries
from backend.stream import ports as stream_ports


ROOT = Path(__file__).resolve().parents[2]

DISALLOWED_CONTRACT_IMPORT_ROOTS = {
    "fastapi",
    "starlette",
    "pydantic",
    "openai",
    "fitz",
    "pymupdf",
    "pathlib",
    "os",
    "socket",
    "subprocess",
    "tempfile",
    "shutil",
}


def _field_names(cls: type) -> list[str]:
    if not is_dataclass(cls):
        raise AssertionError(f"{cls.__name__} 不是 dataclass")
    return [field.name for field in fields(cls)]


class SharedKernelContractTests(unittest.TestCase):
    def test_error_codes_snapshot(self) -> None:
        expected = [
            "JOB_NOT_FOUND",
            "PAGE_NOT_FOUND",
            "JOB_STATUS_CONFLICT",
            "PAGE_EDIT_FORBIDDEN",
            "PAGE_RETRY_FORBIDDEN",
            "OUTPUT_EDIT_FORBIDDEN",
            "OUTPUT_NOT_READY",
            "BUILD_OUTPUT_INVALID",
            "CONFIG_INVALID",
            "CONFIG_MISSING_API_KEY",
            "PDF_OPEN_FAILED",
            "LLM_AUTH_FAILED",
            "LLM_RATE_LIMITED",
            "LLM_TIMEOUT",
            "PERSISTENCE_ERROR",
            "STATE_CORRUPTED",
            "UNEXPECTED_ERROR",
        ]
        self.assertEqual(expected, [item.value for item in ErrorCode])

    def test_error_http_status_snapshot(self) -> None:
        expected = {
            ErrorCode.JOB_NOT_FOUND: 404,
            ErrorCode.PAGE_NOT_FOUND: 404,
            ErrorCode.JOB_STATUS_CONFLICT: 409,
            ErrorCode.PAGE_EDIT_FORBIDDEN: 409,
            ErrorCode.PAGE_RETRY_FORBIDDEN: 409,
            ErrorCode.OUTPUT_EDIT_FORBIDDEN: 409,
            ErrorCode.OUTPUT_NOT_READY: 409,
            ErrorCode.BUILD_OUTPUT_INVALID: 500,
            ErrorCode.CONFIG_INVALID: 400,
            ErrorCode.CONFIG_MISSING_API_KEY: 400,
            ErrorCode.PDF_OPEN_FAILED: 400,
            ErrorCode.LLM_AUTH_FAILED: 401,
            ErrorCode.LLM_RATE_LIMITED: 429,
            ErrorCode.LLM_TIMEOUT: 504,
            ErrorCode.PERSISTENCE_ERROR: 500,
            ErrorCode.STATE_CORRUPTED: 500,
            ErrorCode.UNEXPECTED_ERROR: 500,
        }
        self.assertEqual(expected, ERROR_HTTP_STATUS)

    def test_app_error_default_behavior(self) -> None:
        error = AppError(ErrorCode.JOB_NOT_FOUND)
        self.assertEqual(ErrorCode.JOB_NOT_FOUND, error.code)
        self.assertEqual("job not found", error.message)
        self.assertEqual(404, error.http_status)
        self.assertEqual(
            {"code": "JOB_NOT_FOUND", "message": "job not found", "details": None},
            error.to_dict(),
        )

    def test_result_success_and_failure(self) -> None:
        success = Result.success("ok")
        failure = Result.failure(AppError(ErrorCode.UNEXPECTED_ERROR, "boom"))

        self.assertTrue(success.is_ok)
        self.assertFalse(success.is_err)
        self.assertEqual("ok", success.unwrap())

        self.assertTrue(failure.is_err)
        self.assertFalse(failure.is_ok)
        with self.assertRaises(AppError):
            failure.unwrap()

    def test_result_rejects_invalid_state(self) -> None:
        with self.assertRaises(ValueError):
            Result(ok=True, value="x", error=AppError(ErrorCode.UNEXPECTED_ERROR))

        with self.assertRaises(ValueError):
            Result(ok=False, value="x", error=None)


class SharedClockAndIdContractTests(unittest.TestCase):
    def test_clock_and_id_generator_can_be_faked(self) -> None:
        class FakeClock:
            def now(self):  # noqa: ANN202 - test-only fake
                return "fake-now"

        class FakeIdGenerator:
            def new(self):  # noqa: ANN202 - test-only fake
                return "fake-id"

        fake_clock = FakeClock()
        fake_id_generator = FakeIdGenerator()

        self.assertIsInstance(fake_clock, Clock)
        self.assertIsInstance(fake_id_generator, IdGenerator)
        self.assertEqual("fake-now", fake_clock.now())
        self.assertEqual("fake-id", fake_id_generator.new())

    def test_system_clock_and_uuid_generator_defaults(self) -> None:
        now = SystemClock().now()
        new_id = Uuid4Generator().new()
        self.assertIsNotNone(now.tzinfo)
        self.assertIsInstance(new_id, UUID)


class DomainSnapshotTests(unittest.TestCase):
    def test_domain_enums_snapshot(self) -> None:
        self.assertEqual(
            ["idle", "extracting", "extracted", "building", "ready", "failed"],
            [item.value for item in JobStatus],
        )
        self.assertEqual(
            ["pending", "extracting", "done", "failed"],
            [item.value for item in PageStatus],
        )
        self.assertEqual(
            ["direct", "separator", "separator_with_page_number"],
            [item.value for item in BuildMergeMode],
        )
        self.assertEqual(
            [
                "output_md",
                "knowledge_points_md",
                "flashcards_md",
                "step1_chat_json",
                "step2_chat_json",
            ],
            [item.value for item in ArtifactType],
        )
        self.assertEqual(
            [
                "page_processed",
                "extraction_completed",
                "build_completed",
                "status_changed",
                "job_failed",
            ],
            [item.value for item in EventType],
        )

    def test_domain_dataclass_field_snapshot(self) -> None:
        expected = {
            ModelConfig: ["name", "timeout_seconds"],
            ExtractConfig: ["dpi", "concurrency", "max_retries", "prompt"],
            RuntimeConfig: ["model", "extract", "has_api_key"],
            PageDocument: [
                "job_id",
                "page_num",
                "status",
                "content",
                "error_message",
                "updated_at",
            ],
            JobAggregate: [
                "job_id",
                "source_pdf_name",
                "total_pages",
                "status",
                "succeeded_pages",
                "failed_pages",
                "created_at",
                "updated_at",
                "version",
                "last_error",
            ],
            ArtifactRef: [
                "job_id",
                "artifact_type",
                "relative_path",
                "content_type",
                "filename",
            ],
            SourceDocumentRef: [
                "job_id",
                "relative_path",
                "content_type",
                "filename",
                "size_bytes",
            ],
            OutputDocument: ["job_id", "content", "updated_at"],
            JobEvent: ["job_id", "seq", "event_type", "payload", "created_at"],
        }

        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")


class ContractSnapshotTests(unittest.TestCase):
    def test_job_contract_snapshot(self) -> None:
        expected = {
            job_commands.CreateJobCommand: ["pdf_filename", "pdf_bytes"],
            job_commands.SavePageCommand: ["job_id", "page_num", "content"],
            job_commands.RetryPageCommand: ["job_id", "page_num"],
            job_commands.BuildJobCommand: ["job_id", "merge_mode"],
            job_commands.DiscardOutputCommand: ["job_id"],
            job_commands.SaveOutputCommand: ["job_id", "content"],
            job_queries.GetJobQuery: ["job_id"],
            job_queries.GetSourceDocumentQuery: ["job_id"],
            job_queries.ListPagesQuery: ["job_id"],
            job_queries.GetPageQuery: ["job_id", "page_num"],
            job_queries.GetOutputDocumentQuery: ["job_id"],
            job_queries.GetOutputArtifactQuery: ["job_id"],
            job_dto.JobView: [
                "job_id",
                "status",
                "total_pages",
                "succeeded_pages",
                "failed_pages",
                "processed_count",
            ],
            job_dto.PageSummary: ["page_num", "status"],
            job_dto.PageView: [
                "job_id",
                "page_num",
                "status",
                "content",
                "error_message",
                "updated_at",
            ],
            job_dto.OutputDocumentView: ["job_id", "content", "updated_at"],
            job_dto.CreateJobResult: ["job_id", "total_pages", "status"],
            job_dto.AcceptedResult: ["job_id", "page_num"],
            job_dto.BuildResult: ["status", "output_url", "download_url"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_build_contract_snapshot(self) -> None:
        expected = {
            build_commands.BuildOutputCommand: ["job_id"],
            build_commands.SaveOutputDocumentCommand: ["job_id", "content"],
            build_queries.GetOutputDocumentQuery: ["job_id"],
            build_queries.GetOutputArtifactQuery: ["job_id"],
            build_dto.BuildOutputResult: ["job_id", "status", "artifact"],
            build_dto.OutputDocumentView: ["job_id", "content", "updated_at"],
            build_dto.OutputArtifactView: [
                "job_id",
                "artifact_type",
                "relative_path",
                "content_type",
                "filename",
            ],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_extraction_contract_snapshot(self) -> None:
        expected = {
            extraction_commands.StartJobExtractionCommand: ["job_id"],
            extraction_commands.RetryPageExtractionCommand: ["job_id", "page_num"],
            extraction_queries.GetExtractionProgressQuery: ["job_id"],
            extraction_dto.ExtractionProgressView: [
                "job_id",
                "status",
                "total_pages",
                "processed_count",
            ],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_config_contract_snapshot(self) -> None:
        expected = {
            config_commands.ModelConfigInput: [
                "name",
                "timeout_seconds",
            ],
            config_commands.ExtractConfigInput: [
                "dpi",
                "concurrency",
                "max_retries",
                "prompt",
            ],
            config_commands.UpdateConfigCommand: ["model", "extract", "api_key"],
            config_queries.GetPublicConfigQuery: [],
            config_dto.ModelConfigView: [
                "name",
                "timeout_seconds",
            ],
            config_dto.ExtractConfigView: [
                "dpi",
                "concurrency",
                "max_retries",
                "prompt",
            ],
            config_dto.PublicConfigView: ["model", "extract", "has_api_key"],
            config_dto.TestConnectionResultView: ["ok", "message", "reply_preview"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_stream_contract_snapshot(self) -> None:
        expected = {
            stream_commands.PublishEventCommand: ["event"],
            stream_queries.SubscribeJobEventsQuery: ["job_id", "replay"],
            stream_dto.StreamEventView: ["job_id", "seq", "event_type", "payload", "created_at"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

    def test_flashcard_v2_placeholder_contract_snapshot(self) -> None:
        expected = {
            flashcard_commands.StartStep1Command: ["job_id"],
            flashcard_commands.ChatStep1Command: ["job_id", "message"],
            flashcard_commands.StartStep2Command: ["job_id", "card_limit"],
            flashcard_commands.ChatStep2Command: ["job_id", "message"],
            flashcard_queries.GetStep1ArtifactQuery: ["job_id"],
            flashcard_queries.GetStep2ArtifactQuery: ["job_id"],
            flashcard_dto.FlashcardAcceptedResult: ["job_id", "phase"],
            flashcard_dto.FlashcardArtifactView: [
                "job_id",
                "artifact_type",
                "relative_path",
                "content_type",
                "filename",
            ],
            flashcard_dto.FlashcardChatTurn: ["role", "content", "created_at"],
        }
        for cls, names in expected.items():
            self.assertEqual(names, _field_names(cls), f"{cls.__name__} 字段漂移")

        self.assertEqual(
            ["step1", "step2"],
            [item.value for item in flashcard_dto.FlashcardPhase],
        )

    def test_port_protocols_snapshot(self) -> None:
        expected = {
            job_ports: [
                "JobRepository",
                "PageRepository",
                "SourceDocumentStore",
                "ArtifactRepository",
                "EventPublisher",
                "ExtractionApplication",
                "BuildApplication",
                "PdfDocumentGateway",
            ],
            extraction_ports: [
                "VisionExtractionGateway",
                "TaskScheduler",
            ],
            build_ports: [
                "MarkdownBuildPipeline",
            ],
            config_ports: [
                "ConfigRepository",
                "SecretStore",
            ],
            stream_ports: [
                "EventLogRepository",
                "LiveSubscriberHub",
                "LiveEventSubscriber",
            ],
            flashcard_ports: [
                "TextGenerationGateway",
            ],
        }

        for module, names in expected.items():
            for name in names:
                self.assertTrue(
                    hasattr(module, name),
                    f"{module.__name__} 缺少协议接口 {name}",
                )


class ContractBoundaryTests(unittest.TestCase):
    def test_contracts_do_not_depend_on_framework_or_external_io_sdk(self) -> None:
        contract_files = [
            ROOT / "backend" / "shared_kernel" / "errors.py",
            ROOT / "backend" / "shared_kernel" / "types.py",
            ROOT / "backend" / "shared_kernel" / "time.py",
            ROOT / "backend" / "shared_kernel" / "contracts.py",
            ROOT / "backend" / "job" / "domain" / "models.py",
            ROOT / "backend" / "job" / "ports.py",
            ROOT / "backend" / "job" / "application" / "commands.py",
            ROOT / "backend" / "job" / "application" / "queries.py",
            ROOT / "backend" / "job" / "application" / "dto.py",
            ROOT / "backend" / "extraction" / "ports.py",
            ROOT / "backend" / "extraction" / "application" / "commands.py",
            ROOT / "backend" / "extraction" / "application" / "queries.py",
            ROOT / "backend" / "extraction" / "application" / "dto.py",
            ROOT / "backend" / "build" / "ports.py",
            ROOT / "backend" / "build" / "application" / "commands.py",
            ROOT / "backend" / "build" / "application" / "queries.py",
            ROOT / "backend" / "build" / "application" / "dto.py",
            ROOT / "backend" / "config" / "ports.py",
            ROOT / "backend" / "config" / "application" / "commands.py",
            ROOT / "backend" / "config" / "application" / "queries.py",
            ROOT / "backend" / "config" / "application" / "dto.py",
            ROOT / "backend" / "stream" / "ports.py",
            ROOT / "backend" / "stream" / "application" / "commands.py",
            ROOT / "backend" / "stream" / "application" / "queries.py",
            ROOT / "backend" / "stream" / "application" / "dto.py",
            ROOT / "backend" / "flashcard" / "ports.py",
            ROOT / "backend" / "flashcard" / "application" / "commands.py",
            ROOT / "backend" / "flashcard" / "application" / "queries.py",
            ROOT / "backend" / "flashcard" / "application" / "dto.py",
        ]

        violations: list[str] = []
        for path in contract_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.module is None:
                        continue
                    imported = [node.module]
                else:
                    continue

                for module_name in imported:
                    root_name = module_name.split(".")[0]
                    if root_name in DISALLOWED_CONTRACT_IMPORT_ROOTS:
                        violations.append(
                            f"{path.relative_to(ROOT)} imports forbidden dependency {module_name}"
                        )

        self.assertEqual([], violations, "契约层不应耦合框架/外部 IO/SDK:\n" + "\n".join(violations))
