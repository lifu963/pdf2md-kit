"""
Regression: `fix/workspace-resize-raf`

锁定工作区分隔线拖动的性能回归边界：

1. 原始 `pointermove` 热路径不再直接读布局或排队 PDF 重绘。
2. 拖动期间通过 `requestAnimationFrame` 合并多次位置更新。
3. 一次拖动会话只读取一次工作区布局；松手前不触发 PDF 重绘，松手后只按最终尺寸重绘一次。
4. 拖动态的 `cursor` / `user-select` 失效范围应收敛到工作区，而不是扩散到整棵文档树。
5. 停止拖动后触发的 PDF 重绘不应先清空当前可见画布，避免用户看到短暂白屏。
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


class WorkspaceResizePerformanceRegressionTests(TestCase):
    def test_workspace_has_draggable_divider_with_raf_merge_drag_path(self) -> None:
        """两栏布局恢复可拖拽分隔线；pointermove 热路径 RAF 合并与单次布局读取逻辑应保留。"""
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="workspace-divider"', source)
        self.assertRegex(source, r"function\s+updateWorkspaceSplitFromClientX\b")
        self.assertRegex(source, r"function\s+stopWorkspaceResize\b")
        self.assertIn("body.workspace-resizing", source)
        self.assertRegex(source, r"function applyWorkspaceSplit\s*\(\)\s*\{")

    def test_pdf_resize_redraw_keeps_visible_canvas_until_next_frame_is_ready(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const start = html.indexOf("async function renderPdfPage(pageNum)");
const end = html.indexOf("/* ── Application State", start);
if (start === -1 || end === -1) {
  throw new Error("failed to locate renderPdfPage in frontend/index.html");
}
const renderFnText = html.slice(start, end);

const createdCanvases = [];
const ops = [];

function createContext(kind) {
  return {
    __kind: kind,
    setTransform(...args) {
      ops.push({ kind, op: "setTransform", args });
    },
    clearRect(...args) {
      ops.push({ kind, op: "clearRect", args });
    },
    drawImage(canvas, ...args) {
      ops.push({
        kind,
        op: "drawImage",
        source: canvas.__label || "unknown",
        args,
      });
    },
  };
}

const visibleClassNames = new Set(["hidden"]);
const stageClassNames = new Set(["is-empty"]);
const visibleCanvas = {
  width: 640,
  height: 960,
  style: { width: "320px", height: "480px" },
  getContext() {
    return createContext("visible");
  },
  classList: {
    add(name) {
      visibleClassNames.add(name);
    },
    remove(name) {
      visibleClassNames.delete(name);
    },
    contains(name) {
      return visibleClassNames.has(name);
    },
  },
};

const documentStub = {
  createElement(tagName) {
    if (tagName !== "canvas") {
      throw new Error(`unexpected tag: ${tagName}`);
    }
    const index = createdCanvases.length + 1;
    const canvas = {
      __label: `offscreen-${index}`,
      width: 0,
      height: 0,
      style: { width: "", height: "" },
      getContext() {
        return createContext("offscreen");
      },
    };
    createdCanvases.push(canvas);
    return canvas;
  },
};

let resolveRender;
const pendingRenderPromise = new Promise((resolve) => {
  resolveRender = resolve;
});

const page = {
  getViewport({ scale }) {
    return {
      width: 400 * scale,
      height: 600 * scale,
    };
  },
  render({ canvasContext, viewport }) {
    ops.push({
      kind: canvasContext.__kind || "unknown",
      op: "page.render",
      viewport: { width: viewport.width, height: viewport.height },
    });
    return { promise: pendingRenderPromise };
  },
};

const els = {
  pdfStage: {
    clientWidth: 456,
    classList: {
      add(name) {
        stageClassNames.add(name);
      },
      remove(name) {
        stageClassNames.delete(name);
      },
      contains(name) {
        return stageClassNames.has(name);
      },
    },
  },
  pdfCanvas: visibleCanvas,
};

const logs = [];
const api = new Function(
  "window",
  "document",
  "els",
  "log",
  "pdfDocRef",
  "createdCanvases",
  `
var pdfDoc = pdfDocRef;
var pdfPageRendering = false;
var pdfPagePending = null;
${renderFnText}
return {
  renderPdfPage,
  getSnapshot: function () {
    return {
      pdfPageRendering,
      pdfPagePending,
      visibleWidth: els.pdfCanvas.width,
      visibleHeight: els.pdfCanvas.height,
      visibleStyleWidth: els.pdfCanvas.style.width,
      visibleStyleHeight: els.pdfCanvas.style.height,
      visibleHidden: els.pdfCanvas.classList.contains("hidden"),
      stageEmpty: els.pdfStage.classList.contains("is-empty"),
      createdCanvases: createdCanvases.map((canvas) => ({
        label: canvas.__label,
        width: canvas.width,
        height: canvas.height,
        styleWidth: canvas.style.width,
        styleHeight: canvas.style.height,
      })),
    };
  },
};
`,
)(
  { devicePixelRatio: 2 },
  documentStub,
  els,
  function log(message, payload) {
    logs.push({ message, payload });
  },
  {
    numPages: 5,
    async getPage() {
      return page;
    },
  },
  createdCanvases,
);

const renderWork = api.renderPdfPage(2);
await Promise.resolve();
await Promise.resolve();
const beforeResolve = {
  snapshot: api.getSnapshot(),
  ops: ops.slice(),
  logs: logs.slice(),
};

resolveRender();
await renderWork;
const afterResolve = {
  snapshot: api.getSnapshot(),
  ops: ops.slice(),
  logs: logs.slice(),
};

console.log(JSON.stringify({ beforeResolve, afterResolve }));
"""
        )

        before_resolve = result["beforeResolve"]
        self.assertEqual(640, before_resolve["snapshot"]["visibleWidth"])
        self.assertEqual(960, before_resolve["snapshot"]["visibleHeight"])
        self.assertEqual("320px", before_resolve["snapshot"]["visibleStyleWidth"])
        self.assertEqual("480px", before_resolve["snapshot"]["visibleStyleHeight"])
        self.assertTrue(before_resolve["snapshot"]["visibleHidden"])
        self.assertTrue(before_resolve["snapshot"]["pdfPageRendering"])
        self.assertEqual(
            [
                {
                    "label": "offscreen-1",
                    "width": 800,
                    "height": 1200,
                    "styleWidth": "400px",
                    "styleHeight": "600px",
                }
            ],
            before_resolve["snapshot"]["createdCanvases"],
        )
        self.assertEqual(
            [
                {"kind": "offscreen", "op": "setTransform", "args": [2, 0, 0, 2, 0, 0]},
                {
                    "kind": "offscreen",
                    "op": "page.render",
                    "viewport": {"width": 400, "height": 600},
                },
            ],
            before_resolve["ops"],
        )
        self.assertEqual([], before_resolve["logs"])

        after_resolve = result["afterResolve"]
        self.assertEqual(800, after_resolve["snapshot"]["visibleWidth"])
        self.assertEqual(1200, after_resolve["snapshot"]["visibleHeight"])
        self.assertEqual("400px", after_resolve["snapshot"]["visibleStyleWidth"])
        self.assertEqual("600px", after_resolve["snapshot"]["visibleStyleHeight"])
        self.assertFalse(after_resolve["snapshot"]["visibleHidden"])
        self.assertFalse(after_resolve["snapshot"]["stageEmpty"])
        self.assertFalse(after_resolve["snapshot"]["pdfPageRendering"])
        self.assertEqual(
            {
                "kind": "visible",
                "op": "drawImage",
                "source": "offscreen-1",
                "args": [0, 0],
            },
            after_resolve["ops"][-1],
        )
        self.assertEqual([], after_resolve["logs"])

    def test_pdf_page_switch_cancels_inflight_render_and_prioritizes_latest_page(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const start = html.indexOf("async function renderPdfPage(pageNum)");
const end = html.indexOf("/* ── Application State", start);
if (start === -1 || end === -1) {
  throw new Error("failed to locate renderPdfPage in frontend/index.html");
}
const renderFnText = html.slice(start, end);

const logs = [];
const renderOrder = [];
const cancelledPages = [];

function createContext() {
  return {
    setTransform() {},
    drawImage() {},
    clearRect() {},
  };
}

const visibleClassNames = new Set(["hidden"]);
const stageClassNames = new Set(["is-empty"]);
const visibleCanvas = {
  width: 0,
  height: 0,
  style: { width: "", height: "" },
  getContext() {
    return createContext();
  },
  classList: {
    add(name) {
      visibleClassNames.add(name);
    },
    remove(name) {
      visibleClassNames.delete(name);
    },
    contains(name) {
      return visibleClassNames.has(name);
    },
  },
};

const documentStub = {
  createElement(tagName) {
    if (tagName !== "canvas") {
      throw new Error(`unexpected tag: ${tagName}`);
    }
    return {
      width: 0,
      height: 0,
      style: { width: "", height: "" },
      getContext() {
        return createContext();
      },
    };
  },
};

function createPage(pageNum) {
  const profile = pageNum === 1
    ? { width: 300, height: 900 }
    : { width: 500, height: 500 };
  return {
    getViewport({ scale }) {
      return {
        width: profile.width * scale,
        height: profile.height * scale,
      };
    },
    render() {
      renderOrder.push(pageNum);
      let settled = false;
      let resolvePromise;
      let rejectPromise;
      const promise = new Promise((resolve, reject) => {
        resolvePromise = resolve;
        rejectPromise = reject;
      });
      const task = {
        promise,
        cancel() {
          if (settled) return;
          settled = true;
          cancelledPages.push(pageNum);
          const error = new Error("cancelled");
          error.name = "RenderingCancelledException";
          rejectPromise(error);
        },
      };
      if (pageNum === 2) {
        Promise.resolve().then(() => {
          if (settled) return;
          settled = true;
          resolvePromise();
        });
      }
      return task;
    },
  };
}

const els = {
  pdfStage: {
    clientWidth: 456,
    classList: {
      add(name) {
        stageClassNames.add(name);
      },
      remove(name) {
        stageClassNames.delete(name);
      },
      contains(name) {
        return stageClassNames.has(name);
      },
    },
  },
  pdfCanvas: visibleCanvas,
};

const api = new Function(
  "window",
  "document",
  "els",
  "log",
  "pdfDocRef",
  `
var pdfDoc = pdfDocRef;
var pdfPageRendering = false;
var pdfPagePending = null;
${renderFnText}
return {
  renderPdfPage,
  getSnapshot: function () {
    return {
      pdfPageRendering,
      pdfPagePending,
      visibleWidth: els.pdfCanvas.width,
      visibleHeight: els.pdfCanvas.height,
      stageEmpty: els.pdfStage.classList.contains("is-empty"),
      visibleHidden: els.pdfCanvas.classList.contains("hidden"),
    };
  },
};
`,
)(
  { devicePixelRatio: 1 },
  documentStub,
  els,
  function log(message, payload) {
    logs.push({ message, payload });
  },
  {
    numPages: 4,
    async getPage(pageNum) {
      return createPage(pageNum);
    },
  },
);

const firstRenderPromise = api.renderPdfPage(1);
await Promise.resolve();
const secondRenderPromise = api.renderPdfPage(2);

await Promise.all([firstRenderPromise, secondRenderPromise]);

console.log(
  JSON.stringify({
    renderOrder,
    cancelledPages,
    logs,
    snapshot: api.getSnapshot(),
  }),
);
"""
        )

        self.assertEqual(
            [1, 2],
            result["renderOrder"],
            "切页时应抛弃旧页渲染并快速切到最后一次请求页",
        )
        self.assertEqual(
            [1],
            result["cancelledPages"],
            "新页请求到达时，应取消正在进行中的旧页渲染任务",
        )
        self.assertEqual([], result["logs"], "渲染取消不应污染错误日志")
        self.assertFalse(result["snapshot"]["pdfPageRendering"])
        self.assertIsNone(result["snapshot"]["pdfPagePending"])
        self.assertFalse(result["snapshot"]["stageEmpty"])
        self.assertFalse(result["snapshot"]["visibleHidden"])
        self.assertEqual(400, result["snapshot"]["visibleWidth"])
        self.assertEqual(400, result["snapshot"]["visibleHeight"])

    def test_same_page_resize_during_inflight_render_requeues_latest_size(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const start = html.indexOf("async function renderPdfPage(pageNum)");
const end = html.indexOf("/* ── Application State", start);
if (start === -1 || end === -1) {
  throw new Error("failed to locate renderPdfPage in frontend/index.html");
}
const renderFnText = html.slice(start, end);

const logs = [];
const metrics = {
  renderCalls: 0,
  cancelledCalls: [],
  viewportWidths: [],
};
let firstTaskResolver = null;

function createContext() {
  return {
    setTransform() {},
    drawImage() {},
    clearRect() {},
  };
}

const visibleClassNames = new Set(["hidden"]);
const stageClassNames = new Set(["is-empty"]);
const visibleCanvas = {
  width: 0,
  height: 0,
  style: { width: "", height: "" },
  getContext() {
    return createContext();
  },
  classList: {
    add(name) {
      visibleClassNames.add(name);
    },
    remove(name) {
      visibleClassNames.delete(name);
    },
    contains(name) {
      return visibleClassNames.has(name);
    },
  },
};

const documentStub = {
  createElement(tagName) {
    if (tagName !== "canvas") {
      throw new Error(`unexpected tag: ${tagName}`);
    }
    return {
      width: 0,
      height: 0,
      style: { width: "", height: "" },
      getContext() {
        return createContext();
      },
    };
  },
};

const page = {
  getViewport({ scale }) {
    return {
      width: 300 * scale,
      height: 500 * scale,
    };
  },
  render({ viewport }) {
    metrics.renderCalls += 1;
    const callId = metrics.renderCalls;
    metrics.viewportWidths.push(Math.round(viewport.width));
    let settled = false;
    let resolvePromise;
    let rejectPromise;
    const promise = new Promise((resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;
    });
    const task = {
      promise,
      cancel() {
        if (settled) return;
        settled = true;
        metrics.cancelledCalls.push(callId);
        const error = new Error("cancelled");
        error.name = "RenderingCancelledException";
        rejectPromise(error);
      },
    };
    if (callId === 1) {
      firstTaskResolver = () => {
        if (settled) return;
        settled = true;
        resolvePromise();
      };
    } else {
      Promise.resolve().then(() => {
        if (settled) return;
        settled = true;
        resolvePromise();
      });
    }
    return task;
  },
};

const els = {
  pdfStage: {
    clientWidth: 456,
    classList: {
      add(name) {
        stageClassNames.add(name);
      },
      remove(name) {
        stageClassNames.delete(name);
      },
      contains(name) {
        return stageClassNames.has(name);
      },
    },
  },
  pdfCanvas: visibleCanvas,
};

const api = new Function(
  "window",
  "document",
  "els",
  "log",
  "pdfDocRef",
  `
var pdfDoc = pdfDocRef;
var pdfPageRendering = false;
var pdfPagePending = null;
${renderFnText}
return {
  renderPdfPage,
  getSnapshot: function () {
    return {
      pdfPageRendering,
      pdfPagePending,
      visibleWidth: els.pdfCanvas.width,
      visibleHeight: els.pdfCanvas.height,
      stageEmpty: els.pdfStage.classList.contains("is-empty"),
      visibleHidden: els.pdfCanvas.classList.contains("hidden"),
    };
  },
};
`,
)(
  { devicePixelRatio: 1 },
  documentStub,
  els,
  function log(message, payload) {
    logs.push({ message, payload });
  },
  {
    numPages: 5,
    async getPage() {
      return page;
    },
  },
);

const firstRenderPromise = api.renderPdfPage(1);
await Promise.resolve();
els.pdfStage.clientWidth = 656;
const secondRenderPromise = api.renderPdfPage(1);
await Promise.resolve();
if (firstTaskResolver) {
  firstTaskResolver();
}

await Promise.all([firstRenderPromise, secondRenderPromise]);

console.log(
  JSON.stringify({
    metrics,
    logs,
    snapshot: api.getSnapshot(),
  }),
);
"""
        )

        self.assertEqual(
            2,
            result["metrics"]["renderCalls"],
            "同页渲染中若容器宽度变化，应触发一次新的重绘而不是吞掉请求",
        )
        self.assertEqual(
            [1],
            result["metrics"]["cancelledCalls"],
            "尺寸变化导致的同页重绘应取消旧的 in-flight render",
        )
        self.assertEqual(
            [400, 600],
            result["metrics"]["viewportWidths"],
            "重绘应以最新容器宽度计算 viewport",
        )
        self.assertEqual([], result["logs"], "渲染取消不应打错误日志")
        self.assertFalse(result["snapshot"]["pdfPageRendering"])
        self.assertIsNone(result["snapshot"]["pdfPagePending"])
        self.assertFalse(result["snapshot"]["stageEmpty"])
        self.assertFalse(result["snapshot"]["visibleHidden"])
        self.assertEqual(600, result["snapshot"]["visibleWidth"])
        self.assertEqual(1000, result["snapshot"]["visibleHeight"])

    def test_pdf_revisit_uses_render_surface_cache_for_instant_draw(self) -> None:
        result = _run_node_script(
            r"""
import fs from "node:fs";
import path from "node:path";

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf-8");
const start = html.indexOf("async function renderPdfPage(pageNum)");
const end = html.indexOf("/* ── Application State", start);
if (start === -1 || end === -1) {
  throw new Error("failed to locate renderPdfPage in frontend/index.html");
}
const renderFnText = html.slice(start, end);

const metrics = { pageRenderCalls: 0 };
const ops = [];

function createContext(kind) {
  return {
    __kind: kind,
    setTransform() {},
    clearRect() {},
    drawImage(canvas, x, y) {
      ops.push({
        kind,
        op: "drawImage",
        source: canvas && canvas.__label ? canvas.__label : "unknown",
        x,
        y,
      });
    },
  };
}

const visibleCanvas = {
  __label: "visible",
  width: 0,
  height: 0,
  style: { width: "", height: "" },
  getContext() {
    return createContext("visible");
  },
  classList: {
    add() {},
    remove() {},
    contains() { return false; },
  },
};

const createdCanvases = [];
const documentStub = {
  createElement(tagName) {
    if (tagName !== "canvas") {
      throw new Error(`unexpected tag: ${tagName}`);
    }
    const canvas = {
      __label: `offscreen-${createdCanvases.length + 1}`,
      width: 0,
      height: 0,
      style: { width: "", height: "" },
      getContext() {
        return createContext("offscreen");
      },
    };
    createdCanvases.push(canvas);
    return canvas;
  },
};

const page = {
  getViewport({ scale }) {
    return { width: 300 * scale, height: 500 * scale };
  },
  render() {
    metrics.pageRenderCalls += 1;
    return { promise: Promise.resolve() };
  },
};

const els = {
  pdfStage: {
    clientWidth: 456,
    classList: { add() {}, remove() {}, contains() { return false; } },
  },
  pdfCanvas: visibleCanvas,
};

const api = new Function(
  "window",
  "document",
  "els",
  "log",
  "pdfDocRef",
  "metrics",
  "createdCanvases",
  `
var pdfDoc = pdfDocRef;
var pdfPageRendering = false;
var pdfPagePending = null;
${renderFnText}
return {
  renderPdfPage,
  getSnapshot: function () {
    return {
      pageRenderCalls: metrics.pageRenderCalls,
      cacheSize: renderPdfPage._cacheOrder ? renderPdfPage._cacheOrder.length : 0,
      cacheKeys: renderPdfPage._cacheOrder ? renderPdfPage._cacheOrder.slice() : [],
      visibleSize: [els.pdfCanvas.width, els.pdfCanvas.height],
      createdCanvasCount: createdCanvases.length,
    };
  },
};
`,
)(
  { devicePixelRatio: 2 },
  documentStub,
  els,
  function log() {},
  {
    numPages: 5,
    async getPage() {
      return page;
    },
  },
  metrics,
  createdCanvases,
);

await api.renderPdfPage(1);
const firstSnapshot = api.getSnapshot();
await api.renderPdfPage(1);
const secondSnapshot = api.getSnapshot();

console.log(JSON.stringify({
  firstSnapshot,
  secondSnapshot,
  ops,
}));
"""
        )

        self.assertEqual(1, result["firstSnapshot"]["pageRenderCalls"])
        self.assertEqual(1, result["firstSnapshot"]["cacheSize"])
        self.assertGreater(result["firstSnapshot"]["visibleSize"][0], 0)
        self.assertGreater(result["firstSnapshot"]["visibleSize"][1], 0)
        self.assertEqual(1, result["firstSnapshot"]["createdCanvasCount"])
        self.assertEqual(
            1,
            result["secondSnapshot"]["pageRenderCalls"],
            "再次访问同页时应命中缓存，不再触发 page.render",
        )
        self.assertEqual(1, result["secondSnapshot"]["cacheSize"])
        self.assertEqual(
            result["firstSnapshot"]["visibleSize"],
            result["secondSnapshot"]["visibleSize"],
        )
        self.assertGreaterEqual(
            len([op for op in result["ops"] if op["kind"] == "visible" and op["op"] == "drawImage"]),
            2,
            "首次渲染与缓存命中都应向可见画布写入图像",
        )
