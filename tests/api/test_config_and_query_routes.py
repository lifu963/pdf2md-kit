"""
Step 20: 实现配置与查询类路由

验收目标（严格对齐实施步骤）：
1. 打通 `GET/PUT /api/config`、`GET/POST /api/jobs`、`DELETE /api/jobs/{id}`、
   `GET /api/jobs/{id}`、`GET /api/jobs/{id}/pages`、`GET /api/jobs/{id}/pages/{n}`。
2. API 集成测试逐个验证响应状态、响应字段、错误映射。
3. 验证 config 的 write-only 语义（永不回传 api_key 明文）。
4. 验证 create_job 返回值字段稳定，并覆盖任务历史列表/删除语义。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
from unittest import TestCase
from uuid import UUID

import fitz
from starlette.testclient import TestClient

from backend.api import create_api_app
from backend.api.dependencies import ApiContainer
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.shared_kernel.contracts import JobAggregate, JobStatus


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


class _FakeVisionGateway:
    def __init__(self) -> None:
        self.result: str | Exception = "OK from test route"

    def test_connection(self, *, model, api_key) -> str:  # type: ignore[no-untyped-def]
        del model, api_key
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Step20ConfigAndQueryRouteTests(TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)

        project_root = Path(self._tmp_dir.name)
        data_root = project_root / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        (project_root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")

        self.fixed_job_id = UUID("11111111-2222-3333-4444-555555555555")
        self.task_scheduler = _RecordingTaskScheduler()
        self.vision_gateway = _FakeVisionGateway()
        self.container = ApiContainer(
            project_root=project_root,
            data_root=data_root,
            id_generator=_FixedIdGenerator(self.fixed_job_id),
            task_scheduler=self.task_scheduler,
            vision_gateway=self.vision_gateway,
        )
        self.client = TestClient(create_api_app(container=self.container))
        self.addCleanup(self.client.close)

    def test_config_routes_keep_api_key_write_only_and_map_validation_error(self) -> None:
        initial_resp = self.client.get("/api/config")
        self.assertEqual(200, initial_resp.status_code)
        self.assertEqual(
            {
                "model": {
                    "name": "vision-template",
                    "timeout": 30,
                },
                "extract": {
                    "dpi": 150,
                    "concurrency": 2,
                    "max_retries": 1,
                    "prompt": "请提取成 Markdown",
                },
                "has_api_key": False,
            },
            initial_resp.json(),
        )
        self.assertNotIn("api_key", initial_resp.json())

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
                "api_key": "step20-secret",
            },
        )
        self.assertEqual(200, update_resp.status_code)
        self.assertEqual(
            {
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
                "has_api_key": True,
            },
            update_resp.json(),
        )
        self.assertNotIn("api_key", update_resp.json())

        reloaded_resp = self.client.get("/api/config")
        self.assertEqual(200, reloaded_resp.status_code)
        self.assertTrue(reloaded_resp.json()["has_api_key"])
        self.assertNotIn("api_key", reloaded_resp.json())

        invalid_resp = self.client.put(
            "/api/config",
            json={
                "model": {
                    "name": "vision-http",
                    "timeout": 60,
                },
                "extract": {
                    "dpi": 180,
                    "concurrency": 0,
                    "max_retries": 2,
                    "prompt": "请严格提取 Markdown",
                },
            },
        )
        self.assert_api_error(
            invalid_resp,
            status_code=400,
            code="CONFIG_INVALID",
            message="extract.concurrency must be an integer > 0",
        )

    def test_config_reset_route_restores_template_values_and_keeps_api_key_write_only(self) -> None:
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
                "api_key": "step20-secret",
            },
        )
        self.assertEqual(200, update_resp.status_code)
        self.assertTrue(update_resp.json()["has_api_key"])

        reset_resp = self.client.post("/api/config/reset")
        self.assertEqual(200, reset_resp.status_code)
        self.assertEqual(
            {
                "model": {
                    "name": "vision-template",
                    "timeout": 30,
                },
                "extract": {
                    "dpi": 150,
                    "concurrency": 2,
                    "max_retries": 1,
                    "prompt": "请提取成 Markdown",
                },
                "has_api_key": True,
            },
            reset_resp.json(),
        )
        self.assertNotIn("api_key", reset_resp.json())

        reloaded_resp = self.client.get("/api/config")
        self.assertEqual(200, reloaded_resp.status_code)
        self.assertEqual(reset_resp.json(), reloaded_resp.json())
        self.assertNotIn("api_key", reloaded_resp.json())

    def test_test_connection_route_uses_saved_config_and_maps_gateway_errors(self) -> None:
        self.client.put(
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
                "api_key": "step20-secret",
            },
        )

        success_resp = self.client.post("/api/config/test-connection")
        self.assertEqual(200, success_resp.status_code)
        self.assertEqual(
            {
                "ok": True,
                "message": "LLM API 响应正常",
                "reply_preview": "OK from test route",
            },
            success_resp.json(),
        )

        self.vision_gateway.result = AppError(code=ErrorCode.LLM_AUTH_FAILED)
        failure_resp = self.client.post("/api/config/test-connection")
        self.assert_api_error(
            failure_resp,
            status_code=401,
            code="LLM_AUTH_FAILED",
            message="llm auth failed",
        )

    def test_test_connection_route_requires_existing_api_key(self) -> None:
        response = self.client.post("/api/config/test-connection")
        self.assert_api_error(
            response,
            status_code=400,
            code="CONFIG_MISSING_API_KEY",
            message="config missing api key",
        )

    def test_create_job_and_query_routes_return_stable_payloads(self) -> None:
        self.client.put(
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
                "api_key": "step20-secret",
            },
        )

        create_resp = self.client.post(
            "/api/jobs",
            files={
                "file": (
                    "高压课程.pdf",
                    _build_pdf_bytes(total_pages=3),
                    "application/pdf",
                )
            },
        )
        self.assertEqual(200, create_resp.status_code)
        self.assertEqual(
            {
                "job_id": str(self.fixed_job_id),
                "total_pages": 3,
                "status": "extracting",
            },
            create_resp.json(),
        )
        self.assertEqual([(self.fixed_job_id, "extract-all")], self.task_scheduler.calls)

        job_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}")
        self.assertEqual(200, job_resp.status_code)
        self.assertEqual(
            {
                "job_id": str(self.fixed_job_id),
                "status": "extracting",
                "total_pages": 3,
                "succeeded_pages": [],
                "failed_pages": [],
                "processed_count": 0,
            },
            job_resp.json(),
        )

        list_pages_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/pages")
        self.assertEqual(200, list_pages_resp.status_code)
        self.assertEqual(
            [
                {"page_num": 1, "status": "pending"},
                {"page_num": 2, "status": "pending"},
                {"page_num": 3, "status": "pending"},
            ],
            list_pages_resp.json(),
        )

        page_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/pages/1")
        self.assertEqual(200, page_resp.status_code)
        self.assertEqual({"page_num": 1, "status": "pending"}, page_resp.json())
        self.assertNotIn("content", page_resp.json())
        self.assertNotIn("error", page_resp.json())

    def test_query_routes_map_job_and_page_not_found_errors(self) -> None:
        missing_job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        missing_job_resp = self.client.get(f"/api/jobs/{missing_job_id}")
        self.assert_api_error(
            missing_job_resp,
            status_code=404,
            code="JOB_NOT_FOUND",
            message="job not found",
        )

        self.client.put(
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
                "api_key": "step20-secret",
            },
        )
        self.client.post(
            "/api/jobs",
            files={
                "file": (
                    "高压课程.pdf",
                    _build_pdf_bytes(total_pages=1),
                    "application/pdf",
                )
            },
        )
        missing_page_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/pages/9")
        self.assert_api_error(
            missing_page_resp,
            status_code=404,
            code="PAGE_NOT_FOUND",
            message="page not found",
        )

    def test_history_routes_list_in_updated_order_and_delete_terminal_jobs(self) -> None:
        older_job_id = UUID("11111111-2222-3333-4444-000000000020")
        active_job_id = UUID("11111111-2222-3333-4444-000000000021")
        newest_job_id = UUID("11111111-2222-3333-4444-000000000022")
        older_created = datetime(2026, 4, 9, 8, 0, tzinfo=timezone.utc)
        older_updated = datetime(2026, 4, 9, 9, 30, tzinfo=timezone.utc)
        active_created = datetime(2026, 4, 10, 7, 0, tzinfo=timezone.utc)
        active_updated = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
        newest_created = datetime(2026, 4, 11, 9, 0, tzinfo=timezone.utc)
        newest_updated = datetime(2026, 4, 11, 10, 15, tzinfo=timezone.utc)

        self._save_job(
            job_id=older_job_id,
            pdf_name="older.pdf",
            status=JobStatus.READY,
            total_pages=2,
            succeeded_pages=[1, 2],
            created_at=older_created,
            updated_at=older_updated,
        )
        self._save_job(
            job_id=active_job_id,
            pdf_name="running.pdf",
            status=JobStatus.EXTRACTING,
            total_pages=3,
            created_at=active_created,
            updated_at=active_updated,
        )
        self._save_job(
            job_id=newest_job_id,
            pdf_name="summary.pdf",
            status=JobStatus.EXTRACTED,
            total_pages=4,
            succeeded_pages=[1, 2, 3],
            failed_pages=[4],
            created_at=newest_created,
            updated_at=newest_updated,
        )
        marker_path = self.container.workspace.job_dir(newest_job_id) / "marker.txt"
        marker_path.write_text("cleanup-me", encoding="utf-8")

        history_resp = self.client.get("/api/jobs")
        self.assertEqual(200, history_resp.status_code)
        self.assertEqual(
            [
                {
                    "job_id": str(newest_job_id),
                    "pdf_name": "summary.pdf",
                    "status": "extracted",
                    "total_pages": 4,
                    "processed_count": 4,
                    "created_at": newest_created.isoformat().replace("+00:00", "Z"),
                    "updated_at": newest_updated.isoformat().replace("+00:00", "Z"),
                },
                {
                    "job_id": str(active_job_id),
                    "pdf_name": "running.pdf",
                    "status": "extracting",
                    "total_pages": 3,
                    "processed_count": 0,
                    "created_at": active_created.isoformat().replace("+00:00", "Z"),
                    "updated_at": active_updated.isoformat().replace("+00:00", "Z"),
                },
                {
                    "job_id": str(older_job_id),
                    "pdf_name": "older.pdf",
                    "status": "ready",
                    "total_pages": 2,
                    "processed_count": 2,
                    "created_at": older_created.isoformat().replace("+00:00", "Z"),
                    "updated_at": older_updated.isoformat().replace("+00:00", "Z"),
                },
            ],
            history_resp.json(),
        )

        delete_active_resp = self.client.delete(f"/api/jobs/{active_job_id}")
        self.assert_api_error(
            delete_active_resp,
            status_code=409,
            code="JOB_STATUS_CONFLICT",
            message="history deletion is only allowed for inactive jobs",
            details={"status": "extracting"},
        )

        delete_terminal_resp = self.client.delete(f"/api/jobs/{newest_job_id}")
        self.assertEqual(204, delete_terminal_resp.status_code)
        self.assertEqual("", delete_terminal_resp.text)
        self.assertFalse(self.container.workspace.job_dir(newest_job_id).exists())

        refreshed_history = self.client.get("/api/jobs")
        self.assertEqual(
            [str(active_job_id), str(older_job_id)],
            [item["job_id"] for item in refreshed_history.json()],
        )

    def assert_api_error(
        self,
        response,  # type: ignore[no-untyped-def]
        *,
        status_code: int,
        code: str,
        message: str,
        details=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.assertEqual(status_code, response.status_code)
        self.assertEqual(
            {
                "detail": {
                    "code": code,
                    "message": message,
                    "details": details,
                }
            },
            response.json(),
        )

    def _save_job(
        self,
        *,
        job_id: UUID,
        pdf_name: str,
        status: JobStatus,
        total_pages: int,
        created_at: datetime,
        updated_at: datetime,
        succeeded_pages: list[int] | None = None,
        failed_pages: list[int] | None = None,
    ) -> None:
        self.container.job_repository.save(
            JobAggregate(
                job_id=job_id,
                source_pdf_name=pdf_name,
                total_pages=total_pages,
                status=status,
                succeeded_pages=list(succeeded_pages or []),
                failed_pages=list(failed_pages or []),
                created_at=created_at.astimezone(timezone.utc),
                updated_at=updated_at.astimezone(timezone.utc),
                version=1,
                last_error=None,
            )
        )
