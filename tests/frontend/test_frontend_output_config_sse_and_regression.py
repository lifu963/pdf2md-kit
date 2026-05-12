"""
Step 25: 实现 output-editor、config-panel、SSE 联动与 building 恢复，并完成前端回归

验收目标（严格对齐实施步骤）：
1. `ready` 时 output 可显示与保存，非 `ready` 时不可编辑。
2. 配置面板可加载/保存，API Key 只写不回显。
3. `type=page` 事件命中当前页时，先覆盖摘要再重拉正文；未命中时只更新摘要。
4. SSE replay 不重复累计进度。
5. `building` 刷新恢复后可转 `ready` 并切换到 output 工作区。
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]


def _run_node_script(script: str) -> dict[str, object]:
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Node 断言脚本失败:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    payload = result.stdout.strip()
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Node 输出不是 JSON: {payload}") from exc


class Step25FrontendOutputConfigSseAndRegressionTests(TestCase):
    def test_output_editor_ready_save_and_non_ready_readonly_behaviors(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const storeModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/store.ts")).href);
const outputModule = await import(pathToFileURL(path.resolve("frontend/src/modules/output-editor/index.ts")).href);

const calls = [];
const store = storeModule.createWorkspaceStore({
  job_id: "job-25-output",
  workspace_mode: "output",
  is_building_busy: false,
});

const controller = outputModule.createOutputPaneController("job-25-output", {
  workspaceStore: store,
  jobApiClient: {
    async saveOutput(jobId, content) {
      calls.push(`saveOutput:${jobId}:${content}`);
      return {
        content: `${content}\\n<!-- saved -->`,
        updated_at: "2026-04-08T10:01:00Z",
      };
    },
    getOutputDownloadUrl(jobId) {
      return `/api/jobs/${jobId}/output/download`;
    },
  },
});

const readyJob = {
  job_id: "job-25-output",
  status: "ready",
  total_pages: 3,
  succeeded_pages: [1, 2, 3],
  failed_pages: [],
  processed_count: 3,
};
const readyOutput = {
  content: "# merged output",
  updated_at: "2026-04-08T10:00:00Z",
};

const readyView = controller.getViewModel(readyJob, readyOutput);
const saved = await controller.save(readyJob, "# revised output");

const extractedJob = {
  ...readyJob,
  status: "extracted",
};
const nonReadyView = controller.getViewModel(extractedJob, readyOutput);

store.setState({ is_building_busy: true });
const busyReadyView = controller.getViewModel(readyJob, readyOutput);

let nonReadySaveError = null;
try {
  await controller.save(extractedJob, "# should fail");
} catch (error) {
  nonReadySaveError = String(error);
}

console.log(
  JSON.stringify({
    calls,
    readyView,
    saved,
    nonReadyView,
    busyReadyView,
    nonReadySaveError,
  }),
);
"""
        )
        self.assertEqual("# merged output", result["readyView"]["content"])
        self.assertTrue(result["readyView"]["can_edit"])
        self.assertTrue(result["readyView"]["can_save"])
        self.assertEqual(
            "/api/jobs/job-25-output/output/download",
            result["readyView"]["download_url"],
        )
        self.assertEqual("# revised output\n<!-- saved -->", result["saved"]["content"])
        self.assertFalse(result["nonReadyView"]["can_edit"])
        self.assertFalse(result["nonReadyView"]["can_save"])
        self.assertFalse(result["busyReadyView"]["can_save"])
        self.assertEqual(
            ["saveOutput:job-25-output:# revised output"],
            result["calls"],
        )
        self.assertIn("unavailable", result["nonReadySaveError"])

    def test_config_panel_load_save_test_and_api_key_write_only(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const configModule = await import(pathToFileURL(path.resolve("frontend/src/modules/config-panel/index.ts")).href);

const updatePayloads = [];
const testCalls = [];
const resetCalls = [];
const templateConfig = {
  model: {
    name: "vision-lite",
    timeout: 60,
  },
  extract: {
    dpi: 180,
    concurrency: 4,
    max_retries: 2,
    prompt: "initial prompt",
  },
};
let currentConfig = {
  model: structuredClone(templateConfig.model),
  extract: structuredClone(templateConfig.extract),
  has_api_key: false,
};

const controller = configModule.createConfigPanelController({
  configApiClient: {
    async getPublicConfig() {
      return structuredClone(currentConfig);
    },
    async updateConfig(payload) {
      updatePayloads.push(structuredClone(payload));
      currentConfig = {
        model: structuredClone(payload.model),
        extract: structuredClone(payload.extract),
        has_api_key: currentConfig.has_api_key || Boolean(payload.api_key),
      };
      return structuredClone(currentConfig);
    },
    async restoreInitialConfig() {
      resetCalls.push("restoreInitialConfig");
      currentConfig = {
        model: structuredClone(templateConfig.model),
        extract: structuredClone(templateConfig.extract),
        has_api_key: currentConfig.has_api_key,
      };
      return structuredClone(currentConfig);
    },
    async testConnection() {
      testCalls.push("testConnection");
      return {
        ok: true,
        message: "LLM API 响应正常",
        reply_preview: "OK from test",
      };
    },
  },
});

const firstLoad = await controller.load();
const afterFirstSave = await controller.save({
  model: {
    name: "vision-pro",
    timeout: 90,
  },
  extract: {
    dpi: 220,
    concurrency: 2,
    max_retries: 3,
    prompt: "updated prompt",
  },
  api_key: "secret-value",
});

const afterBlankApiKeySave = await controller.save({
  model: {
    name: "vision-pro",
    timeout: 90,
  },
  extract: {
    dpi: 240,
    concurrency: 2,
    max_retries: 3,
    prompt: "second prompt",
  },
  api_key: "   ",
});
const saveAndTestResult = await controller.saveAndTest({
  model: {
    name: "vision-pro",
    timeout: 90,
  },
  extract: {
    dpi: 260,
    concurrency: 2,
    max_retries: 3,
    prompt: "probe prompt",
  },
  api_key: " next-secret ",
});

const secondPayloadHasApiKey = Object.prototype.hasOwnProperty.call(updatePayloads[1], "api_key");
const thirdPayloadHasApiKey = Object.prototype.hasOwnProperty.call(updatePayloads[2], "api_key");
const restored = await controller.restoreInitial();
const reloaded = await controller.load();

console.log(
  JSON.stringify({
    firstLoad,
    afterFirstSave,
    afterBlankApiKeySave,
    saveAndTestResult,
    restored,
    reloaded,
    updatePayloads,
    secondPayloadHasApiKey,
    thirdPayloadHasApiKey,
    testCalls,
    resetCalls,
    cachedView: controller.getViewModel(),
  }),
);
"""
        )
        self.assertFalse(result["firstLoad"]["has_api_key"])
        self.assertEqual("", result["firstLoad"]["api_key"])
        self.assertTrue(result["afterFirstSave"]["has_api_key"])
        self.assertEqual("", result["afterFirstSave"]["api_key"])
        self.assertFalse(result["secondPayloadHasApiKey"])
        self.assertTrue(result["thirdPayloadHasApiKey"])
        self.assertEqual("LLM API 响应正常", result["saveAndTestResult"]["connection_test"]["message"])
        self.assertEqual("OK from test", result["saveAndTestResult"]["connection_test"]["reply_preview"])
        self.assertEqual(["testConnection"], result["testCalls"])
        self.assertEqual(["restoreInitialConfig"], result["resetCalls"])
        self.assertEqual(180, result["restored"]["extract"]["dpi"])
        self.assertEqual(180, result["reloaded"]["extract"]["dpi"])
        self.assertTrue(result["reloaded"]["has_api_key"])
        self.assertEqual("", result["reloaded"]["api_key"])
        self.assertEqual("", result["cachedView"]["api_key"])

    def test_current_page_sse_event_updates_summary_before_reloading_current_page(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const calls = [];
let capturedSubscriber = null;
let pageLoadCount = 0;
let summaryStatusDuringRefetch = null;
let controller = null;

const store = workspaceModule.createWorkspaceStore({ current_page_num: 1 });

controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery(jobId) {
      return {
        queryKey: ["job", jobId],
        async queryFn() {
          calls.push(`getJob:${jobId}`);
          return {
            job_id: jobId,
            status: "extracting",
            total_pages: 2,
            succeeded_pages: [],
            failed_pages: [],
            processed_count: 0,
          };
        },
      };
    },
    usePagesQuery(jobId) {
      return {
        queryKey: ["job-pages", jobId],
        async queryFn() {
          calls.push(`listPages:${jobId}`);
          return [
            { page_num: 1, status: "extracting" },
            { page_num: 2, status: "pending" },
          ];
        },
      };
    },
    usePageQuery(jobId, pageNum) {
      return {
        queryKey: ["job-page", jobId, pageNum],
        async queryFn() {
          calls.push(`getPage:${jobId}:${pageNum}`);
          pageLoadCount += 1;
          if (pageLoadCount > 1 && controller) {
            summaryStatusDuringRefetch = controller
              .getSnapshot()
              .pages.find((page) => page.page_num === pageNum)?.status ?? null;
          }
          return pageLoadCount === 1
            ? { page_num: 1, status: "extracting" }
            : { page_num: 1, status: "done", content: "# refreshed current page" };
        },
      };
    },
    useOutputQuery(jobId) {
      return {
        queryKey: ["job-output", jobId],
        async queryFn() {
          calls.push(`getOutput:${jobId}`);
          return { content: "", updated_at: "2026-04-08T00:00:00Z" };
        },
      };
    },
  },
  streamClient: {
    subscribeJobEvents(jobId, subscriber) {
      calls.push(`subscribe:${jobId}`);
      capturedSubscriber = subscriber;
      return {
        close() {
          calls.push(`closeStream:${jobId}`);
        },
      };
    },
  },
});

await controller.bootstrap("job-25-sse-current");
await capturedSubscriber.onEvent({
  type: "page",
  page_num: 1,
  status: "done",
  processed_count: 1,
  total_pages: 2,
});

console.log(
  JSON.stringify({
    calls,
    summaryStatusDuringRefetch,
    snapshot: controller.getSnapshot(),
  }),
);
"""
        )
        self.assertEqual(
            [
                "getJob:job-25-sse-current",
                "listPages:job-25-sse-current",
                "getPage:job-25-sse-current:1",
                "subscribe:job-25-sse-current",
                "getPage:job-25-sse-current:1",
            ],
            result["calls"],
        )
        self.assertEqual("done", result["summaryStatusDuringRefetch"])
        self.assertEqual("done", result["snapshot"]["pages"][0]["status"])
        self.assertEqual("# refreshed current page", result["snapshot"]["current_page"]["content"])
        self.assertEqual(1, result["snapshot"]["job"]["processed_count"])
        self.assertEqual([1], result["snapshot"]["job"]["succeeded_pages"])

    def test_non_current_page_sse_replay_updates_summary_only_without_duplicate_progress(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const calls = [];
let capturedSubscriber = null;

const store = workspaceModule.createWorkspaceStore({ current_page_num: 1 });
const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery(jobId) {
      return {
        queryKey: ["job", jobId],
        async queryFn() {
          calls.push(`getJob:${jobId}`);
          return {
            job_id: jobId,
            status: "extracting",
            total_pages: 3,
            succeeded_pages: [],
            failed_pages: [],
            processed_count: 0,
          };
        },
      };
    },
    usePagesQuery(jobId) {
      return {
        queryKey: ["job-pages", jobId],
        async queryFn() {
          calls.push(`listPages:${jobId}`);
          return [
            { page_num: 1, status: "extracting" },
            { page_num: 2, status: "pending" },
            { page_num: 3, status: "pending" },
          ];
        },
      };
    },
    usePageQuery(jobId, pageNum) {
      return {
        queryKey: ["job-page", jobId, pageNum],
        async queryFn() {
          calls.push(`getPage:${jobId}:${pageNum}`);
          return { page_num: pageNum, status: "extracting" };
        },
      };
    },
    useOutputQuery(jobId) {
      return {
        queryKey: ["job-output", jobId],
        async queryFn() {
          calls.push(`getOutput:${jobId}`);
          return { content: "", updated_at: "2026-04-08T00:00:00Z" };
        },
      };
    },
  },
  streamClient: {
    subscribeJobEvents(jobId, subscriber) {
      calls.push(`subscribe:${jobId}`);
      capturedSubscriber = subscriber;
      return {
        close() {
          calls.push(`closeStream:${jobId}`);
        },
      };
    },
  },
});

