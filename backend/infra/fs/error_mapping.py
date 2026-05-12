"""Common filesystem I/O error mapping helpers."""

from __future__ import annotations

from backend.shared_kernel.errors import AppError, ErrorCode


def raise_persistence_error(action: str, exc: OSError) -> None:
    """Convert low-level file system errors to stable contract errors."""
    raise AppError(
        code=ErrorCode.PERSISTENCE_ERROR,
        message=f"{action}: {exc}",
    ) from exc


__all__ = ["raise_persistence_error"]
