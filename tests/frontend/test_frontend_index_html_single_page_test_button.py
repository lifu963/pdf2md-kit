"""
Task 8: 单页测试按钮在 `frontend/index.html` 中的结构与行为

锁定 `spec.md §5.2 / §5.3 / §7.4` 的用户可见行为：

1. 静态结构：`.editor-pane` 顶栏 `.editor-status-actions` 内存在 `#single-page-test-btn`，
   位于 `#start-extraction-btn` **之前**，文本 `单页测试`。
2. `state` 初始含 `singlePageTest: { status: "idle", pageNum: null, content: null, error: null }`。
3. `updateControlAvailability()` 的按钮可用性/可见性按生命周期表派生：
   - 无 preparedFile / preview 未 ready → 单页测试按钮可见、禁用；
   - 准备就绪 → 可点；
   - `singlePageTest.status === "running"` → 单页测试 **与** 开始提取**同时禁用**；
   - `state.jobId` 非空 → 两个按钮同步 `display: none`。
4. `runSinglePageTestForPreparedFile`/`renderSinglePageTest*` 渲染在 `test-running` /
   `test-done` / `test-failed` 三种态下的 `#status-badge` / `#status-text` / `#page-editor` /
   `#page-error`；单页标题仅出现在顶栏 `#status-text`，正文区 `#page-status-indicator` 不占重复标题。

说明：`frontend/index.html` 的内联脚本不走 TypeScript 模块，因此用"抽取函数 + new Function"
的方式复用（参考 `test_frontend_index_html_save_page_no_pdf_reload_regression.py`）。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
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


class SinglePageTestButtonStructureTests(TestCase):
    def test_single_page_test_button_appears_before_start_extraction_in_editor_pane(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn('id="single-page-test-btn"', source)
        self.assertIn("单页测试", source)

        section_start = source.index('class="editor-status-bar"')
        section_chunk = source[section_start : section_start + 4000]

        single_idx = section_chunk.find('id="single-page-test-btn"')
        start_idx = section_chunk.find('id="start-extraction-btn"')

        self.assertGreater(single_idx, 0, "#single-page-test-btn 必须在编辑区顶栏内")
        self.assertGreater(start_idx, 0, "#start-extraction-btn 必须在编辑区顶栏内")
        self.assertLess(
            single_idx,
            start_idx,
            "#single-page-test-btn 必须排在 #start-extraction-btn 之前",
        )

        button_match = re.search(
            r'<button\s+id="single-page-test-btn"[^>]*>[^<]*</button>',
            section_chunk,
        )
        self.assertIsNotNone(button_match)
        button_tag = button_match.group(0) if button_match else ""
        self.assertIn("pixel-btn-sm", button_tag, "按钮应使用紧凑像素按钮样式")
        self.assertNotIn("btn-success", button_tag, "按钮不得套用 btn-success（属于开始提取）")
        self.assertIn("单页测试", button_tag)
        self.assertIn('type="button"', button_tag)
        self.assertIn("disabled", button_tag, "初始应为禁用（尚无 preparedFile）")

    def test_inline_state_carries_single_page_test_idle_default(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        state_block_start = source.index("var state = {")
        state_block = source[state_block_start : state_block_start + 800]
        self.assertIn("singlePageTest", state_block)
        # 初始字段形状足以让 updateControlAvailability 分支稳定
        self.assertRegex(
            state_block,
            r'singlePageTest:\s*\{\s*status:\s*"idle"',
        )

    def test_click_binding_exists_for_single_page_test_button(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("singlePageTestBtn", source)
        self.assertRegex(
            source,
            r'els\.singlePageTestBtn\.addEventListener\(\s*"click"',
        )
        self.assertIn("runSinglePageTestForPreparedFile", source)


def _extract_update_control_availability() -> str:
    source = INDEX_HTML.read_text(encoding="utf-8")
    start = source.index("function updateControlAvailability()")
    # 函数体止于紧随其后的第一条顶层声明；复用与现有回归测试一致的切片策略。
    # 先抓大块，再按"随后出现的下一个 function/async function"做粗切。
    block = source[start : start + 14000]
    # updateControlAvailability 之后紧接 updateProgress；用它做终止锚点。
    end = block.index("\n        function updateProgress(")
    return block[:end]


class UpdateControlAvailabilityBehaviorTests(TestCase):
    def test_button_visibility_and_disabled_flags_cover_lifecycle(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const startIdx = html.indexOf("function updateControlAvailability()");
const chunk = html.slice(startIdx, startIdx + 14000);
const endIdx = chunk.indexOf("\n        function updateProgress(");
const fnText = chunk.slice(0, endIdx);

function makeFakeEl() {
  return {
    disabled: false,
    style: { display: "" },
    className: "",
    textContent: "",
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    value: "",
  };
}

function makeEls() {
  return {
    startExtractionBtn: makeFakeEl(),
    singlePageTestBtn: makeFakeEl(),
    retryPageBtn: makeFakeEl(),
    discardOutputBtn: makeFakeEl(),
    pageEditor: makeFakeEl(),
    outputEditor: makeFakeEl(),
    uploadPdfBtn: makeFakeEl(),
    uploadPdfHeaderBtn: makeFakeEl(),
    pdfFileInput: makeFakeEl(),
    refreshJobBtn: makeFakeEl(),
    loadConfigBtn: makeFakeEl(),
    restoreInitialConfigBtn: makeFakeEl(),
    saveConfigBtn: makeFakeEl(),
    configTestBtn: makeFakeEl(),
    buildOutputBtn: makeFakeEl(),
    outputBuildMenuWrap: makeFakeEl(),
    pageEditorSection: makeFakeEl(),
    outputEditorSection: makeFakeEl(),
    statusBadge: makeFakeEl(),
    statusText: makeFakeEl(),
    progressWrapper: makeFakeEl(),
    progressFill: makeFakeEl(),
    progressLabel: makeFakeEl(),
    pageStatusIndicator: makeFakeEl(),
    pageStatusText: makeFakeEl(),
    pageError: makeFakeEl(),
    pdfFooterNav: makeFakeEl(),
  };
}

function baseState(overrides) {
  return {
    jobId: null,
    job: null,
    historyJobs: [],
    pages: [],
    currentPageNum: 1,
    currentPage: null,
    isBusy: false,
    pendingUpload: false,
    previewLoading: false,
    previewReady: false,
    preparedFile: null,
    singlePageTest: { status: "idle", pageNum: null, content: null, error: null },
    ...(overrides ?? {}),
  };
}

function callUpdate(state) {
  const els = makeEls();
  const factorySrc = `${fnText}\nreturn updateControlAvailability;`;
  const update = new Function(
    "state",
    "els",
    "STATUS_LABELS",
    "getWorkspaceStatus",
    "updateProgress",
    "closeBuildOutputMenu",
    "getPageNavigationTotal",
    "updatePageSelect",
    "renderHistoryList",
    "refreshHistoryActionAvailability",
    "renderSinglePageTestView",
    factorySrc,
  )(
    state,
    els,
    { idle: "空闲", uploading: "上传中", previewing: "载入中", prepared: "就绪" },
    () => {
      if (state.pendingUpload) return "uploading";
      if (state.job) return state.job.status;
      if (state.previewLoading) return "previewing";
      if (state.previewReady) return "prepared";
      return "idle";
    },
    () => {},
    () => {},
    () => 0,
    () => {},
    () => {},
    () => {},
    () => {},
  );
  update();
  return { els };
}

// Case 1: idle, no file -> 单页测试 visible but disabled
const case1 = callUpdate(baseState());

// Case 2: file prepared + previewReady + no job -> 单页测试可点
const case2 = callUpdate(
  baseState({ preparedFile: { name: "a.pdf" }, previewReady: true }),
);

// Case 3: 进行中 -> 两按钮均禁用
const case3 = callUpdate(
  baseState({
    preparedFile: { name: "a.pdf" },
    previewReady: true,
    singlePageTest: { status: "running", pageNum: 2, content: null, error: null },
  }),
);

// Case 4: 文件操作中 -> 单页测试禁用
const case4 = callUpdate(
  baseState({
    preparedFile: { name: "a.pdf" },
    previewReady: true,
    previewLoading: true,
  }),
);

// Case 5: jobId 存在 -> 两按钮同步 display:none
const case5 = callUpdate(
  baseState({ jobId: "job-x", job: { status: "extracting", total_pages: 2, processed_count: 0 }, preparedFile: null, previewReady: false }),
);

// Case 6: 测试已完成 (done)，无 job -> 按钮恢复可点
const case6 = callUpdate(
  baseState({
    preparedFile: { name: "a.pdf" },
    previewReady: true,
    singlePageTest: { status: "done", pageNum: 1, content: "## ok", error: null },
  }),
);

// Case 7: extracting + 当前页 done -> 允许重新提取
const case7 = callUpdate(
  baseState({
    jobId: "job-x",
    job: { status: "extracting", total_pages: 2, processed_count: 1 },
    pages: [{ page_num: 1, status: "done" }],
    currentPageNum: 1,
    currentPage: { page_num: 1, status: "done", content: "ok", error: "" },
  }),
);

// Case 8: extracting + 当前页 extracting -> 禁止重新提取（避免并发乱序）
const case8 = callUpdate(
  baseState({
    jobId: "job-x",
    job: { status: "extracting", total_pages: 2, processed_count: 1 },
    pages: [{ page_num: 1, status: "extracting" }],
    currentPageNum: 1,
    currentPage: { page_num: 1, status: "extracting", content: "", error: "" },
  }),
);

// Case 9: extracted + 当前页 failed -> 允许重新提取
const case9 = callUpdate(
  baseState({
    jobId: "job-x",
    job: { status: "extracted", total_pages: 2, processed_count: 2 },
    pages: [{ page_num: 2, status: "failed" }],
    currentPageNum: 2,
    currentPage: { page_num: 2, status: "failed", content: "", error: "boom" },
  }),
);

// Case 10: failed + 当前页 done -> 禁止重新提取（后端仅允许 extracting/extracted）
const case10 = callUpdate(
  baseState({
    jobId: "job-x",
    job: { status: "failed", total_pages: 2, processed_count: 1 },
    pages: [{ page_num: 1, status: "done" }],
    currentPageNum: 1,
    currentPage: { page_num: 1, status: "done", content: "ok", error: "" },
  }),
);

function snap(ctx) {
  return {
    singleDisabled: ctx.els.singlePageTestBtn.disabled,
    singleDisplay: ctx.els.singlePageTestBtn.style.display,
    startDisabled: ctx.els.startExtractionBtn.disabled,
    startDisplay: ctx.els.startExtractionBtn.style.display,
    retryDisabled: ctx.els.retryPageBtn.disabled,
  };
}

console.log(
  JSON.stringify({
    case1: snap(case1),
    case2: snap(case2),
    case3: snap(case3),
    case4: snap(case4),
    case5: snap(case5),
    case6: snap(case6),
    case7: snap(case7),
    case8: snap(case8),
    case9: snap(case9),
    case10: snap(case10),
  }),
);
"""
        )

        # Case 1: idle + 无 file
        self.assertTrue(result["case1"]["singleDisabled"])
        self.assertEqual("", result["case1"]["singleDisplay"])
        self.assertTrue(result["case1"]["startDisabled"])

        # Case 2: 准备就绪 -> 可点
        self.assertFalse(result["case2"]["singleDisabled"])
        self.assertFalse(result["case2"]["startDisabled"])

        # Case 3: 测试进行中 -> 两按钮均禁用
        self.assertTrue(result["case3"]["singleDisabled"])
        self.assertTrue(result["case3"]["startDisabled"])

        # Case 4: 文件操作中 -> 两按钮均禁用
        self.assertTrue(result["case4"]["singleDisabled"])
        self.assertTrue(result["case4"]["startDisabled"])

        # Case 5: jobId 存在 -> 两按钮 display:none
        self.assertEqual("none", result["case5"]["singleDisplay"])
        self.assertEqual("none", result["case5"]["startDisplay"])

        # Case 6: 完成态 -> 恢复可点
        self.assertFalse(result["case6"]["singleDisabled"])
        self.assertFalse(result["case6"]["startDisabled"])

        # Case 7: extracting + 当前页 done -> 可重试
        self.assertFalse(result["case7"]["retryDisabled"])

        # Case 8: 当前页 extracting（请求进行中） -> 禁止重试
        self.assertTrue(result["case8"]["retryDisabled"])

        # Case 9: extracted + 当前页 failed -> 可重试
        self.assertFalse(result["case9"]["retryDisabled"])

        # Case 10: job failed -> 禁止重试
        self.assertTrue(result["case10"]["retryDisabled"])

    def test_page_selection_and_page_reload_recompute_retry_button_for_current_page(self) -> None:
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

