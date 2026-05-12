"""
Regression: `fix/sse-page-event-invalidation`

锁定 Task 3「收敛 SSE 事件的前端失效域」的两个关键回归边界：

1. `updateControlAvailability()` 不再无条件重建页码下拉或历史列表；
   `updateCurrentHistoryJobState()` 也不再在每条 `page` 事件里触发整份历史栏重绘。
2. 高频 `page` 事件改为按帧批处理 UI patch：
   - 非当前页事件不额外拉取当前页正文；
   - 同一帧内多条事件合并后，再增量 patch 页码导航和当前任务历史项；
   - 当前页只有在状态真正变化为需要正文/错误详情时才重拉正文。
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


class StreamEventInvalidationRegressionTests(TestCase):
    def test_source_detaches_control_and_page_events_from_full_nav_history_rebuilds(
        self,
    ) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        control_start = source.index("function updateControlAvailability()")
        control_chunk = source[control_start : control_start + 14000]
        control_body = control_chunk[: control_chunk.index("\n        function updateProgress(")]

        history_start = source.index("function updateCurrentHistoryJobState(patch)")
        history_chunk = source[history_start : history_start + 2600]
        history_body = history_chunk[: history_chunk.index("\n        async function openHistoryJob(")]

        stream_start = source.index("async function onStreamEvent(payload)")
        stream_chunk = source[stream_start : stream_start + 3500]
        stream_body = stream_chunk[: stream_chunk.index("\n        function connectStream(")]
        flush_start = source.index("function flushPageEventUi()")
        flush_chunk = source[flush_start : flush_start + 1800]
        flush_body = flush_chunk[: flush_chunk.index("\n        function shouldReloadCurrentPageFromPageEvent(")]
        open_start = source.index("async function openHistoryJob(jobId)")
        open_chunk = source[open_start : open_start + 1200]
        open_body = open_chunk[: open_chunk.index("\n        async function deleteHistoryJob(jobId)")]
        delete_start = source.index("async function deleteHistoryJob(jobId)")
        delete_chunk = source[delete_start : delete_start + 1400]
        delete_body = delete_chunk[: delete_chunk.index("\n        function getCurrentPageIndex(")]

        self.assertNotIn(
            "updatePageSelect()",
            control_body,
            "updateControlAvailability 不应再作为页码下拉的统一刷新入口",
        )
        self.assertNotIn(
            "renderHistoryList()",
            control_body,
            "updateControlAvailability 不应再作为历史列表的统一刷新入口",
        )
        self.assertIn(
            "refreshHistoryActionAvailability()",
            control_body,
            "updateControlAvailability 应同步刷新已渲染历史项的按钮可用态",
        )
        self.assertNotIn(
            "renderHistoryList()",
            history_body,
            "updateCurrentHistoryJobState 不应在每条 page 事件里重绘整份历史列表",
        )
        self.assertNotIn(
            "updatePageSelect()",
            stream_body,
            "page 事件不应再直接触发页码下拉全量重建",
        )
        self.assertIn(
            "schedulePageEventUiFlush",
            stream_body,
            "page 事件路径必须改为按帧批处理 UI patch",
        )
        self.assertIn(
            "function schedulePageEventUiFlush(patch)",
            source,
            "应显式存在 SSE page 事件的按帧合并入口",
        )
        schedule_start = source.index("function schedulePageEventUiFlush(patch)")
        schedule_chunk = source[schedule_start : schedule_start + 2000]
        schedule_body = schedule_chunk[: schedule_chunk.index("\n        function flushPageEventUi(")]
        self.assertIn(
            "requestAnimationFrame(",
            schedule_body,
            "page 事件高频 UI 刷新必须通过 requestAnimationFrame 合并",
        )
        self.assertIn(
            "if (!historyPanelExpanded)",
            flush_body,
            "历史栏折叠时，flushPageEventUi 应跳过 history DOM patch",
        )
        self.assertIn(
            "historyDirty = true",
            flush_body,
            "折叠态下命中 history patch 时应标记 historyDirty",
        )
        self.assertIn(
            "if (isHistoryActionBlocked())",
            open_body,
            "openHistoryJob 应在执行前校验全局忙碌态，避免误操作",
        )
        self.assertIn(
            "if (isHistoryActionBlocked())",
            delete_body,
            "deleteHistoryJob 应在执行前校验全局忙碌态，避免误操作",
        )

    def test_page_events_batch_ui_patch_and_only_reload_current_page_when_needed(
        self,
    ) -> None:
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
  job: {
    job_id: "job-1",
    status: "extracting",
    total_pages: 3,
    processed_count: 1,
    succeeded_pages: [1],
    failed_pages: [],
  },
  historyJobs: [
    {
      job_id: "job-1",
      status: "extracting",
      total_pages: 3,
      processed_count: 1,
      updated_at: "2026-04-18T00:00:00.000Z",
    },
  ],
  pages: [
    { page_num: 1, status: "done" },
    { page_num: 2, status: "pending" },
    { page_num: 3, status: "pending" },
  ],
  currentPageNum: 1,
  currentPage: { page_num: 1, status: "done", content: "# page-1", error: "" },
};

const pageSelectPatches = [];
const historyUiPatches = [];
const historyStatePatches = [];
const logs = [];

let syncPageNavCalls = 0;
let progressCalls = 0;
let loadCurrentPageCalls = 0;
let statusIndicatorCalls = 0;
let controlAvailabilityCalls = 0;

const rafQueue = [];
let nextRafId = 1;
const windowStub = {
  requestAnimationFrame(callback) {
    const entry = { id: nextRafId++, callback };
    rafQueue.push(entry);
    return entry.id;
  },
  cancelAnimationFrame(rafId) {
    const index = rafQueue.findIndex((entry) => entry.id === rafId);
    if (index >= 0) {
      rafQueue.splice(index, 1);
    }
  },
};

function patchPageSelectOption(pageNum) {
  pageSelectPatches.push(pageNum);
}

function patchHistoryJobItem(jobId) {
  historyUiPatches.push(jobId);
}

function syncPageNavigationState() {
  syncPageNavCalls += 1;
}

function updateProgress() {
  progressCalls += 1;
}

async function loadCurrentPage() {
  loadCurrentPageCalls += 1;
}

function log(kind, payload) {
  logs.push({ kind, payload });
}

function updateCurrentHistoryJobState(patch) {
  historyStatePatches.push({ ...patch });
  const item = state.historyJobs.find((entry) => entry.job_id === state.jobId);
  if (!item) return;
  if (patch.status) item.status = patch.status;
  if (typeof patch.total_pages === "number") item.total_pages = patch.total_pages;
  if (typeof patch.processed_count === "number") item.processed_count = patch.processed_count;
  if (patch.updated_at) item.updated_at = patch.updated_at;
}

function updatePageStatusIndicator() {
  statusIndicatorCalls += 1;
}

function updateControlAvailability() {
  controlAvailabilityCalls += 1;
}

const factoryBody = `
var pageIndexByNum = Object.create(null);
for (var i = 0; i < state.pages.length; i++) {
  pageIndexByNum[state.pages[i].page_num] = i;
}
var jobDonePageSet = new Set((state.job && state.job.succeeded_pages) || []);
var jobFailedPageSet = new Set((state.job && state.job.failed_pages) || []);
var pendingUiFlush = null;
var pendingUiFrameId = null;
var historyPanelExpanded = true;
var historyDirty = false;
${pick("function normalizePageNum(value)")}
${pick("function syncJobProgressBucket(pageNum, previousStatus, nextStatus)")}
${pick("function applyPageSummaryDelta(pageNum, status)")}
${pick("function schedulePageEventUiFlush(patch)")}
${pick("function flushPageEventUi()")}
${pick("function updateJobProgressFromPageEvent(event)")}
${pick("function shouldReloadCurrentPageFromPageEvent(pageNum, previousStatus, nextStatus)")}
${pick("function applyCurrentPageStatusFromEvent(pageNum, status)")}
${pick("async function onStreamEvent(payload)")}
return {
  onStreamEvent,
  flushRaf() {
    if (!rafQueue.length) return false;
    const entry = rafQueue.shift();
    entry.callback(0);
    return true;
  },
  getInternals() {
    return {
      pendingUiFlush: pendingUiFlush
        ? {
            pageNums: pendingUiFlush.pageNums ? Array.from(pendingUiFlush.pageNums) : [],
            historyJobIds: pendingUiFlush.historyJobIds ? Array.from(pendingUiFlush.historyJobIds) : [],
          }
        : null,
      pendingUiFrameId,
      historyPanelExpanded,
      historyDirty,
      pageIndexByNum: { ...pageIndexByNum },
      done: Array.from(jobDonePageSet).sort((a, b) => a - b),
      failed: Array.from(jobFailedPageSet).sort((a, b) => a - b),
    };
  },
};
`;

const api = new Function(
  "state",
  "window",
  "patchPageSelectOption",
  "patchHistoryJobItem",
  "syncPageNavigationState",
  "updateProgress",
  "loadCurrentPage",
  "updateControlAvailability",
  "log",
  "updateCurrentHistoryJobState",
  "updatePageStatusIndicator",
  "rafQueue",
  factoryBody,
)(
  state,
  windowStub,
  patchPageSelectOption,
  patchHistoryJobItem,
  syncPageNavigationState,
  updateProgress,
  loadCurrentPage,
  updateControlAvailability,
  log,
  updateCurrentHistoryJobState,
  updatePageStatusIndicator,
  rafQueue,
);

await api.onStreamEvent({ type: "page", page_num: 2, status: "extracting", processed_count: 1, total_pages: 3 });
await api.onStreamEvent({ type: "page", page_num: 3, status: "failed", processed_count: 2, total_pages: 3 });

const beforeFlush = {
  pageSelectPatches: pageSelectPatches.slice(),
  historyUiPatches: historyUiPatches.slice(),
  historyStatePatches: historyStatePatches.slice(),
  syncPageNavCalls,
  progressCalls,
  loadCurrentPageCalls,
  statusIndicatorCalls,
  controlAvailabilityCalls,
  rafQueueLength: rafQueue.length,
  job: {
    processed_count: state.job.processed_count,
    succeeded_pages: state.job.succeeded_pages.slice(),
    failed_pages: state.job.failed_pages.slice(),
  },
  internals: api.getInternals(),
};

api.flushRaf();

const afterFirstFlush = {
  pageSelectPatches: pageSelectPatches.slice(),
  historyUiPatches: historyUiPatches.slice(),
  syncPageNavCalls,
  progressCalls,
  loadCurrentPageCalls,
  statusIndicatorCalls,
  controlAvailabilityCalls,
  job: {
    processed_count: state.job.processed_count,
    succeeded_pages: state.job.succeeded_pages.slice(),
    failed_pages: state.job.failed_pages.slice(),
  },
  internals: api.getInternals(),
};

await api.onStreamEvent({ type: "page", page_num: 1, status: "done", processed_count: 2, total_pages: 3 });
const sameStatusBeforeFlush = {
  loadCurrentPageCalls,
  controlAvailabilityCalls,
  rafQueueLength: rafQueue.length,
};
api.flushRaf();

state.currentPageNum = 2;
state.currentPage = { page_num: 2, status: "extracting", content: "", error: "" };
await api.onStreamEvent({ type: "page", page_num: 2, status: "done", processed_count: 3, total_pages: 3 });
const changedCurrentBeforeFlush = {
  loadCurrentPageCalls,
  controlAvailabilityCalls,
  rafQueueLength: rafQueue.length,
  statusIndicatorCalls,
  job: {
    processed_count: state.job.processed_count,
    succeeded_pages: state.job.succeeded_pages.slice(),
    failed_pages: state.job.failed_pages.slice(),
  },
};
api.flushRaf();

const afterChangedCurrent = {
  pageSelectPatches: pageSelectPatches.slice(),
  historyUiPatches: historyUiPatches.slice(),
  syncPageNavCalls,
  progressCalls,
  loadCurrentPageCalls,
  statusIndicatorCalls,
  controlAvailabilityCalls,
  job: {
    processed_count: state.job.processed_count,
    succeeded_pages: state.job.succeeded_pages.slice(),
    failed_pages: state.job.failed_pages.slice(),
  },
  currentPage: { ...state.currentPage },
  internals: api.getInternals(),
  logs,
};

console.log(
  JSON.stringify({
    beforeFlush,
    afterFirstFlush,
    sameStatusBeforeFlush,
    changedCurrentBeforeFlush,
    afterChangedCurrent,
  }),
);
"""
        )

        self.assertEqual([], result["beforeFlush"]["pageSelectPatches"])
        self.assertEqual([], result["beforeFlush"]["historyUiPatches"])
        self.assertEqual(1, result["beforeFlush"]["rafQueueLength"])
        self.assertEqual(0, result["beforeFlush"]["loadCurrentPageCalls"])
        self.assertEqual(0, result["beforeFlush"]["controlAvailabilityCalls"])
        self.assertEqual([1], result["beforeFlush"]["job"]["succeeded_pages"])
        self.assertEqual([3], result["beforeFlush"]["job"]["failed_pages"])
        self.assertEqual(2, result["beforeFlush"]["job"]["processed_count"])
        self.assertFalse(result["beforeFlush"]["internals"]["historyDirty"])

        self.assertEqual([2, 3], result["afterFirstFlush"]["pageSelectPatches"])
        self.assertEqual(["job-1"], result["afterFirstFlush"]["historyUiPatches"])
        self.assertEqual(1, result["afterFirstFlush"]["syncPageNavCalls"])
        self.assertEqual(0, result["afterFirstFlush"]["loadCurrentPageCalls"])
        self.assertEqual(0, result["afterFirstFlush"]["controlAvailabilityCalls"])
        self.assertEqual([1], result["afterFirstFlush"]["job"]["succeeded_pages"])
        self.assertEqual([3], result["afterFirstFlush"]["job"]["failed_pages"])
        self.assertEqual(2, result["afterFirstFlush"]["job"]["processed_count"])
        self.assertFalse(result["afterFirstFlush"]["internals"]["historyDirty"])

        self.assertEqual(
            0,
            result["sameStatusBeforeFlush"]["loadCurrentPageCalls"],
            "当前页命中的重复终态事件不应再次拉取正文",
        )
        self.assertEqual(
            1,
            result["sameStatusBeforeFlush"]["controlAvailabilityCalls"],
            "当前页状态事件应触发一次按钮可用性重算",
        )

        self.assertEqual(
            1,
            result["changedCurrentBeforeFlush"]["loadCurrentPageCalls"],
            "当前页从非终态进入 done/failed 时应拉取一次最新正文/错误详情",
        )
        self.assertEqual(
            2,
            result["changedCurrentBeforeFlush"]["controlAvailabilityCalls"],
            "当前页进入终态时应再次触发按钮可用性重算",
        )
        self.assertEqual([1, 2], result["afterChangedCurrent"]["job"]["succeeded_pages"])
        self.assertEqual([3], result["afterChangedCurrent"]["job"]["failed_pages"])
        self.assertEqual(3, result["afterChangedCurrent"]["job"]["processed_count"])
        self.assertEqual(
            "done",
            result["afterChangedCurrent"]["currentPage"]["status"],
            "当前页状态应随着命中事件更新到终态",
        )

    def test_history_action_availability_refresh_follows_busy_state(self) -> None:
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

