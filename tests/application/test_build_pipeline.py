"""
Step 09：build.pipeline 纯函数测试

验证目标：
1. 固定输入产生稳定、可重复的输出
2. 清理函数幂等（对已清理结果再次清理，输出不变）
3. 函数内部不依赖文件系统和时钟（禁止导入 pathlib、os.path、datetime 等）
"""
from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
import unittest
from pathlib import Path


# ─── 辅助函数 ────────────────────────────────────────────────────────────────

def _load_pipeline():
    """导入 build.pipeline 模块，确保无启动副作用。"""
    import backend.build.pipeline as p
    return p


# ─── 1. 无 I/O 依赖守护 ──────────────────────────────────────────────────────

class TestNoDependencyOnIOOrClock(unittest.TestCase):
    """pipeline.py 不得导入文件系统、时钟、环境变量相关模块。"""

    FORBIDDEN_MODULES = {"pathlib", "os.path", "os", "datetime", "time", "glob"}

    def test_pipeline_has_no_filesystem_imports(self):
        pipeline_path = Path(__file__).parent.parent.parent / "backend" / "build" / "pipeline.py"
        source = pipeline_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])

        forbidden_found = imported & self.FORBIDDEN_MODULES
        self.assertEqual(
            forbidden_found,
            set(),
            f"pipeline.py 禁止导入以下模块: {forbidden_found}",
        )

    def test_pipeline_importable_without_side_effects(self):
        """导入 pipeline 不应触发任何启动副作用（如读写文件、网络）。"""
        p = _load_pipeline()
        self.assertIsNotNone(p)


# ─── 2. CleanStats 数据类 ─────────────────────────────────────────────────────

