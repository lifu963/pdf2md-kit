"""
Step 26: 完成端到端验收与架构终检

验收目标（严格对齐实施步骤）：
1. 真实文件系统 + 真实 HTTP 路由打通主链路：
   上传 PDF -> 提取 -> build -> 获取/编辑 output -> 修改配置。
2. 校验真实产物：state.json / pages/ / events.jsonl / artifacts/output.md，并覆盖任务历史查询/删除。
3. 覆盖非 happy path：SSE 重连回放、单页失败、鉴权失败、损坏事件日志。
4. 验证 SPA 页面具备端到端交互所需的前端控件骨架。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import asyncio
import json
import tempfile
import threading
from typing import Any
from unittest import TestCase
from uuid import UUID

import fitz
from starlette.testclient import TestClient

from backend.api import create_api_app
from backend.api.dependencies import ApiContainer
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


class _ImmediateTaskScheduler:
    """同步执行任务以获得稳定的端到端测试时序。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []
        self.errors: list[Exception] = []

    def schedule(self, *, job_id: UUID, task_name: str, task_factory) -> bool:  # type: ignore[no-untyped-def]
        self.calls.append((job_id, task_name))
        try:
            asyncio.get_running_loop()
            running_loop = True
        except RuntimeError:
            running_loop = False

        if running_loop:
            run_error: Exception | None = None

            def _run_in_thread() -> None:
                nonlocal run_error
                try:
                    asyncio.run(task_factory())
                except Exception as exc:  # noqa: BLE001
                    run_error = exc

            worker = threading.Thread(target=_run_in_thread, daemon=True)
            worker.start()
            worker.join()
            if run_error is not None:
                self.errors.append(run_error)
            return True

        try:
            asyncio.run(task_factory())
        except Exception as exc:  # noqa: BLE001
            self.errors.append(exc)
        return True


@dataclass(slots=True)
class _Outcome:
    value: str | Exception


