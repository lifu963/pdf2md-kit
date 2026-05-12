"""Config application command contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelConfigInput:
    name: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ExtractConfigInput:
    dpi: int
    concurrency: int
    max_retries: int
    prompt: str


@dataclass(frozen=True, slots=True)
class UpdateConfigCommand:
    model: ModelConfigInput
    extract: ExtractConfigInput
    api_key: str | None = None


__all__ = ["ExtractConfigInput", "ModelConfigInput", "UpdateConfigCommand"]