function pickAsyncByName(functionName) {
  const pattern = new RegExp(`async function ${escapeRegex(functionName)}\\([^)]*\\) \\{[\\s\\S]*?\\r?\\n        \\}`);
  const match = html.match(pattern);
  if (!match) {
    throw new Error(`failed to locate async function ${functionName}(...) in frontend/index.html`);
  }
  return match[0];
}

function makeFakeEl() {
  return {
    disabled: false,
    readOnly: false,
    style: { display: "" },
    className: "",
    textContent: "",
    value: "",
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() { return false; },
    },
  };
}

const state = {
  jobId: "job-x",
  job: { status: "extracting", total_pages: 100, processed_count: 57 },
  historyJobs: [],
  pages: [
    { page_num: 1, status: "done" },
    { page_num: 95, status: "pending" },
  ],
  currentPageNum: 1,
  currentPage: { page_num: 1, status: "done", content: "ok", error: "" },
  isBusy: false,
  pendingUpload: false,
  previewLoading: false,
  previewReady: false,
  preparedFile: null,
  singlePageTest: { status: "idle", pageNum: null, content: null, error: null },
};

const els = {
  startExtractionBtn: makeFakeEl(),
  singlePageTestBtn: makeFakeEl(),
  retryPageBtn: makeFakeEl(),
  discardOutputBtn: makeFakeEl(),
  pageEditor: makeFakeEl(),
  outputEditor: makeFakeEl(),
  uploadPdfBtn: makeFakeEl(),
  uploadPdfHeaderBtn: makeFakeEl(),
  pdfFileInput: makeFakeEl(),
  refreshJobBtn: makeFakeEl(),
  loadConfigBtn: makeFakeEl(),
  restoreInitialConfigBtn: makeFakeEl(),
  saveConfigBtn: makeFakeEl(),
  configTestBtn: makeFakeEl(),
  buildOutputBtn: makeFakeEl(),
  outputBuildMenuWrap: makeFakeEl(),
  pageEditorSection: makeFakeEl(),
  outputEditorSection: makeFakeEl(),
  statusBadge: makeFakeEl(),
  statusText: makeFakeEl(),
  progressWrapper: makeFakeEl(),
  progressFill: makeFakeEl(),
  progressLabel: makeFakeEl(),
  pageStatusIndicator: makeFakeEl(),
  pageStatusText: makeFakeEl(),
  pageError: makeFakeEl(),
  pdfFooterNav: makeFakeEl(),
  pageSelect: makeFakeEl(),
};
els.pageSelect.value = "95";

