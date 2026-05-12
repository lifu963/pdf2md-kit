"""
Step 22: 实现 source、SSE 与 SPA 路由，并完成 HTTP 回归

验收目标（严格对齐实施步骤）：
1. `GET /api/jobs/{id}/source` 支持整文件与 `Range` 响应。
2. `GET /api/jobs/{id}/stream` 支持 replay，并在终态事件后自动关闭。
3. `GET /` 与 `GET /jobs/{id}` 可作为 SPA fallback 访问。
4. 覆盖整文件下载、合法/非法 Range、`206`、`Accept-Ranges`、SSE replay、
   SSE 终态自动关闭、SPA 路由可访问等核心场景。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from uuid import UUID

import fitz
from starlette.testclient import TestClient

from backend.api import create_api_app
from backend.api.dependencies import ApiContainer
from backend.shared_kernel.contracts import EventType, JobEvent, JobStatus
from backend.shared_kernel.errors import AppError, ErrorCode


def _template_config_text() -> str:
    return (
        "model:\n"
        '  name: "vision-template"\n'
        "  timeout_seconds: 30\n"
        "\n"
        "extract:\n"
        "  dpi: 150\n"
        "  concurrency: 2\n"
        "  max_retries: 1\n"
        "  prompt: |\n"
        "    请提取成 Markdown\n"
    )


def _build_pdf_bytes(*, total_pages: int) -> bytes:
    doc = fitz.open()
    try:
        for _ in range(total_pages):
            doc.new_page()
        return doc.tobytes()
    finally:
        doc.close()


class _FixedIdGenerator:
    def __init__(self, value: UUID) -> None:
        self._value = value

    def new(self) -> UUID:
        return self._value


class _RecordingTaskScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    def schedule(self, *, job_id: UUID, task_name: str, task_factory) -> bool:  # type: ignore[no-untyped-def]
        del task_factory
        self.calls.append((job_id, task_name))
        return True


class _NoWholeReadStream:
    def __init__(self, raw_stream, read_sizes: list[int]) -> None:  # type: ignore[no-untyped-def]
        self._raw_stream = raw_stream
        self._read_sizes = read_sizes

    def read(self, size: int = -1) -> bytes:
        self._read_sizes.append(size)
        if size < 0:
            raise AssertionError("source route should not read full pdf in one call")
        return self._raw_stream.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._raw_stream.seek(offset, whence)

    def __enter__(self):  # type: ignore[no-untyped-def]
        self._raw_stream.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        return self._raw_stream.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._raw_stream, name)


class Step22SourceStreamSpaRouteTests(TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)

        project_root = Path(self._tmp_dir.name)
        data_root = project_root / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        (project_root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")

        self.fixed_job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000022")
        self.task_scheduler = _RecordingTaskScheduler()
        self.container = ApiContainer(
            project_root=project_root,
            data_root=data_root,
            id_generator=_FixedIdGenerator(self.fixed_job_id),
            task_scheduler=self.task_scheduler,
        )
        self.client = TestClient(create_api_app(container=self.container))
        self.addCleanup(self.client.close)

    def test_source_route_supports_full_content_and_valid_ranges(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="source-range.pdf", total_pages=2)

        full_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/source")
        self.assertEqual(200, full_resp.status_code)
        self.assertTrue(full_resp.headers["content-type"].startswith("application/pdf"))
        self.assertEqual("bytes", full_resp.headers.get("accept-ranges"))
        full_pdf_bytes = full_resp.content
        self.assertGreater(len(full_pdf_bytes), 16)

        range_resp = self.client.get(
            f"/api/jobs/{self.fixed_job_id}/source",
            headers={"Range": "bytes=0-9"},
        )
        self.assertEqual(206, range_resp.status_code)
        self.assertEqual("bytes", range_resp.headers.get("accept-ranges"))
        self.assertEqual(f"bytes 0-9/{len(full_pdf_bytes)}", range_resp.headers.get("content-range"))
        self.assertEqual(full_pdf_bytes[0:10], range_resp.content)

        open_range_start = 10
        open_range_resp = self.client.get(
            f"/api/jobs/{self.fixed_job_id}/source",
            headers={"Range": f"bytes={open_range_start}-"},
        )
        self.assertEqual(206, open_range_resp.status_code)
        self.assertEqual(
            f"bytes {open_range_start}-{len(full_pdf_bytes) - 1}/{len(full_pdf_bytes)}",
            open_range_resp.headers.get("content-range"),
        )
        self.assertEqual(full_pdf_bytes[open_range_start:], open_range_resp.content)

    def test_source_route_rejects_invalid_ranges_with_416_and_content_range(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="source-invalid-range.pdf", total_pages=1)

        full_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/source")
        total_size = len(full_resp.content)

        invalid_start_resp = self.client.get(
            f"/api/jobs/{self.fixed_job_id}/source",
            headers={"Range": f"bytes={total_size}-"},
        )
        self.assertEqual(416, invalid_start_resp.status_code)
        self.assertEqual("bytes", invalid_start_resp.headers.get("accept-ranges"))
        self.assertEqual(f"bytes */{total_size}", invalid_start_resp.headers.get("content-range"))

        malformed_resp = self.client.get(
            f"/api/jobs/{self.fixed_job_id}/source",
            headers={"Range": "bytes=abc-def"},
        )
        self.assertEqual(416, malformed_resp.status_code)
        self.assertEqual("bytes", malformed_resp.headers.get("accept-ranges"))
        self.assertEqual(f"bytes */{total_size}", malformed_resp.headers.get("content-range"))

    def test_source_route_streams_chunks_without_full_read(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="source-streamed.pdf", total_pages=2)

        read_sizes: list[int] = []
        source_store = self.container.source_store
        original_open_read = source_store.open_read

        def guarded_open_read(job_id: UUID):  # type: ignore[no-untyped-def]
            return _NoWholeReadStream(original_open_read(job_id), read_sizes)

        source_store.open_read = guarded_open_read  # type: ignore[method-assign]
        self.addCleanup(setattr, source_store, "open_read", original_open_read)

        full_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/source")
        self.assertEqual(200, full_resp.status_code)
        self.assertGreater(len(full_resp.content), 16)

        range_resp = self.client.get(
            f"/api/jobs/{self.fixed_job_id}/source",
            headers={"Range": "bytes=0-15"},
        )
        self.assertEqual(206, range_resp.status_code)
        self.assertEqual(16, len(range_resp.content))

        self.assertGreaterEqual(len(read_sizes), 2)
        self.assertTrue(all(size > 0 for size in read_sizes), f"unexpected read sizes: {read_sizes}")

    def test_source_route_maps_open_read_failure_before_stream_starts(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="source-open-failure.pdf", total_pages=1)

        source_store = self.container.source_store
        original_open_read = source_store.open_read

        def broken_open_read(job_id: UUID):  # type: ignore[no-untyped-def]
            del job_id
            raise AppError(code=ErrorCode.JOB_NOT_FOUND)

        source_store.open_read = broken_open_read  # type: ignore[method-assign]
        self.addCleanup(setattr, source_store, "open_read", original_open_read)

        full_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/source")
        self.assertEqual(404, full_resp.status_code)
        self.assertEqual("JOB_NOT_FOUND", full_resp.json().get("detail", {}).get("code"))

        range_resp = self.client.get(
            f"/api/jobs/{self.fixed_job_id}/source",
            headers={"Range": "bytes=0-15"},
        )
        self.assertEqual(404, range_resp.status_code)
        self.assertEqual("JOB_NOT_FOUND", range_resp.json().get("detail", {}).get("code"))

    def test_stream_route_replays_sse_events_and_closes_after_terminal_event(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="sse-replay.pdf", total_pages=2)

        now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
        job = self.container.job_repository.get(self.fixed_job_id)
        self.container.job_repository.save(
            replace(
                job,
                status=JobStatus.EXTRACTED,
                updated_at=now,
                version=job.version + 1,
            )
        )
        self.container.stream_application.publish(
            JobEvent(
                job_id=self.fixed_job_id,
                seq=1,
                event_type=EventType.PAGE_PROCESSED,
                payload={
                    "page_num": 1,
                    "status": "done",
                    "processed_count": 1,
                    "total_pages": 2,
                },
                created_at=now,
            )
        )
        self.container.stream_application.publish(
            JobEvent(
                job_id=self.fixed_job_id,
                seq=2,
                event_type=EventType.EXTRACTION_COMPLETED,
                payload={
                    "processed_count": 2,
                    "total_pages": 2,
                    "succeeded_pages": [1, 2],
                    "failed_pages": [],
                },
                created_at=now,
            )
        )

        with self.client.stream("GET", f"/api/jobs/{self.fixed_job_id}/stream") as stream_resp:
            self.assertEqual(200, stream_resp.status_code)
            self.assertTrue(stream_resp.headers["content-type"].startswith("text/event-stream"))
            payloads = self._collect_sse_payloads(stream_resp)

        self.assertEqual(
            [
                {
                    "type": "page",
                    "page_num": 1,
                    "status": "done",
                    "processed_count": 1,
                    "total_pages": 2,
                },
                {
                    "type": "complete",
                    "processed_count": 2,
                    "total_pages": 2,
                    "succeeded_pages": [1, 2],
                    "failed_pages": [],
                },
            ],
            payloads,
        )

    def test_spa_fallback_routes_are_accessible(self) -> None:
        root_resp = self.client.get("/")
        self.assertEqual(200, root_resp.status_code)
        self.assertTrue(root_resp.headers["content-type"].startswith("text/html"))
        self.assertIn("<html", root_resp.text.lower())

        workspace_resp = self.client.get(f"/jobs/{self.fixed_job_id}")
        self.assertEqual(200, workspace_resp.status_code)
        self.assertTrue(workspace_resp.headers["content-type"].startswith("text/html"))
        self.assertIn("<html", workspace_resp.text.lower())

    def _create_job(self, *, pdf_name: str, total_pages: int) -> None:
        create_resp = self.client.post(
            "/api/jobs",
            files={
                "file": (
                    pdf_name,
                    _build_pdf_bytes(total_pages=total_pages),
                    "application/pdf",
                )
            },
        )
        self.assertEqual(200, create_resp.status_code)
        self.assertEqual(
            {
                "job_id": str(self.fixed_job_id),
                "total_pages": total_pages,
                "status": "extracting",
            },
            create_resp.json(),
        )

    def _update_config_with_api_key(self) -> None:
        update_resp = self.client.put(
            "/api/config",
            json={
                "model": {
                    "name": "vision-http",
                    "timeout": 60,
                },
                "extract": {
                    "dpi": 180,
                    "concurrency": 4,
                    "max_retries": 2,
                    "prompt": "请严格提取 Markdown",
                },
                "api_key": "step22-secret",
            },
        )
        self.assertEqual(200, update_resp.status_code)
        self.assertTrue(update_resp.json()["has_api_key"])

    def _collect_sse_payloads(self, stream_resp) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
        payloads: list[dict[str, object]] = []
        for raw_line in stream_resp.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line or not line.startswith("data: "):
                continue
            payloads.append(json.loads(line.removeprefix("data: ")))
        return payloads
