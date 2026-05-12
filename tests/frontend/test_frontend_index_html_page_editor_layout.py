"""
页面编辑区排版契约：顶栏「左侧状态 + 右侧操作」、未建任务时隐藏页统计、操作与刷新同排。
"""

from __future__ import annotations

from pathlib import Path
import re
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class PageEditorLayoutStructureTests(TestCase):
    def test_job_stats_wrap_stays_hidden(self) -> None:
        """顶栏不再展示页统计块；脚本始终维持 hidden。"""
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r'id="control-job-stats-wrap"[^>]*\bclass="[^"]*\bhidden\b',
        )
        stats_idx = source.index("els.controlJobStatsWrap")
        stats_block = source[stats_idx : stats_idx + 350]
        self.assertIn(
            'classList.add("hidden")',
            stats_block,
            "始终保持 hidden，勿在有 jobId 时再移除",
        )

    def test_editor_status_left_does_not_use_flex_grow(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        m = re.search(
            r"\.editor-status-left\s*\{[^}]*\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(m)
        rule = m.group(0)
        self.assertRegex(
            rule,
            r"flex\s*:\s*0\s+1\s+auto",
            ".editor-status-left 不应 flex-grow，避免左侧条在宽屏下被撑裂",
        )

    def test_action_buttons_grouped_with_refresh_in_status_bar(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        start = source.index('class="editor-status-bar"')
        chunk = source[start : start + 3500]
        self.assertIn('class="editor-status-actions"', chunk)
        single_idx = chunk.find('id="single-page-test-btn"')
        start_idx = chunk.find('id="start-extraction-btn"')
        refresh_idx = chunk.find('id="refresh-job-btn"')
        self.assertGreater(single_idx, 0)
        self.assertGreater(start_idx, 0)
        self.assertGreater(refresh_idx, 0)
        self.assertLess(single_idx, start_idx)
        self.assertLess(start_idx, refresh_idx, "刷新应与单页测试/开始提取同处顶栏操作组末端")

    def test_page_section_has_no_page_content_title_row(self) -> None:
        """已移除「◆ 页 面 内 容」标题栏，页级状态直接进入内容区。"""
        source = INDEX_HTML.read_text(encoding="utf-8")
        sec = source.index('id="page-editor-section"')
        block = source[sec : sec + 1200]
        self.assertNotRegex(
            block,
            r"page-editor-toolbar",
            "不应再保留独立 page-editor-toolbar 标题行",
        )
        self.assertRegex(
            block,
            r'id="page-status-indicator"',
            "页状态条应留在 #page-editor-section 内",
        )

    def test_editor_status_actions_locks_pixel_btn_height(self) -> None:
        """顶栏 pixel-btn-sm（直连按钮、合并菜单内、phase 分组内按钮与同组下载链接）：高度契约一致。"""
        source = INDEX_HTML.read_text(encoding="utf-8")
        m_btn = re.search(
            r"\.editor-status-actions > button\.pixel-btn-sm,\s*\n\s*\.editor-status-actions \.build-output-menu button\.pixel-btn-sm,\s*\n\s*\.editor-status-actions \.editor-status-phase-actions > button\.pixel-btn-sm \{[^}]+\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(m_btn)
        rule_btn = m_btn.group(0)
        self.assertIn("height: 34px", rule_btn)
        self.assertIn("min-height: 34px", rule_btn)
        self.assertIn("max-height: 34px", rule_btn)
        self.assertIn("white-space: nowrap", rule_btn)

        m_a = re.search(
            r"\.editor-status-actions \.editor-status-phase-actions > a\.pixel-btn-sm \{[^}]+\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(m_a)
        rule_a = m_a.group(0)
        self.assertIn("height: 34px", rule_a)
        self.assertIn("min-height: 34px", rule_a)
        self.assertIn("max-height: 34px", rule_a)
        self.assertIn("white-space: nowrap", rule_a)


class PageEditorStartButtonSizeTests(TestCase):
    def test_start_extraction_matches_compact_button_class(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        m = re.search(
            r'<button[^>]*id="start-extraction-btn"[^>]*>',
            source,
            re.I,
        )
        self.assertIsNotNone(m)
        tag = m.group(0)
        self.assertIn("pixel-btn-sm", tag)
        self.assertNotRegex(
            tag,
            r'class="[^"]*\bpixel-btn"(?![-\w])',
            "开始提取应与单页测试等使用同一紧凑按钮尺寸，不得单独使用 .pixel-btn",
        )