let requestedPage95Status = "pending";
let renderPdfPageCalls = 0;
let syncPageNavigationStateCalls = 0;
let updatePageStatusIndicatorCalls = 0;

const factorySrc = `
${pick("function updateControlAvailability()")}
${pick("function normalizePageNum(value)")}
${pickAsyncByName("loadCurrentPage")}
${pickAsyncByName("onPageSelectionChanged")}
return { updateControlAvailability, loadCurrentPage, onPageSelectionChanged };
`;

const api = new Function(
  "state",
  "els",
  "STATUS_LABELS",
  "getWorkspaceStatus",
  "updateProgress",
  "closeBuildOutputMenu",
  "getPageNavigationTotal",
  "refreshHistoryActionAvailability",
  "renderSinglePageTestView",
  "requestJson",
  "updatePageStatusIndicator",
  "flushPendingPageSave",
  "syncPageNavigationState",
  "renderPdfPage",
  factorySrc,
)(
  state,
  els,
  { extracting: "提取中", extracted: "提取完成", failed: "失败" },
  () => {
    if (state.pendingUpload) return "uploading";
    if (state.job) return state.job.status;
    if (state.previewLoading) return "previewing";
    if (state.previewReady) return "prepared";
    return "idle";
  },
  () => {},
  () => {},
  () => state.pages.length,
  () => {},
  () => {},
  async (path) => {
    if (!path.endsWith("/pages/95")) {
      throw new Error(`unexpected request path: ${path}`);
    }
    return {
      page_num: 95,
      status: requestedPage95Status,
      content: requestedPage95Status === "done" ? "## page 95" : "",
      error: "",
    };
  },
  () => {
    updatePageStatusIndicatorCalls += 1;
  },
  async () => {},
  () => {
    syncPageNavigationStateCalls += 1;
  },
  async () => {
    renderPdfPageCalls += 1;
  },
);

