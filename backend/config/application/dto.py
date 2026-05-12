"""Config application DTO contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelConfigView:
    name: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ExtractConfigView:
    dpi: int
    concurrency: int
    max_retries: int
    prompt: str


@dataclass(frozen=True, slots=True)
class PublicConfigView:
    model: ModelConfigView
    extract: ExtractConfigView
    has_api_key: bool


@dataclass(frozen=True, slots=True)
class TestConnectionResultView:
    ok: bool
    message: str
    reply_preview: str | None = None


__all__ = [
    "ExtractConfigView",
    "ModelConfigView",
    "PublicConfigView",
    "TestConnectionResultView",
]
