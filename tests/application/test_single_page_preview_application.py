"""
Tests for `SinglePagePreviewApplication` (Task 1 of single-page-preview spec).

验收目标：
1. Happy path：读取 runtime config、API Key，渲染指定页，调用 LLM，返回 Markdown。
2. 非法/越界 page_num → AppError(PAGE_NOT_FOUND)。
3. PDF 打不开/渲染失败 → AppError(PDF_OPEN_FAILED)。
4. API Key 缺失 / LLM 错 → 原样上抛 AppError。
5. 零副作用：不依赖 JobRepository / PageRepository / SourceDocumentStore /
   EventPublisher / TaskScheduler（通过构造器签名显式拒绝）。
6. PDF render session 必须被关闭（资源释放）。
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest import TestCase

from backend.extraction.application.dto import SinglePagePreviewResult
from backend.extraction.application.single_page_preview import (
    SinglePagePreviewApplication,
)
from backend.shared_kernel.contracts import (
    ExtractConfig,
    ModelConfig,
    RuntimeConfig,
)
from backend.shared_kernel.errors import AppError, ErrorCode


_PDF_BYTES = b"%PDF-1.7 fake payload"


class _FakeConfigRepository:
    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config
        self.load_calls = 0

    def load(self) -> RuntimeConfig:
        self.load_calls += 1
        return self._config

    def save(self, config: RuntimeConfig) -> RuntimeConfig:
        self._config = config
        return config


class _FakeSecretStore:
    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key
        self.require_calls = 0

    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def get_api_key(self) -> str | None:
        return self._api_key

    def require_api_key(self) -> str:
        self.require_calls += 1
        if not self._api_key:
            raise AppError(ErrorCode.CONFIG_MISSING_API_KEY)
        return self._api_key

    def set_api_key(self, api_key: str) -> None:
        self._api_key = api_key


class _FakePdfRenderSession:
    def __init__(
        self,
        *,
        gateway: "_FakePdfGateway",
        pdf_bytes: bytes,
    ) -> None:
        self._gateway = gateway
        self._pdf_bytes = pdf_bytes
        self.closed = False
        self.page_count_reads = 0

    @property
    def page_count(self) -> int:
        self.page_count_reads += 1
        outcome = self._gateway.page_count_outcome
        if isinstance(outcome, Exception):
            raise outcome
        return self._gateway.page_count

    def render_page(self, page_num: int, dpi: int) -> bytes:
        self._gateway.render_calls.append((page_num, dpi))
        outcome = self._gateway.render_outcomes.get(page_num)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return f"image:{page_num}:{dpi}".encode("utf-8")
        return outcome

    def close(self) -> None:
        self.closed = True
        self._gateway.closed_sessions.append(self)


class _FakePdfGateway:
    def __init__(self, *, page_count: int = 3) -> None:
        self.page_count = page_count
        self.page_count_outcome: Any = None
        self.open_calls: list[bytes] = []
        self.open_outcomes: list[Any] = []
        self.render_calls: list[tuple[int, int]] = []
        self.render_outcomes: dict[int, Any] = {}
        self.closed_sessions: list[_FakePdfRenderSession] = []
        self.opened_sessions: list[_FakePdfRenderSession] = []

    def count_pages(self, pdf_bytes: bytes) -> int:
        raise AssertionError(
            "single-page preview should not call gateway.count_pages; "
            "read session.page_count inside the render session instead"
        )

    def render_page(self, pdf_bytes: bytes, page_num: int, dpi: int) -> bytes:
        raise AssertionError("single-page preview should use open_render_session")

    def open_render_session(self, pdf_bytes: bytes) -> _FakePdfRenderSession:
        self.open_calls.append(pdf_bytes)
        if self.open_outcomes:
            outcome = self.open_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        session = _FakePdfRenderSession(gateway=self, pdf_bytes=pdf_bytes)
        self.opened_sessions.append(session)
        return session


class _FakeVisionGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcomes: dict[int, Any] = {}
        self.open_session_calls = 0

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
                "image_bytes": image_bytes,
                "prompt": prompt,
                "model": model,
                "api_key": api_key,
                "max_retries": max_retries,
                "page_num": page_num,
            }
        )
        outcome = self.outcomes.get(page_num if page_num is not None else 0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return f"# page {page_num}"
        return outcome

    def open_session(self, *, model: ModelConfig, api_key: str) -> Any:
        self.open_session_calls += 1
        raise AssertionError("single-page preview should not open a reusable session")

    def test_connection(self, *, model: ModelConfig, api_key: str) -> str:
        raise AssertionError("single-page preview should not call test_connection")


def _default_runtime() -> RuntimeConfig:
    return RuntimeConfig(
        model=ModelConfig(name="vision-model", timeout_seconds=30),
        extract=ExtractConfig(
            dpi=180,
            concurrency=2,
            max_retries=1,
            prompt="提取成 Markdown",
        ),
        has_api_key=True,
    )


class SinglePagePreviewApplicationTests(TestCase):
    def setUp(self) -> None:
        self.config_repo = _FakeConfigRepository(_default_runtime())
        self.secret_store = _FakeSecretStore(api_key="test-key")
        self.pdf_gateway = _FakePdfGateway(page_count=3)
        self.vision_gateway = _FakeVisionGateway()
        self.app = SinglePagePreviewApplication(
            config_repository=self.config_repo,
            secret_store=self.secret_store,
            pdf_gateway=self.pdf_gateway,
            vision_gateway=self.vision_gateway,
        )

    def test_happy_path_returns_markdown_and_page_num(self) -> None:
        self.vision_gateway.outcomes[2] = "## 第二页内容"

        result = self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=2)

        self.assertIsInstance(result, SinglePagePreviewResult)
        self.assertEqual(2, result.page_num)
        self.assertEqual("## 第二页内容", result.content)

    def test_happy_path_forwards_runtime_config_and_api_key(self) -> None:
        self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(1, len(self.vision_gateway.calls))
        call = self.vision_gateway.calls[0]
        runtime = self.config_repo.load()
        self.assertEqual(runtime.extract.prompt, call["prompt"])
        self.assertEqual(runtime.model, call["model"])
        self.assertEqual(runtime.extract.max_retries, call["max_retries"])
        self.assertEqual("test-key", call["api_key"])
        self.assertEqual(1, call["page_num"])
        self.assertEqual(b"image:1:180", call["image_bytes"])

        self.assertEqual([(1, 180)], self.pdf_gateway.render_calls)

    def test_pdf_render_session_is_closed_after_success(self) -> None:
        self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(1, len(self.pdf_gateway.opened_sessions))
        self.assertTrue(self.pdf_gateway.opened_sessions[0].closed)

    def test_pdf_render_session_is_closed_after_render_failure(self) -> None:
        self.pdf_gateway.render_outcomes[1] = AppError(
            ErrorCode.PDF_OPEN_FAILED, "boom"
        )

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)
        self.assertEqual(1, len(self.pdf_gateway.opened_sessions))
        self.assertTrue(self.pdf_gateway.opened_sessions[0].closed)

    def test_pdf_render_session_is_closed_after_llm_failure(self) -> None:
        self.vision_gateway.outcomes[1] = AppError(ErrorCode.LLM_AUTH_FAILED, "nope")

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(ErrorCode.LLM_AUTH_FAILED, ctx.exception.code)
        self.assertEqual(1, len(self.pdf_gateway.opened_sessions))
        self.assertTrue(self.pdf_gateway.opened_sessions[0].closed)

    def test_zero_page_num_raises_page_not_found(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=0)

        self.assertEqual(ErrorCode.PAGE_NOT_FOUND, ctx.exception.code)
        self.assertEqual([], self.pdf_gateway.open_calls)
        self.assertEqual([], self.vision_gateway.calls)

    def test_negative_page_num_raises_page_not_found(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=-1)

        self.assertEqual(ErrorCode.PAGE_NOT_FOUND, ctx.exception.code)
        self.assertEqual([], self.pdf_gateway.open_calls)

    def test_out_of_range_page_num_raises_page_not_found(self) -> None:
        self.pdf_gateway.page_count = 2

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=5)

        self.assertEqual(ErrorCode.PAGE_NOT_FOUND, ctx.exception.code)
        details = ctx.exception.details or {}
        self.assertEqual(5, details.get("page_num"))
        self.assertEqual([], self.vision_gateway.calls)

    def test_bad_pdf_raises_pdf_open_failed(self) -> None:
        self.pdf_gateway.open_outcomes.append(
            AppError(ErrorCode.PDF_OPEN_FAILED, "failed to open pdf")
        )

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=b"not-a-pdf", page_num=1)

        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)
        self.assertEqual([], self.vision_gateway.calls)

    def test_bad_pdf_during_page_count_raises_pdf_open_failed(self) -> None:
        self.pdf_gateway.page_count_outcome = AppError(
            ErrorCode.PDF_OPEN_FAILED, "failed to open pdf"
        )

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=b"not-a-pdf", page_num=1)

        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)
        self.assertEqual([], self.vision_gateway.calls)
        self.assertEqual(1, len(self.pdf_gateway.opened_sessions))
        self.assertTrue(self.pdf_gateway.opened_sessions[0].closed)

    def test_single_render_session_is_used_for_page_count_and_render(self) -> None:
        self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=2)

        self.assertEqual(1, len(self.pdf_gateway.opened_sessions))
        session = self.pdf_gateway.opened_sessions[0]
        self.assertEqual(1, session.page_count_reads)
        self.assertEqual([(2, 180)], self.pdf_gateway.render_calls)

    def test_missing_api_key_raises_config_missing(self) -> None:
        self.secret_store = _FakeSecretStore(api_key=None)
        self.app = SinglePagePreviewApplication(
            config_repository=self.config_repo,
            secret_store=self.secret_store,
            pdf_gateway=self.pdf_gateway,
            vision_gateway=self.vision_gateway,
        )

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(ErrorCode.CONFIG_MISSING_API_KEY, ctx.exception.code)
        self.assertEqual([], self.pdf_gateway.open_calls)
        self.assertEqual([], self.vision_gateway.calls)

    def test_llm_auth_failure_propagates(self) -> None:
        self.vision_gateway.outcomes[1] = AppError(
            ErrorCode.LLM_AUTH_FAILED, "bad api key"
        )

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(ErrorCode.LLM_AUTH_FAILED, ctx.exception.code)

    def test_llm_timeout_propagates(self) -> None:
        self.vision_gateway.outcomes[1] = AppError(ErrorCode.LLM_TIMEOUT, "timeout")

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(ErrorCode.LLM_TIMEOUT, ctx.exception.code)

    def test_unexpected_render_exception_wrapped_as_pdf_open_failed(self) -> None:
        self.pdf_gateway.render_outcomes[1] = RuntimeError("surprise")

        with self.assertRaises(AppError) as ctx:
            self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)

    def test_returns_frozen_dataclass(self) -> None:
        result = self.app.preview_page(pdf_bytes=_PDF_BYTES, page_num=1)

        with self.assertRaises(Exception):
            result.page_num = 999  # type: ignore[misc]

    def test_constructor_does_not_accept_clock_or_task_context_ports(self) -> None:
        sig = inspect.signature(SinglePagePreviewApplication.__init__)
        forbidden = {
            "clock",
            "job_repository",
            "page_repository",
            "source_store",
            "event_publisher",
            "task_scheduler",
        }
        self.assertEqual(
            set(),
            forbidden & set(sig.parameters),
            f"SinglePagePreviewApplication 不应依赖任务上下文 ports: got params {sig.parameters}",
        )


class SinglePagePreviewResultTests(TestCase):
    def test_is_slots_frozen_dataclass(self) -> None:
        result = SinglePagePreviewResult(page_num=3, content="x")
        self.assertEqual(3, result.page_num)
        self.assertEqual("x", result.content)

        with self.assertRaises(Exception):
            result.page_num = 5  # type: ignore[misc]
