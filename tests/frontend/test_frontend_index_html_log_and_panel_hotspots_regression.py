"""
Regression: `task8/log-and-panel-hotspots`

锁定 Task 8「收尾处理日志与折叠动画热点」的关键不变量：

1. 事件日志追加必须改为内存缓冲 + 按需 flush，不能再每次重写整段 textContent。
2. 设置面板 / 历史面板折叠路径不再依赖 `max-height` 动画，避免持续放大布局计算。
3. 折叠态与展开态交互仍正确，按钮激活态与面板可见性保持一致。
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


class LogAndPanelHotspotsRegressionTests(TestCase):
    def test_source_uses_buffered_events_log_and_non_layout_panel_transition(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        panel_base = re.search(
            r"\.history-panel \{[\s\S]*?\r?\n      \}",
            source,
        )
        panel_collapsed = re.search(
            r"\.history-panel\.collapsed \{[\s\S]*?\r?\n      \}",
            source,
        )
        if panel_base is None or panel_collapsed is None:
            raise AssertionError("未能在 frontend/index.html 中定位历史面板样式片段")

        panel_css = "\n".join([panel_base.group(0), panel_collapsed.group(0)])
        self.assertNotIn(
            "max-height",
            panel_css,
            "历史面板折叠动画不应继续依赖 max-height",
        )
        self.assertIn(
            "transition: none",
            panel_css,
            "像素 spec：历史面板折叠应无 CSS transition（transition: none）",
        )
        self.assertRegex(
            source,
            r'<section[^>]*class="[^"]*\bconfig-panel\b[^"]*"',
            "配置应以内联折叠 section.config-panel 呈现",
        )
        self.assertIn(
            ".history-panel[hidden]",
            source,
            "折叠态应支持 hidden 快速脱离布局树",
        )
        self.assertIn(
            "function syncPanelCollapsedState(panel, collapsed)",
            source,
            "应存在统一的面板折叠可见性同步入口",
        )
        self.assertIn(
            "function setConfigPanelExpanded(expanded)",
            source,
            "设置面板应通过显式 setter 与 syncPanelCollapsedState 同步可见性",
        )
        self.assertRegex(
            source,
            r"if \(els\.configPanel\)[\s\S]*syncPanelCollapsedState\(els\.configPanel",
            "setter 应对 config-panel 调用统一折叠同步入口",
        )
        self.assertNotIn(
            'els.configPanel.classList.toggle("collapsed")',
            source,
            "设置面板不应继续直接切 collapsed（应走统一同步入口）",
        )

        log_start = source.index("function log(message, payload)")
        log_end = source.index("\n        function normalizeBuildMergeMode(", log_start)
        log_body = source[log_start:log_end]
        render_match = re.search(
            r"function renderEventsLog\(\) \{[\s\S]*?\r?\n        \}",
            source,
        )
        if render_match is None:
            raise AssertionError("未能在 frontend/index.html 中定位 renderEventsLog")
        render_body = render_match.group(0)

        self.assertNotIn(
            "els.eventsLog.textContent = els.eventsLog.textContent +",
            log_body,
            "日志追加不应再通过 textContent 全量重写全文",
        )
        self.assertIn(
            "eventsLogLines.push(",
            log_body,
            "日志应先进入内存缓冲，再按需刷到 DOM",
        )
        self.assertIn(
            "scheduleEventsLogFlush(",
            log_body,
            "日志追加应走统一 flush 调度入口",
        )
        self.assertIn(
            "appendEventsLogTail(",
            render_body,
            "日志 flush 应优先走增量 append，而非每次重建全文",
        )
        self.assertIn(
            "rewriteEventsLogFromBuffer()",
            render_body,
            "仅在必要场景（如裁剪后）才允许回退到全量重建",
        )
        self.assertNotIn(
            "eventsLogLines.join(\"\\n\")",
            render_body,
            "renderEventsLog 本体不应在每次 flush 都 join 全量日志",
        )
        self.assertIn("function flushEventsLog()", source)
        self.assertIn("function setEventsLogExpanded(expanded)", source)
        self.assertIn("function rewriteEventsLogFromBuffer()", source)
        self.assertIn("function appendEventsLogTail(fromIndex)", source)

    def test_log_buffer_defers_dom_updates_until_expanded_and_batches_flush(self) -> None:
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

function makeClassList(initialNames) {
  const names = new Set(initialNames || []);
  return {
    add(name) {
      names.add(name);
    },
    remove(name) {
      names.delete(name);
    },
    contains(name) {
      return names.has(name);
    },
    toggle(name, force) {
      if (force === undefined) {
        if (names.has(name)) {
          names.delete(name);
          return false;
        }
        names.add(name);
        return true;
      }
      if (force) {
        names.add(name);
      } else {
        names.delete(name);
      }
      return !!force;
    },
  };
}

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

const eventsLog = {
  _textContent: "",
  writeCount: 0,
  appendCount: 0,
  scrollTop: 0,
  scrollHeight: 0,
  clientHeight: 120,
  classList: makeClassList([]),
  set textContent(value) {
    this._textContent = String(value);
    this.writeCount += 1;
    this.scrollHeight = this._textContent.length * 2;
  },
  get textContent() {
    return this._textContent;
  },
  insertAdjacentText(position, value) {
    if (position !== "beforeend") {
      throw new Error(`unexpected insertAdjacentText position: ${position}`);
    }
    this._textContent = this._textContent + String(value);
    this.appendCount += 1;
    this.scrollHeight = this._textContent.length * 2;
  },
};

const toggleEventsBtn = {
  classList: makeClassList([]),
  setAttribute() {},
};

let tick = 0;
const baseMs = Date.parse("2026-04-19T00:00:00.000Z");
function FakeDate() {
  this._iso = new globalThis.Date(baseMs + tick * 1000).toISOString();
  tick += 1;
}
FakeDate.prototype.toISOString = function () {
  return this._iso;
};

const api = new Function(
  "window",
  "els",
  "Date",
  "rafQueue",
  `
var MAX_EVENTS_LOG_LINES = 4;
var eventsLogLines = [];
var eventsLogDirty = false;
var eventsLogFrameId = null;
var eventsLogExpanded = false;
var eventsLogRenderedCount = 0;
var eventsLogNeedsFullRender = false;
${pick("function syncPanelCollapsedState(panel, collapsed)")}
${pick("function isEventsLogNearBottom()")}
${pick("function renderEventsLog()")}
${pick("function rewriteEventsLogFromBuffer()")}
${pick("function appendEventsLogTail(fromIndex)")}
${pick("function flushEventsLog()")}
${pick("function scheduleEventsLogFlush(forceImmediate)")}
${pick("function setEventsLogExpanded(expanded)")}
${pick("function log(message, payload)")}
return {
  log,
  setEventsLogExpanded,
  flushRaf() {
    if (!rafQueue.length) return false;
    const entry = rafQueue.shift();
    entry.callback(0);
    return true;
  },
  getSnapshot() {
    return {
      textContent: els.eventsLog.textContent,
      writeCount: els.eventsLog.writeCount,
      appendCount: els.eventsLog.appendCount,
      queuedRaf: rafQueue.length,
      eventsLogExpanded,
      eventsLogDirty,
      lineCount: eventsLogLines.length,
      lines: eventsLogLines.slice(),
      buttonExpanded: els.toggleEventsBtn.classList.contains("expanded"),
      panelExpanded: els.eventsLog.classList.contains("expanded"),
    };
  },
};
`,
)(
  windowStub,
  { eventsLog, toggleEventsBtn },
  FakeDate,
  rafQueue,
);

api.log("first");
api.log("second", { x: 1 });
const collapsed = api.getSnapshot();

api.setEventsLogExpanded(true);
const expandedBeforeFlush = api.getSnapshot();
api.flushRaf();
const expandedAfterFlush = api.getSnapshot();

api.log("third");
api.log("fourth");
const secondBatchBeforeFlush = api.getSnapshot();
api.flushRaf();
const secondBatchAfterFlush = api.getSnapshot();

api.log("fifth");
api.flushRaf();
const afterTrim = api.getSnapshot();

console.log(
  JSON.stringify({
    collapsed,
    expandedBeforeFlush,
    expandedAfterFlush,
    secondBatchBeforeFlush,
    secondBatchAfterFlush,
    afterTrim,
  }),
);
"""
        )

        self.assertEqual(2, result["collapsed"]["lineCount"])
        self.assertEqual(
            "",
            result["collapsed"]["textContent"],
            "日志面板折叠时应只写入内存缓冲，不应立即重写 DOM",
        )
        self.assertEqual(0, result["collapsed"]["writeCount"])
        self.assertEqual(0, result["collapsed"]["appendCount"])
        self.assertEqual(0, result["collapsed"]["queuedRaf"])

        self.assertEqual(1, result["expandedBeforeFlush"]["queuedRaf"])
        self.assertTrue(result["expandedBeforeFlush"]["eventsLogExpanded"])
        self.assertTrue(result["expandedBeforeFlush"]["eventsLogDirty"])

        self.assertEqual(0, result["expandedAfterFlush"]["writeCount"])
        self.assertEqual(1, result["expandedAfterFlush"]["appendCount"])
        self.assertIn("first", result["expandedAfterFlush"]["textContent"])
        self.assertIn("second", result["expandedAfterFlush"]["textContent"])
        self.assertFalse(result["expandedAfterFlush"]["eventsLogDirty"])
        self.assertTrue(result["expandedAfterFlush"]["buttonExpanded"])
        self.assertTrue(result["expandedAfterFlush"]["panelExpanded"])

        self.assertEqual(
            1,
            result["secondBatchBeforeFlush"]["queuedRaf"],
            "展开态下连续多条日志应合并到同一帧 flush",
        )
        self.assertEqual(0, result["secondBatchBeforeFlush"]["writeCount"])
        self.assertEqual(1, result["secondBatchBeforeFlush"]["appendCount"])

        self.assertEqual(0, result["secondBatchAfterFlush"]["writeCount"])
        self.assertEqual(2, result["secondBatchAfterFlush"]["appendCount"])
        self.assertEqual(4, result["secondBatchAfterFlush"]["lineCount"])
        self.assertIn("third", result["secondBatchAfterFlush"]["textContent"])
        self.assertIn("fourth", result["secondBatchAfterFlush"]["textContent"])

        self.assertEqual(
            4,
            result["afterTrim"]["lineCount"],
            "日志缓冲应保留上限，避免无限增长",
        )
        self.assertTrue(
            any("fifth" in line for line in result["afterTrim"]["lines"]),
            "最新日志应保留在缓冲中",
        )
        self.assertTrue(
            all("first" not in line for line in result["afterTrim"]["lines"]),
            "超出上限时应裁剪最旧日志",
        )
        self.assertEqual(
            1,
            result["afterTrim"]["writeCount"],
            "发生日志裁剪时允许一次全量重建，确保 DOM 与缓冲重新对齐",
        )

    def test_panel_setters_keep_visibility_and_button_state_in_sync(self) -> None:
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

