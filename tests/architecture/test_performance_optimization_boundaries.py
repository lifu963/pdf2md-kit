from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from backend.api.app import _DEFAULT_SPA_HTML, _load_spa_index_html
from backend.api.dependencies import ApiContainer


class PerformanceOptimizationBoundaryTests(unittest.TestCase):
    def test_api_container_defaults_still_bind_runtime_state_to_fs_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            data_root = project_root / "data"
            data_root.mkdir(parents=True, exist_ok=True)

            container = ApiContainer(project_root=project_root, data_root=data_root)

            adapter_modules = {
                type(container.workspace).__module__,
                type(container.job_repository).__module__,
                type(container.page_repository).__module__,
                type(container.source_store).__module__,
                type(container.artifact_repository).__module__,
                type(container.event_log_repository).__module__,
                type(container.secret_store).__module__,
                type(container.config_repository).__module__,
            }

            self.assertEqual(
                {
                    "backend.infra.fs.workspace",
                    "backend.infra.fs.job_repository",
                    "backend.infra.fs.page_repository",
                    "backend.infra.fs.source_store",
                    "backend.infra.fs.artifact_repository",
                    "backend.infra.fs.event_log_repository",
                    "backend.infra.fs.secret_store",
                    "backend.infra.fs.config_repository",
                },
                adapter_modules,
            )

    def test_spa_runtime_prefers_frontend_index_html_over_dist_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            frontend_dir = project_root / "frontend"
            dist_dir = frontend_dir / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)

            source_html = "<!doctype html><html><body>source-entry</body></html>"
            dist_html = "<!doctype html><html><body>dist-entry</body></html>"
            (frontend_dir / "index.html").write_text(source_html, encoding="utf-8")
            (dist_dir / "index.html").write_text(dist_html, encoding="utf-8")

            self.assertEqual(source_html, _load_spa_index_html(project_root))

    def test_spa_runtime_falls_back_to_dist_then_builtin_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            dist_dir = project_root / "frontend" / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            dist_html = "<!doctype html><html><body>dist-only</body></html>"
            (dist_dir / "index.html").write_text(dist_html, encoding="utf-8")

            self.assertEqual(dist_html, _load_spa_index_html(project_root))

        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertEqual(_DEFAULT_SPA_HTML, _load_spa_index_html(Path(tmp_dir)))
