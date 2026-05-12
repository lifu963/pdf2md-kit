"""Shared helpers for HTTP schema serialization."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


def dump_http_model(value: Any, *, exclude_none: bool = False) -> Any:
    """Convert dataclass-based HTTP schemas into JSON-safe primitives."""
    if is_dataclass(value):
        payload: dict[str, Any] = {}
        for field in fields(value):
            field_value = getattr(value, field.name)
            if field_value is None and exclude_none:
                continue
            payload[field.name] = dump_http_model(field_value, exclude_none=exclude_none)
        return payload

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, datetime):
        return _format_datetime(value)

    if isinstance(value, list):
        return [dump_http_model(item, exclude_none=exclude_none) for item in value]

    if isinstance(value, tuple):
        return [dump_http_model(item, exclude_none=exclude_none) for item in value]

    if isinstance(value, dict):
        payload: dict[str, Any] = {}
        for key, item in value.items():
            if item is None and exclude_none:
                continue
            payload[str(key)] = dump_http_model(item, exclude_none=exclude_none)
        return payload

    return value


def _format_datetime(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["dump_http_model"]
