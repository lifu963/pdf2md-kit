"""Review fixes：分隔线比例 localStorage；窄视口仍为两栏（横向滚动外层）。"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class WorkspaceSplitPersistenceTests(TestCase):
    def test_bootstrap_loads_split_before_first_apply(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        pos = text.index("runAction(async function () {")
        snippet = text[pos : pos + 220]
        load_pos = snippet.index("loadPersistedWorkspaceSplitRatio")
        apply_pos = snippet.index("applyWorkspaceSplit", load_pos)
        self.assertGreater(apply_pos, load_pos)

    def test_workspace_split_storage_key_defined(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"WORKSPACE_SPLIT_STORAGE_KEY\s*=")

    def test_load_and_set_item_for_split_ratio_present(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(text, r"function\s+loadPersistedWorkspaceSplitRatio\s*\(")
        self.assertRegex(text, r"function\s+persistWorkspaceSplitRatio\s*\(")
        self.assertRegex(
            text,
            re.compile(
                r"localStorage\.getItem\s*\(\s*WORKSPACE_SPLIT_STORAGE_KEY\s*\)",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            text,
            re.compile(
                r"localStorage\.setItem\s*\(\s*WORKSPACE_SPLIT_STORAGE_KEY\s*,",
                re.DOTALL,
            ),
        )

    def test_stop_workspace_resize_calls_persist(self) -> None:
        """松手拖动后应将比例写入 localStorage。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        idx = text.find("function stopWorkspaceResize")
        self.assertGreaterEqual(idx, 0)
        end = text.find("function ", idx + 5)
        block = text[idx:end]
        self.assertIn("persistWorkspaceSplitRatio(", block)


class WorkspaceTwoColumnViewportTests(TestCase):
    def test_workspace_wrapped_for_horizontal_overflow(self) -> None:
        """两栏始终保持三轨 grid；外层横向滚动以避免窄窗口垂直堆叠。"""
        raw = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('class="workspace-scroll"', raw)
        self.assertRegex(
            raw,
            re.compile(r"\.workspace-scroll\s*\{[^}]*overflow-x:\s*auto", re.DOTALL),
        )
        self.assertRegex(
            raw,
            re.compile(
                r"min-width:\s*calc\(\s*var\(--workspace-min-left-pane\)\s*\+\s*var\(--workspace-min-right-pane\)\s*\+\s*var\(--workspace-divider-width\)\s*\)",
                re.DOTALL,
            ),
        )

    def test_stylesheet_has_no_workspace_single_track_at_980(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(
            text,
            re.compile(
                r"@media[^{]+\{[^}]*\.workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0\s*,\s*1fr\)",
                re.DOTALL | re.I,
            ),
            msg="narrow viewport 不允许把 workspace 收成单列 grid",
        )
        self.assertNotRegex(
            text,
            re.compile(
                r"@media[^{]+\{[^}]*\.workspace-divider\s*\{[^}]*display:\s*none",
                re.DOTALL | re.I,
            ),
            msg="narrow viewport 不应隐藏分隔条（与工作区始终两栏一致）",
        )

    def test_workspace_scroll_is_column_flex_so_inner_fills_viewport_height(self) -> None:
        """外包层须为 flex 列容器，内层 .workspace 的 flex:1 才能占满滚动区剩余高度。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        scroll_start = text.index(".workspace-scroll {")
        workspace_rule = text.index(".workspace {", scroll_start)
        block = text[scroll_start:workspace_rule]
        self.assertRegex(block, r"display:\s*flex\b")
        self.assertRegex(block, r"flex-direction:\s*column\b")


class WorkspaceClampAsymmetricMinimumTests(TestCase):
    """非对称左右最小宽度下，窄视口 clamp 不得回退 0.5，否则 persist 会污染 localStorage（见 context/review-comments.md P1）。"""

    def _clamp_workspace_split_ratio_block(self, text: str) -> str:
        needle = "function clampWorkspaceSplitRatio(ratio, availableWidth)"
        start = text.find(needle)
        self.assertGreaterEqual(start, 0, "应存在 clampWorkspaceSplitRatio")
        end = text.find("function updateWorkspaceDividerA11y", start)
        self.assertGreater(end, start)
        return text[start:end]

    def test_degenerate_min_clamp_returns_min_ratio_not_half(self) -> None:
        raw = INDEX_HTML.read_text(encoding="utf-8")
        block = self._clamp_workspace_split_ratio_block(raw)
        self.assertRegex(
            block,
            re.compile(r"if\s*\(\s*minRatio\s*>=\s*maxRatio\s*\)\s*return\s+minRatio\s*;"),
            msg="视口仅能刚好放下双最小时应为唯一合法比例（≈320/(320+600)），不能用 0.5",
        )
        self.assertNotRegex(
            block,
            r"if\s*\(\s*minRatio\s*>=\s*maxRatio\s*\)\s*return\s+0\.5",
            msg="旧的对称最小宽度特例：在非对称 mins 下会错误覆盖用户持久化比例",
        )

    def test_persist_uses_clamp_before_writing_storage(self) -> None:
        """resize 触发 persist → clamp；退化分支须写回可用比例而非占位 0.5。"""
        raw = INDEX_HTML.read_text(encoding="utf-8")
        idx = raw.find("function persistWorkspaceSplitRatio")
        self.assertGreaterEqual(idx, 0)
        end = raw.find("function ", idx + 15)
        block = raw[idx:end]
        self.assertRegex(block, r"clampWorkspaceSplitRatio\s*\(\s*workspaceSplitRatio\s*,")
        self.assertRegex(block, r"workspaceSplitRatio\s*=\s*clamped\s*")
        self.assertRegex(block, r"localStorage\.setItem\s*\(\s*WORKSPACE_SPLIT_STORAGE_KEY")
