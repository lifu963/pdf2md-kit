"""
Phase 3 Task 7 — 前端 spec 合规（`context/spec.md` 关键约束的静态扫描）。

仅扫描 `frontend/index.html` 内联样式；`frontend/src/**` 经仓库检索无对应违规模式。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


def _index_html_style_block() -> str:
    text = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r"<style>\s*([\s\S]*?)\s*</style>", text)
    assert match is not None
    return match.group(1)


class Phase3IndexHtmlComplianceTests(TestCase):
    def test_no_disallowed_border_radius_values(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        for m in re.finditer(r"border-radius\s*:\s*([^;]+);", text):
            val = m.group(1).strip()
            if val == "0":
                continue
            if val == "0 !important":
                continue
            self.fail(f"不允许的 border-radius: {val!r}（仅允许 0 或 0 !important）")

    def test_radial_gradient_only_for_dot_texture(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        allowed = frozenset(
            {
                "var(--fg)1px,transparent1px",
                "var(--bg)1px,transparent1px",
            }
        )
        for m in re.finditer(
            r"radial-gradient\s*\(\s*([^;]+?)\)\s*;",
            text,
        ):
            inner = re.sub(r"\s+", "", m.group(1))
            if inner in allowed:
                continue
            self.fail(f"不允许的 radial-gradient（仅允许点阵纹理）: {m.group(0)!r}")

    def test_no_linear_gradient(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn("linear-gradient", text, "spec 禁止 linear-gradient")

    def test_no_backdrop_filter_blur(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        for prop in ("backdrop-filter", "-webkit-backdrop-filter"):
            for m in re.finditer(rf"{re.escape(prop)}\s*:\s*([^;]+);", text):
                val = m.group(1).strip()
                self.assertEqual(
                    val,
                    "none",
                    f"{prop} 必须为 none，发现: {val!r}",
                )

    def test_transition_is_none_only(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        for m in re.finditer(r"transition\s*:\s*([^;]+);", text):
            val = m.group(1).strip()
            self.assertEqual(val, "none", f"transition 必须为 none，发现: {val!r}")

    def test_animation_only_blink_and_dots(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        for m in re.finditer(r"animation\s*:\s*([^;]+);", text):
            val = re.sub(r"\s+", " ", m.group(1).strip())
            if val == "none":
                continue
            if re.fullmatch(r"none\s+!important", val):
                continue
            self.assertRegex(
                val,
                re.compile(r"(blink-anim|dots-anim)", re.I),
                f"仅允许 blink-anim / dots-anim 动画，发现: {val!r}",
            )

    def test_style_block_has_no_rgba_or_color_mix(self) -> None:
        """Task 7 / review：双色调收敛，禁止 rgba(...) 与 color-mix(...)（含 :root 派生色）。"""
        style = _index_html_style_block()
        self.assertNotIn("rgba(", style)
        self.assertNotIn("color-mix(", style)

    def test_style_block_hex_only_allowlisted(self) -> None:
        """仅允许 spec 体系色 + 错误红 + PDF 画布白。"""
        style = _index_html_style_block()
        allowed = frozenset(
            {"#e8e4d9", "#1a1a2e", "#ddd8ca", "#ebe6d9", "#c62828", "#ffffff"}
        )
        for match in re.finditer(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", style):
            hx = match.group(0).lower()
            if len(hx) == 4:
                r, g, b = hx[1], hx[2], hx[3]
                hx = f"#{r}{r}{g}{g}{b}{b}"
            self.assertIn(
                hx,
                allowed,
                f"不允许的硬编码颜色: {match.group(0)!r}（仅允许 {sorted(allowed)!r}）",
            )

    def test_no_orphan_keyframe_tail_after_status_hint_spinner(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(
            text,
            re.compile(r"to\s*\{\s*opacity:\s*1;\s*transform:\s*scale\(1\);\s*\}"),
            "不应残留孤立 keyframes 尾片（to { opacity/scale }）",
        )

    def test_form_pixel_input_rules_follow_base_form_input(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        pos_base = text.find(".form-input, .form-textarea {")
        pos_override = text.find(".form-input.pixel-input")
        self.assertGreaterEqual(pos_base, 0)
        self.assertGreater(pos_override, pos_base, ".form-input.pixel-input 必须在基础 .form-input 规则之后")
