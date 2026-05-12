"""Job domain re-exports for stable local import path."""

from __future__ import annotations

from backend.shared_kernel.contracts import (
    ArtifactRef,
    ArtifactType,
    BuildMergeMode,
    EventType,
    ExtractConfig,
    JobAggregate,
    JobEvent,
    JobStatus,
    ModelConfig,
    OutputDocument,
    PageDocument,
    PageStatus,
    RuntimeConfig,
    SourceDocumentRef,
)


__all__ = [
    "ArtifactRef",
    "ArtifactType",
    "BuildMergeMode",
    "EventType",
    "ExtractConfig",
    "JobAggregate",
    "JobEvent",
    "JobStatus",
    "ModelConfig",
    "OutputDocument",
    "PageDocument",
    "PageStatus",
    "RuntimeConfig",
    "SourceDocumentRef",
]