await controller.bootstrap("job-25-sse-replay");
const replayEvent = {
  type: "page",
  page_num: 2,
  status: "failed",
  processed_count: 1,
  total_pages: 3,
  error: "timeout",
};

await capturedSubscriber.onEvent(replayEvent);
const afterFirstReplay = controller.getSnapshot();
await capturedSubscriber.onEvent(replayEvent);
const afterSecondReplay = controller.getSnapshot();

console.log(
  JSON.stringify({
    calls,
    afterFirstReplay,
    afterSecondReplay,
  }),
);
"""
        )
        self.assertEqual(
            [
                "getJob:job-25-sse-replay",
                "listPages:job-25-sse-replay",
                "getPage:job-25-sse-replay:1",
                "subscribe:job-25-sse-replay",
            ],
            result["calls"],
        )
        self.assertEqual("failed", result["afterFirstReplay"]["pages"][1]["status"])
        self.assertEqual("failed", result["afterSecondReplay"]["pages"][1]["status"])
        self.assertEqual(1, result["afterFirstReplay"]["job"]["processed_count"])
        self.assertEqual(1, result["afterSecondReplay"]["job"]["processed_count"])
        self.assertEqual([2], result["afterSecondReplay"]["job"]["failed_pages"])
        self.assertEqual(1, result["afterSecondReplay"]["current_page"]["page_num"])

    def test_building_recovery_to_ready_switches_to_output_workspace_and_loads_output(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const calls = [];
const statuses = ["building", "building", "ready"];
const store = workspaceModule.createWorkspaceStore({ current_page_num: 2 });

const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery(jobId) {
      return {
        queryKey: ["job", jobId],
        async queryFn() {
          calls.push(`getJob:${jobId}`);
          const status = statuses.shift() ?? "ready";
          return {
            job_id: jobId,
            status,
            total_pages: 2,
            succeeded_pages: status === "ready" ? [1, 2] : [1],
            failed_pages: [],
            processed_count: status === "ready" ? 2 : 1,
          };
        },
      };
    },
    usePagesQuery(jobId) {
      return {
        queryKey: ["job-pages", jobId],
        async queryFn() {
          calls.push(`listPages:${jobId}`);
          return [];
        },
      };
    },
    usePageQuery(jobId, pageNum) {
      return {
        queryKey: ["job-page", jobId, pageNum],
        async queryFn() {
          calls.push(`getPage:${jobId}:${pageNum}`);
          return { page_num: pageNum, status: "done", content: "# should not load page" };
        },
      };
    },
    useOutputQuery(jobId) {
      return {
        queryKey: ["job-output", jobId],
        async queryFn() {
          calls.push(`getOutput:${jobId}`);
          return {
            content: "# built output",
            updated_at: "2026-04-08T11:00:00Z",
          };
        },
      };
    },
  },
  streamClient: {
    subscribeJobEvents() {
      throw new Error("building recovery to ready must not create SSE");
    },
  },
  buildingRecovery: {
    async waitForNextPoll() {
      calls.push("waitForNextPoll");
    },
    maxPollAttempts: 5,
  },
});

const snapshot = await controller.bootstrap("job-25-building-ready");
const state = store.getState();

console.log(JSON.stringify({ calls, snapshot, state }));
"""
        )
        self.assertEqual(
            [
                "getJob:job-25-building-ready",
                "waitForNextPoll",
                "getJob:job-25-building-ready",
                "waitForNextPoll",
                "getJob:job-25-building-ready",
                "getOutput:job-25-building-ready",
            ],
            result["calls"],
        )
        self.assertEqual("ready", result["snapshot"]["job"]["status"])
        self.assertEqual("# built output", result["snapshot"]["output_document"]["content"])
        self.assertEqual("output", result["state"]["workspace_mode"])
        self.assertFalse(result["state"]["is_building_busy"])

