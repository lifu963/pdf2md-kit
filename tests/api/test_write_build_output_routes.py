"""
Step 21: 实现写命令、build 与 output 路由

验收目标（严格对齐实施步骤）：
1. 打通 `PUT /api/jobs/{id}/pages/{n}`、`POST /api/jobs/{id}/pages/{n}/retry`、
   `POST /api/jobs/{id}/build`、`GET /api/jobs/{id}/output`、
   `PUT /api/jobs/{id}/output`、`GET /api/jobs/{id}/output/download`。
2. API 集成测试覆盖合法路径与 409 拒绝路径。
3. 重点验证 `ready` 后页面编辑与页面重试被拒绝。
4. 重点验证未构建 output 返回 409，且下载文件名遵守契约。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from unittest import TestCase
from urllib.parse import quote
from uuid import UUID

import fitz
from starlette.testclient import TestClient

from backend.api import create_api_app
from backend.api.dependencies import ApiContainer


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


class Step21WriteBuildOutputRouteTests(TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)

        project_root = Path(self._tmp_dir.name)
        data_root = project_root / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        (project_root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")

        self.fixed_job_id = UUID("66666666-7777-8888-9999-000000000000")
        self.task_scheduler = _RecordingTaskScheduler()
        self.container = ApiContainer(
            project_root=project_root,
            data_root=data_root,
            id_generator=_FixedIdGenerator(self.fixed_job_id),
            task_scheduler=self.task_scheduler,
        )
        self.client = TestClient(create_api_app(container=self.container))
        self.addCleanup(self.client.close)

    def test_write_build_output_routes_success_and_download_filename_contract(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="grid-course.pdf", total_pages=2)

        save_page_1_resp = self.client.put(
            f"/api/jobs/{self.fixed_job_id}/pages/1",
            json={"content": "第一页内容"},
        )
        self.assertEqual(200, save_page_1_resp.status_code)
        self.assertEqual(
            {"page_num": 1, "status": "done", "content": "第一页内容"},
            save_page_1_resp.json(),
        )

        retry_resp = self.client.post(f"/api/jobs/{self.fixed_job_id}/pages/1/retry")
        self.assertEqual(200, retry_resp.status_code)
        self.assertEqual(
            {"job_id": str(self.fixed_job_id), "page_num": 1},
            retry_resp.json(),
        )
        self.assertEqual(
            [
                (self.fixed_job_id, "extract-all"),
                (self.fixed_job_id, "extract-page-1"),
            ],
            self.task_scheduler.calls,
        )

        save_page_1_again_resp = self.client.put(
            f"/api/jobs/{self.fixed_job_id}/pages/1",
            json={"content": "第一页修订"},
        )
        self.assertEqual(200, save_page_1_again_resp.status_code)
        self.assertEqual(
            {"page_num": 1, "status": "done", "content": "第一页修订"},
            save_page_1_again_resp.json(),
        )

        save_page_2_resp = self.client.put(
            f"/api/jobs/{self.fixed_job_id}/pages/2",
            json={"content": "第二页内容"},
        )
        self.assertEqual(200, save_page_2_resp.status_code)
        self.assertEqual(
            {"page_num": 2, "status": "done", "content": "第二页内容"},
            save_page_2_resp.json(),
        )

        job_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}")
        self.assertEqual(200, job_resp.status_code)
        self.assertEqual(
            {
                "job_id": str(self.fixed_job_id),
                "status": "extracted",
                "total_pages": 2,
                "succeeded_pages": [1, 2],
                "failed_pages": [],
                "processed_count": 2,
            },
            job_resp.json(),
        )

        build_resp = self.client.post(f"/api/jobs/{self.fixed_job_id}/build")
        self.assertEqual(200, build_resp.status_code)
        self.assertEqual(
            {
                "status": "ready",
                "output_url": f"/api/jobs/{self.fixed_job_id}/output",
                "download_url": f"/api/jobs/{self.fixed_job_id}/output/download",
            },
            build_resp.json(),
        )

        get_output_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/output")
        self.assertEqual(200, get_output_resp.status_code)
        self.assertIn("第一页修订", get_output_resp.json()["content"])
        self.assertIn("第二页内容", get_output_resp.json()["content"])
        self.assertTrue(get_output_resp.json()["updated_at"].endswith("Z"))

        save_output_resp = self.client.put(
            f"/api/jobs/{self.fixed_job_id}/output",
            json={"content": "# 最终产物\n- A"},
        )
        self.assertEqual(200, save_output_resp.status_code)
        self.assertEqual("# 最终产物\n- A", save_output_resp.json()["content"])
        self.assertTrue(save_output_resp.json()["updated_at"].endswith("Z"))

        reloaded_output_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/output")
        self.assertEqual(200, reloaded_output_resp.status_code)
        self.assertEqual("# 最终产物\n- A", reloaded_output_resp.json()["content"])

        download_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/output/download")
        self.assertEqual(200, download_resp.status_code)
        self.assertEqual("# 最终产物\n- A", download_resp.text)
        self.assertTrue(download_resp.headers["content-type"].startswith("text/markdown; charset=utf-8"))
        self.assert_content_disposition_has_filename(
            download_resp.headers["content-disposition"],
            "grid-course-整理.md",
        )

    def test_ready_state_rejects_page_edit_retry_and_second_build(self) -> None:
        self._prepare_ready_job()

        save_page_resp = self.client.put(
            f"/api/jobs/{self.fixed_job_id}/pages/1",
            json={"content": "ready 后不允许改页"},
        )
        self.assert_api_error(
            save_page_resp,
            status_code=409,
            code="PAGE_EDIT_FORBIDDEN",
            message="pages are frozen in ready state",
            details={"status": "ready"},
        )

        retry_resp = self.client.post(f"/api/jobs/{self.fixed_job_id}/pages/1/retry")
        self.assert_api_error(
            retry_resp,
            status_code=409,
            code="PAGE_RETRY_FORBIDDEN",
            message="pages are frozen in ready state",
            details={"status": "ready"},
        )

        build_again_resp = self.client.post(f"/api/jobs/{self.fixed_job_id}/build")
        self.assert_api_error(
            build_again_resp,
            status_code=409,
            code="JOB_STATUS_CONFLICT",
            message="build is only allowed in extracted state",
            details={"status": "ready"},
        )

    def test_output_routes_return_409_before_build(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="before-build.pdf", total_pages=1)

        get_output_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/output")
        self.assert_api_error(
            get_output_resp,
            status_code=409,
            code="OUTPUT_NOT_READY",
            message="output is not ready in current job state",
            details={"status": "extracting"},
        )

        put_output_resp = self.client.put(
            f"/api/jobs/{self.fixed_job_id}/output",
            json={"content": "not-ready"},
        )
        self.assert_api_error(
            put_output_resp,
            status_code=409,
            code="OUTPUT_EDIT_FORBIDDEN",
            message="output can only be edited in ready state",
            details={"status": "extracting"},
        )

        download_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/output/download")
        self.assert_api_error(
            download_resp,
            status_code=409,
            code="OUTPUT_NOT_READY",
            message="output is not ready in current job state",
            details={"status": "extracting"},
        )

    def test_discard_output_route_returns_to_extracted_and_allows_rebuild(self) -> None:
        self._prepare_ready_job()
        self.client.put(
            f"/api/jobs/{self.fixed_job_id}/output",
            json={"content": "# 人工修订\n- keep? no"},
        )

        discard_resp = self.client.post(f"/api/jobs/{self.fixed_job_id}/output/discard")
        self.assertEqual(200, discard_resp.status_code)
        self.assertEqual(
            {
                "job_id": str(self.fixed_job_id),
                "status": "extracted",
                "total_pages": 2,
                "succeeded_pages": [1, 2],
                "failed_pages": [],
                "processed_count": 2,
            },
            discard_resp.json(),
        )

        get_output_resp = self.client.get(f"/api/jobs/{self.fixed_job_id}/output")
        self.assert_api_error(
            get_output_resp,
            status_code=409,
            code="OUTPUT_NOT_READY",
            message="output is not ready in current job state",
            details={"status": "extracted"},
        )

        save_page_resp = self.client.put(
            f"/api/jobs/{self.fixed_job_id}/pages/1",
            json={"content": "回退后允许改页"},
        )
        self.assertEqual(200, save_page_resp.status_code)
        self.assertEqual("回退后允许改页", save_page_resp.json()["content"])

        rebuild_resp = self.client.post(f"/api/jobs/{self.fixed_job_id}/build")
        self.assertEqual(200, rebuild_resp.status_code)
        self.assertEqual("ready", rebuild_resp.json()["status"])

    def _prepare_ready_job(self) -> None:
        self._update_config_with_api_key()
        self._create_job(pdf_name="ready-case.pdf", total_pages=2)
        self.client.put(f"/api/jobs/{self.fixed_job_id}/pages/1", json={"content": "第一页"})
        self.client.put(f"/api/jobs/{self.fixed_job_id}/pages/2", json={"content": "第二页"})
        build_resp = self.client.post(f"/api/jobs/{self.fixed_job_id}/build")
        self.assertEqual(200, build_resp.status_code)

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
                "api_key": "step21-secret",
            },
        )
        self.assertEqual(200, update_resp.status_code)
        self.assertTrue(update_resp.json()["has_api_key"])

    def assert_content_disposition_has_filename(self, header_value: str, expected_filename: str) -> None:
        normalized = header_value.lower()
        encoded_filename = quote(expected_filename).lower()
        self.assertIn("attachment", normalized)
        self.assertTrue(
            f'filename="{expected_filename}"' in header_value
            or f"filename*=utf-8''{encoded_filename}" in normalized
        )

    def assert_api_error(
        self,
        response,  # type: ignore[no-untyped-def]
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, str] | None = None,
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
