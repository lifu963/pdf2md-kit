"""Port contracts used by config application."""

from __future__ import annotations

from typing import Protocol

from backend.shared_kernel.contracts import RuntimeConfig


class ConfigRepository(Protocol):
    def load(self) -> RuntimeConfig:
        """Load runtime config (initialize when missing)."""

    def save(self, config: RuntimeConfig) -> RuntimeConfig:
        """Persist runtime config and return persisted view."""

    def reset_to_template(self) -> RuntimeConfig:
        """Restore runtime config from project template and return persisted view."""


class SecretStore(Protocol):
    def has_api_key(self) -> bool:
        """Return whether any API key source is available."""

    def get_api_key(self) -> str | None:
        """Return API key value if available."""

    def require_api_key(self) -> str:
        """Return API key or raise CONFIG_MISSING_API_KEY."""

    def set_api_key(self, api_key: str) -> None:
        """Persist API key to preferred secret source."""


__all__ = ["ConfigRepository", "SecretStore"]
