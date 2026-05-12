"""HTTP routes for job create/query/stream/source endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import json
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Response, UploadFile
from fastapi.responses import StreamingResponse

from backend.api.dependencies import get_job_application, get_source_store, get_stream_application
from backend.api.schemas import (
    BuildOutputRequest,
    SaveOutputRequest,
    SavePageRequest,
    dump_http_model,
    output_download_filename_for,
    to_build_job_command,
    to_build_response,
    to_create_job_command,
    to_create_job_response,
    to_discard_output_command,
    to_job_history_list_response,
    to_job_response,
    to_output_document_response,
    to_page_response,
    to_page_summaries_response,
    to_retry_page_command,
    to_retry_page_response,
    to_save_output_command,
    to_save_page_command,
    to_sse_payload,
)
from backend.job.application import JobApplication
from backend.job.ports import SourceDocumentStore
from backend.stream.application import StreamApplication

router = APIRouter()
_SOURCE_STREAM_CHUNK_SIZE = 64 * 1024


def resolve_job_application() -> JobApplication:
    return get_job_application()


def resolve_stream_application() -> StreamApplication:
    return get_stream_application()


def resolve_source_store() -> SourceDocumentStore:
    return get_source_store()


@router.post("/jobs")
async def create_job(
    file: UploadFile = File(...),
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    filename = file.filename or ""
    pdf_bytes = await file.read()
    command = to_create_job_command(pdf_filename=filename, pdf_bytes=pdf_bytes)
    result = job_application.create_job(command)
    return dump_http_model(to_create_job_response(result), exclude_none=True)


@router.get("/jobs")
def list_jobs(
    job_application: JobApplication = Depends(resolve_job_application),
) -> list[dict[str, Any]]:
    jobs = job_application.list_jobs()
    return dump_http_model(to_job_history_list_response(jobs), exclude_none=True)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: UUID,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    view = job_application.get_job(job_id)
    return dump_http_model(to_job_response(view), exclude_none=True)


@router.get("/jobs/{job_id}/pages")
def list_pages(
    job_id: UUID,
    job_application: JobApplication = Depends(resolve_job_application),
) -> list[dict[str, Any]]:
    summaries = job_application.list_pages(job_id)
    return dump_http_model(to_page_summaries_response(summaries), exclude_none=True)


@router.get("/jobs/{job_id}/pages/{page_num}")
def get_page(
    job_id: UUID,
    page_num: int,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    page = job_application.get_page(job_id, page_num)
    return dump_http_model(to_page_response(page), exclude_none=True)


@router.get("/jobs/{job_id}/source")
def get_source(
    job_id: UUID,
    range_header: str | None = Header(default=None, alias="Range"),
    job_application: JobApplication = Depends(resolve_job_application),
    source_store: SourceDocumentStore = Depends(resolve_source_store),
) -> Response:
    source = job_application.get_source_document(job_id)
    total_size = source.size_bytes
    headers = {"Accept-Ranges": "bytes"}
    if range_header is None:
        stream_body = _build_source_stream_body(
            source_store=source_store,
            job_id=job_id,
            start=0,
            length=total_size,
        )
        return StreamingResponse(
            stream_body,
            media_type=source.content_type,
            headers={
                **headers,
                "Content-Length": str(total_size),
            },
        )

    parsed = _parse_http_range(range_header, total_size)
    if parsed is None:
        return Response(
            status_code=416,
            headers={
                **headers,
                "Content-Range": f"bytes */{total_size}",
            },
        )

    start, end = parsed
    length = end - start + 1
    stream_body = _build_source_stream_body(
        source_store=source_store,
        job_id=job_id,
        start=start,
        length=length,
    )
    return StreamingResponse(
        stream_body,
        status_code=206,
        media_type=source.content_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{total_size}",
            "Content-Length": str(length),
        },
    )


@router.get("/jobs/{job_id}/stream")
async def stream_job_events(
    job_id: UUID,
    job_application: JobApplication = Depends(resolve_job_application),
    stream_application: StreamApplication = Depends(resolve_stream_application),
) -> StreamingResponse:
    # Ensure errors such as JOB_NOT_FOUND are resolved before starting SSE stream.
    job_application.get_job(job_id)
    historical_events = stream_application.load_replay_events(
        job_id,
        for_live_follow=True,
    )
    has_terminal_event = stream_application.has_terminal_event(
        job_id=job_id,
        events=historical_events,
    )

    async def _event_stream() -> AsyncIterator[str]:
        for event in historical_events:
            payload = to_sse_payload(event)
            if payload is None:
                continue
            event_json = json.dumps(
                dump_http_model(payload, exclude_none=True),
                ensure_ascii=False,
            )
            yield f"data: {event_json}\n\n"
        if has_terminal_event:
            return

        async for event in stream_application.subscribe_job_events(job_id, replay=False):
            payload = to_sse_payload(event)
            if payload is None:
                continue
            event_json = json.dumps(
                dump_http_model(payload, exclude_none=True),
                ensure_ascii=False,
            )
            yield f"data: {event_json}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.put("/jobs/{job_id}/pages/{page_num}")
def save_page(
    job_id: UUID,
    page_num: int,
    request: SavePageRequest,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    command = to_save_page_command(job_id=job_id, page_num=page_num, request=request)
    page = job_application.save_page(command)
    return dump_http_model(to_page_response(page), exclude_none=True)


@router.post("/jobs/{job_id}/pages/{page_num}/retry")
def retry_page(
    job_id: UUID,
    page_num: int,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    command = to_retry_page_command(job_id=job_id, page_num=page_num)
    accepted = job_application.retry_page(command)
    return dump_http_model(to_retry_page_response(accepted), exclude_none=True)


@router.post("/jobs/{job_id}/build")
def build_output(
    job_id: UUID,
    request: BuildOutputRequest | None = None,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    command = to_build_job_command(job_id=job_id, request=request)
    result = job_application.build_output(command)
    return dump_http_model(to_build_response(result), exclude_none=True)


@router.get("/jobs/{job_id}/output")
def get_output(
    job_id: UUID,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    output = job_application.get_output_document(job_id)
    return dump_http_model(to_output_document_response(output), exclude_none=True)


@router.put("/jobs/{job_id}/output")
def save_output(
    job_id: UUID,
    request: SaveOutputRequest,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    command = to_save_output_command(job_id=job_id, request=request)
    output = job_application.save_output(command)
    return dump_http_model(to_output_document_response(output), exclude_none=True)


@router.post("/jobs/{job_id}/output/discard")
def discard_output(
    job_id: UUID,
    job_application: JobApplication = Depends(resolve_job_application),
) -> dict[str, Any]:
    command = to_discard_output_command(job_id=job_id)
    job = job_application.discard_output(command)
    return dump_http_model(to_job_response(job), exclude_none=True)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: UUID,
    job_application: JobApplication = Depends(resolve_job_application),
) -> Response:
    job_application.delete_job(job_id)
    return Response(status_code=204)


@router.get("/jobs/{job_id}/output/download")
def download_output(
    job_id: UUID,
    job_application: JobApplication = Depends(resolve_job_application),
) -> Response:
    artifact = job_application.get_output_artifact(job_id)
    source = job_application.get_source_document(job_id)
    output = job_application.get_output_document(job_id)
    filename = output_download_filename_for(source.filename)
    return Response(
        content=output.content,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": _attachment_disposition_for(filename),
        },
    )


def _attachment_disposition_for(filename: str) -> str:
    encoded = quote(filename)
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        return f"attachment; filename*=UTF-8''{encoded}"
    return f'attachment; filename="{filename}"'


def _iter_source_chunks(
    *,
    source_store: SourceDocumentStore,
    job_id: UUID,
    start: int,
    length: int,
    chunk_size: int = _SOURCE_STREAM_CHUNK_SIZE,
) -> Iterator[bytes]:
    with source_store.open_read(job_id) as stream:
        if start > 0:
            stream.seek(start)

        remaining = max(length, 0)
        while remaining > 0:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _build_source_stream_body(
    *,
    source_store: SourceDocumentStore,
    job_id: UUID,
    start: int,
    length: int,
) -> Iterator[bytes]:
    # Prime one chunk before building the streaming response so open/seek/read
    # failures still go through the regular AppError -> HTTP mapping.
    chunk_iterator = _iter_source_chunks(
        source_store=source_store,
        job_id=job_id,
        start=start,
        length=length,
    )
    first_chunk = _take_first_chunk(chunk_iterator)
    return _iter_prefetched_source_chunks(first_chunk=first_chunk, chunk_iterator=chunk_iterator)


def _take_first_chunk(chunk_iterator: Iterator[bytes]) -> bytes | None:
    try:
        return next(chunk_iterator)
    except StopIteration:
        return None


def _iter_prefetched_source_chunks(
    *,
    first_chunk: bytes | None,
    chunk_iterator: Iterator[bytes],
) -> Iterator[bytes]:
    if first_chunk is not None:
        yield first_chunk
    yield from chunk_iterator


def _parse_http_range(range_header: str, total_size: int) -> tuple[int, int] | None:
    if total_size <= 0:
        return None

    unit, separator, range_spec = range_header.strip().partition("=")
    if separator != "=" or unit.lower() != "bytes":
        return None
    if "," in range_spec:
        return None

    start_text, dash, end_text = range_spec.strip().partition("-")
    if dash != "-":
        return None

    start_text = start_text.strip()
    end_text = end_text.strip()
    if not start_text and not end_text:
        return None

    if not start_text:
        if not end_text.isdigit():
            return None
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        if suffix_length >= total_size:
            return 0, total_size - 1
        return total_size - suffix_length, total_size - 1

    if not start_text.isdigit():
        return None
    start = int(start_text)
    if start >= total_size:
        return None

    if not end_text:
        return start, total_size - 1
    if not end_text.isdigit():
        return None

    end = int(end_text)
    if end < start:
        return None
    if end >= total_size:
        end = total_size - 1
    return start, end


__all__ = [
    "download_output",
    "resolve_job_application",
    "resolve_source_store",
    "resolve_stream_application",
    "router",
]
