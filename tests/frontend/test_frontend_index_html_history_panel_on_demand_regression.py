"""
Regression: `task4/history-panel-on-demand`

锁定 Task 4「历史栏按需刷新」的关键不变量：

1. 历史栏同步应走「折叠态只标记 dirty，展开后再按需拉取」路径。
2. `refreshWorkspace()` 不应再直接全量调用 `loadHistoryJobs()`，而应委托按需同步入口。
3. SSE 终态事件（`complete` / `failed`）不应再无条件全量重载历史列表。
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]


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


class HistoryPanelOnDemandRegressionTests(TestCase):
    def test_sync_history_panel_on_demand_defers_reload_until_expanded(self) -> None:
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

const api = new Function(
  `
var historyPanelExpanded = false;
var historyDirty = false;
var historyJobsLoaded = false;
var loadHistoryJobsCalls = 0;
var refreshHistoryActionAvailabilityCalls = 0;

async function loadHistoryJobs() {
  loadHistoryJobsCalls += 1;
  historyJobsLoaded = true;
  historyDirty = false;
}

function refreshHistoryActionAvailability() {
  refreshHistoryActionAvailabilityCalls += 1;
}

${pick("async function refreshHistoryPanelOnExpand()")}
${pick("async function syncHistoryPanelOnDemand(forceSync)")}

return {
  async sync(forceSync) {
    await syncHistoryPanelOnDemand(forceSync);
    return this.getSnapshot();
  },
  setExpanded(value) {
    historyPanelExpanded = !!value;
  },
  getSnapshot() {
    return {
      historyPanelExpanded,
      historyDirty,
      historyJobsLoaded,
      loadHistoryJobsCalls,
      refreshHistoryActionAvailabilityCalls,
    };
  },
};
`,
)();

const collapsedForced = await api.sync(true);
api.setExpanded(true);
const expandedAfterDirty = await api.sync(false);
const expandedClean = await api.sync(false);
const expandedForced = await api.sync(true);

console.log(
  JSON.stringify({
    collapsedForced,
    expandedAfterDirty,
    expandedClean,
    expandedForced,
  }),
);
"""
        )

        self.assertTrue(
            result["collapsedForced"]["historyDirty"],
            "折叠态下强制同步应只打 dirty 标记，不直接拉全量历史",
        )
        self.assertEqual(0, result["collapsedForced"]["loadHistoryJobsCalls"])
        self.assertEqual(0, result["collapsedForced"]["refreshHistoryActionAvailabilityCalls"])

        self.assertEqual(1, result["expandedAfterDirty"]["loadHistoryJobsCalls"])
        self.assertFalse(result["expandedAfterDirty"]["historyDirty"])
        self.assertTrue(result["expandedAfterDirty"]["historyJobsLoaded"])

        self.assertEqual(
            1,
            result["expandedClean"]["loadHistoryJobsCalls"],
            "展开且已同步时，不应重复全量拉取",
        )
        self.assertEqual(1, result["expandedClean"]["refreshHistoryActionAvailabilityCalls"])

        self.assertEqual(
            2,
            result["expandedForced"]["loadHistoryJobsCalls"],
            "展开态下显式 force sync 应触发一次全量同步",
        )

    def test_refresh_workspace_delegates_history_sync_instead_of_direct_full_reload(self) -> None:
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

const state = { jobId: "job-1", job: null, isBusy: false };
let closeStreamCalls = 0;
let requestJsonCalls = 0;
let updateControlAvailabilityCalls = 0;
let loadOutputCalls = 0;
let clearOutputStateCalls = 0;
let loadPagesCalls = 0;
let connectStreamCalls = 0;
let loadHistoryJobsCalls = 0;
const syncHistoryPanelOnDemandCalls = [];
let showPdfStatusHintCalls = 0;
let hidePdfStatusHintCalls = 0;
let loadPdfDocumentCalls = 0;
const logs = [];

function closeStream() {
  closeStreamCalls += 1;
}

async function requestJson() {
  requestJsonCalls += 1;
  return {
    job_id: "job-1",
    status: "extracting",
    total_pages: 3,
    processed_count: 1,
    succeeded_pages: [],
    failed_pages: [],
  };
}

function updateControlAvailability() {
  updateControlAvailabilityCalls += 1;
}

async function loadOutput() {
  loadOutputCalls += 1;
}

function clearOutputState() {
  clearOutputStateCalls += 1;
}

async function loadPages() {
  loadPagesCalls += 1;
}

function connectStream() {
  connectStreamCalls += 1;
}

async function loadHistoryJobs() {
  loadHistoryJobsCalls += 1;
}

async function syncHistoryPanelOnDemand(forceSync) {
  syncHistoryPanelOnDemandCalls.push(!!forceSync);
}

function showPdfStatusHint() {
  showPdfStatusHintCalls += 1;
}

function hidePdfStatusHint() {
  hidePdfStatusHintCalls += 1;
}

async function loadPdfDocument() {
  loadPdfDocumentCalls += 1;
}

function log(kind, payload) {
  logs.push({ kind, payload });
}

