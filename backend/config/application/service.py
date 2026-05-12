"""Config application use-cases."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config.application.commands import UpdateConfigCommand
from backend.config.application.dto import (
    ExtractConfigView,
    ModelConfigView,
    PublicConfigView,
    TestConnectionResultView,
)
from backend.config.application.queries import GetPublicConfigQuery
from backend.config.ports import ConfigRepository, SecretStore
from backend.shared_kernel.contracts import ExtractConfig, ModelConfig, RuntimeConfig
from backend.shared_kernel.errors import AppError, ErrorCode

if TYPE_CHECKING:
    from backend.extraction.ports import VisionExtractionGateway

_CONNECTION_TEST_PREVIEW_LIMIT = 120


class ConfigApplication:
    """Application service for runtime config read/write."""

    def __init__(
        self,
        config_repository: ConfigRepository,
        secret_store: SecretStore,
        vision_gateway: VisionExtractionGateway,
    ) -> None:
        self._config_repository = config_repository
        self._secret_store = secret_store
        self._vision_gateway = vision_gateway

    def get_public_config(self, query: GetPublicConfigQuery | None = None) -> PublicConfigView:
        del query  # query object reserved for stable contract evolution
        loaded = self._config_repository.load()
        return _to_public_view(
            RuntimeConfig(
                model=loaded.model,
                extract=loaded.extract,
                has_api_key=self._secret_store.has_api_key(),
            )
        )

    def update_config(self, command: UpdateConfigCommand) -> PublicConfigView:
        _validate_update_command(command)

        runtime_config = RuntimeConfig(
            model=ModelConfig(
                name=command.model.name.strip(),
                timeout_seconds=command.model.timeout_seconds,
            ),
            extract=ExtractConfig(
                dpi=command.extract.dpi,
                concurrency=command.extract.concurrency,
                max_retries=command.extract.max_retries,
                prompt=command.extract.prompt.strip(),
            ),
            has_api_key=self._secret_store.has_api_key(),
        )

        persisted = self._config_repository.save(runtime_config)
        if command.api_key is not None:
            self._secret_store.set_api_key(command.api_key)

        return _to_public_view(
            RuntimeConfig(
                model=persisted.model,
                extract=persisted.extract,
                has_api_key=self._secret_store.has_api_key(),
            )
        )

    def reset_to_initial_config(self) -> PublicConfigView:
        restored = self._config_repository.reset_to_template()
        return _to_public_view(
            RuntimeConfig(
                model=restored.model,
                extract=restored.extract,
                has_api_key=self._secret_store.has_api_key(),
            )
        )

    def test_connection(self) -> TestConnectionResultView:
        loaded = self._config_repository.load()
        api_key = self._secret_store.require_api_key()
        reply = self._vision_gateway.test_connection(
            model=loaded.model,
            api_key=api_key,
        )
        return TestConnectionResultView(
            ok=True,
            message="LLM API 响应正常",
            reply_preview=_trim_reply_preview(reply),
        )


def _to_public_view(config: RuntimeConfig) -> PublicConfigView:
    return PublicConfigView(
        model=ModelConfigView(
            name=config.model.name,
            timeout_seconds=config.model.timeout_seconds,
        ),
        extract=ExtractConfigView(
            dpi=config.extract.dpi,
            concurrency=config.extract.concurrency,
            max_retries=config.extract.max_retries,
            prompt=config.extract.prompt,
        ),
        has_api_key=config.has_api_key,
    )


def _trim_reply_preview(reply: str) -> str:
    normalized = reply.strip()
    if len(normalized) <= _CONNECTION_TEST_PREVIEW_LIMIT:
        return normalized
    return normalized[:_CONNECTION_TEST_PREVIEW_LIMIT].rstrip() + "..."


def _validate_update_command(command: UpdateConfigCommand) -> None:
    _require_non_empty_str(command.model.name, "model.name")
    _require_positive_int(command.model.timeout_seconds, "model.timeout_seconds")

    _require_positive_int(command.extract.dpi, "extract.dpi")
    _require_positive_int(command.extract.concurrency, "extract.concurrency")
    _require_non_negative_int(command.extract.max_retries, "extract.max_retries")
    _require_non_empty_str(command.extract.prompt, "extract.prompt")

    if command.api_key is not None:
        _require_non_empty_str(command.api_key, "api_key")


def _require_non_empty_str(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message=f"{field} must be a non-empty string",
        )


def _require_positive_int(value: int, field: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message=f"{field} must be an integer > 0",
        )


def _require_non_negative_int(value: int, field: str) -> None:
    if not isinstance(value, int) or value < 0:
        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message=f"{field} must be an integer >= 0",
        )


__all__ = ["ConfigApplication"]