function makeArticleNode() {
  const openButton = { disabled: null };
  const deleteButton = { disabled: null, title: "" };
  return {
    node: {
      parentNode: {},
      querySelector(selector) {
        if (selector === '[data-history-action="open"]') return openButton;
        if (selector === '[data-history-action="delete"]') return deleteButton;
        return null;
      },
    },
    openButton,
    deleteButton,
  };
}

const state = {
  pendingUpload: false,
  previewLoading: false,
  isBusy: false,
  historyJobs: [
    { job_id: "job-done", status: "done" },
    { job_id: "job-extracting", status: "extracting" },
  ],
};
const doneEntry = makeArticleNode();
const extractingEntry = makeArticleNode();
const historyItemByJobId = {
  "job-done": doneEntry.node,
  "job-extracting": extractingEntry.node,
};

const api = new Function(
  "state",
  "historyItemByJobId",
  `
var historyPanelExpanded = true;
${pick("function canDeleteHistoryJob(item)")}
${pick("function isHistoryActionBlocked()")}
${pick("function applyHistoryJobActionAvailability(item, article)")}
${pick("function refreshHistoryActionAvailability()")}
return {
  refreshHistoryActionAvailability,
  setHistoryPanelExpanded(value) {
    historyPanelExpanded = !!value;
  },
};
`,
)(state, historyItemByJobId);