api.updateControlAvailability();
const beforeSwitch = {
  retryDisabled: els.retryPageBtn.disabled,
};

await api.onPageSelectionChanged();
const afterSwitchToPending = {
  retryDisabled: els.retryPageBtn.disabled,
  currentPageNum: state.currentPageNum,
  currentPageStatus: state.currentPage && state.currentPage.status,
  renderPdfPageCalls,
  syncPageNavigationStateCalls,
  updatePageStatusIndicatorCalls,
};

requestedPage95Status = "done";
state.pages[1] = { page_num: 95, status: "done" };
await api.loadCurrentPage(95);
const afterReloadToDone = {
  retryDisabled: els.retryPageBtn.disabled,
  currentPageNum: state.currentPageNum,
  currentPageStatus: state.currentPage && state.currentPage.status,
  updatePageStatusIndicatorCalls,
};

console.log(JSON.stringify({ beforeSwitch, afterSwitchToPending, afterReloadToDone }));
"""
        )

        self.assertFalse(result["beforeSwitch"]["retryDisabled"])
        self.assertTrue(
            result["afterSwitchToPending"]["retryDisabled"],
            "切到 pending/extracting 页后，应按当前页状态立即禁用重新提取单页",
        )
        self.assertEqual(95, result["afterSwitchToPending"]["currentPageNum"])
        self.assertEqual("pending", result["afterSwitchToPending"]["currentPageStatus"])
        self.assertEqual(1, result["afterSwitchToPending"]["renderPdfPageCalls"])
        self.assertEqual(1, result["afterSwitchToPending"]["syncPageNavigationStateCalls"])
        self.assertGreaterEqual(result["afterSwitchToPending"]["updatePageStatusIndicatorCalls"], 1)

        self.assertFalse(
            result["afterReloadToDone"]["retryDisabled"],
            "当前页详情重新加载为 done 后，应同步恢复重新提取单页按钮",
        )
        self.assertEqual("done", result["afterReloadToDone"]["currentPageStatus"])


class RenderSinglePageTestResultTests(TestCase):
    def test_test_running_done_failed_render_status_and_editor(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const startIdx = html.indexOf("function renderSinglePageTestView(");
if (startIdx < 0) {
  throw new Error("renderSinglePageTestView 未在 frontend/index.html 定义");
}
const chunk = html.slice(startIdx, startIdx + 14000);
const endMatch = chunk.match(/\n        (?:async\s+)?function\s+\w+/);
if (!endMatch) {
  throw new Error("无法定位 renderSinglePageTestView 结尾");
}
const fnText = chunk.slice(0, endMatch.index);

function makeFakeEl() {
  return {
    disabled: false,
    readOnly: false,
    style: { display: "" },
    className: "",
    textContent: "",
    value: "",
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
  };
}

function makeClassListSpy() {
  var bag = {};
  return {
    add(name) {
      bag[name] = true;
    },
    remove(name) {
      delete bag[name];
    },
    toggle() {},
    contains(name) {
      return Object.prototype.hasOwnProperty.call(bag, name) && !!bag[name];
    },
  };
}

function makeEls() {
  return {
    statusBadge: makeFakeEl(),
    statusText: {
      disabled: false,
      readOnly: false,
      style: { display: "" },
      className: "",
      textContent: "",
      value: "",
      classList: makeClassListSpy(),
    },
    pageStatusIndicator: {
      disabled: false,
      readOnly: false,
      style: { display: "" },
      className: "",
      textContent: "",
      value: "",
      classList: makeClassListSpy(),
    },
    pageStatusText: makeFakeEl(),
    pageEditor: makeFakeEl(),
    pageError: makeFakeEl(),
  };
}

function invoke(test) {
  const els = makeEls();
  const factorySrc = `${fnText}\nreturn renderSinglePageTestView;`;
  const fn = new Function("els", factorySrc)(els);
  fn(test);
  return {
    statusBadge: els.statusBadge.className,
    statusText: els.statusText.textContent,
    statusTextBlinking: els.statusText.classList.contains("blink"),
    pageStatusHidden: els.pageStatusIndicator.classList.contains("hidden"),
    pageStatusIndicator: els.pageStatusIndicator.className,
    pageStatusText: els.pageStatusText.textContent,
    editorValue: els.pageEditor.value,
    editorDisabled: els.pageEditor.disabled,
    editorReadOnly: els.pageEditor.readOnly,
    pageError: els.pageError.textContent,
  };
}

const running = invoke({ status: "running", pageNum: 3, content: null, error: null });
const done = invoke({ status: "done", pageNum: 3, content: "## preview body", error: null });
const failedAuth = invoke({
  status: "failed",
  pageNum: 5,
  content: null,
  error: { code: "LLM_AUTH_FAILED", message: "认证失败" },
});
const failedGeneric = invoke({
  status: "failed",
  pageNum: 5,
  content: null,
  error: { code: "LLM_TIMEOUT", message: "请求超时" },
});

console.log(JSON.stringify({ running, done, failedAuth, failedGeneric }));
"""
        )

        running = result["running"]
        self.assertIn("测试中", running["statusText"])
        self.assertIn("第 3 页", running["statusText"])
        self.assertTrue(running["pageStatusHidden"])
        self.assertTrue(running["statusTextBlinking"])
        self.assertEqual("", running["pageStatusText"])
        self.assertIn("status-extracting", running["statusBadge"])
        self.assertEqual("", running["editorValue"])
        self.assertTrue(running["editorDisabled"])

        done = result["done"]
        self.assertIn("测试结果", done["statusText"])
        self.assertIn("第 3 页", done["statusText"])
        self.assertTrue(done["pageStatusHidden"])
        self.assertFalse(done["statusTextBlinking"])
        self.assertEqual("", done["pageStatusText"])
        self.assertIn("status-done", done["statusBadge"])
        self.assertEqual("## preview body", done["editorValue"])
        self.assertTrue(done["editorDisabled"] or done["editorReadOnly"])

        failed_auth = result["failedAuth"]
        self.assertIn("测试失败", failed_auth["statusText"])
        self.assertIn("第 5 页", failed_auth["statusText"])
        self.assertTrue(failed_auth["pageStatusHidden"])
        self.assertFalse(failed_auth["statusTextBlinking"])
        self.assertEqual("", failed_auth["pageStatusText"])
        self.assertIn("status-failed", failed_auth["statusBadge"])
        self.assertEqual("", failed_auth["editorValue"])
        self.assertIn("认证失败", failed_auth["pageError"])
        self.assertIn("API Key", failed_auth["pageError"])

        failed_generic = result["failedGeneric"]
        self.assertIn("请求超时", failed_generic["pageError"])
        # 非 auth/missing-key 类错误不应强制出现 API Key 提示
        self.assertNotIn("API Key", failed_generic["pageError"])


