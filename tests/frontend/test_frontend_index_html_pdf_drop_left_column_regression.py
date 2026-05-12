"""
Review P2：左栏 PDF 拖入命中区须覆盖整列（含顶栏）。

`#pdf-pane` 仅含正文后，拖拽监听器仍绑在 `#pdf-pane` 会导致「拖到 PDF 预览标题行」无效。
锁定：`workspace-col-left` 稳定 id，且 Drag & Drop 段落内监听器绑在对应 `els.*` 上（非 `els.pdfPane`）。
"""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class PdfDropLeftColumnRegressionTests(TestCase):
    def test_workspace_col_left_has_stable_id(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="workspace-col-left"', raw)
        self.assertRegex(
            raw,
            r'<div\s+id="workspace-col-left"\s+class="[^"]*\bworkspace-col-left\b',
        )

    def test_drag_drop_handlers_use_left_column_not_pdf_pane_only(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        pos = text.index("pdfDropZone: document.getElementById(\"workspace-col-left\")")
        self.assertLess(pos, text.index("/* ── Drag & Drop ─────────────────────── */"))
        pos = text.index("/* ── Drag & Drop ─────────────────────── */")
        end = text.index('document.addEventListener("dragover"', pos)
        block = text[pos:end]
        self.assertIn("els.pdfDropZone.addEventListener(\"dragenter\"", block)
        self.assertIn("els.pdfDropZone.addEventListener(\"drop\"", block)
        self.assertNotIn('els.pdfPane.addEventListener("dragenter"', block)