function makeClassList(initialNames) {
  const names = new Set(initialNames || []);
  return {
    add(name) {
      names.add(name);
    },
    remove(name) {
      names.delete(name);
    },
    contains(name) {
      return names.has(name);
    },
    toggle(name, force) {
      if (force === undefined) {
        if (names.has(name)) {
          names.delete(name);
          return false;
        }
        names.add(name);
        return true;
      }
      if (force) names.add(name);
      else names.delete(name);
      return !!force;
    },
  };
}

const historyPanel = {
  hidden: true,
  classList: makeClassList(["panel", "history-panel", "collapsed"]),
};
const configPanel = {
  hidden: true,
  classList: makeClassList(["panel", "config-panel", "collapsed"]),
};
const toggleHistoryBtn = { classList: makeClassList([]) };
const toggleConfigBtn = { classList: makeClassList([]) };

const api = new Function(
  "els",
  `
var historyPanelExpanded = false;
var configPanelExpanded = false;
${pick("function syncPanelCollapsedState(panel, collapsed)")}
${pick("function setHistoryPanelExpanded(expanded)")}
${pick("function setConfigPanelExpanded(expanded)")}
return {
  setHistoryPanelExpanded,
  setConfigPanelExpanded,
  snapshot() {
    return {
      historyExpanded: historyPanelExpanded,
      configExpanded: configPanelExpanded,
      historyHidden: els.historyPanel.hidden,
      historyCollapsed: els.historyPanel.classList.contains("collapsed"),
      configHidden: els.configPanel.hidden,
      configCollapsed: els.configPanel.classList.contains("collapsed"),
      historyBtnActive: els.toggleHistoryBtn.classList.contains("active"),
      configBtnActive: els.toggleConfigBtn.classList.contains("active"),
    };
  },
};
`,
)({
  historyPanel,
  configPanel,
  toggleHistoryBtn,
  toggleConfigBtn,
});

