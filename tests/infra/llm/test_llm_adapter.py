"""
Step 12: llm-adapter 适配器测试

验收目标（严格对齐实施步骤）：
1. 成功提取时，返回 Markdown，且请求参数组装正确。
2. 鉴权失败映射为 LLM_AUTH_FAILED，且不得重试。
3. 限流错误映射为 LLM_RATE_LIMITED。
4. 超时错误映射为 LLM_TIMEOUT。
5. 超过最大重试次数时，调用次数严格服从 max_retries 配置。
6. 可恢复错误后可重试并最终成功。
"""

from __future__ import annotations

import base64
import struct
import sys
from types import SimpleNamespace
from unittest import TestCase, mock

from backend.infra.llm import OpenAIVisionExtractionGateway
from backend.infra.llm.vision_gateway import (
    DEFAULT_OPENAI_BASE_URL,
    _CONNECTION_TEST_IMAGE_BYTES,
    _CONNECTION_TEST_PROMPT,
)
from backend.shared_kernel.contracts import ModelConfig
from backend.shared_kernel.errors import AppError, ErrorCode

vision_gateway_module = sys.modules[OpenAIVisionExtractionGateway.__module__]


class _FakeAuthenticationError(Exception):
    pass


class _FakeRateLimitError(Exception):
    pass


class _FakeTimeoutError(Exception):
    pass


