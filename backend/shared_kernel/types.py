"""Shared basic types and generic Result contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeAlias, TypeVar
from uuid import UUID

from backend.shared_kernel.errors import AppError


JobId: TypeAlias = UUID
PageNumber: TypeAlias = int
EventSequence: TypeAlias = int
JsonDict: TypeAlias = dict[str, Any]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    """Application-wide operation result object."""

    ok: bool
    value: T | None = None
    error: AppError | None = None

    def __post_init__(self) -> None:
        has_error = self.error is not None
        if self.ok and has_error:
            raise ValueError("Success result must not contain an error")
        if not self.ok and not has_error:
            raise ValueError("Failure result must contain an error")

    @classmethod
    def success(cls, value: T) -> "Result[T]":
        return cls(ok=True, value=value, error=None)

    @classmethod
    def failure(cls, error: AppError) -> "Result[T]":
        return cls(ok=False, value=None, error=error)

    @property
    def is_ok(self) -> bool:
        return self.ok

    @property
    def is_err(self) -> bool:
        return not self.ok

    def unwrap(self) -> T:
        if self.error is not None:
            raise self.error
        return self.value  # type: ignore[return-value]


__all__ = ["EventSequence", "JobId", "JsonDict", "PageNumber", "Result"]