function snapshot() {
  return {
    done: {
      openDisabled: doneEntry.openButton.disabled,
      deleteDisabled: doneEntry.deleteButton.disabled,
      deleteTitle: doneEntry.deleteButton.title,
    },
    extracting: {
      openDisabled: extractingEntry.openButton.disabled,
      deleteDisabled: extractingEntry.deleteButton.disabled,
      deleteTitle: extractingEntry.deleteButton.title,
    },
  };
}

api.refreshHistoryActionAvailability();
const idle = snapshot();

state.isBusy = true;
api.refreshHistoryActionAvailability();
const busy = snapshot();

state.isBusy = false;
api.setHistoryPanelExpanded(false);
api.refreshHistoryActionAvailability();
const collapsed = snapshot();

console.log(JSON.stringify({ idle, busy, collapsed }));
"""
        )

        self.assertFalse(result["idle"]["done"]["openDisabled"])
        self.assertFalse(result["idle"]["done"]["deleteDisabled"])
        self.assertEqual("删除该历史任务", result["idle"]["done"]["deleteTitle"])
        self.assertFalse(result["idle"]["extracting"]["openDisabled"])
        self.assertTrue(result["idle"]["extracting"]["deleteDisabled"])
        self.assertEqual("进行中的任务暂不可删除", result["idle"]["extracting"]["deleteTitle"])

        self.assertTrue(result["busy"]["done"]["openDisabled"])
        self.assertTrue(result["busy"]["done"]["deleteDisabled"])
        self.assertTrue(result["busy"]["extracting"]["openDisabled"])
        self.assertTrue(result["busy"]["extracting"]["deleteDisabled"])

        self.assertTrue(
            result["collapsed"]["done"]["openDisabled"],
            "历史栏折叠态下不应继续写入历史按钮 DOM（保持上一次状态）",
        )

    def test_history_patch_is_short_circuited_when_panel_collapsed(self) -> None:
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

const rafQueue = [];
let nextRafId = 1;
const windowStub = {
  requestAnimationFrame(callback) {
    const entry = { id: nextRafId++, callback };
    rafQueue.push(entry);
    return entry.id;
  },
};

const patchedHistoryJobs = [];

const api = new Function(
  "window",
  "rafQueue",
  "patchedHistoryJobs",
  `
var pendingUiFlush = null;
var pendingUiFrameId = null;
var historyPanelExpanded = false;
var historyDirty = false;
function normalizePageNum(value) {
  var pageNum = Number(value);
  return Number.isInteger(pageNum) && pageNum > 0 ? pageNum : null;
}
function patchPageSelectOption() {}
function syncPageNavigationState() {}
function patchHistoryJobItem(jobId) {
  patchedHistoryJobs.push(jobId);
}
${pick("function schedulePageEventUiFlush(patch)")}
${pick("function flushPageEventUi()")}
return {
  schedulePageEventUiFlush,
  flushRaf() {
    if (!rafQueue.length) return false;
    const entry = rafQueue.shift();
    entry.callback();
    return true;
  },
  setHistoryPanelExpanded(value) {
    historyPanelExpanded = !!value;
  },
  getInternals() {
    return { historyDirty, pendingUiFrameId, queuedRaf: rafQueue.length };
  },
};
`,
)(windowStub, rafQueue, patchedHistoryJobs);

api.schedulePageEventUiFlush({ historyJobIds: ["job-1"] });
const beforeFlush = {
  patchedHistoryJobs: patchedHistoryJobs.slice(),
  internals: api.getInternals(),
};
api.flushRaf();
const afterCollapsedFlush = {
  patchedHistoryJobs: patchedHistoryJobs.slice(),
  internals: api.getInternals(),
};

api.setHistoryPanelExpanded(true);
api.schedulePageEventUiFlush({ historyJobIds: ["job-1"] });
api.flushRaf();
const afterExpandedFlush = {
  patchedHistoryJobs: patchedHistoryJobs.slice(),
  internals: api.getInternals(),
};

console.log(JSON.stringify({ beforeFlush, afterCollapsedFlush, afterExpandedFlush }));
"""
        )

        self.assertEqual([], result["beforeFlush"]["patchedHistoryJobs"])
        self.assertEqual(1, result["beforeFlush"]["internals"]["queuedRaf"])
        self.assertFalse(result["beforeFlush"]["internals"]["historyDirty"])

        self.assertEqual([], result["afterCollapsedFlush"]["patchedHistoryJobs"])
        self.assertTrue(
            result["afterCollapsedFlush"]["internals"]["historyDirty"],
            "历史栏折叠时，SSE flush 应只打 dirty 标记，不执行 DOM patch",
        )

        self.assertEqual(
            ["job-1"],
            result["afterExpandedFlush"]["patchedHistoryJobs"],
            "历史栏展开后，同类 patch 才应执行 history item 的 DOM 更新",
        )
