"""
PDF 预览区布局回归：`.pdf-stage` 合法 CSS、空态垂直居中、状态提示与占位符互斥。

锁定 Neo-brutalist 视觉不变前提下，避免占位文案、载入提示、拖拽遮罩在同一行挤压重叠。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class PdfPreviewLayoutRegressionTests(TestCase):
    def test_pdf_stage_rule_is_valid_css_not_nested_selector_glitch(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(
            text,
            re.compile(r"\.pdf-stage\s*\{\s*\n\s*\.pdf-stage", re.MULTILINE),
            msg="`.pdf-stage {` 后不得直接拼接另一个选择器（会破坏整段规则解析）",
        )

    def test_pdf_stage_declares_flex_viewport_container(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        idx = text.find(".pdf-stage {")
        self.assertGreaterEqual(idx, 0, msg="缺少 `.pdf-stage {` 基础规则")
        segment = text[idx : idx + 900]
        self.assertRegex(segment, r"position:\s*relative")
        self.assertRegex(segment, r"display:\s*flex")
        self.assertRegex(segment, r"justify-content:\s*center")

    def test_empty_stage_pseudo_layers_share_base_geometry(self) -> None:
        """双层装饰页仍共用一套几何，避免出现半截规则（::before 缺 content）。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r"\.pdf-stage\.is-empty::before\s*,\s*\.pdf-stage\.is-empty::after\s*\{[^}]*content:\s*[\"']{2}",
                re.DOTALL,
            ),
        )

    def test_empty_stage_paper_layers_vertically_centered(self) -> None:
        """空态装饰纸层垂直居中，避免顶对齐 + 超高宽比撑出外层滚动条。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        idx = text.find(".pdf-stage.is-empty::before,")
        self.assertGreaterEqual(idx, 0, msg="缺少空态双层装饰联合规则")
        segment = text[idx : idx + 900]
        self.assertRegex(segment, r"top:\s*50%")

    def test_empty_stage_paper_height_capped_to_stage(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        idx = text.find(".pdf-stage.is-empty::before,")
        self.assertGreaterEqual(idx, 0)
        segment = text[idx : idx + 900]
        self.assertRegex(
            segment,
            r"height:\s*min\(",
            msg="空态纸层应用 min(固定上限, calc(100% - …)) 限制高度以适配左栏",
        )

    def test_show_pdf_status_hint_accepts_layout_mode_empty_overlay_vs_toast(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        pos_show = text.find("function showPdfStatusHint")
        self.assertGreaterEqual(pos_show, 0)
        pos_hide = text.find("function hidePdfStatusHint", pos_show)
        self.assertGreater(pos_hide, pos_show)
        block = text[pos_show:pos_hide]
        self.assertRegex(block, r"function\s+showPdfStatusHint\s*\(\s*message\s*,\s*layoutMode\s*\)")
        self.assertIn('layoutMode || "toast"', block)
        self.assertIn("empty-overlay", block)
        self.assertIn("is-empty", block)

    def test_pdf_status_hint_local_preview_load_uses_empty_overlay(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn(
            'showPdfStatusHint("正在载入本地 PDF 预览...", "empty-overlay")',
            text,
        )

    def test_pdf_status_hint_preview_failure_uses_toast(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn(
            'showPdfStatusHint("PDF 预览加载失败，请重新选择文件。", "toast")',
            text,
        )

    def test_pdf_status_hint_upload_and_workspace_sync_use_toast(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn(
            'showPdfStatusHint("正在上传 PDF 并创建任务...", "toast")',
            text,
        )
        self.assertIn(
            'showPdfStatusHint("任务已创建，正在同步工作区...", "toast")',
            text,
        )

    def test_pdf_status_hint_remote_workspace_pdf_load_uses_empty_overlay(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn(
            'showPdfStatusHint("正在载入 PDF 预览...", "empty-overlay")',
            text,
        )

    def test_hide_pdf_status_hint_clears_container_flag_and_restores_placeholder_when_empty(
        self,
    ) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        pos_hide = text.find("function hidePdfStatusHint")
        self.assertGreaterEqual(pos_hide, 0)
        block = text[pos_hide : pos_hide + 550]
        self.assertIn('els.pdfViewerContainer.classList.remove("pdf-status-active")', block)
        self.assertIn('els.pdfPlaceholder.classList.remove("hidden")', block)
        self.assertIn("is-empty", block)

    def test_pdf_status_active_centers_hint_overlay_css(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r"\.pdf-viewer-container\.pdf-status-active\s+\.pdf-status-hint\s*\{[^}]*"
                r"transform:\s*translate\(-50%\s*,\s*-50%\)",
                re.DOTALL,
            ),
        )

    def test_pdf_header_upload_button_uses_hugeicons_file_upload_svg(self) -> None:
        """PDF 预览标题栏上传入口使用 Hugeicons「File Upload」描边矢量，而非 emoji 回形针。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        start = text.find('id="upload-pdf-header-btn"')
        self.assertGreaterEqual(start, 0, msg="缺少 #upload-pdf-header-btn")
        end = text.find("</button>", start)
        self.assertGreater(end, start)
        fragment = text[start:end]
        self.assertNotIn("📎", fragment)
        self.assertIn("<svg", fragment)
        self.assertIn('viewBox="0 0 24 24"', fragment)
        # 与 npm @hugeicons/core-free-icons FileUploadIcon 两处 path d 对齐，防止误换为其它图标
        self.assertIn("M4 12L4 14.5442C4 17.7892", fragment)
        self.assertIn("M10 5C9.41016 4.39316", fragment)
