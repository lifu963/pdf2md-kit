"""
Regression: `fix/page-status-pending-display`

锁定页状态展示语义：
- 任务整体为 extracting 时，页面级 pending 不应被前端强制映射为 extracting。
- 下拉页码文本按页面真实状态渲染。
- 页内单行状态徽标已不再展示。
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


class PendingStatusDisplayRegressionTests(TestCase):
    def test_pending_page_remains_pending_during_extracting_job(self) -> None:
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
  job: { status: "extracting" },
  currentPage: { page_num: 89, status: "pending", content: "", error: "" },
};

const els = {
  pageStatusIndicator: { className: "" },
  pageStatusText: { textContent: "", classList: { remove() {}, add() {} } },
};

const api = new Function(
  "state",
  "els",
  `
${pick("function setPageSelectOptionText(option, pageNum, status)")}
${pick("function updatePageStatusIndicator()")}
return { setPageSelectOptionText, updatePageStatusIndicator };
`,
)(state, els);

const pendingOption = { textContent: "" };
api.setPageSelectOptionText(pendingOption, 89, "pending");
api.updatePageStatusIndicator();
const pendingView = {
  optionText: pendingOption.textContent,
  indicatorClass: els.pageStatusIndicator.className,
  indicatorText: els.pageStatusText.textContent,
};

const extractingOption = { textContent: "" };
state.currentPage.status = "extracting";
api.setPageSelectOptionText(extractingOption, 90, "extracting");
api.updatePageStatusIndicator();
const extractingView = {
  optionText: extractingOption.textContent,
  indicatorClass: els.pageStatusIndicator.className,
  indicatorText: els.pageStatusText.textContent,
};

const fallbackOption = { textContent: "" };
api.setPageSelectOptionText(fallbackOption, 1);

console.log(
  JSON.stringify({
    pendingView,
    extractingView,
    fallbackOptionText: fallbackOption.textContent,
  }),
);
"""
        )

        self.assertEqual(
            "第 89 页 (pending)",
            result["pendingView"]["optionText"],
            "任务 extracting 时，pending 页在下拉中仍应显示 pending",
        )
        self.assertEqual(
            "page-status-bar page-status-inline status-pending hidden",
            result["pendingView"]["indicatorClass"],
        )
        self.assertEqual("", result["pendingView"]["indicatorText"])

        self.assertEqual("第 90 页 (extracting)", result["extractingView"]["optionText"])
        self.assertEqual(
            "page-status-bar page-status-inline status-extracting hidden",
            result["extractingView"]["indicatorClass"],
        )
        self.assertEqual("", result["extractingView"]["indicatorText"])

        self.assertEqual("第 1 页", result["fallbackOptionText"])

