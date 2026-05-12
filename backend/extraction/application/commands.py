"""Extraction application command contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StartJobExtractionCommand:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class RetryPageExtractionCommand:
    job_id: UUID
    page_num: int


__all__ = ["RetryPageExtractionCommand", "StartJobExtractionCommand"]
