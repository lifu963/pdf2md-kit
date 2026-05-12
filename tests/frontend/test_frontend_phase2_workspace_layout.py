"""
Phase 2 — 双栏工作区骨架与分隔线（布局修复回归）。

断言 `frontend/index.html` 中关键 DOM 锚点：两栏 + `workspace-divider`，无三栏专有节点。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class Phase2WorkspaceStructureTests(TestCase):
    def test_workspace_divider_exists(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r'id="workspace-divider"')

    def test_workspace_children_are_pdf_divider_editor(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        ws = text.index('id="workspace"')
        chunk = text[ws : ws + 12000]
        self.assertRegex(chunk, r'id="pdf-pane"')
        self.assertRegex(chunk, r'id="workspace-divider"')
        self.assertRegex(chunk, r'class="[^"]*\beditor-pane\b[^"]*"')
        self.assertNotRegex(chunk, r'id="page-list-pane"')
        self.assertNotRegex(chunk, r'id="control-pane"')
        self.assertNotRegex(chunk, r'workspace-main-row')

    def test_workspace_css_is_two_column_grid(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"\.workspace\s*\{[^}]*display:\s*grid", re.DOTALL)
        self.assertIn("grid-template-columns:", text)
        self.assertRegex(
            text,
            r"minmax\(var\(--workspace-min-left-pane\),\s*var\(--workspace-left-width\)\)\s+var\(--workspace-divider-width\)\s+minmax\(var\(--workspace-min-right-pane\),\s*1fr\)",
        )


class Phase2HeaderWorkspaceTests(TestCase):
    def test_workspace_header_anchors_exist(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r'id="header-workspace-row"')
        self.assertRegex(text, r'id="header-task-title"')
        self.assertRegex(text, r'id="header-task-status-symbol"')
        self.assertRegex(text, r'id="header-page-indicator"')
        self.assertRegex(text, r'id="header-back-home-btn"')


class Phase2PageNavigationScriptTests(TestCase):
    """Review P1：防止 updatePageNavButtons 声明丢失导致 IIFE 提前 return。"""

    def test_update_page_nav_buttons_function_declared(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"function updatePageNavButtons\s*\(\s*currentIndex\s*\)\s*\{")
        pos_nav = text.find("function updatePageNavButtons")
        pos_status = text.find("function updatePageStatusIndicator")
        self.assertGreaterEqual(pos_nav, 0)
        self.assertGreater(pos_status, pos_nav)


class Phase2PageListSummaryHydrationTests(TestCase):
    """Review P2：摘要来源为逐页 GET /pages/{n} 的 content / error 首行（左栏列表已移除，仍保留 hydrate 以供状态复用）。"""

    def test_hydrate_helpers_exist(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"async function fetchPageSummarySnippet\s*\(")
        self.assertRegex(text, r"async function hydratePageListSummaries\s*\(")

    def test_replace_page_summaries_merges_summary_map(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("nextNums[state.pages[pi].page_num]", text)
        self.assertIn("state.pageListSummaryByNum = merged", text)
