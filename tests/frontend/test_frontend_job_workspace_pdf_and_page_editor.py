"""
Step 24: 实现 job-workspace、pdf-preview 与 page-editor

验收目标（严格对齐实施步骤）：
1. 覆盖 extracting / extracted / ready / building 四种初始状态。
2. 验证启动顺序：job -> pages -> current page，extracting 再建立 SSE，building 先轮询恢复。
3. 验证 PdfPane 文档地址稳定为 `/api/jobs/{id}/source`。
4. 验证切换页码后只重拉目标页正文，不重复拉 pages 摘要。
5. 验证 page-editor 对 pending / extracting / done / failed 的展示语义。
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


class Step24FrontendWorkspacePdfAndPageEditorTests(TestCase):
    def test_extracting_bootstrap_sequence_pdf_url_and_page_switch_reload_only_target_page(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);
const pdfModule = await import(pathToFileURL(path.resolve("frontend/src/modules/pdf-preview/index.ts")).href);

const calls = [];
const store = workspaceModule.createWorkspaceStore({
  current_page_num: 2,
});

const queryHooks = {
  useJobQuery(jobId) {
    return {
      queryKey: ["job", jobId],
      async queryFn() {
        calls.push(`getJob:${jobId}`);
        return {
          job_id: jobId,
          status: "extracting",
          total_pages: 4,
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
          { page_num: 1, status: "pending" },
          { page_num: 2, status: "extracting" },
          { page_num: 3, status: "done" },
          { page_num: 4, status: "failed" },
        ];
      },
    };
  },
  usePageQuery(jobId, pageNum) {
    return {
      queryKey: ["job-page", jobId, pageNum],
      async queryFn() {
        calls.push(`getPage:${jobId}:${pageNum}`);
        return {
          page_num: pageNum,
          status: pageNum === 3 ? "done" : "extracting",
          content: pageNum === 3 ? "# page-3" : undefined,
          error: pageNum === 4 ? "timeout" : undefined,
        };
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
};

const streamClient = {
  subscribeJobEvents(jobId) {
    calls.push(`subscribe:${jobId}`);
    return {
      close() {
        calls.push(`closeStream:${jobId}`);
      },
    };
  },
};

const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks,
  streamClient,
  buildingRecovery: {
    async waitForNextPoll() {
      calls.push("waitForNextPoll");
    },
    maxPollAttempts: 5,
  },
});

const firstSnapshot = await controller.bootstrap("job-24-a");
const pdfPane = pdfModule.createPdfPaneController("job-24-a", {
  jobApiClient: {
    getSourceDocumentUrl(jobId) {
      return `/api/jobs/${jobId}/source`;
    },
  },
  workspaceStore: store,
  onChangePage: (pageNum) => controller.switchPage(pageNum),
});
const beforeSwitch = pdfPane.getViewModel();
await pdfPane.setCurrentPage(3);
const afterSwitch = pdfPane.getViewModel();
const lastSnapshot = controller.getSnapshot();

if (beforeSwitch.document_url !== "/api/jobs/job-24-a/source") {
  throw new Error(`pdf url mismatch before switch: ${beforeSwitch.document_url}`);
}
if (afterSwitch.document_url !== "/api/jobs/job-24-a/source") {
  throw new Error(`pdf url mismatch after switch: ${afterSwitch.document_url}`);
}
if (beforeSwitch.current_page_num !== 2) {
  throw new Error(`expected initial page 2, got ${beforeSwitch.current_page_num}`);
}
if (afterSwitch.current_page_num !== 3) {
  throw new Error(`expected switched page 3, got ${afterSwitch.current_page_num}`);
}
if (!firstSnapshot.is_stream_connected) {
  throw new Error("extracting bootstrap should establish SSE subscription");
}
if (lastSnapshot.current_page?.page_num !== 3) {
  throw new Error(`expected current page to be 3, got ${JSON.stringify(lastSnapshot.current_page)}`);
}

console.log(
  JSON.stringify({
    calls,
    beforeSwitch,
    afterSwitch,
    currentPage: lastSnapshot.current_page,
  }),
);
"""
        )
        self.assertEqual(
            [
                "getJob:job-24-a",
                "listPages:job-24-a",
                "getPage:job-24-a:2",
                "subscribe:job-24-a",
                "getPage:job-24-a:3",
            ],
            result["calls"],
        )
        self.assertEqual("/api/jobs/job-24-a/source", result["beforeSwitch"]["document_url"])
        self.assertEqual("/api/jobs/job-24-a/source", result["afterSwitch"]["document_url"])
        self.assertEqual(3, result["currentPage"]["page_num"])

    def test_extracted_and_ready_bootstrap_behaviors_are_distinct(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const extractedCalls = [];
const extractedStore = workspaceModule.createWorkspaceStore({ current_page_num: 1 });
const extractedController = workspaceModule.createJobWorkspaceController({
  store: extractedStore,
  queryHooks: {
    useJobQuery(jobId) {
      return {
        queryKey: ["job", jobId],
        async queryFn() {
          extractedCalls.push(`getJob:${jobId}`);
          return {
            job_id: jobId,
            status: "extracted",
            total_pages: 2,
            succeeded_pages: [1],
            failed_pages: [2],
            processed_count: 2,
          };
        },
      };
    },
    usePagesQuery(jobId) {
      return {
        queryKey: ["job-pages", jobId],
        async queryFn() {
          extractedCalls.push(`listPages:${jobId}`);
          return [
            { page_num: 1, status: "done" },
            { page_num: 2, status: "failed" },
          ];
        },
      };
    },
    usePageQuery(jobId, pageNum) {
      return {
        queryKey: ["job-page", jobId, pageNum],
        async queryFn() {
          extractedCalls.push(`getPage:${jobId}:${pageNum}`);
          return { page_num: pageNum, status: "done", content: "# page" };
        },
      };
    },
    useOutputQuery(jobId) {
      return {
        queryKey: ["job-output", jobId],
        async queryFn() {
          extractedCalls.push(`getOutput:${jobId}`);
          return { content: "# output", updated_at: "2026-04-08T00:00:00Z" };
        },
      };
    },
  },
  streamClient: {
    subscribeJobEvents() {
      throw new Error("extracted bootstrap must not create SSE subscription");
    },
  },
});

const extractedSnapshot = await extractedController.bootstrap("job-24-b");

const readyCalls = [];
const readyStore = workspaceModule.createWorkspaceStore();
const readyController = workspaceModule.createJobWorkspaceController({
  store: readyStore,
  queryHooks: {
    useJobQuery(jobId) {
      return {
        queryKey: ["job", jobId],
        async queryFn() {
          readyCalls.push(`getJob:${jobId}`);
          return {
            job_id: jobId,
            status: "ready",
            total_pages: 2,
            succeeded_pages: [1, 2],
            failed_pages: [],
            processed_count: 2,
          };
        },
      };
    },
    usePagesQuery(jobId) {
      return {
        queryKey: ["job-pages", jobId],
        async queryFn() {
          readyCalls.push(`listPages:${jobId}`);
          return [];
        },
      };
    },
    usePageQuery(jobId, pageNum) {
      return {
        queryKey: ["job-page", jobId, pageNum],
        async queryFn() {
          readyCalls.push(`getPage:${jobId}:${pageNum}`);
          return { page_num: pageNum, status: "done", content: "" };
        },
      };
    },
    useOutputQuery(jobId) {
      return {
        queryKey: ["job-output", jobId],
        async queryFn() {
          readyCalls.push(`getOutput:${jobId}`);
          return { content: "# output", updated_at: "2026-04-08T01:00:00Z" };
        },
      };
    },
  },
  streamClient: {
    subscribeJobEvents() {
      throw new Error("ready bootstrap must not create SSE subscription");
    },
  },
});

const readySnapshot = await readyController.bootstrap("job-24-c");
const readyState = readyStore.getState();

console.log(
  JSON.stringify({
    extractedCalls,
    extractedSnapshot,
    readyCalls,
    readySnapshot,
    readyState,
  }),
);
"""
        )
        self.assertEqual(
            ["getJob:job-24-b", "listPages:job-24-b", "getPage:job-24-b:1"],
            result["extractedCalls"],
        )
        self.assertEqual(["getJob:job-24-c", "getOutput:job-24-c"], result["readyCalls"])
        self.assertEqual("output", result["readyState"]["workspace_mode"])
        self.assertEqual("# output", result["readySnapshot"]["output_document"]["content"])

    def test_building_bootstrap_polls_until_recovered_then_enters_page_workspace(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const calls = [];
const statuses = ["building", "building", "extracting"];
const store = workspaceModule.createWorkspaceStore({ current_page_num: 2 });

const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery(jobId) {
      return {
        queryKey: ["job", jobId],
        async queryFn() {
          calls.push(`getJob:${jobId}`);
          const status = statuses.shift() ?? "extracting";
          return {
            job_id: jobId,
            status,
            total_pages: 3,
            succeeded_pages: status === "building" ? [1] : [1, 2],
            failed_pages: status === "building" ? [] : [3],
            processed_count: status === "building" ? 1 : 3,
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
            { page_num: 1, status: "done" },
            { page_num: 2, status: "done" },
            { page_num: 3, status: "failed" },
          ];
        },
      };
    },
    usePageQuery(jobId, pageNum) {
      return {
        queryKey: ["job-page", jobId, pageNum],
        async queryFn() {
          calls.push(`getPage:${jobId}:${pageNum}`);
          return { page_num: pageNum, status: "done", content: "# recovered" };
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
    subscribeJobEvents(jobId) {
      calls.push(`subscribe:${jobId}`);
      return { close() {} };
    },
  },
  buildingRecovery: {
    async waitForNextPoll() {
      calls.push("waitForNextPoll");
    },
    maxPollAttempts: 5,
  },
});

const snapshot = await controller.bootstrap("job-24-d");
const state = store.getState();

console.log(JSON.stringify({ calls, snapshot, state }));
"""
        )
        self.assertEqual(
            [
                "getJob:job-24-d",
                "waitForNextPoll",
                "getJob:job-24-d",
                "waitForNextPoll",
                "getJob:job-24-d",
                "listPages:job-24-d",
                "getPage:job-24-d:2",
                "subscribe:job-24-d",
            ],
            result["calls"],
        )
        self.assertEqual("extracting", result["snapshot"]["job"]["status"])
        self.assertTrue(result["snapshot"]["is_stream_connected"])
        self.assertFalse(result["state"]["is_building_busy"])
        self.assertEqual("page", result["state"]["workspace_mode"])

    def test_page_editor_renders_pending_extracting_done_failed_states(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const storeModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/store.ts")).href);
const editorModule = await import(pathToFileURL(path.resolve("frontend/src/modules/page-editor/index.ts")).href);

const store = storeModule.createWorkspaceStore({ current_page_num: 4 });
const pagePane = editorModule.createPagePaneController({ workspaceStore: store });

const pendingView = pagePane.getViewModel({ page_num: 4, status: "pending" });
const extractingView = pagePane.getViewModel({ page_num: 4, status: "extracting" });
const doneView = pagePane.getViewModel({ page_num: 4, status: "done", content: "# done-content" });
const failedView = pagePane.getViewModel({ page_num: 4, status: "failed", error: "llm timeout" });

console.log(
  JSON.stringify({
    pendingView,
    extractingView,
    doneView,
    failedView,
  }),
);
"""
        )
        self.assertEqual("pending", result["pendingView"]["state"])
        self.assertEqual("extracting", result["extractingView"]["state"])
        self.assertEqual("done", result["doneView"]["state"])
        self.assertEqual("# done-content", result["doneView"]["content"])
        self.assertEqual("failed", result["failedView"]["state"])
        self.assertEqual("llm timeout", result["failedView"]["error"])

    def test_single_page_test_store_initial_is_idle_and_reset_clears_snapshot(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const store = workspaceModule.createWorkspaceStore();
const initial = store.getState().singlePageTest;

const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery() { throw new Error("must not query job"); },
    usePagesQuery() { throw new Error("must not query pages"); },
    usePageQuery() { throw new Error("must not query page"); },
    useOutputQuery() { throw new Error("must not query output"); },
  },
  streamClient: {
    subscribeJobEvents() { throw new Error("must not subscribe"); },
  },
  singlePagePreviewFn: async () => ({ page_num: 1, content: "## ignored" }),
});

store.setState({
  singlePageTest: {
    status: "done",
    pageNum: 3,
    content: "## stale",
    error: null,
  },
});
const beforeReset = store.getState().singlePageTest;

controller.resetSinglePageTest();
const afterReset = store.getState().singlePageTest;

console.log(JSON.stringify({ initial, beforeReset, afterReset }));
"""
        )
        self.assertEqual(
            {"status": "idle", "pageNum": None, "content": None, "error": None},
            result["initial"],
        )
        self.assertEqual("done", result["beforeReset"]["status"])
        self.assertEqual(3, result["beforeReset"]["pageNum"])
        self.assertEqual(
            {"status": "idle", "pageNum": None, "content": None, "error": None},
            result["afterReset"],
        )

    def test_run_single_page_test_preconditions_throw_without_request(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const calls = [];
const store = workspaceModule.createWorkspaceStore();

const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery() { throw new Error("must not query job"); },
    usePagesQuery() { throw new Error("must not query pages"); },
    usePageQuery() { throw new Error("must not query page"); },
    useOutputQuery() { throw new Error("must not query output"); },
  },
  streamClient: {
    subscribeJobEvents() { throw new Error("must not subscribe"); },
  },
  singlePagePreviewFn: async (file, pageNum) => {
    calls.push({ fileName: file?.name ?? null, pageNum });
    return { page_num: pageNum, content: "## should-not-arrive" };
  },
});

