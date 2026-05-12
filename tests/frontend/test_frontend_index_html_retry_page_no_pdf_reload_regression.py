"""
Regression: `fix/retry-page-keep-pdf-preview`

锁定“重新提取”路径中的 PDF 预览行为：当当前是本地预览时，触发页级重试后刷新工作区
应延续 preserve 分支，避免切回 `/api/jobs/{id}/source` 导致可见闪烁与阅读位置打断。
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


def _run_node_script(script: str) -> dict[str, object]:
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
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
    payload = (result.stdout or "").strip()
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Node 输出不是 JSON: {payload}") from exc


class RetryPageNoPdfReloadRegressionTests(TestCase):
    def test_retry_current_page_passes_preserve_existing_pdf_option(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn(
            '<button id="retry-page-btn" class="pixel-btn-sm" type="button">重新提取单页</button>',
            source,
            "按钮文案应明确为“重新提取单页”，避免与全量提取歧义",
        )

        self.assertIn(
            "function hasLocalPdfPreviewLoaded()",
            source,
            "应提供本地预览已加载判断，供重新提取时复用",
        )
        self.assertIn(
            "function applyRetriedPageToWorkspaceState(pageNum) {",
            source,
            "提取中的单页重试应存在本地状态回滚逻辑，避免依赖整页 refreshWorkspace",
        )

        retry_fn_start = source.index("async function retryCurrentPage() {")
        retry_fn_end = source.index("\n        /* ── Page Auto Save", retry_fn_start)
        retry_fn_body = source[retry_fn_start:retry_fn_end]

        self.assertIn(
            "var preserveExistingPdf = hasLocalPdfPreviewLoaded();",
            retry_fn_body,
            "重新提取前应先快照本地预览复用意图",
        )
        self.assertIn(
            'var shouldApplyRetryLocally = workspaceStatus === "extracting" && !!state.stream;',
            retry_fn_body,
            "提取中的单页重试应优先走本地状态回滚，避免中途重连 SSE 造成状态回退",
        )
        self.assertIn(
            "applyRetriedPageToWorkspaceState(currentPageNum);",
            retry_fn_body,
            "活跃提取中的单页重试应即时把当前页与总进度切回 extracting",
        )
        self.assertIn(
            "await refreshWorkspace({ preserveExistingPdf: preserveExistingPdf });",
            retry_fn_body,
            "非活跃流场景（例如 extracted）仍应显式传递复用 PDF 预览选项",
        )
        self.assertNotIn(
            "await refreshWorkspace();",
            retry_fn_body,
            "重新提取不应再无参刷新工作区（会丢失本地预览复用语义）",
        )
        self.assertIn(
            "state.isBusy = true;",
            retry_fn_body,
            "重新提取发起后应立刻进入 busy，防止重复点击并发提交",
        )
        self.assertIn(
            "state.isBusy = false;",
            retry_fn_body,
            "重新提取结束后应恢复 busy 标记",
        )
        self.assertIn(
            'currentPageStatus !== "done" && currentPageStatus !== "failed"',
            retry_fn_body,
            "仅当当前页处于 done/failed 终态时才允许重新提取",
        )

    def test_retry_current_page_runtime_uses_local_preview_snapshot(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");

function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function pickByName(functionName) {
  const pattern = new RegExp(`async function ${escapeRegex(functionName)}\\([^)]*\\) \\{[\\s\\S]*?\\r?\\n        \\}`);
  const match = html.match(pattern);
  if (!match) {
    throw new Error(`failed to locate async function ${functionName}(...) in frontend/index.html`);
  }
  return match[0];
}

function createHarness(localPreviewLoaded) {
  const state = {
    jobId: "job-1",
    job: { status: "extracted", total_pages: 3, processed_count: 1 },
    pages: [{ page_num: 2, status: "done" }],
    currentPageNum: 2,
    currentPage: { page_num: 2, status: "done", content: "ok", error: "" },
    isBusy: false,
    stream: null,
  };
  const requestJsonCalls = [];
  const refreshWorkspaceCalls = [];
  const logs = [];
  const busySnapshots = [];
  let resolveRequest = null;
  const requestGate = new Promise((resolve) => {
    resolveRequest = resolve;
  });

  async function requestJson(path, init) {
    requestJsonCalls.push({
      path,
      method: init && typeof init.method === "string" ? init.method : null,
    });
    return requestGate;
  }

  function log(kind, payload) {
    logs.push({ kind, payload });
  }

  function hasLocalPdfPreviewLoaded() {
    return localPreviewLoaded;
  }

  async function refreshWorkspace(options) {
    refreshWorkspaceCalls.push({
      preserveExistingPdf: !!(options && options.preserveExistingPdf),
    });
  }

  function updateControlAvailability() {
    busySnapshots.push(!!state.isBusy);
  }

  function getWorkspaceStatus() {
    if (state.job) return state.job.status;
    return "idle";
  }

  const retryCurrentPage = new Function(
    "state",
    "requestJson",
    "log",
    "hasLocalPdfPreviewLoaded",
    "refreshWorkspace",
    "updateControlAvailability",
    "getWorkspaceStatus",
    `${pickByName("retryCurrentPage")}
return retryCurrentPage;`,
  )(
    state,
    requestJson,
    log,
    hasLocalPdfPreviewLoaded,
    refreshWorkspace,
    updateControlAvailability,
    getWorkspaceStatus,
  );

  return {
    async run() {
      const retryPromise = retryCurrentPage();
      await Promise.resolve();
      const pendingSnapshot = {
        isBusy: !!state.isBusy,
        refreshWorkspaceCalls: refreshWorkspaceCalls.length,
      };
      if (!resolveRequest) {
        throw new Error("request gate resolver is missing");
      }
      resolveRequest({ accepted: true, page_num: state.currentPageNum });
      await retryPromise;
      return {
        requestJsonCalls: requestJsonCalls.slice(),
        refreshWorkspaceCalls: refreshWorkspaceCalls.slice(),
        logs: logs.slice(),
        busySnapshots: busySnapshots.slice(),
        pendingSnapshot,
        finalIsBusy: !!state.isBusy,
      };
    },
  };
}

const preserveHit = await createHarness(true).run();
const preserveMiss = await createHarness(false).run();

console.log(
  JSON.stringify({
    preserveHit,
    preserveMiss,
  }),
);
"""
        )

        for scenario_name in ("preserveHit", "preserveMiss"):
            scenario = result[scenario_name]
            self.assertEqual(1, len(scenario["requestJsonCalls"]))
            self.assertEqual(
                "/api/jobs/job-1/pages/2/retry",
                scenario["requestJsonCalls"][0]["path"],
            )
            self.assertEqual("POST", scenario["requestJsonCalls"][0]["method"])
            self.assertEqual(1, len(scenario["refreshWorkspaceCalls"]))
            self.assertEqual(1, len(scenario["logs"]))
            self.assertEqual("page retried", scenario["logs"][0]["kind"])
            self.assertTrue(
                scenario["pendingSnapshot"]["isBusy"],
                "重试请求进行中应保持 isBusy=true，避免按钮可重复点击",
            )
            self.assertFalse(
                scenario["finalIsBusy"],
                "重试流程结束后应恢复 isBusy=false",
            )
            self.assertGreaterEqual(len(scenario["busySnapshots"]), 2)
            self.assertTrue(scenario["busySnapshots"][0])
            self.assertFalse(scenario["busySnapshots"][-1])

        self.assertEqual(
            {"preserveExistingPdf": True},
            result["preserveHit"]["refreshWorkspaceCalls"][0],
            "本地预览已加载时，重试后应携带 preserve=true",
        )
        self.assertEqual(
            {"preserveExistingPdf": False},
            result["preserveMiss"]["refreshWorkspaceCalls"][0],
            "本地预览不可复用时，重试后应显式携带 preserve=false",
        )

    def test_retry_current_page_during_active_extraction_rolls_back_local_state_without_refresh(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");

function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function pick(signature) {
  const pattern = new RegExp(`${escapeRegex(signature)} \\{[\\s\\S]*?\\r?\\n        \\}`);
  const match = html.match(pattern);
  if (!match) {
    throw new Error(`failed to locate ${signature} in frontend/index.html`);
  }
  return match[0];
}

function pickByName(functionName) {
  const pattern = new RegExp(`async function ${escapeRegex(functionName)}\\([^)]*\\) \\{[\\s\\S]*?\\r?\\n        \\}`);
  const match = html.match(pattern);
  if (!match) {
    throw new Error(`failed to locate async function ${functionName}(...) in frontend/index.html`);
  }
  return match[0];
}

const state = {
  jobId: "job-1",
  job: {
    status: "extracting",
    total_pages: 5,
    succeeded_pages: [2, 4],
    failed_pages: [],
    processed_count: 2,
  },
  pages: [
    { page_num: 1, status: "pending" },
    { page_num: 2, status: "done" },
    { page_num: 3, status: "pending" },
    { page_num: 4, status: "done" },
    { page_num: 5, status: "pending" },
  ],
  currentPageNum: 4,
  currentPage: { page_num: 4, status: "done", content: "## stale", error: "" },
  isBusy: false,
  stream: { close() {} },
};

const els = {
  pageEditor: { value: "## stale" },
  pageError: { textContent: "" },
};

const refreshWorkspaceCalls = [];
const historyPatches = [];
const progressSnapshots = [];
const busySnapshots = [];
const scheduledPatches = [];
const logs = [];
let statusIndicatorCalls = 0;
let resolveRequest = null;
const requestGate = new Promise((resolve) => {
  resolveRequest = resolve;
});

async function requestJson(path, init) {
  return requestGate.then(() => ({
    path,
    method: init && typeof init.method === "string" ? init.method : null,
    accepted: true,
    page_num: state.currentPageNum,
  }));
}

function log(kind, payload) {
  logs.push({ kind, payload });
}

function hasLocalPdfPreviewLoaded() {
  return true;
}

async function refreshWorkspace(options) {
  refreshWorkspaceCalls.push(options || null);
}

function updateControlAvailability() {
  busySnapshots.push(!!state.isBusy);
}

function getWorkspaceStatus() {
  if (state.job) return state.job.status;
  return "idle";
}

function updateCurrentHistoryJobState(patch) {
  historyPatches.push({ ...patch });
}

function schedulePageEventUiFlush(patch) {
  scheduledPatches.push({
    pageNums: Array.isArray(patch && patch.pageNums) ? patch.pageNums.slice() : [],
    historyJobIds: Array.isArray(patch && patch.historyJobIds) ? patch.historyJobIds.slice() : [],
  });
}

function updateProgress() {
  progressSnapshots.push({
    status: state.job && state.job.status,
    processed_count: state.job && state.job.processed_count,
    succeeded_pages: state.job ? state.job.succeeded_pages.slice() : [],
    failed_pages: state.job ? state.job.failed_pages.slice() : [],
  });
}

function updatePageStatusIndicator() {
  statusIndicatorCalls += 1;
}

const factoryBody = `
var pageIndexByNum = Object.create(null);
for (var i = 0; i < state.pages.length; i++) {
  pageIndexByNum[state.pages[i].page_num] = i;
}
var jobDonePageSet = new Set((state.job && state.job.succeeded_pages) || []);
var jobFailedPageSet = new Set((state.job && state.job.failed_pages) || []);
${pick("function syncJobProgressBucket(pageNum, previousStatus, nextStatus)")}
${pick("function applyPageSummaryDelta(pageNum, status)")}
${pick("function applyRetriedPageToWorkspaceState(pageNum)")}
${pickByName("retryCurrentPage")}
return {
  retryCurrentPage,
  getJobSets() {
    return {
      done: Array.from(jobDonePageSet).sort((a, b) => a - b),
      failed: Array.from(jobFailedPageSet).sort((a, b) => a - b),
    };
  },
};
`;

const api = new Function(
  "state",
  "els",
  "requestJson",
  "log",
  "hasLocalPdfPreviewLoaded",
  "refreshWorkspace",
  "updateControlAvailability",
  "getWorkspaceStatus",
  "updateCurrentHistoryJobState",
  "schedulePageEventUiFlush",
  "updateProgress",
  "updatePageStatusIndicator",
  factoryBody,
)(
  state,
  els,
  requestJson,
  log,
  hasLocalPdfPreviewLoaded,
  refreshWorkspace,
  updateControlAvailability,
  getWorkspaceStatus,
  updateCurrentHistoryJobState,
  schedulePageEventUiFlush,
  updateProgress,
  updatePageStatusIndicator,
);

const retryPromise = api.retryCurrentPage();
await Promise.resolve();
const pendingSnapshot = {
  isBusy: !!state.isBusy,
  refreshWorkspaceCalls: refreshWorkspaceCalls.length,
};
if (!resolveRequest) {
  throw new Error("request gate resolver is missing");
}
resolveRequest();
await retryPromise;

console.log(JSON.stringify({
  pendingSnapshot,
  finalIsBusy: !!state.isBusy,
  refreshWorkspaceCalls,
  historyPatches,
  progressSnapshots,
  busySnapshots,
  scheduledPatches,
  logs,
  statusIndicatorCalls,
  currentPage: { ...state.currentPage },
  pageEditorValue: els.pageEditor.value,
  pageErrorText: els.pageError.textContent,
  pageSummaryStatus: state.pages.find((page) => page.page_num === 4).status,
  job: {
    status: state.job.status,
    processed_count: state.job.processed_count,
    succeeded_pages: state.job.succeeded_pages.slice(),
    failed_pages: state.job.failed_pages.slice(),
  },
  sets: api.getJobSets(),
}));
"""
        )

        self.assertTrue(
            result["pendingSnapshot"]["isBusy"],
            "重试请求尚未返回时，应保持 isBusy=true 以防止重复点击",
        )
        self.assertEqual(
            0,
            result["pendingSnapshot"]["refreshWorkspaceCalls"],
            "活跃提取中的单页重试不应立刻触发整页 refreshWorkspace",
        )
        self.assertFalse(result["finalIsBusy"])
        self.assertEqual([], result["refreshWorkspaceCalls"])
        self.assertEqual("extracting", result["currentPage"]["status"])
        self.assertEqual("", result["currentPage"]["content"])
        self.assertEqual("", result["currentPage"]["error"])
        self.assertEqual("", result["pageEditorValue"])
        self.assertEqual("", result["pageErrorText"])
        self.assertEqual("extracting", result["pageSummaryStatus"])
        self.assertEqual("extracting", result["job"]["status"])
        self.assertEqual(1, result["job"]["processed_count"])
        self.assertEqual([2], result["job"]["succeeded_pages"])
        self.assertEqual([], result["job"]["failed_pages"])
        self.assertEqual([2], result["sets"]["done"])
        self.assertEqual([], result["sets"]["failed"])
        self.assertEqual(1, len(result["historyPatches"]))
        self.assertEqual("extracting", result["historyPatches"][0]["status"])
        self.assertEqual(1, result["historyPatches"][0]["processed_count"])
        self.assertEqual(5, result["historyPatches"][0]["total_pages"])
        self.assertEqual(
            [{"pageNums": [4], "historyJobIds": ["job-1"]}],
            result["scheduledPatches"],
        )
        self.assertEqual(1, len(result["progressSnapshots"]))
        self.assertEqual("extracting", result["progressSnapshots"][0]["status"])
        self.assertEqual(1, result["progressSnapshots"][0]["processed_count"])
        self.assertGreaterEqual(len(result["busySnapshots"]), 2)
        self.assertTrue(result["busySnapshots"][0])
        self.assertFalse(result["busySnapshots"][-1])
        self.assertEqual(1, result["statusIndicatorCalls"])
        self.assertEqual(1, len(result["logs"]))
        self.assertEqual("page retried", result["logs"][0]["kind"])
