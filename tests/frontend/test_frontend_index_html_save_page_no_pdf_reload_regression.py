"""
Regression: `fix/save-page-no-pdf-reload`

锁定如下两条用户可感知的不变量，防止保存页面再次触发 PDF 重载 / 闪烁：

1. `frontend/index.html` 内联脚本中，`saveCurrentPage` 不再调用任何会重新加载 PDF 的
   路径（`refreshWorkspace()` / `loadPdfDocument(` / `pdfjsLib.getDocument(`），而是
   通过 `applySavedPage(saved)` 用 PUT 响应在本地派生后续状态。
2. `applySavedPage` 只做纯本地状态派生：
   - 覆盖 `state.currentPage`、`state.pages` 中对应条目；
   - 由 `state.pages` 重算 `state.job.succeeded_pages / failed_pages / processed_count`；
   - `EXTRACTING` 状态下仅当全部页终态且 **`failed_pages` 为空** 时跃迁为 `EXTRACTED`（与后端 `save_page` 规则一致）；
   - 不发起任何网络请求，不调用 PDF 相关 API。
3. `loadPdfDocument` 对同一 URL 幂等：已加载时直接返回，不再销毁+重下载。
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


class SavePageNoPdfReloadRegressionTests(TestCase):
    def test_save_current_page_source_does_not_trigger_pdf_reload(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        save_fn_start = source.index("async function saveCurrentPage()")
        save_fn_tail = source[save_fn_start + 1 :]
        next_decl = re.search(r"\n        (?:async\s+)?function\s+\w+\(", save_fn_tail)
        self.assertIsNotNone(next_decl, "无法定位 saveCurrentPage 后继的顶层函数声明")
        save_fn_end = save_fn_start + 1 + (next_decl.start() if next_decl is not None else 0)
        save_fn_body = source[save_fn_start:save_fn_end]

        self.assertIn(
            "applySavedPage(saved)",
            save_fn_body,
            "saveCurrentPage 必须通过 applySavedPage 本地派生状态",
        )
        for forbidden in ("refreshWorkspace(", "loadPdfDocument(", "pdfjsLib.getDocument("):
            self.assertNotIn(
                forbidden,
                save_fn_body,
                f"saveCurrentPage 不得再触发 {forbidden}（会导致 PDF 重载/闪烁）",
            )

        apply_fn_start = source.index("function applySavedPage(")
        apply_fn_end = source.index("\n        async function saveCurrentPage(", apply_fn_start)
        apply_fn_body = source[apply_fn_start:apply_fn_end]

        for forbidden in (
            "refreshWorkspace(",
            "loadPdfDocument(",
            "pdfjsLib",
            "fetch(",
            "requestJson(",
            "apiFetch(",
        ):
            self.assertNotIn(
                forbidden,
                apply_fn_body,
                f"applySavedPage 必须是纯本地派生，不得出现 {forbidden}",
            )

        load_fn_start = source.index("async function loadPdfDocument(")
        load_fn_body = source[load_fn_start : load_fn_start + 1500]
        load_fn_body = load_fn_body[: load_fn_body.index("\n        async function renderPdfPage(")]
        self.assertIn(
            "pdfCurrentUrl === url",
            load_fn_body,
            "loadPdfDocument 必须保留同 URL 幂等保护，避免无意重复加载",
        )

    def test_apply_saved_page_derives_pages_and_job_aggregate_locally(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const match = html.match(/function applySavedPage\(saved\) \{[\s\S]*?\r?\n        \}\r?\n/);
if (!match) {
  throw new Error("failed to locate applySavedPage definition in frontend/index.html");
}
const applyFnText = match[0];

function normalizePageNum(value) {
  const n = typeof value === "number" ? value : parseInt(String(value), 10);
  return Number.isFinite(n) && n >= 1 ? n : null;
}

function buildHost(state) {
  const calls = [];
  const els = {
    pageEditor: { value: "" },
    pageError: { textContent: "" },
  };
  const host = {
    state,
    els,
    normalizePageNum,
    updateCurrentHistoryJobState(patch) {
      calls.push({ kind: "history", patch });
    },
    schedulePageEventUiFlush(patch) {
      calls.push({ kind: "history-flush", patch });
    },
    updatePageStatusIndicator() {
      calls.push({ kind: "indicator" });
    },
    updatePageSelect() {
      calls.push({ kind: "select" });
    },
    updateControlAvailability() {
      calls.push({ kind: "control" });
    },
  };
  const factoryBody = `${applyFnText}\nreturn applySavedPage;`;
  const applySavedPage = new Function(
    "state",
    "els",
    "normalizePageNum",
    "updateCurrentHistoryJobState",
    "schedulePageEventUiFlush",
    "updatePageStatusIndicator",
    "updatePageSelect",
    "updateControlAvailability",
    factoryBody,
  )(
    host.state,
    host.els,
    host.normalizePageNum,
    host.updateCurrentHistoryJobState,
    host.schedulePageEventUiFlush,
    host.updatePageStatusIndicator,
    host.updatePageSelect,
    host.updateControlAvailability,
  );
  return { applySavedPage, host, calls, els };
}

// Scenario A: 当前页号与被保存页号一致，且该页原本 failed，保存后改为 done。
const stateA = {
  jobId: "job-1",
  currentPageNum: 2,
  currentPage: { page_num: 2, status: "failed", content: "", error: "timeout" },
  pages: [
    { page_num: 1, status: "done" },
    { page_num: 2, status: "failed" },
    { page_num: 3, status: "done" },
  ],
  job: {
    job_id: "job-1",
    status: "extracted",
    total_pages: 3,
    succeeded_pages: [1, 3],
    failed_pages: [2],
    processed_count: 3,
  },
};
const ctxA = buildHost(stateA);
ctxA.applySavedPage({ page_num: 2, status: "done", content: "# edited", error: "" });

// Scenario B: EXTRACTING 状态下保存最后一个 failed 页，应派生 EXTRACTING → EXTRACTED。
const stateB = {
  jobId: "job-2",
  currentPageNum: 3,
  currentPage: { page_num: 3, status: "failed", content: "", error: "llm" },
  pages: [
    { page_num: 1, status: "done" },
    { page_num: 2, status: "done" },
    { page_num: 3, status: "failed" },
  ],
  job: {
    job_id: "job-2",
    status: "extracting",
    total_pages: 3,
    succeeded_pages: [1, 2],
    failed_pages: [3],
    processed_count: 3,
  },
};
const ctxB = buildHost(stateB);
ctxB.applySavedPage({ page_num: 3, status: "done", content: "# recovered", error: "" });

// Scenario C: 保存结果页号与当前页不一致时，不应污染 pageEditor/pageError。
const stateC = {
  jobId: "job-3",
  currentPageNum: 1,
  currentPage: { page_num: 1, status: "done", content: "# keep", error: "" },
  pages: [
    { page_num: 1, status: "done" },
    { page_num: 2, status: "failed" },
  ],
  job: {
    job_id: "job-3",
    status: "extracted",
    total_pages: 2,
    succeeded_pages: [1],
    failed_pages: [2],
    processed_count: 2,
  },
};
const ctxC = buildHost(stateC);
ctxC.els.pageEditor.value = "# keep";
ctxC.applySavedPage({ page_num: 2, status: "done", content: "# page-2", error: "" });

// Scenario D: EXTRACTING + 全页终态但仍含失败页时，保存已成功页不得本地升为 EXTRACTED。
const stateD = {
  jobId: "job-4",
  currentPageNum: 1,
  currentPage: { page_num: 1, status: "done", content: "# p1", error: "" },
  pages: [
    { page_num: 1, status: "done" },
    { page_num: 2, status: "failed" },
  ],
  job: {
    job_id: "job-4",
    status: "extracting",
    total_pages: 2,
    succeeded_pages: [1],
    failed_pages: [2],
    processed_count: 2,
  },
};
const ctxD = buildHost(stateD);
ctxD.applySavedPage({ page_num: 1, status: "done", content: "# p1 edited", error: "" });

console.log(
  JSON.stringify({
    scenarioA: {
      state: stateA,
      editor: ctxA.els.pageEditor.value,
      pageError: ctxA.els.pageError.textContent,
      calls: ctxA.calls,
      callKinds: ctxA.calls.map((c) => c.kind),
    },
    scenarioB: {
      state: stateB,
    },
    scenarioC: {
      editor: ctxC.els.pageEditor.value,
      pageError: ctxC.els.pageError.textContent,
      currentPage: stateC.currentPage,
      pages: stateC.pages,
      job: stateC.job,
    },
    scenarioD: {
      state: stateD,
      historyPatch: ctxD.calls.filter((c) => c.kind === "history").map((c) => c.patch),
    },
  }),
);
"""
        )

        a = result["scenarioA"]
        self.assertEqual("# edited", a["editor"])
        self.assertEqual("", a["pageError"])
        self.assertEqual(
            {"page_num": 2, "status": "done"},
            a["state"]["pages"][1],
        )
        self.assertEqual([1, 2, 3], a["state"]["job"]["succeeded_pages"])
        self.assertEqual([], a["state"]["job"]["failed_pages"])
        self.assertEqual(3, a["state"]["job"]["processed_count"])
        self.assertEqual("extracted", a["state"]["job"]["status"])
        self.assertEqual(
            {"page_num": 2, "status": "done", "content": "# edited", "error": ""},
            a["state"]["currentPage"],
        )
        self.assertIn("history", a["callKinds"])
        self.assertIn("history-flush", a["callKinds"])
        self.assertIn("indicator", a["callKinds"])
        self.assertIn("select", a["callKinds"])
        self.assertIn("control", a["callKinds"])

        history_calls = [entry for entry in a["calls"] if entry["kind"] == "history"]
        self.assertEqual(1, len(history_calls))
        history_patch = history_calls[0]["patch"]
        self.assertEqual("extracted", history_patch["status"])
        self.assertEqual(3, history_patch["processed_count"])
        self.assertEqual(3, history_patch["total_pages"])
        self.assertIsInstance(history_patch["updated_at"], str)
        self.assertTrue(history_patch["updated_at"])

        flush_calls = [entry for entry in a["calls"] if entry["kind"] == "history-flush"]
        self.assertEqual(1, len(flush_calls))
        self.assertEqual(
            {"historyJobIds": ["job-1"]},
            flush_calls[0]["patch"],
        )

        b = result["scenarioB"]["state"]
        self.assertEqual("extracted", b["job"]["status"])
        self.assertEqual([1, 2, 3], b["job"]["succeeded_pages"])
        self.assertEqual([], b["job"]["failed_pages"])
        self.assertEqual(3, b["job"]["processed_count"])

        c = result["scenarioC"]
        self.assertEqual(
            "# keep",
            c["editor"],
            "保存非当前页时不得覆盖编辑器内容",
        )
        self.assertEqual("", c["pageError"])
        self.assertEqual(
            {"page_num": 1, "status": "done", "content": "# keep", "error": ""},
            c["currentPage"],
            "保存非当前页时不应把 currentPage 覆盖为其它页，避免翻页态抖动",
        )
        self.assertEqual(
            {"page_num": 2, "status": "done"},
            c["pages"][1],
        )
        self.assertEqual([1, 2], c["job"]["succeeded_pages"])
        self.assertEqual([], c["job"]["failed_pages"])

        d = result["scenarioD"]
        self.assertEqual("extracting", d["state"]["job"]["status"])
        self.assertEqual([1], d["state"]["job"]["succeeded_pages"])
        self.assertEqual([2], d["state"]["job"]["failed_pages"])
        self.assertEqual(2, d["state"]["job"]["processed_count"])
        history_d = d["historyPatch"]
        self.assertEqual(1, len(history_d))
        self.assertEqual("extracting", history_d[0]["status"])
