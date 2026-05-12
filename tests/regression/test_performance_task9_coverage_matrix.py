"""
Task 9 coverage matrix regression guard.

锁定本轮性能优化收口时必须保留的关键回归断言，避免后续重构时
误删测试文件或弱化核心行为保护。
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import inspect
import re
import unittest


@dataclass(frozen=True, slots=True)
class RequiredTestGuard:
    method_name_snippet: str
    assertion_snippets: tuple[str, ...]


REQUIRED_TEST_GUARDS: dict[str, tuple[RequiredTestGuard, ...]] = {
    "tests.frontend.test_frontend_index_html_workspace_resize_performance_regression": (
        RequiredTestGuard(
            method_name_snippet="workspace_has_draggable_divider_with_raf_merge_drag_path",
            assertion_snippets=(
                "self.assertIn(",
                "workspace-divider",
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="pdf_resize_redraw_keeps_visible_canvas",
            assertion_snippets=(
                "self.assertEqual(",
                "drawImage",
            ),
        ),
    ),
    "tests.frontend.test_frontend_index_html_sse_page_event_invalidation_regression": (
        RequiredTestGuard(
            method_name_snippet="batch_ui_patch",
            assertion_snippets=(
                'result["afterFirstFlush"]["pageSelectPatches"]',
                'result["changedCurrentBeforeFlush"]["loadCurrentPageCalls"]',
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="short_circuited_when_panel_collapsed",
            assertion_snippets=(
                'result["afterCollapsedFlush"]["internals"]["historyDirty"]',
                'result["afterExpandedFlush"]["patchedHistoryJobs"]',
            ),
        ),
    ),
    "tests.frontend.test_frontend_index_html_history_panel_on_demand_regression": (
        RequiredTestGuard(
            method_name_snippet="defers_reload_until_expanded",
            assertion_snippets=(
                'result["collapsedForced"]["historyDirty"]',
                'result["expandedForced"]["loadHistoryJobsCalls"]',
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="no_longer_force_history_full_reload",
            assertion_snippets=(
                'result["loadHistoryJobsCalls"]',
                "len(result[\"uiFlushPatches\"])",
            ),
        ),
    ),
    "tests.frontend.test_frontend_index_html_log_and_panel_hotspots_regression": (
        RequiredTestGuard(
            method_name_snippet="source_uses_buffered_events_log",
            assertion_snippets=(
                "appendEventsLogTail(",
                "rewriteEventsLogFromBuffer()",
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="defers_dom_updates_until_expanded",
            assertion_snippets=(
                'result["expandedAfterFlush"]["appendCount"]',
                'result["afterTrim"]["writeCount"]',
            ),
        ),
    ),
    "tests.application.test_job_query_application": (
        RequiredTestGuard(
            method_name_snippet="summary_query_does_not_read_markdown_bodies",
            assertion_snippets=(
                "_guard_markdown_reads",
                "mock.patch(",
            ),
        ),
    ),
    "tests.api.test_source_stream_spa_routes": (
        RequiredTestGuard(
            method_name_snippet="supports_full_content_and_valid_ranges",
            assertion_snippets=(
                'range_resp.headers.get("content-range")',
                'open_range_resp.headers.get("content-range")',
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="rejects_invalid_ranges_with_416",
            assertion_snippets=(
                'self.assertEqual(416, invalid_start_resp.status_code)',
                '"bytes */{total_size}"',
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="streams_chunks_without_full_read",
            assertion_snippets=(
                "self.assertGreaterEqual(len(read_sizes), 2)",
                "all(size > 0 for size in read_sizes)",
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="maps_open_read_failure_before_stream_starts",
            assertion_snippets=(
                "broken_open_read",
                'full_resp.json().get("detail", {}).get("code")',
            ),
        ),
    ),
    "tests.infra.fs.test_page_artifact_event_fs_adapter": (
        RequiredTestGuard(
            method_name_snippet="list_summaries_by_job_does_not_read_markdown_bodies",
            assertion_snippets=(
                "_guard_markdown_reads",
                "mock.patch(",
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="does_not_read_all_lines_for_steady_state_appends",
            assertion_snippets=(
                '"append should not read the whole events.jsonl in steady state"',
                "self._repo.append(",
            ),
        ),
    ),
    "tests.infra.fs.test_workspace_fs_adapter": (
        RequiredTestGuard(
            method_name_snippet="reuses_cached_job_for_unchanged_state_file",
            assertion_snippets=(
                "_guard_state_read",
                "self._repo.list_all()",
            ),
        ),
        RequiredTestGuard(
            method_name_snippet="holds_job_lock_while_refreshing_cache",
            assertion_snippets=(
                "save_thread.is_alive()",
                "allow_list_continue.set()",
            ),
        ),
    ),
}


class PerformanceTask9CoverageMatrixTests(unittest.TestCase):
    def test_task9_critical_regression_assertions_remain_present(self) -> None:
        for module_name, guards in REQUIRED_TEST_GUARDS.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                test_methods = self._collect_test_methods(module)
                self.assertTrue(
                    test_methods,
                    f"{module_name} 未发现任何 unittest 测试方法",
                )
                for guard in guards:
                    self._assert_test_guard(module_name=module_name, test_methods=test_methods, guard=guard)

    def _assert_test_guard(
        self,
        *,
        module_name: str,
        test_methods: dict[str, object],
        guard: RequiredTestGuard,
    ) -> None:
        matched = [
            (method_name, method_obj)
            for method_name, method_obj in test_methods.items()
            if guard.method_name_snippet in method_name
        ]
        self.assertTrue(
            matched,
            (
                f"{module_name} 缺少关键回归测试方法片段 `{guard.method_name_snippet}`；"
                "请确认未误删 Task 9 关键测试"
            ),
        )
        for method_name, method_obj in matched:
            source = inspect.getsource(method_obj)
            self.assertRegex(
                source,
                re.compile(r"self\.assert[A-Za-z_]+\(", re.MULTILINE),
                f"{module_name}.{method_name} 缺少显式断言，测试可能已被弱化",
            )
            for assertion_snippet in guard.assertion_snippets:
                self.assertIn(
                    assertion_snippet,
                    source,
                    (
                        f"{module_name}.{method_name} 缺少关键断言片段 `{assertion_snippet}`；"
                        "请确认测试强度未被削弱"
                    )
                )

    @staticmethod
    def _collect_test_methods(module: object) -> dict[str, object]:
        methods: dict[str, object] = {}
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, unittest.TestCase):
                continue
            for method_name, method_obj in inspect.getmembers(obj, inspect.isfunction):
                if method_name.startswith("test_"):
                    methods[method_name] = method_obj
        return methods
