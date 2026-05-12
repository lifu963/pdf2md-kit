"""HTTP config request/response contracts."""

from __future__ import annotations

from dataclasses import dataclass

from backend.config.application.commands import (
    ExtractConfigInput,
    ModelConfigInput,
    UpdateConfigCommand,
)
from backend.config.application.dto import PublicConfigView
from backend.config.application.dto import TestConnectionResultView


@dataclass(frozen=True, slots=True)
class ConfigModelPayload:
    name: str
    timeout: int


@dataclass(frozen=True, slots=True)
class ExtractConfigPayload:
    dpi: int
    concurrency: int
    max_retries: int
    prompt: str


@dataclass(frozen=True, slots=True)
class PublicConfigResponse:
    model: ConfigModelPayload
    extract: ExtractConfigPayload
    has_api_key: bool


@dataclass(frozen=True, slots=True)
class UpdateConfigRequest:
    model: ConfigModelPayload
    extract: ExtractConfigPayload
    api_key: str | None = None


@dataclass(frozen=True, slots=True)
class TestConnectionResponse:
    ok: bool
    message: str
    reply_preview: str | None = None


def to_public_config_response(view: PublicConfigView) -> PublicConfigResponse:
    return PublicConfigResponse(
        model=ConfigModelPayload(
            name=view.model.name,
            timeout=view.model.timeout_seconds,
        ),
        extract=ExtractConfigPayload(
            dpi=view.extract.dpi,
            concurrency=view.extract.concurrency,
            max_retries=view.extract.max_retries,
            prompt=view.extract.prompt,
        ),
        has_api_key=view.has_api_key,
    )


def to_update_config_command(request: UpdateConfigRequest) -> UpdateConfigCommand:
    return UpdateConfigCommand(
        model=ModelConfigInput(
            name=request.model.name,
            timeout_seconds=request.model.timeout,
        ),
        extract=ExtractConfigInput(
            dpi=request.extract.dpi,
            concurrency=request.extract.concurrency,
            max_retries=request.extract.max_retries,
            prompt=request.extract.prompt,
        ),
        api_key=request.api_key,
    )


def to_test_connection_response(view: TestConnectionResultView) -> TestConnectionResponse:
    return TestConnectionResponse(
        ok=view.ok,
        message=view.message,
        reply_preview=view.reply_preview,
    )


__all__ = [
    "ConfigModelPayload",
    "ExtractConfigPayload",
    "PublicConfigResponse",
    "TestConnectionResponse",
    "UpdateConfigRequest",
    "to_public_config_response",
    "to_test_connection_response",
    "to_update_config_command",
]
