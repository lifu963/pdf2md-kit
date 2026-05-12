"""Job application command contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.shared_kernel.contracts import BuildMergeMode


@dataclass(frozen=True, slots=True)
class CreateJobCommand:
    pdf_filename: str
    pdf_bytes: bytes


@dataclass(frozen=True, slots=True)
class SavePageCommand:
    job_id: UUID
    page_num: int
    content: str


@dataclass(frozen=True, slots=True)
class RetryPageCommand:
    job_id: UUID
    page_num: int


@dataclass(frozen=True, slots=True)
class BuildJobCommand:
    job_id: UUID
    merge_mode: BuildMergeMode = BuildMergeMode.DIRECT


@dataclass(frozen=True, slots=True)
class SaveOutputCommand:
    job_id: UUID
    content: str


@dataclass(frozen=True, slots=True)
class DiscardOutputCommand:
    job_id: UUID


__all__ = [
    "BuildJobCommand",
    "CreateJobCommand",
    "DiscardOutputCommand",
    "RetryPageCommand",
    "SaveOutputCommand",
    "SavePageCommand",
]
