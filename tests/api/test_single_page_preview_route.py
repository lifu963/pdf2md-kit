"""
Step (Plan §4): 后端测试 — `POST /api/extraction/single-page-preview`

验收目标：
1. happy path：multipart 上传 + page_num → 200 + `{page_num, content}`。
2. 越界页号 → 404，`code=PAGE_NOT_FOUND`。
3. 坏 PDF 字节 → 400，`code=PDF_OPEN_FAILED`。
4. 缺 API Key → 400，`code=CONFIG_MISSING_API_KEY`。
5. LLM 认证失败 → 401，`code=LLM_AUTH_FAILED`。
6. 缺 `file` 字段 → 422（FastAPI 默认 multipart validation）。
7. 零副作用：任意一次测试请求结束后，`data/` 下都没有 `{job_id}/`
   目录，`job_repository.list_all()` 为空，事件日志无任何 job 的事件。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase
from uuid import UUID

import fitz
from starlette.testclient import TestClient

from backend.api import create_api_app
from backend.api.dependencies import ApiContainer
from backend.shared_kernel.contracts import ModelConfig
from backend.shared_kernel.errors import AppError, ErrorCode


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


def _build_pdf_bytes(*, total_pages: int) -> bytes:
    doc = fitz.open()
    try:
        for _ in range(total_pages):
            doc.new_page()
        return doc.tobytes()
    finally:
        doc.close()


class _RecordingVisionGateway:
    """
    Captures arguments passed to extract_markdown and returns canned content
    or raises a queued AppError. Asserts open_session/test_connection are
    never called from the single-page-preview path.
    """

    def __init__(self, *, content: str = "## hello\n正文") -> None:
        self.calls: list[dict[str, Any]] = []
        self.next_outcome: str | Exception = content

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
        self.calls.append(
            {
                "image_bytes_len": len(image_bytes),
                "prompt": prompt,
                "model": model,
                "api_key": api_key,
                "max_retries": max_retries,
                "page_num": page_num,
            }
        )
        if isinstance(self.next_outcome, Exception):
            raise self.next_outcome
        return self.next_outcome

    def open_session(self, *, model: ModelConfig, api_key: str) -> Any:
        raise AssertionError(
            "single-page-preview route should not open a reusable session"
        )

    def test_connection(self, *, model: ModelConfig, api_key: str) -> str:
        raise AssertionError(
            "single-page-preview route should not call test_connection"
        )


class _RecordingTaskScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    def schedule(self, *, job_id: UUID, task_name: str, task_factory) -> bool:  # type: ignore[no-untyped-def]
        del task_factory
        self.calls.append((job_id, task_name))
        return True


class _SinglePagePreviewRouteTestBase(TestCase):
    """Shared container/test-client setup, plus zero-side-effect assertions."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)

        project_root = Path(self._tmp_dir.name)
        data_root = project_root / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        (project_root / "config.yaml").write_text(
            _template_config_text(), encoding="utf-8"
        )

        self.project_root = project_root
        self.data_root = data_root
        self.task_scheduler = _RecordingTaskScheduler()
        self.vision_gateway = _RecordingVisionGateway()
        self.container = ApiContainer(
            project_root=project_root,
            data_root=data_root,
            task_scheduler=self.task_scheduler,
            vision_gateway=self.vision_gateway,
        )
        self.client = TestClient(create_api_app(container=self.container))
        self.addCleanup(self.client.close)
        # Ensure side-effect snapshots are stable across tests.
        self.addCleanup(self._assert_no_job_side_effects)

    def _save_api_key(self, api_key: str = "test-api-key") -> None:
        self.container.secret_store.set_api_key(api_key)

    def _assert_no_job_side_effects(self) -> None:
        # 1. job_repository.list_all() 应该完全为空（永远没有 Job 被创建）。
        self.assertEqual(
            [],
            self.container.job_repository.list_all(),
            "single-page-preview 不应创建任何 Job",
        )
        # 2. data/ 下不应出现任何 UUID 形态的 {job_id}/ 目录。
        for entry in self.data_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                UUID(entry.name)
            except ValueError:
                continue
            self.fail(
                f"single-page-preview 不应在 data/ 下创建 job 目录: {entry.name}"
            )
        # 3. 任务调度器不应被调用一次。
        self.assertEqual(
            [],
            self.task_scheduler.calls,
            "single-page-preview 不应触发任何后台任务",
        )

    def _assert_api_error(
        self,
        response,  # type: ignore[no-untyped-def]
        *,
        status_code: int,
        code: str,
    ) -> None:
        self.assertEqual(status_code, response.status_code, response.text)
        body = response.json()
        self.assertIn("detail", body)
        self.assertEqual(code, body["detail"]["code"])
        self.assertIn("message", body["detail"])