class TestCleanStats(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def test_all_fields_default_to_zero(self):
        stats = self.p.CleanStats()
        for field_name in vars(stats):
            self.assertEqual(
                getattr(stats, field_name), 0,
                f"CleanStats.{field_name} 默认值应为 0",
            )

    def test_fields_are_mutable(self):
        stats = self.p.CleanStats()
        stats.removed_page_markers = 3
        self.assertEqual(stats.removed_page_markers, 3)


# ─── 3. merge_pages 纯函数 ────────────────────────────────────────────────────

class TestMergePages(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def test_empty_page_list_returns_only_title(self):
        result = self.p.merge_pages([], "My Title")
        self.assertIn("# My Title", result)

    def test_single_page_with_content(self):
        result = self.p.merge_pages(["page one content"], "Doc")
        joined = "\n".join(result)
        self.assertIn("# Doc", joined)
        self.assertIn("**第 1 页**", joined)
        self.assertIn("page one content", joined)

    def test_empty_page_content_produces_placeholder(self):
        result = self.p.merge_pages([""], "Doc")
        joined = "\n".join(result)
        self.assertIn("*（本页无内容）*", joined)

    def test_whitespace_only_page_produces_placeholder(self):
        result = self.p.merge_pages(["   \n  "], "Doc")
        joined = "\n".join(result)
        self.assertIn("*（本页无内容）*", joined)

    def test_multiple_pages_all_appear_in_order(self):
        pages = ["content of page one", "content of page two", "content of page three"]
        result = self.p.merge_pages(pages, "Test")
        joined = "\n".join(result)
        self.assertIn("**第 1 页**", joined)
        self.assertIn("**第 2 页**", joined)
        self.assertIn("**第 3 页**", joined)
        self.assertIn("content of page one", joined)
        self.assertIn("content of page two", joined)
        self.assertIn("content of page three", joined)

    def test_page_order_is_correct(self):
        pages = ["alpha", "beta"]
        result = self.p.merge_pages(pages, "Test")
        joined = "\n".join(result)
        pos1 = joined.index("alpha")
        pos2 = joined.index("beta")
        self.assertLess(pos1, pos2)

    def test_stable_output_for_same_input(self):
        pages = ["hello world"]
        r1 = self.p.merge_pages(pages, "Title")
        r2 = self.p.merge_pages(pages, "Title")
        self.assertEqual(r1, r2)

    def test_separators_present(self):
        result = self.p.merge_pages(["content"], "Doc")
        # 每页前有 --- 分隔线
        self.assertIn("---", result)


# ─── 4. strip_page_scaffold ──────────────────────────────────────────────────

class TestStripPageScaffold(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def _run(self, lines):
        stats = self.p.CleanStats()
        result = self.p.strip_page_scaffold(lines, stats)
        return result, stats

    def test_removes_page_marker(self):
        lines = ["some text", "**第 1 页**", "more text"]
        result, stats = self._run(lines)
        self.assertNotIn("**第 1 页**", result)
        self.assertEqual(stats.removed_page_markers, 1)

    def test_removes_scaffold_split_before_page_marker(self):
        lines = ["---", "", "**第 2 页**", "", "content"]
        result, stats = self._run(lines)
        self.assertNotIn("---", result)
        self.assertEqual(stats.removed_page_splits, 1)

    def test_keeps_content_separator_not_before_page_marker(self):
        """--- 后面不跟页码标记时，应保留。"""
        lines = ["content", "---", "more content"]
        result, stats = self._run(lines)
        self.assertIn("---", result)
        self.assertEqual(stats.removed_page_splits, 0)

    def test_removes_image_ref(self):
        lines = ["text", "![alt](image.png)", "more"]
        result, stats = self._run(lines)
        self.assertNotIn("![alt](image.png)", result)
        self.assertEqual(stats.removed_image_refs, 1)

    def test_removes_failed_page_comment(self):
        lines = ["<!-- 提取失败，原因：超时 -->", "next line"]
        result, stats = self._run(lines)
        self.assertNotIn("<!-- 提取失败，原因：超时 -->", result)
        self.assertEqual(stats.removed_failed_pages, 1)

    def test_removes_empty_page_placeholder(self):
        lines = ["*（本页无内容）*", "real content"]
        result, stats = self._run(lines)
        self.assertNotIn("*（本页无内容）*", result)
        self.assertEqual(stats.removed_empty_pages, 1)

    def test_idempotent(self):
        lines = ["---", "", "**第 1 页**", "", "content", "![img](x.jpg)"]
        stats1 = self.p.CleanStats()
        r1 = self.p.strip_page_scaffold(lines, stats1)
        stats2 = self.p.CleanStats()
        r2 = self.p.strip_page_scaffold(r1, stats2)
        self.assertEqual(r1, r2)

    def test_stable_output(self):
        lines = ["**第 1 页**", "hello"]
        s1 = self.p.CleanStats()
        s2 = self.p.CleanStats()
        self.assertEqual(
            self.p.strip_page_scaffold(lines, s1),
            self.p.strip_page_scaffold(lines, s2),
        )


# ─── 5. normalize_heading_levels ─────────────────────────────────────────────

class TestNormalizeHeadingLevels(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def test_first_heading_becomes_h1(self):
        lines = ["## Some Title", "content"]
        result = self.p.normalize_heading_levels(lines, 5)
        self.assertEqual(result[0], "# Some Title")

    def test_subsequent_headings_mapped_from_h2(self):
        lines = ["# Doc", "## Section", "### Subsection"]
        result = self.p.normalize_heading_levels(lines, 5)
        self.assertEqual(result[0], "# Doc")
        self.assertEqual(result[1], "## Section")
        self.assertEqual(result[2], "### Subsection")

    def test_respects_max_heading_level(self):
        lines = ["# Title", "## A", "### B", "#### C", "##### D", "###### E"]
        result = self.p.normalize_heading_levels(lines, 3)
        for line in result[1:]:
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                self.assertLessEqual(level, 3)

    def test_non_heading_lines_unchanged(self):
        lines = ["# Title", "plain text", "- list item"]
        result = self.p.normalize_heading_levels(lines, 5)
        self.assertIn("plain text", result)
        self.assertIn("- list item", result)

    def test_idempotent(self):
        lines = ["# Doc", "## Sec", "### Sub", "content"]
        r1 = self.p.normalize_heading_levels(lines, 5)
        r2 = self.p.normalize_heading_levels(r1, 5)
        self.assertEqual(r1, r2)

    def test_stable_output(self):
        lines = ["# Doc", "## Section"]
        self.assertEqual(
            self.p.normalize_heading_levels(lines, 5),
            self.p.normalize_heading_levels(lines, 5),
        )


# ─── 6. merge_duplicate_headings ─────────────────────────────────────────────

class TestMergeDuplicateHeadings(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def _run(self, lines):
        stats = self.p.CleanStats()
        result = self.p.merge_duplicate_headings(lines, stats)
        return result, stats

    def test_removes_duplicate_heading(self):
        lines = ["## Section", "content", "## Section", "more content"]
        result, stats = self._run(lines)
        # 重复的 ## Section 应被删除
        heading_count = sum(1 for l in result if l == "## Section")
        self.assertEqual(heading_count, 1)
        self.assertEqual(stats.removed_duplicate_headings, 1)

    def test_keeps_unique_headings(self):
        lines = ["## Section A", "content", "## Section B", "more"]
        result, stats = self._run(lines)
        self.assertIn("## Section A", result)
        self.assertIn("## Section B", result)
        self.assertEqual(stats.removed_duplicate_headings, 0)

    def test_keeps_content_lines(self):
        lines = ["## Section", "important content", "## Section", "also important"]
        result, stats = self._run(lines)
        self.assertIn("important content", result)
        self.assertIn("also important", result)

    def test_idempotent(self):
        lines = ["## A", "text", "## A", "text2", "## B", "text3"]
        s1 = self.p.CleanStats()
        r1 = self.p.merge_duplicate_headings(lines, s1)
        s2 = self.p.CleanStats()
        r2 = self.p.merge_duplicate_headings(r1, s2)
        self.assertEqual(r1, r2)

    def test_stable_output(self):
        lines = ["## X", "a", "## X", "b"]
        s1 = self.p.CleanStats()
        s2 = self.p.CleanStats()
        self.assertEqual(
            self.p.merge_duplicate_headings(lines, s1),
            self.p.merge_duplicate_headings(lines, s2),
        )


# ─── 7. remove_empty_sections ────────────────────────────────────────────────

class TestRemoveEmptySections(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def _run(self, lines):
        stats = self.p.CleanStats()
        result = self.p.remove_empty_sections(lines, stats)
        return result, stats

    def test_removes_empty_h2_section(self):
        lines = ["# Doc", "## Empty Section", "## Real Section", "content"]
        result, stats = self._run(lines)
        self.assertNotIn("## Empty Section", result)
        self.assertEqual(stats.removed_empty_sections, 1)

    def test_keeps_section_with_content(self):
        lines = ["# Doc", "## Section", "real content"]
        result, stats = self._run(lines)
        self.assertIn("## Section", result)
        self.assertEqual(stats.removed_empty_sections, 0)

    def test_keeps_h1(self):
        """H1 即使无内容也不应被删除（只删 H2+）。"""
        lines = ["# Main Title", "## Sub", "content"]
        result, stats = self._run(lines)
        self.assertIn("# Main Title", result)

    def test_idempotent(self):
        lines = ["# Doc", "## Empty", "## Full", "data"]
        s1 = self.p.CleanStats()
        r1 = self.p.remove_empty_sections(lines, s1)
        s2 = self.p.CleanStats()
        r2 = self.p.remove_empty_sections(r1, s2)
        self.assertEqual(r1, r2)

    def test_stable_output(self):
        lines = ["# T", "## E", "## F", "x"]
        s1 = self.p.CleanStats()
        s2 = self.p.CleanStats()
        self.assertEqual(
            self.p.remove_empty_sections(lines, s1),
            self.p.remove_empty_sections(lines, s2),
        )


# ─── 8. remove_duplicate_fragments ───────────────────────────────────────────

class TestRemoveDuplicateFragments(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def _run(self, lines):
        stats = self.p.CleanStats()
        result = self.p.remove_duplicate_fragments(lines, stats)
        return result, stats

    def test_removes_adjacent_duplicate_text(self):
        lines = ["some long content line", "", "some long content line"]
        result, stats = self._run(lines)
        content_count = sum(1 for l in result if l == "some long content line")
        self.assertEqual(content_count, 1)
        self.assertEqual(stats.removed_duplicate_fragments, 1)

    def test_keeps_non_duplicate(self):
        lines = ["line one content", "", "line two different content"]
        result, stats = self._run(lines)
        self.assertIn("line one content", result)
        self.assertIn("line two different content", result)
        self.assertEqual(stats.removed_duplicate_fragments, 0)

    def test_heading_resets_dedup_context(self):
        """标题出现后，重置去重上下文。"""
        lines = ["the content line", "", "## New Section", "", "the content line"]
        result, stats = self._run(lines)
        content_count = sum(1 for l in result if l == "the content line")
        self.assertEqual(content_count, 2)

    def test_idempotent(self):
        lines = ["repeated long content here", "", "repeated long content here", "unique line"]
        s1 = self.p.CleanStats()
        r1 = self.p.remove_duplicate_fragments(lines, s1)
        s2 = self.p.CleanStats()
        r2 = self.p.remove_duplicate_fragments(r1, s2)
        self.assertEqual(r1, r2)

    def test_stable_output(self):
        lines = ["abc def ghi", "", "abc def ghi"]
        s1 = self.p.CleanStats()
        s2 = self.p.CleanStats()
        self.assertEqual(
            self.p.remove_duplicate_fragments(lines, s1),
            self.p.remove_duplicate_fragments(lines, s2),
        )


# ─── 9. compact_blank_lines ──────────────────────────────────────────────────

class TestCompactBlankLines(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def test_collapses_multiple_blanks(self):
        lines = ["a", "", "", "", "b"]
        result = self.p.compact_blank_lines(lines)
        # 连续空行变为单空行
        for i in range(len(result) - 1):
            self.assertFalse(
                result[i] == "" and result[i + 1] == "",
                "不应存在连续空行",
            )

    def test_trims_trailing_blanks(self):
        lines = ["content", "", ""]
        result = self.p.compact_blank_lines(lines)
        self.assertNotEqual(result[-1], "")

    def test_trims_leading_blanks(self):
        lines = ["", "", "content"]
        result = self.p.compact_blank_lines(lines)
        self.assertNotEqual(result[0], "")

    def test_empty_input_returns_empty(self):
        result = self.p.compact_blank_lines([])
        self.assertEqual(result, [])

    def test_idempotent(self):
        lines = ["a", "", "", "b", "", ""]
        r1 = self.p.compact_blank_lines(lines)
        r2 = self.p.compact_blank_lines(r1)
        self.assertEqual(r1, r2)

    def test_stable_output(self):
        lines = ["x", "", "", "y"]
        self.assertEqual(
            self.p.compact_blank_lines(lines),
            self.p.compact_blank_lines(lines),
        )


# ─── 10. 整体管道：build_clean_pipeline ───────────────────────────────────────

class TestBuildCleanPipeline(unittest.TestCase):
    """验证完整清理管道的稳定性与幂等性。"""

    def setUp(self):
        self.p = _load_pipeline()

    def _make_raw_lines(self):
        return [
            "# Test Document",
            "",
            "---",
            "",
            "**第 1 页**",
            "",
            "## Section One",
            "This is the first section content.",
            "",
            "![image](fig.png)",
            "",
            "---",
            "",
            "**第 2 页**",
            "",
            "## Section One",
            "Repeated heading should be merged.",
            "",
            "## Empty Section",
            "",
            "## Section Two",
            "Second section content.",
        ]

    def test_full_pipeline_stable_output(self):
        lines = self._make_raw_lines()
        r1 = self._apply_pipeline(lines)
        r2 = self._apply_pipeline(lines)
        self.assertEqual(r1, r2)

    def test_full_pipeline_removes_scaffold(self):
        lines = self._make_raw_lines()
        result = self._apply_pipeline(lines)
        joined = "\n".join(result)
        self.assertNotIn("**第 1 页**", joined)
        self.assertNotIn("**第 2 页**", joined)
        self.assertNotIn("![image](fig.png)", joined)

    def test_full_pipeline_no_consecutive_blanks(self):
        lines = self._make_raw_lines()
        result = self._apply_pipeline(lines)
        for i in range(len(result) - 1):
            self.assertFalse(
                result[i] == "" and result[i + 1] == "",
                f"第 {i} 和 {i+1} 行不应连续为空",
            )

    def _apply_pipeline(self, lines: list[str]) -> list[str]:
        p = self.p
        stats = p.CleanStats()
        lines = p.strip_page_scaffold(lines, stats)
        lines = p.normalize_heading_levels(lines, max_heading_level=5)
        lines = p.merge_duplicate_headings(lines, stats)
        prev_len = -1
        while len(lines) != prev_len:
            prev_len = len(lines)
            lines = p.remove_empty_sections(lines, stats)
        lines = p.remove_duplicate_fragments(lines, stats)
        lines = p.compact_blank_lines(lines)
        return lines


class TestLlmBuildGuards(unittest.TestCase):
    def setUp(self):
        self.p = _load_pipeline()

    def test_rule_based_baseline_is_valid_against_original_scaffold(self):
        raw_lines = [
            "# 高压课程",
            "",
            "---",
            "",
            "**第 1 页**",
            "",
            "## 第一节",
            "内容 A",
            "",
            "---",
            "",
            "**第 2 页**",
            "",
            "## 第二节",
            "内容 B",
        ]
        baseline = self.p.build_rule_based_baseline(raw_lines)
        self.assertEqual([], self.p.validate_candidate_output(raw_lines, baseline))

    def test_validation_rejects_rewritten_content(self):
        raw_lines = [
            "# 高压课程",
            "",
            "---",
            "",
            "**第 1 页**",
            "",
            "## 第一节",
            "原始句子",
        ]
        candidate = ["# 高压课程", "", "## 第一节", "被改写后的句子"]
        violations = self.p.validate_candidate_output(raw_lines, candidate)
        self.assertTrue(violations)
        self.assertIn("new or rewritten", violations[0])

    def test_validation_rejects_reordered_content(self):
        raw_lines = [
            "# 高压课程",
            "",
            "---",
            "",
            "**第 1 页**",
            "",
            "## 第一节",
            "内容 A",
            "",
            "---",
            "",
            "**第 2 页**",
            "",
            "## 第二节",
            "内容 B",
        ]
        candidate = ["# 高压课程", "", "## 第二节", "内容 B", "", "## 第一节", "内容 A"]
        violations = self.p.validate_candidate_output(raw_lines, candidate)
        self.assertTrue(violations)
        self.assertIn("reorders", violations[0])

    def test_strip_wrapped_markdown_fence_unwraps_single_outer_block(self):
        text = "```markdown\n# 标题\n\n正文\n```"
        self.assertEqual("# 标题\n\n正文", self.p.strip_wrapped_markdown_fence(text))


if __name__ == "__main__":
    unittest.main()
