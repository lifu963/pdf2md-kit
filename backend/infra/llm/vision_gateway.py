"""Vision LLM gateway adapter with protocol-level retry and error mapping."""

from __future__ import annotations

import base64
import threading
from typing import Any, Protocol

from openai import APITimeoutError, APIConnectionError, AuthenticationError, OpenAI, RateLimitError

from backend.shared_kernel.contracts import ModelConfig
from backend.shared_kernel.errors import AppError, ErrorCode

DEFAULT_OPENAI_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
_CONNECTION_TEST_PROMPT = "Hello, please reply with OK."
_CONNECTION_TEST_IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAA4AAAAOCAQAAAC1QeVaAAAAGElEQVR4nGNkYGD4z0AEYCJG0ahC6ikEAHImARJQmW+QAAAAAElFTkSuQmCC"
)


class OpenAIClientFactory(Protocol):
    def __call__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: int,
    ) -> Any:
        """Create and return an OpenAI-compatible client object."""


class OpenAIVisionExtractionGateway:
    """Extract markdown from rendered PNG bytes through OpenAI-compatible vision API."""

    def __init__(self, *, client_factory: OpenAIClientFactory | None = None) -> None:
        self._client_factory = client_factory or _default_client_factory

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
        session = self.open_session(model=model, api_key=api_key)
        try:
            return session.extract_markdown(
                image_bytes=image_bytes,
                prompt=prompt,
                max_retries=max_retries,
                page_num=page_num,
            )
        finally:
            session.close()

    def open_session(
        self,
        *,
        model: ModelConfig,
        api_key: str,
    ) -> "_OpenAIVisionExtractionSession":
        return _OpenAIVisionExtractionSession(
            model=model,
            api_key=api_key,
            client_factory=self._client_factory,
        )

    def test_connection(
        self,
        *,
        model: ModelConfig,
        api_key: str,
    ) -> str:
        session = self.open_session(model=model, api_key=api_key)
        try:
            return session.extract_markdown(
                image_bytes=_CONNECTION_TEST_IMAGE_BYTES,
                prompt=_CONNECTION_TEST_PROMPT,
                max_retries=0,
            )
        finally:
            session.close()


class _OpenAIVisionExtractionSession:
    def __init__(
        self,
        *,
        model: ModelConfig,
        api_key: str,
        client_factory: OpenAIClientFactory,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client_factory = client_factory
        self._client: Any | None = None
        self._owner_thread_id: int | None = None

    def extract_markdown(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        del page_num
        _validate_request(image_bytes=image_bytes, max_retries=max_retries)

        client = self._ensure_client()
        message_content = _build_message_content(image_bytes=image_bytes, prompt=prompt)
        max_attempts = max_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                response = client.chat.completions.create(
                    model=self._model.name,
                    messages=[{"role": "user", "content": message_content}],
                    timeout=self._model.timeout_seconds,
                )
                return _extract_markdown_text(response)
            except AuthenticationError as exc:
                raise AppError(
                    code=ErrorCode.LLM_AUTH_FAILED,
                    message=f"llm authentication failed: {exc}",
                ) from exc
            except RateLimitError as exc:
                if attempt < max_attempts:
                    continue
                raise AppError(
                    code=ErrorCode.LLM_RATE_LIMITED,
                    message=f"llm rate limited after {attempt} attempts: {exc}",
                    details={"attempts": attempt, "max_retries": max_retries},
                ) from exc
            except (APITimeoutError, APIConnectionError, TimeoutError) as exc:
                if attempt < max_attempts:
                    continue
                raise AppError(
                    code=ErrorCode.LLM_TIMEOUT,
                    message=f"llm request timed out after {attempt} attempts: {exc}",
                    details={"attempts": attempt, "max_retries": max_retries},
                ) from exc
            except AppError:
                raise
            except Exception as exc:
                raise AppError(
                    code=ErrorCode.UNEXPECTED_ERROR,
                    message=f"llm request failed: {exc}",
                ) from exc

        raise AppError(
            code=ErrorCode.UNEXPECTED_ERROR,
            message="llm request exited retry loop unexpectedly",
        )

    def close(self) -> None:
        client = self._client
        self._client = None
        self._owner_thread_id = None
        if client is None:
            return

        try:
            close_method = client.close
        except AttributeError:
            return
        close_method()

    def _ensure_client(self) -> Any:
        current_thread_id = threading.get_ident()
        if self._client is None:
            self._client = self._client_factory(
                api_key=self._api_key,
                base_url=DEFAULT_OPENAI_BASE_URL,
                timeout_seconds=self._model.timeout_seconds,
            )
            self._owner_thread_id = current_thread_id
            return self._client

        if self._owner_thread_id != current_thread_id:
            raise AppError(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="vision extraction session cannot be used across multiple threads",
            )

        return self._client


def _default_client_factory(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: int,
) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_seconds,
    )


def _validate_request(*, image_bytes: bytes, max_retries: int) -> None:
    if max_retries < 0:
        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message="max_retries must be non-negative",
            details={"max_retries": max_retries},
        )

    if not image_bytes:
        raise AppError(
            code=ErrorCode.UNEXPECTED_ERROR,
            message="image bytes are empty",
        )


def _build_message_content(*, image_bytes: bytes, prompt: str) -> list[dict[str, Any]]:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{encoded}",
            },
        },
        {
            "type": "text",
            "text": prompt,
        },
    ]


def _extract_markdown_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise AppError(
            code=ErrorCode.UNEXPECTED_ERROR,
            message="llm response contains no choices",
        )

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    text = _normalize_content(content)
    if not text:
        raise AppError(
            code=ErrorCode.UNEXPECTED_ERROR,
            message="llm response content is empty",
        )
    return text


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = item.get("text")
        else:
            value = getattr(item, "text", None)

        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                parts.append(stripped)

    return "\n".join(parts).strip()


__all__ = ["OpenAIVisionExtractionGateway", "OpenAIClientFactory"]

