"""
合并前 review 约定的 UI 结构/样式契约（frontend/index.html 字符串级断言）。

锁定历史面板右侧对齐与配置操作区分组等较易在后续改样式时被无意回退的细节。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class ReviewUiContractsTests(TestCase):
    def test_history_strip_padding_token_shared(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(r"--history-strip-pad-inline-end:\s*calc\(var\(--btn-hard\) \+ 6px\)\s*;"),
        )
        toolbar = self._extract_rule_block(text, r"\.history-toolbar\s*\{")
        self.assertIn("padding-inline-end: var(--history-strip-pad-inline-end);", toolbar)
        lst = self._extract_rule_block(text, r"(?<!pixel-border\.)\.history-list\s*\{")
        self.assertIn("padding-inline-end: var(--history-strip-pad-inline-end);", lst)

    def test_history_count_pinned_end_of_toolbar(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        block = self._extract_rule_block(text, r"\.history-toolbar \.history-count\s*\{")
        self.assertIn("margin-inline-start: auto;", block)

    def test_config_actions_dom_and_save_button_classes(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r'<div class="config-actions-buttons">[\s\S]*?'
                r'<button id="save-config-btn"[^>]*class="[^"]*\bpixel-btn-sm\b[^"]*\bconfig-save-primary\b[^"]*"'
                r"[^>]*>",
                re.DOTALL,
            ),
        )
        self.assertIn('<div class="config-actions-extra">', text)

    def test_config_actions_css_layout_present(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"\.config-actions-buttons\s*\{")
        self.assertRegex(text, r"\.config-actions-extra\s*\{")

    @staticmethod
    def _extract_rule_block(html: str, selector_pattern: str) -> str:
        match = re.search(selector_pattern, html)
        if not match:
            raise AssertionError(f"selector not found: {selector_pattern!r}")
        start = match.end() - 1
        depth = 0
        for i in range(start, len(html)):
            ch = html[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return html[start : i + 1]
        raise AssertionError(f"unclosed rule for {selector_pattern!r}")
