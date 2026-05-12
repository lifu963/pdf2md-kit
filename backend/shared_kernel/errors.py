"""Shared-kernel errors and error mappings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ErrorCode(str, Enum):
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    PAGE_NOT_FOUND = "PAGE_NOT_FOUND"
    JOB_STATUS_CONFLICT = "JOB_STATUS_CONFLICT"
    PAGE_EDIT_FORBIDDEN = "PAGE_EDIT_FORBIDDEN"
    PAGE_RETRY_FORBIDDEN = "PAGE_RETRY_FORBIDDEN"
    OUTPUT_EDIT_FORBIDDEN = "OUTPUT_EDIT_FORBIDDEN"
    OUTPUT_NOT_READY = "OUTPUT_NOT_READY"
    BUILD_OUTPUT_INVALID = "BUILD_OUTPUT_INVALID"
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_MISSING_API_KEY = "CONFIG_MISSING_API_KEY"
    PDF_OPEN_FAILED = "PDF_OPEN_FAILED"
    LLM_AUTH_FAILED = "LLM_AUTH_FAILED"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    STATE_CORRUPTED = "STATE_CORRUPTED"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.JOB_NOT_FOUND: 404,
    ErrorCode.PAGE_NOT_FOUND: 404,
    ErrorCode.JOB_STATUS_CONFLICT: 409,
    ErrorCode.PAGE_EDIT_FORBIDDEN: 409,
    ErrorCode.PAGE_RETRY_FORBIDDEN: 409,
    ErrorCode.OUTPUT_EDIT_FORBIDDEN: 409,
    ErrorCode.OUTPUT_NOT_READY: 409,
    ErrorCode.BUILD_OUTPUT_INVALID: 500,
    ErrorCode.CONFIG_INVALID: 400,
    ErrorCode.CONFIG_MISSING_API_KEY: 400,
    ErrorCode.PDF_OPEN_FAILED: 400,
    ErrorCode.LLM_AUTH_FAILED: 401,
    ErrorCode.LLM_RATE_LIMITED: 429,
    ErrorCode.LLM_TIMEOUT: 504,
    ErrorCode.PERSISTENCE_ERROR: 500,
    ErrorCode.STATE_CORRUPTED: 500,
    ErrorCode.UNEXPECTED_ERROR: 500,
}

DEFAULT_ERROR_MESSAGES: dict[ErrorCode, str] = {
    code: code.value.lower().replace("_", " ")
    for code in ErrorCode
}


@dataclass(slots=True)
class AppError(Exception):
    """Stable application error object used by all layers."""

    code: ErrorCode
    message: str | None = None
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.message is None:
            self.message = DEFAULT_ERROR_MESSAGES[self.code]
        Exception.__init__(self, self.message)

    @property
    def http_status(self) -> int:
        return ERROR_HTTP_STATUS[self.code]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": dict(self.details) if self.details is not None else None,
        }


__all__ = ["AppError", "DEFAULT_ERROR_MESSAGES", "ERROR_HTTP_STATUS", "ErrorCode"]