class PreparedFileChangePropagatesSinglePageTestResetTests(TestCase):
    """锁 `plan.md §9` "preparedFile 变更后 singlePageTest 被 reset" 的联动。

    对应 spec §5.2 末行（"用户上传/替换了 PDF → 清空上一次测试结果"）。

    `preparedFile` 是 `frontend/index.html` 内联 state 字段（不是 WorkspaceStore），
    其变更发生在 `prepareFileForExtraction` 路径。实际联动靠两段代码协作：

    1. `prepareFileForExtraction` 在 `state.preparedFile = file` 之后调用 `resetEditors()`；
    2. `resetEditors()` 内部调用 `resetSinglePageTest()`，把 state.singlePageTest 置回 idle。

    本测试通过"静态链路断言 + 动态 resetSinglePageTest 语义断言"联合锁住这条副作用链。
    """

    def test_prepare_file_for_extraction_calls_reset_editors_after_setting_prepared_file(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        start = source.index("async function prepareFileForExtraction(")
        end = source.index("\n        async function ", start + 1)
        fn_body = source[start:end]
        set_idx = fn_body.index("state.preparedFile = file")
        reset_idx = fn_body.index("resetEditors()", set_idx)
        self.assertGreater(
            reset_idx,
            set_idx,
            "prepareFileForExtraction 必须在 state.preparedFile = file 之后调用 resetEditors()，"
            "以保证 spec §5.2 的「上传/替换 PDF → 清空上一次测试结果」语义。",
        )

    def test_reset_editors_invokes_reset_single_page_test(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        start = source.index("function resetEditors()")
        end = source.index("\n        function ", start + 1)
        fn_body = source[start:end]
        self.assertIn(
            "resetSinglePageTest()",
            fn_body,
            "resetEditors() 必须调用 resetSinglePageTest()，否则替换 PDF 时旧的测试结果会残留。",
        )

    def test_reset_single_page_test_puts_store_back_to_idle_snapshot(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const startIdx = html.indexOf("function resetSinglePageTest()");
if (startIdx < 0) {
  throw new Error("resetSinglePageTest 未在 frontend/index.html 定义");
}
const chunk = html.slice(startIdx, startIdx + 2000);
const endMatch = chunk.match(/\n        (?:async\s+)?function\s+\w+/);
if (!endMatch) {
  throw new Error("无法定位 resetSinglePageTest 结尾");
}
const fnText = chunk.slice(0, endMatch.index);

const dirtyState = {
  singlePageTest: {
    status: "done",
    pageNum: 3,
    content: "## stale",
    error: null,
  },
};
const factorySrc = `${fnText}\nreturn resetSinglePageTest;`;
const fn = new Function("state", factorySrc)(dirtyState);
fn();

console.log(JSON.stringify({ singlePageTest: dirtyState.singlePageTest }));
"""
        )
        self.assertEqual(
            {"status": "idle", "pageNum": None, "content": None, "error": None},
            result["singlePageTest"],
        )
