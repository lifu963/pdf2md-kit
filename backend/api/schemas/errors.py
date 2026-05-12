"""HTTP error response contract and mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.shared_kernel.errors import AppError, ErrorCode


@dataclass(frozen=True, slots=True)
class ApiErrorDetail:
    code: str
    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ApiErrorResponse:
    detail: ApiErrorDetail


def to_api_error_response(error: AppError | Exception) -> ApiErrorResponse:
    app_error = _normalize_error(error)
    return ApiErrorResponse(
        detail=ApiErrorDetail(
            code=app_error.code.value,
            message=app_error.message or app_error.code.value.lower().replace("_", " "),
            details=dict(app_error.details) if app_error.details is not None else None,
        )
    )


def map_error_to_http_response(error: AppError | Exception) -> tuple[int, ApiErrorResponse]:
    app_error = _normalize_error(error)
    return app_error.http_status, to_api_error_response(app_error)


def _normalize_error(error: AppError | Exception) -> AppError:
    if isinstance(error, AppError):
        return error
    return AppError(code=ErrorCode.UNEXPECTED_ERROR)


__all__ = [
    "ApiErrorDetail",
    "ApiErrorResponse",
    "map_error_to_http_response",
    "to_api_error_response",
]
