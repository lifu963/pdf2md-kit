"""
Phase 1 — 像素风设计基础（Task 1）回归快照。

通过静态断言 `frontend/index.html` 中包含 spec 定义的 CSS 变量、字体与工具类，
避免在无浏览器截图测试的情况下漏配关键令牌。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class Phase1PixelFoundationTests(TestCase):
    def test_design_tokens_press_start_body_mono_fonts_linked(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(r"fonts\.googleapis\.com[^\n]*Press\+Start\+2P", re.I),
        )
        self.assertRegex(
            text,
            re.compile(r"fonts\.googleapis\.com[^\n]*Noto\+Sans\+SC", re.I),
        )
        self.assertRegex(
            text,
            re.compile(r"fonts\.googleapis\.com[^\n]*JetBrains\+Mono", re.I),
        )

    def test_root_css_variables_from_spec(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, re.compile(r"--bg:\s*#e8e4d9"))
        self.assertRegex(text, re.compile(r"--fg:\s*#1a1a2e"))
        self.assertRegex(text, re.compile(r"--bg-alt:\s*#ddd8ca"))
        self.assertRegex(text, re.compile(r"--bg-invert:\s*#1a1a2e"))
        self.assertRegex(text, re.compile(r"--fg-invert:\s*#ebe6d9"))
        self.assertRegex(text, re.compile(r"--border-w:\s*3px"))
        self.assertRegex(text, re.compile(r"--pixel:\s*3px"))
        self.assertRegex(text, re.compile(r"--btn-hard:\s*5px"))
        self.assertRegex(text, re.compile(r"--btn-face:\s*#ffffff"))

    def test_typography_css_variables(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"--font-pixel:\s*['\"]Press Start 2P['\"]")
        self.assertRegex(text, r"--font-body:\s*['\"]Noto Sans SC['\"]")
        self.assertRegex(text, r"--font-mono:\s*['\"]JetBrains Mono['\"]")

    def test_pixel_border_utilities_exist(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"\.pixel-border\s*\{")
        self.assertRegex(text, r"\.pixel-border-bottom\s*\{")
        self.assertRegex(text, r"\.pixel-border-top\s*\{")
        self.assertRegex(text, r"\.pixel-border-left\s*\{")
        self.assertRegex(text, r"\.pixel-border-right\s*\{")

    def test_scanlines_utility_exists(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"\.scanlines\s*\{")

    def test_pixel_button_and_input_label_utilities_exist(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"\.pixel-btn-sm\s*\{")
        self.assertRegex(text, r"\.pixel-btn(?![-\w])\s*\{")
        self.assertRegex(text, r"\.pixel-input\s*\{")
        self.assertRegex(text, r"\.pixel-label\s*\{")
    def test_root_border_tokens_are_colors_not_shorthand(self) -> None:
        """旧代码大量写作 border: 1px solid var(--border)；--border 必须是颜色而非 shorthand。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, re.compile(r"--border:\s*var\(--fg\)\s*;"))
        self.assertRegex(text, re.compile(r"--border-strong:\s*var\(--fg\)\s*;"))
        self.assertRegex(text, re.compile(r"--border-focus:\s*var\(--fg\)\s*;"))
        condensed = text.replace(" ", "")
        self.assertNotIn("--border:var(--border-w)solidvar(--fg)", condensed)

    def test_panel_pixel_border_rule_follows_panel_and_restores_shadow(self) -> None:
        """.panel 的 box-shadow 会覆盖仅靠来源顺序较早的 .pixel-border，需延后合并选择器兜底。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        panel_pos = text.find(".panel {")
        fix_pos = text.find(".panel.pixel-border")
        self.assertGreaterEqual(panel_pos, 0)
        self.assertGreater(fix_pos, panel_pos)
        slice_end = fix_pos + 600
        self.assertLess(slice_end, len(text))
        block = text[fix_pos:slice_end]
        self.assertRegex(
            block,
            re.compile(
                r"\.panel\.pixel-border\s*\{[^}]*box-shadow:\s*var\(--pixel\)\s+var\(--pixel\)\s+0\s+var\(--fg\)\s*;",
                re.DOTALL,
            ),
        )


class Phase1HomeShellTests(TestCase):
    """首页工作区壳层锚点（两栏布局；底层纯色背景，无点阵纹理）。"""

    def test_workspace_home_shell_markup(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r'id="workspace"')
        ws = text.index('id="workspace"')
        window = text[ws : ws + 160]
        self.assertRegex(window, r'class="[^"]*\bworkspace\b')
        self.assertNotIn("bg-dots", window, "主工作区不再使用点阵背景类 bg-dots")
        self.assertNotRegex(text, r'id="landing-zone"')

    def test_events_log_toggle_uses_native_button_semantics(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r'<button(?=[^>]*id="toggle-events-btn")(?=[^>]*type="button")(?=[^>]*aria-expanded="false")(?=[^>]*aria-controls="events-log")[^>]*>',
                re.DOTALL,
            ),
        )
        self.assertNotRegex(
            text,
            re.compile(r'id="toggle-events-btn"[^>]*\brole="button"', re.DOTALL),
        )
        self.assertNotRegex(
            text,
            re.compile(r'id="toggle-events-btn"[^>]*\btabindex=', re.DOTALL),
        )
