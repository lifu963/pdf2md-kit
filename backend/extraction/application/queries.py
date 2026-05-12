"""Extraction application query contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GetExtractionProgressQuery:
    job_id: UUID


__all__ = ["GetExtractionProgressQuery"]
