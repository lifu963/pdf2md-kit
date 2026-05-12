"""
工作区左右栏视觉：相对主色略深的同系顶栏、浅正文、虚线分隔、左栏无双层线框、分隔条仅保留中部拖拽块。

对 `frontend/index.html` 内联样式做字符串级锁定，避免布局回归。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


def _rule_block(text: str, selector: str) -> str:
    """返回第一个 `selector { ... }` 规则块（含外层花括号）。"""
    needle = selector + " {"
    start = text.find(needle)
    if start < 0:
        raise AssertionError(f"找不到样式规则: {selector!r}")
    brace = text.find("{", start)
    depth = 0
    i = brace
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError(f"样式规则未闭合: {selector!r}")


class WorkspacePaneChromeTests(TestCase):
    def test_workspace_divider_has_no_full_height_line_pseudo(self) -> None:
        """竖向整高「分割线」由 ::before 绘制；需求为去掉线，仅保留中部小矩形（::after）。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(
            text,
            re.compile(
                r"\.workspace-divider::before\s*\{[^}]*background:\s*var\(--fg\)",
                re.DOTALL,
            ),
            msg="应移除分隔条竖线的 ::before（或不得再用 background: var(--fg) 画线）",
        )

    def test_workspace_divider_grip_pseudo_remains(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(r"\.workspace-divider::after\s*\{[^}]*width:\s*8px", re.DOTALL),
            msg="应保留中部可拖拽识别块（::after）",
        )

    def test_pdf_placeholder_has_no_dashed_frame_border(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = _rule_block(raw, ".pdf-placeholder")
        self.assertNotRegex(
            block,
            re.compile(r"\bborder\s*:"),
            msg=".pdf-placeholder 不应再有线框（与内层实线叠加成双框）",
        )

    def test_pdf_viewer_container_before_has_no_inner_solid_frame(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = _rule_block(raw, ".pdf-viewer-container::before")
        self.assertRegex(block, r"\bborder:\s*none\b", msg="内层实线框应去除（::before 边框置 none）")

    def test_workspace_pdf_header_uses_deeper_surface_with_dashed_separator(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = _rule_block(raw, ".workspace-col-left > .pane-header")
        self.assertRegex(block, r"background:\s*var\(--bg-alt\)")
        self.assertRegex(block, r"border-bottom:\s*var\(--border-w\)\s+dashed\s+var\(--fg\)")

    def test_workspace_editor_status_bar_uses_deeper_surface_with_dashed_separator(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = _rule_block(raw, ".workspace-col-right > .editor-status-bar")
        self.assertRegex(block, r"background:\s*var\(--bg-alt\)")
        self.assertRegex(block, r"border-bottom:\s*var\(--border-w\)\s+dashed\s+var\(--fg\)")

    def test_workspace_pdf_header_title_matches_pixel_btn_typography(self) -> None:
        """与空态「选择 PDF」所用 `.pixel-btn` 一致（14px / 0.06em）。"""
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = _rule_block(raw, ".workspace-col-left > .pane-header h2")
        self.assertRegex(block, r"font-family:\s*var\(--font-body\)")
        self.assertRegex(block, r"font-size:\s*14px")
        self.assertRegex(block, r"letter-spacing:\s*0\.06em")
        self.assertRegex(block, r"text-transform:\s*none")

    def test_workspace_status_chip_matches_pixel_btn_sm_typography(self) -> None:
        """与 `.editor-status-actions` 内 `.pixel-btn-sm` 一致（12px / 0.04em）。"""
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = _rule_block(raw, ".workspace-col-right > .editor-status-bar .status-chip")
        self.assertRegex(block, r"font-family:\s*var\(--font-body\)")
        self.assertRegex(block, r"font-size:\s*12px")
        self.assertRegex(block, r"letter-spacing:\s*0\.04em")
        self.assertRegex(block, r"text-transform:\s*none")

    def test_workspace_status_dot_scales_with_chip_font(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = _rule_block(raw, ".workspace-col-right > .editor-status-bar .status-chip .status-dot")
        self.assertRegex(block, r"width:\s*1em")
        self.assertRegex(block, r"height:\s*1em")

    def test_pdf_placeholder_has_no_or_divider_copy(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn("placeholder-divider", raw)
        self.assertNotIn("— 或 —", raw)

    def test_workspace_grid_uses_resizable_columns_and_subgrid_for_equal_header_height(self) -> None:
        """左宽仍由 --workspace-left-width 拖动调节；两行 grid + subgrid 使左右顶栏同高、虚线对齐。"""
        raw = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            raw,
            r"grid-template-columns:\s*minmax\(var\(--workspace-min-left-pane\),\s*var\(--workspace-left-width\)\)\s+var\(--workspace-divider-width\)\s+minmax\(var\(--workspace-min-right-pane\),\s*1fr\)",
        )
        self.assertRegex(raw, r"grid-template-rows:\s*auto\s+1fr")
        self.assertIn("grid-template-rows: subgrid;", raw)

    def test_workspace_panels_use_light_body_background(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        pdf_block = _rule_block(raw, ".workspace .pdf-pane")
        editor_block = _rule_block(raw, ".workspace .editor-pane")
        self.assertRegex(pdf_block, r"background:\s*var\(--bg\)")
        self.assertRegex(editor_block, r"background:\s*var\(--bg\)")