class _ScenarioVisionGateway:
    """按页号返回预设结果，便于覆盖并发与非 happy path。"""

    def __init__(self, outcomes: list[str | Exception]) -> None:
        self._outcomes = [_Outcome(item) for item in outcomes]
        self.calls = 0
        self._lock = threading.Lock()

    def extract_markdown(  # type: ignore[no-untyped-def]
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        model,
        api_key: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        del image_bytes, prompt, model, api_key, max_retries
        resolved_page_num = page_num or 1
        with self._lock:
            self.calls += 1
            if 1 <= resolved_page_num <= len(self._outcomes):
                next_outcome = self._outcomes[resolved_page_num - 1].value
            else:
                next_outcome = f"# page {resolved_page_num}"
        if isinstance(next_outcome, Exception):
            raise next_outcome
        return next_outcome

    def open_session(
        self,
        *,
        model,
        api_key: str,
    ) -> "_ScenarioVisionSession":
        return _ScenarioVisionSession(
            gateway=self,
            model=model,
            api_key=api_key,
        )


class _ScenarioVisionSession:
    def __init__(self, *, gateway: _ScenarioVisionGateway, model, api_key: str) -> None:  # type: ignore[no-untyped-def]
        self._gateway = gateway
        self._model = model
        self._api_key = api_key

    def extract_markdown(  # type: ignore[no-untyped-def]
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        max_retries: int,
        page_num: int | None = None,
    ) -> str:
        return self._gateway.extract_markdown(
            image_bytes=image_bytes,
            prompt=prompt,
            model=self._model,
            api_key=self._api_key,
            max_retries=max_retries,
            page_num=page_num,
        )

    def close(self) -> None:
        return


class Step26EndToEndAcceptanceTests(TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)

    def test_main_chain_generates_expected_files_and_sse_reconnect_replays_consistently(self) -> None:
        job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000026")
        harness = _HttpE2EHarness(
            tmp_root=Path(self._tmp_dir.name),
            job_id=job_id,
            vision_outcomes=["# 第 1 页", "# 第 2 页"],
        )
        client = harness.client

        harness.update_config_with_api_key()

        create_resp = client.post(
            "/api/jobs",
            files={"file": ("main-chain.pdf", _build_pdf_bytes(total_pages=2), "application/pdf")},
        )
        self.assertEqual(200, create_resp.status_code)
        self.assertEqual("extracting", create_resp.json()["status"])

        job_resp = client.get(f"/api/jobs/{job_id}")
        self.assertEqual(200, job_resp.status_code)
        self.assertEqual(
            {
                "job_id": str(job_id),
                "status": "extracted",
                "total_pages": 2,
                "succeeded_pages": [1, 2],
                "failed_pages": [],
                "processed_count": 2,
            },
            job_resp.json(),
        )

        pages_resp = client.get(f"/api/jobs/{job_id}/pages")
        self.assertEqual(200, pages_resp.status_code)
        self.assertEqual(
            [{"page_num": 1, "status": "done"}, {"page_num": 2, "status": "done"}],
            pages_resp.json(),
        )

        page_resp = client.get(f"/api/jobs/{job_id}/pages/1")
        self.assertEqual(200, page_resp.status_code)
        self.assertEqual({"page_num": 1, "status": "done", "content": "# 第 1 页"}, page_resp.json())

        first_replay = harness.read_sse_replay(job_id)
        second_replay = harness.read_sse_replay(job_id)
        self.assertEqual(first_replay, second_replay)
        self.assertEqual("page", first_replay[0]["type"])
        self.assertEqual("complete", first_replay[-1]["type"])

        build_resp = client.post(
            f"/api/jobs/{job_id}/build",
            json={"merge_mode": "separator_with_page_number"},
        )
        self.assertEqual(200, build_resp.status_code)
        self.assertEqual("ready", build_resp.json()["status"])

        output_resp = client.get(f"/api/jobs/{job_id}/output")
        self.assertEqual(200, output_resp.status_code)
        self.assertTrue(output_resp.json()["content"].strip().startswith("--- 第 1 页 ---"))

        save_output_resp = client.put(
            f"/api/jobs/{job_id}/output",
            json={"content": "# 最终版本\n- A\n- B"},
        )
        self.assertEqual(200, save_output_resp.status_code)
        self.assertEqual("# 最终版本\n- A\n- B", save_output_resp.json()["content"])

        discard_resp = client.post(f"/api/jobs/{job_id}/output/discard")
        self.assertEqual(200, discard_resp.status_code)
        self.assertEqual("extracted", discard_resp.json()["status"])

        discarded_output_resp = client.get(f"/api/jobs/{job_id}/output")
        self.assertEqual(409, discarded_output_resp.status_code)
        self.assertEqual("OUTPUT_NOT_READY", discarded_output_resp.json()["detail"]["code"])

        save_page_resp = client.put(
            f"/api/jobs/{job_id}/pages/1",
            json={"content": "# 第 1 页（修订）"},
        )
        self.assertEqual(200, save_page_resp.status_code)
        self.assertEqual("# 第 1 页（修订）", save_page_resp.json()["content"])

        rebuild_resp = client.post(f"/api/jobs/{job_id}/build")
        self.assertEqual(200, rebuild_resp.status_code)
        self.assertEqual("ready", rebuild_resp.json()["status"])

        rebuilt_output_resp = client.get(f"/api/jobs/{job_id}/output")
        self.assertEqual(200, rebuilt_output_resp.status_code)
        self.assertEqual("# 第 1 页（修订）\n\n# 第 2 页", rebuilt_output_resp.json()["content"])

        source_resp = client.get(f"/api/jobs/{job_id}/source")
        self.assertEqual(200, source_resp.status_code)
        self.assertEqual("bytes", source_resp.headers.get("accept-ranges"))

        range_resp = client.get(
            f"/api/jobs/{job_id}/source",
            headers={"Range": "bytes=0-15"},
        )
        self.assertEqual(206, range_resp.status_code)
        self.assertEqual("bytes", range_resp.headers.get("accept-ranges"))

        config_resp = client.get("/api/config")
        self.assertEqual(200, config_resp.status_code)
        self.assertTrue(config_resp.json()["has_api_key"])
        self.assertEqual("vision-e2e", config_resp.json()["model"]["name"])

        reset_config_resp = client.post("/api/config/reset")
        self.assertEqual(200, reset_config_resp.status_code)
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
            reset_config_resp.json(),
        )

        # 文件系统产物校验
        self.assertTrue(harness.state_path.exists(), "state.json 必须存在")
        self.assertTrue(harness.events_path.exists(), "events.jsonl 必须存在")
        self.assertTrue(harness.output_path.exists(), "artifacts/output.md 必须存在")
        self.assertTrue(harness.source_path.exists(), "source.pdf 必须存在")

        state = json.loads(harness.state_path.read_text(encoding="utf-8"))
        self.assertEqual("ready", state["status"])
        self.assertEqual([1, 2], state["succeeded_pages"])

        page_1_meta = json.loads((harness.pages_dir / "page_001.meta.json").read_text(encoding="utf-8"))
        page_2_meta = json.loads((harness.pages_dir / "page_002.meta.json").read_text(encoding="utf-8"))
        self.assertEqual("done", page_1_meta["status"])
        self.assertEqual("done", page_2_meta["status"])
        self.assertEqual("# 第 1 页（修订）", (harness.pages_dir / "page_001.md").read_text(encoding="utf-8"))

        saved_output = harness.output_path.read_text(encoding="utf-8")
        self.assertEqual("# 第 1 页（修订）\n\n# 第 2 页", saved_output)

        event_lines = [line for line in harness.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        event_payloads = [json.loads(line)["payload"]["type"] for line in event_lines]
        self.assertIn("complete", event_payloads)

    def test_non_happy_paths_single_page_failure_auth_failed_and_corrupted_events_log(self) -> None:
        # 单页失败继续
        failure_job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000261")
        failure_harness = _HttpE2EHarness(
            tmp_root=Path(self._tmp_dir.name) / "failure",
            job_id=failure_job_id,
            vision_outcomes=[
                "# 第 1 页",
                AppError(code=ErrorCode.LLM_TIMEOUT, message="page-2 timeout"),
                "# 第 3 页",
            ],
        )
        failure_harness.update_config_with_api_key()
        create_failure_job = failure_harness.client.post(
            "/api/jobs",
            files={"file": ("failure-case.pdf", _build_pdf_bytes(total_pages=3), "application/pdf")},
        )
        self.assertEqual(200, create_failure_job.status_code)

        failure_job = failure_harness.client.get(f"/api/jobs/{failure_job_id}")
        self.assertEqual(200, failure_job.status_code)
        self.assertEqual("extracting", failure_job.json()["status"])
        self.assertEqual([1, 3], failure_job.json()["succeeded_pages"])
        self.assertEqual([2], failure_job.json()["failed_pages"])

        page_two = failure_harness.client.get(f"/api/jobs/{failure_job_id}/pages/2")
        self.assertEqual(200, page_two.status_code)
        self.assertEqual("failed", page_two.json()["status"])
        self.assertEqual("page-2 timeout", page_two.json()["error"])

        # 鉴权失败终止后续页面
        auth_job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000262")
        auth_harness = _HttpE2EHarness(
            tmp_root=Path(self._tmp_dir.name) / "auth-failed",
            job_id=auth_job_id,
            vision_outcomes=[
                AppError(code=ErrorCode.LLM_AUTH_FAILED, message="auth denied"),
                "# 不应执行",
            ],
        )
        auth_harness.update_config_with_api_key()
        create_auth_job = auth_harness.client.post(
            "/api/jobs",
            files={"file": ("auth-failed.pdf", _build_pdf_bytes(total_pages=2), "application/pdf")},
        )
        self.assertEqual(200, create_auth_job.status_code)

        auth_job = auth_harness.client.get(f"/api/jobs/{auth_job_id}")
        self.assertEqual(200, auth_job.status_code)
        self.assertEqual("failed", auth_job.json()["status"])

        auth_events = auth_harness.read_sse_replay(auth_job_id)
        self.assertEqual("failed", auth_events[-1]["type"])
        self.assertIn("auth denied", auth_events[-1]["detail"])

        # events.jsonl 损坏后 stream 返回 STATE_CORRUPTED
        auth_harness.events_path.write_text("{not-json}\n", encoding="utf-8")
        corrupted_stream_resp = auth_harness.client.get(f"/api/jobs/{auth_job_id}/stream")
        self.assertEqual(500, corrupted_stream_resp.status_code)
        self.assertEqual("STATE_CORRUPTED", corrupted_stream_resp.json()["detail"]["code"])

    def test_history_routes_reopen_old_job_and_delete_local_files(self) -> None:
        shared_root = Path(self._tmp_dir.name) / "history"
        first_job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000264")
        second_job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000265")
        first_harness = _HttpE2EHarness(
            tmp_root=shared_root,
            job_id=first_job_id,
            vision_outcomes=["# 第一份 PDF"],
        )
        second_harness = _HttpE2EHarness(
            tmp_root=shared_root,
            job_id=second_job_id,
            vision_outcomes=["# 第二份 PDF 第 1 页", "# 第二份 PDF 第 2 页"],
        )

        first_harness.update_config_with_api_key()

        create_first = first_harness.client.post(
            "/api/jobs",
            files={"file": ("older-history.pdf", _build_pdf_bytes(total_pages=1), "application/pdf")},
        )
        self.assertEqual(200, create_first.status_code)
        build_first = first_harness.client.post(f"/api/jobs/{first_job_id}/build")
        self.assertEqual(200, build_first.status_code)
        self.assertEqual("ready", build_first.json()["status"])

        create_second = second_harness.client.post(
            "/api/jobs",
            files={"file": ("newer-history.pdf", _build_pdf_bytes(total_pages=2), "application/pdf")},
        )
        self.assertEqual(200, create_second.status_code)

        history_resp = second_harness.client.get("/api/jobs")
        self.assertEqual(200, history_resp.status_code)
        history_by_id = {item["job_id"]: item for item in history_resp.json()}
        self.assertEqual("older-history.pdf", history_by_id[str(first_job_id)]["pdf_name"])
        self.assertEqual("ready", history_by_id[str(first_job_id)]["status"])
        self.assertEqual("newer-history.pdf", history_by_id[str(second_job_id)]["pdf_name"])
        self.assertEqual("extracted", history_by_id[str(second_job_id)]["status"])

        reopen_old_job = second_harness.client.get(f"/api/jobs/{first_job_id}")
        self.assertEqual(200, reopen_old_job.status_code)
        self.assertEqual("ready", reopen_old_job.json()["status"])

        old_workspace_html = second_harness.client.get(f"/jobs/{first_job_id}")
        self.assertEqual(200, old_workspace_html.status_code)
        self.assertIn('id="history-list"', old_workspace_html.text)

        delete_old_job = second_harness.client.delete(f"/api/jobs/{first_job_id}")
        self.assertEqual(204, delete_old_job.status_code)
        self.assertFalse(first_harness.job_dir.exists(), "删除历史任务后必须清理整个 job 目录")

        remaining_history = second_harness.client.get("/api/jobs")
        self.assertEqual(200, remaining_history.status_code)
        self.assertEqual([str(second_job_id)], [item["job_id"] for item in remaining_history.json()])

    def test_spa_html_contains_end_to_end_controls(self) -> None:
        job_id = UUID("aaaaaaaa-bbbb-cccc-dddd-000000000263")
        harness = _HttpE2EHarness(
            tmp_root=Path(self._tmp_dir.name) / "frontend",
            job_id=job_id,
            vision_outcomes=["# page 1"],
        )

        root_html = harness.client.get("/")
        self.assertEqual(200, root_html.status_code)
        self.assertIn('id="config-form"', root_html.text)
        self.assertIn('id="restore-initial-config-btn"', root_html.text)
        self.assertIn("恢复默认设置", root_html.text)
        self.assertNotIn('id="cfg-build-max-retries"', root_html.text)
        self.assertNotIn('id="cfg-build-prompt"', root_html.text)
        self.assertIn('id="pdf-pane"', root_html.text)
        self.assertIn('id="single-page-test-btn"', root_html.text)
        self.assertIn('id="start-extraction-btn"', root_html.text)
        self.assertIn('id="build-output-btn"', root_html.text)
        self.assertIn('id="build-output-menu"', root_html.text)
        self.assertIn('id="output-editor"', root_html.text)
        self.assertIn('id="toggle-history-btn"', root_html.text)
        self.assertIn('id="history-list"', root_html.text)
        self.assertIn('id="events-log"', root_html.text)

        workspace_html = harness.client.get(f"/jobs/{job_id}")
        self.assertEqual(200, workspace_html.status_code)
        self.assertIn('id="app-root"', workspace_html.text)
        self.assertIn("window.__pdfKnowledgeBaseApp", workspace_html.text)


class _HttpE2EHarness:
    def __init__(self, *, tmp_root: Path, job_id: UUID, vision_outcomes: list[str | Exception]) -> None:
        self.job_id = job_id
        self.project_root = tmp_root
        self.data_root = self.project_root / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        frontend_dir = self.project_root / "frontend"
        frontend_dir.mkdir(parents=True, exist_ok=True)
        repo_root = Path(__file__).resolve().parents[2]
        frontend_index = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
        (frontend_dir / "index.html").write_text(frontend_index, encoding="utf-8")
        (self.project_root / "config.yaml").write_text(_template_config_text(), encoding="utf-8")

        scheduler = _ImmediateTaskScheduler()
        vision_gateway = _ScenarioVisionGateway(vision_outcomes)

        self.container = ApiContainer(
            project_root=self.project_root,
            data_root=self.data_root,
            id_generator=_FixedIdGenerator(self.job_id),
            task_scheduler=scheduler,
            vision_gateway=vision_gateway,
        )
        self.client = TestClient(create_api_app(container=self.container))

        self.scheduler = scheduler
        self.vision_gateway = vision_gateway

        self.job_dir = self.data_root / str(self.job_id)
        self.state_path = self.job_dir / "state.json"
        self.events_path = self.job_dir / "events.jsonl"
        self.pages_dir = self.job_dir / "pages"
        self.output_path = self.job_dir / "artifacts" / "output.md"
        self.source_path = self.job_dir / "source.pdf"

    def update_config_with_api_key(self) -> None:
        resp = self.client.put(
            "/api/config",
            json={
                "model": {
                    "name": "vision-e2e",
                    "timeout": 60,
                },
                "extract": {
                    "dpi": 180,
                    "concurrency": 2,
                    "max_retries": 1,
                    "prompt": "请提取成 Markdown",
                },
                "api_key": "step26-key",
            },
        )
        if resp.status_code != 200:
            raise AssertionError(f"update config failed: {resp.status_code} {resp.text}")

    def read_sse_replay(self, job_id: UUID) -> list[dict[str, Any]]:
        with self.client.stream("GET", f"/api/jobs/{job_id}/stream") as response:
            if response.status_code != 200:
                raise AssertionError(f"sse stream failed: {response.status_code} {response.text}")
            payloads: list[dict[str, Any]] = []
            for raw_line in response.iter_lines():
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line or not line.startswith("data: "):
                    continue
                payloads.append(json.loads(line.removeprefix("data: ")))
            return payloads