const initial = api.snapshot();
api.setHistoryPanelExpanded(true);
api.setConfigPanelExpanded(true);
const afterExpand = api.snapshot();
api.setHistoryPanelExpanded(false);
api.setConfigPanelExpanded(false);
const afterCollapse = api.snapshot();

console.log(JSON.stringify({ initial, afterExpand, afterCollapse }));
"""
        )

        self.assertTrue(result["initial"]["historyCollapsed"])
        self.assertTrue(result["initial"]["configCollapsed"])
        self.assertTrue(result["initial"]["configHidden"])
        self.assertFalse(result["initial"]["historyBtnActive"])
        self.assertFalse(result["initial"]["configBtnActive"])

        self.assertTrue(result["afterExpand"]["historyExpanded"])
        self.assertTrue(result["afterExpand"]["configExpanded"])
        self.assertFalse(result["afterExpand"]["historyHidden"])
        self.assertFalse(result["afterExpand"]["configHidden"])
        self.assertFalse(result["afterExpand"]["historyCollapsed"])
        self.assertFalse(result["afterExpand"]["configCollapsed"])
        self.assertTrue(result["afterExpand"]["historyBtnActive"])
        self.assertTrue(result["afterExpand"]["configBtnActive"])

        self.assertFalse(result["afterCollapse"]["historyExpanded"])
        self.assertFalse(result["afterCollapse"]["configExpanded"])
        self.assertTrue(result["afterCollapse"]["historyHidden"])
        self.assertTrue(result["afterCollapse"]["configHidden"])
        self.assertTrue(result["afterCollapse"]["historyCollapsed"])
        self.assertTrue(result["afterCollapse"]["configCollapsed"])
        self.assertFalse(result["afterCollapse"]["historyBtnActive"])
        self.assertFalse(result["afterCollapse"]["configBtnActive"])
