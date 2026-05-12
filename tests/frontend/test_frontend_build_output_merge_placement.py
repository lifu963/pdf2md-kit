"""
Review P1：合并入口须与「输出区仅在 ready 显示」解耦。

`#build-output-menu-wrap` 必须在 `#page-editor-section` 上方的编辑区顶栏内（与 `#page-editor-section` 同属
`.editor-pane`），以便 `extracted`/`building` 状态下父级仍可见时用户能点击「合并」。
"""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


def _snippet_between(marker_start: str, marker_end: str, text: str) -> str:
    i0 = text.index(marker_start)
    i1 = text.index(marker_end, i0 + len(marker_start))
    return text[i0:i1]


class BuildOutputMergePlacementTests(TestCase):
    def test_build_output_menu_wrap_is_in_editor_pane_above_page_section(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        pane_to_page_section = _snippet_between(
            'class="workspace-col workspace-col-right panel pixel-border"',
            'id="page-editor-section"',
            text,
        )
        self.assertIn('id="build-output-menu-wrap"', pane_to_page_section)
        self.assertIn('id="build-output-btn"', pane_to_page_section)

    def test_build_output_menu_wrap_not_only_in_output_section(self) -> None:
        """合并后正文区仅含 output 编辑器；下载/回退在顶栏，不在输出 section。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        output_section = _snippet_between(
            'id="output-editor-section"', 'id="output-editor"', text
        )
        self.assertNotIn("build-output-menu-wrap", output_section)
        self.assertNotIn("output-download-link", output_section)
        self.assertNotIn("discard-output-btn", output_section)

    def test_merged_output_actions_live_in_status_bar(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        right_col_to_page = _snippet_between(
            'class="workspace-col workspace-col-right panel pixel-border"',
            'id="page-editor-section"',
            text,
        )
        self.assertIn('id="merged-output-actions"', right_col_to_page)
        self.assertIn('id="output-download-link"', right_col_to_page)
        self.assertIn('id="discard-output-btn"', right_col_to_page)

