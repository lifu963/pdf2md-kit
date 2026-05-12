"""
Regression: `fix/start-extract-keep-pdf-preview`

锁定“开始提取”时的 PDF 预览行为：若左侧已经是本地预览，不应在首次刷新工作区时
强制切换到 `/api/jobs/{id}/source` 重新加载，避免可见闪烁和阅读位置被打断。
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


class StartExtractionNoPdfReloadRegressionTests(TestCase):
    def test_start_extraction_uses_preserve_existing_pdf_option(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn(
            "function hasLocalPdfPreviewLoaded()",
            source,
            "应提供本地预览已加载判断，供开始提取时复用",
        )

        start_fn_start = source.index("async function startExtractionForPreparedFile()")
        start_fn_end = source.index("\n        /* ── Page Operations", start_fn_start)
        start_fn_body = source[start_fn_start:start_fn_end]
        self.assertIn(
            "var preserveExistingPdf = hasLocalPdfPreviewLoaded();",
            start_fn_body,
            "开始提取前应先快照本地预览复用意图",
        )
        self.assertIn(
            "await refreshWorkspace({ preserveExistingPdf: preserveExistingPdf });",
            start_fn_body,
            "开始提取后刷新工作区应显式传递复用 PDF 预览选项",
        )

        refresh_fn_start = source.index("async function refreshWorkspace(options) {")
        refresh_fn_end = source.index("\n        /* ── SSE Stream", refresh_fn_start)
        refresh_fn_body = source[refresh_fn_start:refresh_fn_end]
        self.assertIn(
            "var preserveExistingPdf = !!opts.preserveExistingPdf;",
            refresh_fn_body,
            "refreshWorkspace 应读取 preserveExistingPdf 选项",
        )
        self.assertIn(
            "var shouldPreserveExistingPdf = preserveExistingPdf && hasLocalPdfPreviewLoaded();",
            refresh_fn_body,
            "refreshWorkspace 应在本地预览可复用时跳过重载",
        )
        self.assertIn(
            "if (!shouldPreserveExistingPdf) {",
            refresh_fn_body,
            "loadPdfDocument 需要放到受保护分支中",
        )

        guarded_segment_start = refresh_fn_body.index("if (!shouldPreserveExistingPdf) {")
        guarded_segment = refresh_fn_body[guarded_segment_start : guarded_segment_start + 500]
        self.assertIn(
            "await loadPdfDocument(sourcePdfUrl);",
            guarded_segment,
            "仅在不复用现有预览时才允许重载 PDF",
        )

    def test_refresh_workspace_runtime_preserve_branch_skips_pdf_reload(self) -> None:
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

function createHarness(localPreviewLoaded) {
  const state = {
    jobId: "job-1",
    job: null,
    isBusy: false,
    localPreviewUrl: "blob:local-preview",
  };

  let closeStreamCalls = 0;
  let requestJsonCalls = 0;
  let updateControlAvailabilityCalls = 0;
  let loadOutputCalls = 0;
  let clearOutputStateCalls = 0;
  let loadPagesCalls = 0;
  let connectStreamCalls = 0;
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

  const api = new Function(
    "state",
    "closeStream",
    "requestJson",
    "updateControlAvailability",
    "loadOutput",
    "clearOutputState",
    "loadPages",
    "connectStream",
    "syncHistoryPanelOnDemand",
    "showPdfStatusHint",
    "loadPdfDocument",
    "hidePdfStatusHint",
    "log",
    `
var pdfDoc = null;
var pdfCurrentUrl = null;
${pick("function hasLocalPdfPreviewLoaded()")}
${pickByName("refreshWorkspace")}

function setPdfPreviewLoaded(loaded) {
  if (!loaded) {
    pdfDoc = null;
    pdfCurrentUrl = null;
    return;
  }
  pdfDoc = {};
  pdfCurrentUrl = state.localPreviewUrl;
}

return { refreshWorkspace, setPdfPreviewLoaded };
`,
  )(
    state,
    closeStream,
    requestJson,
    updateControlAvailability,
    loadOutput,
    clearOutputState,
    loadPages,
    connectStream,
    syncHistoryPanelOnDemand,
    showPdfStatusHint,
    loadPdfDocument,
    hidePdfStatusHint,
    log,
  );

  api.setPdfPreviewLoaded(localPreviewLoaded);

  return {
    async run(preserveExistingPdf) {
      await api.refreshWorkspace({ preserveExistingPdf });
      return {
        closeStreamCalls,
        requestJsonCalls,
        updateControlAvailabilityCalls,
        loadOutputCalls,
        clearOutputStateCalls,
        loadPagesCalls,
        connectStreamCalls,
        syncHistoryPanelOnDemandCalls: syncHistoryPanelOnDemandCalls.slice(),
        showPdfStatusHintCalls,
        hidePdfStatusHintCalls,
        loadPdfDocumentCalls,
        logs: logs.slice(),
      };
    },
  };
}

const preserveHit = await createHarness(true).run(true);
const preserveDisabled = await createHarness(true).run(false);
const preserveMiss = await createHarness(false).run(true);

console.log(
  JSON.stringify({
    preserveHit,
    preserveDisabled,
    preserveMiss,
  }),
);
"""
        )

        for scenario_name in ("preserveHit", "preserveDisabled", "preserveMiss"):
            scenario = result[scenario_name]
            self.assertEqual(1, scenario["closeStreamCalls"])
            self.assertEqual(1, scenario["requestJsonCalls"])
            self.assertEqual(1, scenario["loadPagesCalls"])
            self.assertEqual(1, scenario["connectStreamCalls"])
            self.assertEqual(
                [True],
                scenario["syncHistoryPanelOnDemandCalls"],
                f"{scenario_name} 应先执行历史栏按需同步",
            )

        self.assertEqual(
            0,
            result["preserveHit"]["loadPdfDocumentCalls"],
            "当 preserve=true 且当前已是本地预览时，不应重载 PDF",
        )
        self.assertEqual(0, result["preserveHit"]["showPdfStatusHintCalls"])
        self.assertEqual(0, result["preserveHit"]["hidePdfStatusHintCalls"])

        self.assertEqual(1, result["preserveDisabled"]["loadPdfDocumentCalls"])
        self.assertEqual(1, result["preserveDisabled"]["showPdfStatusHintCalls"])
        self.assertEqual(1, result["preserveDisabled"]["hidePdfStatusHintCalls"])

        self.assertEqual(
            1,
            result["preserveMiss"]["loadPdfDocumentCalls"],
            "即使 preserve=true，若本地预览上下文不存在也应回退到 source 加载",
        )
        self.assertEqual(1, result["preserveMiss"]["showPdfStatusHintCalls"])
        self.assertEqual(1, result["preserveMiss"]["hidePdfStatusHintCalls"])