class SinglePagePreviewHappyPathTests(_SinglePagePreviewRouteTestBase):
    def test_returns_page_num_and_content_and_forwards_runtime_config(self) -> None:
        self._save_api_key()
        self.vision_gateway.next_outcome = "## 第 2 页\n核心要点"
        pdf_bytes = _build_pdf_bytes(total_pages=3)

        response = self.client.post(
            "/api/extraction/single-page-preview",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"page_num": "2"},
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"page_num": 2, "content": "## 第 2 页\n核心要点"}, response.json())

        self.assertEqual(1, len(self.vision_gateway.calls))
        call = self.vision_gateway.calls[0]
        self.assertEqual(2, call["page_num"])
        self.assertEqual("test-api-key", call["api_key"])
        # 运行时配置必须严格透传，不允许任何"独立测试配置"。
        runtime = self.container.config_repository.load()
        self.assertEqual(runtime.extract.prompt, call["prompt"])
        self.assertEqual(runtime.extract.max_retries, call["max_retries"])
        self.assertEqual(runtime.model, call["model"])
        # 渲染应当真发生（image_bytes 非空）。
        self.assertGreater(call["image_bytes_len"], 0)


class SinglePagePreviewErrorPathTests(_SinglePagePreviewRouteTestBase):
    def test_out_of_range_page_num_returns_404_page_not_found(self) -> None:
        self._save_api_key()
        pdf_bytes = _build_pdf_bytes(total_pages=2)

        response = self.client.post(
            "/api/extraction/single-page-preview",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"page_num": "9"},
        )

        self._assert_api_error(
            response, status_code=404, code=ErrorCode.PAGE_NOT_FOUND.value
        )
        # LLM 不应被调用。
        self.assertEqual([], self.vision_gateway.calls)

    def test_zero_page_num_returns_404_page_not_found(self) -> None:
        self._save_api_key()
        pdf_bytes = _build_pdf_bytes(total_pages=2)

        response = self.client.post(
            "/api/extraction/single-page-preview",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"page_num": "0"},
        )

        self._assert_api_error(
            response, status_code=404, code=ErrorCode.PAGE_NOT_FOUND.value
        )
        self.assertEqual([], self.vision_gateway.calls)

    def test_bad_pdf_returns_400_pdf_open_failed(self) -> None:
        self._save_api_key()

        response = self.client.post(
            "/api/extraction/single-page-preview",
            files={"file": ("broken.pdf", b"not a real pdf", "application/pdf")},
            data={"page_num": "1"},
        )

        self._assert_api_error(
            response, status_code=400, code=ErrorCode.PDF_OPEN_FAILED.value
        )
        self.assertEqual([], self.vision_gateway.calls)

    def test_missing_api_key_returns_400_config_missing_api_key(self) -> None:
        # API Key 故意不设置。
        pdf_bytes = _build_pdf_bytes(total_pages=1)

        response = self.client.post(
            "/api/extraction/single-page-preview",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"page_num": "1"},
        )

        self._assert_api_error(
            response,
            status_code=400,
            code=ErrorCode.CONFIG_MISSING_API_KEY.value,
        )
        self.assertEqual([], self.vision_gateway.calls)

    def test_llm_auth_failure_returns_401_llm_auth_failed(self) -> None:
        self._save_api_key()
        self.vision_gateway.next_outcome = AppError(
            code=ErrorCode.LLM_AUTH_FAILED, message="bad key"
        )
        pdf_bytes = _build_pdf_bytes(total_pages=1)

        response = self.client.post(
            "/api/extraction/single-page-preview",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"page_num": "1"},
        )

        self._assert_api_error(
            response, status_code=401, code=ErrorCode.LLM_AUTH_FAILED.value
        )
        # LLM 必须被实际调用过一次（否则就不是认证失败）。
        self.assertEqual(1, len(self.vision_gateway.calls))

    def test_missing_file_field_returns_422(self) -> None:
        self._save_api_key()

        response = self.client.post(
            "/api/extraction/single-page-preview",
            data={"page_num": "1"},
        )

        self.assertEqual(422, response.status_code, response.text)
        # FastAPI 默认 422 形态：`{"detail": [...]}`，不是 ApiErrorResponse。
        self.assertEqual([], self.vision_gateway.calls)

    def test_missing_page_num_field_returns_422(self) -> None:
        self._save_api_key()
        pdf_bytes = _build_pdf_bytes(total_pages=1)

        response = self.client.post(
            "/api/extraction/single-page-preview",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
        )

        self.assertEqual(422, response.status_code, response.text)
        self.assertEqual([], self.vision_gateway.calls)
