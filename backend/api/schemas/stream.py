"""SSE payload contracts and event mappings."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.shared_kernel.contracts import EventType, JobEvent


@dataclass(frozen=True, slots=True)
class PageEventPayload:
    type: str = field(default="page", init=False)
    page_num: int
    status: str
    processed_count: int
    total_pages: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CompleteEventPayload:
    type: str = field(default="complete", init=False)
    processed_count: int
    total_pages: int
    succeeded_pages: list[int]
    failed_pages: list[int]


@dataclass(frozen=True, slots=True)
class FailedEventPayload:
    type: str = field(default="failed", init=False)
    detail: str


def to_sse_payload(
    event: JobEvent,
) -> PageEventPayload | CompleteEventPayload | FailedEventPayload | None:
    payload = event.payload
    if event.event_type in {EventType.PAGE_PROCESSED, EventType.STATUS_CHANGED}:
        return PageEventPayload(
            page_num=int(payload["page_num"]),
            status=str(payload["status"]),
            processed_count=int(payload["processed_count"]),
            total_pages=int(payload["total_pages"]),
            error=str(payload["error"]) if payload.get("error") is not None else None,
        )

    if event.event_type == EventType.EXTRACTION_COMPLETED:
        return CompleteEventPayload(
            processed_count=int(payload["processed_count"]),
            total_pages=int(payload["total_pages"]),
            succeeded_pages=[int(item) for item in payload["succeeded_pages"]],
            failed_pages=[int(item) for item in payload["failed_pages"]],
        )

    if event.event_type == EventType.JOB_FAILED:
        return FailedEventPayload(detail=str(payload["detail"]))

    if event.event_type == EventType.BUILD_COMPLETED:
        return None

    raise ValueError(f"unsupported SSE event type: {event.event_type.value}")


__all__ = [
    "CompleteEventPayload",
    "FailedEventPayload",
    "PageEventPayload",
    "to_sse_payload",
]