const refreshWorkspace = new Function(
  "state",
  "closeStream",
  "requestJson",
  "updateControlAvailability",
  "loadOutput",
  "clearOutputState",
  "loadPages",
  "connectStream",
  "loadHistoryJobs",
  "syncHistoryPanelOnDemand",
  "showPdfStatusHint",
  "loadPdfDocument",
  "hidePdfStatusHint",
  "log",
  `${pickByName("refreshWorkspace")}
return refreshWorkspace;`,
)(
  state,
  closeStream,
  requestJson,
  updateControlAvailability,
  loadOutput,
  clearOutputState,
  loadPages,
  connectStream,
  loadHistoryJobs,
  syncHistoryPanelOnDemand,
  showPdfStatusHint,
  loadPdfDocument,
  hidePdfStatusHint,
  log,
);

await refreshWorkspace();

console.log(
  JSON.stringify({
    closeStreamCalls,
    requestJsonCalls,
    updateControlAvailabilityCalls,
    loadOutputCalls,
    clearOutputStateCalls,
    loadPagesCalls,
    connectStreamCalls,
    loadHistoryJobsCalls,
    syncHistoryPanelOnDemandCalls,
    showPdfStatusHintCalls,
    hidePdfStatusHintCalls,
    loadPdfDocumentCalls,
    logs,
  }),
);
"""
        )

        self.assertEqual(0, result["loadHistoryJobsCalls"])
        self.assertEqual(
            [True],
            result["syncHistoryPanelOnDemandCalls"],
            "refreshWorkspace 应统一通过按需入口驱动历史栏同步",
        )
        self.assertEqual(1, result["loadPagesCalls"])
        self.assertEqual(1, result["connectStreamCalls"])
        self.assertEqual(1, result["loadPdfDocumentCalls"])

    def test_terminal_stream_events_no_longer_force_history_full_reload(self) -> None:
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

const state = {
  jobId: "job-1",
  currentPageNum: 1,
  currentPage: null,
  job: {
    job_id: "job-1",
    status: "extracting",
    total_pages: 3,
    processed_count: 1,
    succeeded_pages: [1],
    failed_pages: [2],
  },
};

let closeStreamCalls = 0;
let loadHistoryJobsCalls = 0;
let updateControlAvailabilityCalls = 0;
const logs = [];
const historyPatches = [];
const uiFlushPatches = [];

function closeStream() {
  closeStreamCalls += 1;
}

async function loadHistoryJobs() {
  loadHistoryJobsCalls += 1;
}

function updateControlAvailability() {
  updateControlAvailabilityCalls += 1;
}

function log(kind, payload) {
  logs.push({ kind, payload });
}

function updateCurrentHistoryJobState(patch) {
  historyPatches.push({ ...patch });
}

function schedulePageEventUiFlush(patch) {
  uiFlushPatches.push({
    pageNums: Array.isArray(patch && patch.pageNums) ? patch.pageNums.slice() : [],
    historyJobIds: Array.isArray(patch && patch.historyJobIds) ? patch.historyJobIds.slice() : [],
  });
}

const onStreamEvent = new Function(
  "state",
  "closeStream",
  "loadHistoryJobs",
  "updateControlAvailability",
  "log",
  "updateCurrentHistoryJobState",
  "schedulePageEventUiFlush",
  `${pick("async function onStreamEvent(payload)")}
return onStreamEvent;`,
)(
  state,
  closeStream,
  loadHistoryJobs,
  updateControlAvailability,
  log,
  updateCurrentHistoryJobState,
  schedulePageEventUiFlush,
);

await onStreamEvent({
  type: "complete",
  processed_count: 3,
  total_pages: 3,
  succeeded_pages: [1, 2, 3],
  failed_pages: [],
});
const afterComplete = {
  status: state.job.status,
  processed_count: state.job.processed_count,
  total_pages: state.job.total_pages,
  succeeded_pages: state.job.succeeded_pages.slice(),
  failed_pages: state.job.failed_pages.slice(),
};

state.job.status = "extracting";
await onStreamEvent({ type: "failed", message: "boom" });
const afterFailed = {
  status: state.job.status,
};

console.log(
  JSON.stringify({
    closeStreamCalls,
    loadHistoryJobsCalls,
    updateControlAvailabilityCalls,
    historyPatches,
    uiFlushPatches,
    logs,
    afterComplete,
    afterFailed,
  }),
);
"""
        )

        self.assertEqual(0, result["loadHistoryJobsCalls"])
        self.assertEqual(2, result["closeStreamCalls"])
        self.assertEqual(2, result["updateControlAvailabilityCalls"])
        self.assertEqual("extracted", result["afterComplete"]["status"])
        self.assertEqual([1, 2, 3], result["afterComplete"]["succeeded_pages"])
        self.assertEqual([], result["afterComplete"]["failed_pages"])
        self.assertEqual("failed", result["afterFailed"]["status"])
        self.assertGreaterEqual(
            len(result["historyPatches"]),
            2,
            "complete/failed 终态事件应更新当前任务在历史栏中的内存状态",
        )
        self.assertGreaterEqual(
            len(result["uiFlushPatches"]),
            2,
            "complete/failed 终态事件应通过增量 patch 路径刷新历史栏，而非整表重载",
        )
