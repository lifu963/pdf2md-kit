"""HTTP extraction preview response contracts."""

from __future__ import annotations

from dataclasses import dataclass

from backend.extraction.application import SinglePagePreviewResult


@dataclass(frozen=True, slots=True)
class SinglePagePreviewResponse:
    page_num: int
    content: str


def to_single_page_preview_response(
    result: SinglePagePreviewResult,
) -> SinglePagePreviewResponse:
    return SinglePagePreviewResponse(
        page_num=result.page_num,
        content=result.content,
    )


__all__ = [
    "SinglePagePreviewResponse",
    "to_single_page_preview_response",
]