const file = new File(["%PDF-1.4"], "sample.pdf", { type: "application/pdf" });

async function expectThrow(label, input) {
  let caught = null;
  try {
    await controller.runSinglePageTest(input);
  } catch (err) {
    caught = err;
  }
  if (!caught) {
    throw new Error(`expected ${label} to throw`);
  }
  return String(caught.message ?? caught);
}

const missingFile = await expectThrow("missingFile", { file: null, previewReady: true, pageNum: 2 });
const previewNotReady = await expectThrow("previewNotReady", { file, previewReady: false, pageNum: 2 });

store.setState({ job_id: "job-x" });
const hasJob = await expectThrow("hasJob", { file, previewReady: true, pageNum: 2 });
store.setState({ job_id: null });

store.setState({
  singlePageTest: {
    status: "running",
    pageNum: 1,
    content: null,
    error: null,
  },
});
const alreadyRunning = await expectThrow("alreadyRunning", { file, previewReady: true, pageNum: 2 });

console.log(
  JSON.stringify({
    callCount: calls.length,
    messages: { missingFile, previewNotReady, hasJob, alreadyRunning },
    finalStatus: store.getState().singlePageTest.status,
  }),
);
"""
        )
        self.assertEqual(0, int(result["callCount"]))
        self.assertEqual("running", result["finalStatus"])
        self.assertTrue(result["messages"]["missingFile"])
        self.assertTrue(result["messages"]["previewNotReady"])
        self.assertTrue(result["messages"]["hasJob"])
        self.assertTrue(result["messages"]["alreadyRunning"])

    def test_run_single_page_test_moves_store_to_running_then_done(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);

const observed = [];
const store = workspaceModule.createWorkspaceStore();
store.subscribe((state) => {
  observed.push({
    status: state.singlePageTest.status,
    pageNum: state.singlePageTest.pageNum,
    content: state.singlePageTest.content,
  });
});

let resolveRequest;
const pending = new Promise((resolve) => { resolveRequest = resolve; });

const calls = [];
const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery() { throw new Error("must not query job"); },
    usePagesQuery() { throw new Error("must not query pages"); },
    usePageQuery() { throw new Error("must not query page"); },
    useOutputQuery() { throw new Error("must not query output"); },
  },
  streamClient: {
    subscribeJobEvents() { throw new Error("must not subscribe"); },
  },
  singlePagePreviewFn: async (file, pageNum) => {
    calls.push({ fileName: file.name, pageNum });
    const payload = await pending;
    return { ...payload, page_num: pageNum };
  },
});

const file = new File(["%PDF-1.4"], "doc.pdf", { type: "application/pdf" });
const runPromise = controller.runSinglePageTest({ file, previewReady: true, pageNum: 5 });
await Promise.resolve();
const whileRunning = store.getState().singlePageTest;

resolveRequest({ content: "## preview body" });
await runPromise;
const final = store.getState().singlePageTest;

console.log(
  JSON.stringify({
    calls,
    whileRunning,
    final,
    observedStatuses: observed.map((item) => item.status),
    firstObservedPageNum: observed[0]?.pageNum ?? null,
  }),
);
"""
        )
        self.assertEqual([{"fileName": "doc.pdf", "pageNum": 5}], result["calls"])
        self.assertEqual("running", result["whileRunning"]["status"])
        self.assertEqual(5, result["whileRunning"]["pageNum"])
        self.assertIsNone(result["whileRunning"]["content"])
        self.assertEqual("done", result["final"]["status"])
        self.assertEqual(5, result["final"]["pageNum"])
        self.assertEqual("## preview body", result["final"]["content"])
        self.assertIsNone(result["final"]["error"])
        self.assertEqual(["running", "done"], result["observedStatuses"])
        self.assertEqual(5, result["firstObservedPageNum"])

    def test_run_single_page_test_records_failure_with_error_code_and_message(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspaceModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/index.ts")).href);
const apiModule = await import(pathToFileURL(path.resolve("frontend/src/shared/api/index.ts")).href);

const store = workspaceModule.createWorkspaceStore();

const controller = workspaceModule.createJobWorkspaceController({
  store,
  queryHooks: {
    useJobQuery() { throw new Error("must not query job"); },
    usePagesQuery() { throw new Error("must not query pages"); },
    usePageQuery() { throw new Error("must not query page"); },
    useOutputQuery() { throw new Error("must not query output"); },
  },
  streamClient: {
    subscribeJobEvents() { throw new Error("must not subscribe"); },
  },
  singlePagePreviewFn: async () => {
    throw new apiModule.ApiClientError({
      status: 401,
      code: "LLM_AUTH_FAILED",
      message: "认证失败",
      details: { provider: "openai" },
    });
  },
});

const file = new File(["%PDF-1.4"], "doc.pdf", { type: "application/pdf" });
await controller.runSinglePageTest({ file, previewReady: true, pageNum: 7 });

const final = store.getState().singlePageTest;

console.log(JSON.stringify({ final }));
"""
        )
        self.assertEqual("failed", result["final"]["status"])
        self.assertEqual(7, result["final"]["pageNum"])
        self.assertIsNone(result["final"]["content"])
        self.assertEqual("LLM_AUTH_FAILED", result["final"]["error"]["code"])
        self.assertEqual("认证失败", result["final"]["error"]["message"])

    def test_build_page_pane_view_model_covers_single_page_test_branches(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const editorModule = await import(pathToFileURL(path.resolve("frontend/src/modules/page-editor/index.ts")).href);
const { buildPagePaneViewModel } = editorModule;

const IDLE = { status: "idle", pageNum: null, content: null, error: null };
const RUNNING = { status: "running", pageNum: 5, content: null, error: null };
const DONE = { status: "done", pageNum: 5, content: "## preview body", error: null };
const FAILED = {
  status: "failed",
  pageNum: 7,
  content: null,
  error: { code: "LLM_AUTH_FAILED", message: "认证失败" },
};

const runningView = buildPagePaneViewModel(3, null, RUNNING);
const doneView = buildPagePaneViewModel(3, null, DONE);
const failedView = buildPagePaneViewModel(3, null, FAILED);
const idleFallbackView = buildPagePaneViewModel(3, null, IDLE);
const idleWithPageView = buildPagePaneViewModel(
  4,
  { page_num: 4, status: "done", content: "# page done" },
  IDLE,
);

console.log(
  JSON.stringify({ runningView, doneView, failedView, idleFallbackView, idleWithPageView }),
);
"""
        )
        running = result["runningView"]
        self.assertEqual("test-running", running["state"])
        self.assertEqual(5, running["current_page_num"])
        self.assertIn("测试中", running["title"])
        self.assertIn("第 5 页", running["title"])
        self.assertIsNone(running["content"])
        self.assertIsNone(running["error"])

        done = result["doneView"]
        self.assertEqual("test-done", done["state"])
        self.assertEqual(5, done["current_page_num"])
        self.assertIn("测试结果", done["title"])
        self.assertIn("第 5 页", done["title"])
        self.assertEqual("## preview body", done["content"])
        self.assertIsNone(done["error"])

        failed = result["failedView"]
        self.assertEqual("test-failed", failed["state"])
        self.assertEqual(7, failed["current_page_num"])
        self.assertIn("测试失败", failed["title"])
        self.assertIn("第 7 页", failed["title"])
        self.assertIsNone(failed["content"])
        self.assertEqual("认证失败", failed["error"])

        idle_fallback = result["idleFallbackView"]
        self.assertEqual("pending", idle_fallback["state"])
        self.assertEqual(3, idle_fallback["current_page_num"])

        idle_with_done_page = result["idleWithPageView"]
        self.assertEqual("done", idle_with_done_page["state"])
        self.assertEqual("# page done", idle_with_done_page["content"])

    def test_page_pane_controller_ignores_single_page_test_when_job_is_active(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const storeModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/store.ts")).href);
const editorModule = await import(pathToFileURL(path.resolve("frontend/src/modules/page-editor/index.ts")).href);

const store = storeModule.createWorkspaceStore({ current_page_num: 4 });
store.setState({
  job_id: "job-active",
  singlePageTest: {
    status: "done",
    pageNum: 9,
    content: "## stale preview",
    error: null,
  },
});

const pagePane = editorModule.createPagePaneController({ workspaceStore: store });

const pendingPageView = pagePane.getViewModel(null);
const extractingPageView = pagePane.getViewModel({ page_num: 4, status: "extracting" });
const donePageView = pagePane.getViewModel({ page_num: 4, status: "done", content: "# real" });

console.log(JSON.stringify({ pendingPageView, extractingPageView, donePageView }));
"""
        )
        self.assertEqual("pending", result["pendingPageView"]["state"])
        self.assertEqual(4, result["pendingPageView"]["current_page_num"])
        self.assertEqual("extracting", result["extractingPageView"]["state"])
        self.assertEqual("done", result["donePageView"]["state"])
        self.assertEqual("# real", result["donePageView"]["content"])

    def test_page_pane_controller_reads_single_page_test_snapshot_when_no_job(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const storeModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/store.ts")).href);
const editorModule = await import(pathToFileURL(path.resolve("frontend/src/modules/page-editor/index.ts")).href);

const store = storeModule.createWorkspaceStore({ current_page_num: 2 });
store.setState({
  singlePageTest: {
    status: "running",
    pageNum: 6,
    content: null,
    error: null,
  },
});

const pagePane = editorModule.createPagePaneController({ workspaceStore: store });
const runningView = pagePane.getViewModel(null);

console.log(JSON.stringify({ runningView }));
"""
        )
        self.assertEqual("test-running", result["runningView"]["state"])
        self.assertEqual(6, result["runningView"]["current_page_num"])

