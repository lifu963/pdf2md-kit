"""Job application query contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GetJobQuery:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class GetSourceDocumentQuery:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class ListPagesQuery:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class GetPageQuery:
    job_id: UUID
    page_num: int


@dataclass(frozen=True, slots=True)
class GetOutputDocumentQuery:
    job_id: UUID


@dataclass(frozen=True, slots=True)
class GetOutputArtifactQuery:
    job_id: UUID


__all__ = [
    "GetJobQuery",
    "GetOutputArtifactQuery",
    "GetOutputDocumentQuery",
    "GetPageQuery",
    "GetSourceDocumentQuery",
    "ListPagesQuery",
]
