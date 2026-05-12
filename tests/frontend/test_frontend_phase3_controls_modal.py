"""
Phase 3 — 编辑区控件 + 内联配置面板（静态锚点）。

DOM 为三栏时已迁移为双栏；操作按钮锚定在 `.editor-pane`；配置以内联 `.config-panel` 折叠面板呈现。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class Phase3EditorPaneTests(TestCase):
    def test_editor_pane_has_key_controls_and_pixel_titles(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r'class="[^"]*\beditor-pane\b[^"]*"')
        self.assertRegex(text, r'id="control-stat-total"')
        self.assertRegex(text, r'id="merged-output-actions"')
        self.assertRegex(text, r"class=\"editor-status-actions\"")
        self.assertRegex(text, r'id="status-text"')
        self.assertRegex(text, r'id="progress-label"')
        self.assertRegex(text, r'id="start-extraction-btn"')
        self.assertRegex(text, r'id="single-page-test-btn"')
        self.assertRegex(text, r'id="retry-page-btn"')
        self.assertRegex(text, r'id="build-output-btn"')
        self.assertRegex(text, r'id="output-download-link"')
        self.assertRegex(text, r'id="discard-output-btn"')

    def test_editor_pane_buttons_use_pixel_btn_classes(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r'<button[^>]*id="start-extraction-btn"[^>]*class="[^"]*\bpixel-btn',
                re.I,
            ),
        )
        self.assertRegex(
            text,
            re.compile(
                r'<button[^>]*id="build-output-btn"[^>]*class="[^"]*\bpixel-btn',
                re.I,
            ),
        )


class Phase3ConfigPanelMarkupTests(TestCase):
    def test_config_inline_panel_markup(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(text, r'id="config-modal"')
        self.assertRegex(text, r'<section[^>]*class="[^"]*\bconfig-panel\b[^"]*"')
        self.assertRegex(text, r'id="config-form"')
        self.assertIn("function setConfigPanelExpanded(expanded)", text)
        self.assertRegex(text, r"syncPanelCollapsedState\(els\.configPanel")