def _response_with_text(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


class _FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if not self._outcomes:
            raise AssertionError("unexpected extra completion call")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = _FakeChat(completions)


class _FakeClientFactory:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: int,
    ) -> _FakeClient:
        self.calls.append(
            {
                "api_key": api_key,
                "base_url": base_url,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._client


class TestLlmAdapter(TestCase):
    def setUp(self) -> None:
        self.model = ModelConfig(
            name="vision-model-v1",
            timeout_seconds=30,
        )
        self.prompt = "请提取为 Markdown"
        self.api_key = "test-api-key"
        self.image_bytes = b"\x89PNG\r\n\x1a\nfake"

    def _build_gateway(
        self, outcomes: list[object]
    ) -> tuple[OpenAIVisionExtractionGateway, _FakeCompletions, _FakeClientFactory]:
        completions = _FakeCompletions(outcomes)
        client = _FakeClient(completions)
        client_factory = _FakeClientFactory(client)
        gateway = OpenAIVisionExtractionGateway(client_factory=client_factory)
        return gateway, completions, client_factory

    def test_extract_markdown_success_and_request_payload_is_correct(self) -> None:
        gateway, completions, client_factory = self._build_gateway(
            [_response_with_text("## 标题\n正文内容")]
        )

        markdown = gateway.extract_markdown(
            image_bytes=self.image_bytes,
            prompt=self.prompt,
            model=self.model,
            api_key=self.api_key,
            max_retries=3,
        )

        self.assertEqual("## 标题\n正文内容", markdown)
        self.assertEqual(1, len(client_factory.calls))
        self.assertEqual(self.api_key, client_factory.calls[0]["api_key"])
        self.assertEqual(DEFAULT_OPENAI_BASE_URL, client_factory.calls[0]["base_url"])
        self.assertEqual(self.model.timeout_seconds, client_factory.calls[0]["timeout_seconds"])

        self.assertEqual(1, len(completions.calls))
        payload = completions.calls[0]
        self.assertEqual(self.model.name, payload["model"])
        self.assertNotIn("max_tokens", payload)

        messages = payload["messages"]
        self.assertEqual(1, len(messages))
        self.assertEqual("user", messages[0]["role"])
        content = messages[0]["content"]
        self.assertEqual("image_url", content[0]["type"])
        self.assertEqual("text", content[1]["type"])
        self.assertEqual(self.prompt, content[1]["text"])
        self.assertEqual(
            f"data:image/png;base64,{base64.b64encode(self.image_bytes).decode('ascii')}",
            content[0]["image_url"]["url"],
        )

    def test_open_session_reuses_same_client_across_multiple_requests(self) -> None:
        gateway, completions, client_factory = self._build_gateway(
            [_response_with_text("第一页"), _response_with_text("第二页")]
        )

        session = gateway.open_session(model=self.model, api_key=self.api_key)
        try:
            first = session.extract_markdown(
                image_bytes=self.image_bytes,
                prompt=self.prompt,
                max_retries=1,
                page_num=1,
            )
            second = session.extract_markdown(
                image_bytes=self.image_bytes,
                prompt=self.prompt,
                max_retries=1,
                page_num=2,
            )
        finally:
            session.close()

        self.assertEqual("第一页", first)
        self.assertEqual("第二页", second)
        self.assertEqual(1, len(client_factory.calls))
        self.assertEqual(2, len(completions.calls))

    def test_auth_failure_maps_to_llm_auth_failed_and_never_retries(self) -> None:
        gateway, completions, _ = self._build_gateway([_FakeAuthenticationError("invalid key")])

        with mock.patch.object(
            vision_gateway_module, "AuthenticationError", _FakeAuthenticationError
        ):
            with self.assertRaises(AppError) as ctx:
                gateway.extract_markdown(
                    image_bytes=self.image_bytes,
                    prompt=self.prompt,
                    model=self.model,
                    api_key=self.api_key,
                    max_retries=5,
                )
        self.assertEqual(ErrorCode.LLM_AUTH_FAILED, ctx.exception.code)
        self.assertEqual(1, len(completions.calls))

    def test_rate_limited_maps_to_llm_rate_limited(self) -> None:
        gateway, completions, _ = self._build_gateway([_FakeRateLimitError("rate limited")])

        with mock.patch.object(vision_gateway_module, "RateLimitError", _FakeRateLimitError):
            with self.assertRaises(AppError) as ctx:
                gateway.extract_markdown(
                    image_bytes=self.image_bytes,
                    prompt=self.prompt,
                    model=self.model,
                    api_key=self.api_key,
                    max_retries=0,
                )
        self.assertEqual(ErrorCode.LLM_RATE_LIMITED, ctx.exception.code)
        self.assertEqual(1, len(completions.calls))

    def test_timeout_maps_to_llm_timeout(self) -> None:
        gateway, completions, _ = self._build_gateway([_FakeTimeoutError("timeout")])

        with mock.patch.object(vision_gateway_module, "APITimeoutError", _FakeTimeoutError):
            with self.assertRaises(AppError) as ctx:
                gateway.extract_markdown(
                    image_bytes=self.image_bytes,
                    prompt=self.prompt,
                    model=self.model,
                    api_key=self.api_key,
                    max_retries=0,
                )
        self.assertEqual(ErrorCode.LLM_TIMEOUT, ctx.exception.code)
        self.assertEqual(1, len(completions.calls))

    def test_exceeds_max_retries_strictly_follow_configuration(self) -> None:
        gateway, completions, _ = self._build_gateway(
            [
                _FakeRateLimitError("try1"),
                _FakeRateLimitError("try2"),
                _FakeRateLimitError("try3"),
            ]
        )

        with mock.patch.object(vision_gateway_module, "RateLimitError", _FakeRateLimitError):
            with self.assertRaises(AppError) as ctx:
                gateway.extract_markdown(
                    image_bytes=self.image_bytes,
                    prompt=self.prompt,
                    model=self.model,
                    api_key=self.api_key,
                    max_retries=2,
                )
        self.assertEqual(ErrorCode.LLM_RATE_LIMITED, ctx.exception.code)
        self.assertEqual(3, len(completions.calls), "max_retries=2 时应总计尝试 3 次（1 次首调 + 2 次重试）")

    def test_recoverable_error_then_success_retries_and_returns_markdown(self) -> None:
        gateway, completions, _ = self._build_gateway(
            [_FakeRateLimitError("temporary"), _response_with_text("恢复成功")]
        )

        with mock.patch.object(vision_gateway_module, "RateLimitError", _FakeRateLimitError):
            markdown = gateway.extract_markdown(
                image_bytes=self.image_bytes,
                prompt=self.prompt,
                model=self.model,
                api_key=self.api_key,
                max_retries=3,
            )

        self.assertEqual("恢复成功", markdown)
        self.assertEqual(2, len(completions.calls))

    def test_test_connection_uses_minimal_probe_request_and_returns_reply(self) -> None:
        gateway, completions, client_factory = self._build_gateway([_response_with_text("OK")])

        reply = gateway.test_connection(model=self.model, api_key=self.api_key)

        self.assertEqual("OK", reply)
        self.assertEqual(1, len(client_factory.calls))
        self.assertEqual(1, len(completions.calls))
        payload = completions.calls[0]
        self.assertEqual(self.model.name, payload["model"])
        self.assertEqual(self.model.timeout_seconds, payload["timeout"])
        content = payload["messages"][0]["content"]
        self.assertEqual("image_url", content[0]["type"])
        self.assertEqual("text", content[1]["type"])
        self.assertEqual(_CONNECTION_TEST_PROMPT, content[1]["text"])

    def test_test_connection_maps_auth_failure_without_retry(self) -> None:
        gateway, completions, _ = self._build_gateway([_FakeAuthenticationError("invalid key")])

        with mock.patch.object(
            vision_gateway_module, "AuthenticationError", _FakeAuthenticationError
        ):
            with self.assertRaises(AppError) as ctx:
                gateway.test_connection(model=self.model, api_key=self.api_key)

        self.assertEqual(ErrorCode.LLM_AUTH_FAILED, ctx.exception.code)
        self.assertEqual(1, len(completions.calls))

    def test_test_connection_probe_image_meets_minimum_size_requirement(self) -> None:
        self.assertGreaterEqual(len(_CONNECTION_TEST_IMAGE_BYTES), 24)
        width, height = struct.unpack(">II", _CONNECTION_TEST_IMAGE_BYTES[16:24])
        self.assertGreaterEqual(width, 14)
        self.assertGreaterEqual(height, 14)



