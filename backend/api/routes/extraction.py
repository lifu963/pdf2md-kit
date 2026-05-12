"""HTTP routes for one-shot single-page extraction preview (no job context)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool

from backend.api.dependencies import get_single_page_preview_application
from backend.api.schemas import dump_http_model, to_single_page_preview_response
from backend.extraction.application import SinglePagePreviewApplication

router = APIRouter()


def resolve_single_page_preview_application() -> SinglePagePreviewApplication:
    return get_single_page_preview_application()


@router.post("/extraction/single-page-preview")
async def single_page_preview(
    file: UploadFile = File(...),
    page_num: int = Form(...),
    application: SinglePagePreviewApplication = Depends(
        resolve_single_page_preview_application
    ),
) -> dict[str, Any]:
    pdf_bytes = await file.read()
    result = await run_in_threadpool(
        application.preview_page,
        pdf_bytes=pdf_bytes,
        page_num=page_num,
    )
    return dump_http_model(to_single_page_preview_response(result), exclude_none=True)


__all__ = ["resolve_single_page_preview_application", "router"]
