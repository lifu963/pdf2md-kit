"""Clock and ID generation abstractions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Return current time in UTC."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@runtime_checkable
class IdGenerator(Protocol):
    def new(self) -> UUID:
        """Return a new UUID value."""


class Uuid4Generator:
    def new(self) -> UUID:
        return uuid4()


__all__ = ["Clock", "IdGenerator", "SystemClock", "Uuid4Generator"]
