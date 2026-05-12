"""
Step 06: 配置与密钥持久化（workspace-fs-adapter）集成测试

验收目标：
1. data/config.yaml 缺失时，使用项目根 config.yaml 初始化。
2. 配置可保存并可重读。
3. 非法配置返回 CONFIG_INVALID。
4. API Key 来源优先级：secrets.json > ARK_API_KEY。
5. 对外仅暴露 has_api_key，不暴露明文字段。
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase

from backend.infra.fs.config_repository import FsConfigRepository
from backend.infra.fs.secret_store import FsSecretStore
from backend.shared_kernel.contracts import ExtractConfig, ModelConfig, RuntimeConfig
from backend.shared_kernel.errors import AppError, ErrorCode


def _template_yaml() -> str:
    return (
        "model:\n"
        '  name: "doubao-seed-2-0-lite-260215"\n'
        "  timeout: 60\n"
        "\n"
        "extract:\n"
        "  dpi: 150\n"
        "  concurrency: 10\n"
        "  max_retries: 3\n"
        "  prompt: |\n"
        "    从课件图片中提取文字内容，以 Markdown 格式输出。\n"
        "    保留标题层级与列表结构。\n"
    )


@contextmanager
def _set_ark_api_key(value: str | None):
    old = os.environ.get("ARK_API_KEY")
    if value is None:
        os.environ.pop("ARK_API_KEY", None)
    else:
        os.environ["ARK_API_KEY"] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("ARK_API_KEY", None)
        else:
            os.environ["ARK_API_KEY"] = old


class TestFsConfigRepository(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._project_root = Path(self._tmp)
        self._data_root = self._project_root / "data"
        self._data_root.mkdir(parents=True, exist_ok=True)
        (self._project_root / "config.yaml").write_text(_template_yaml(), encoding="utf-8")
        self._secret_store = FsSecretStore(data_root=self._data_root)
        self._repo = FsConfigRepository(
            data_root=self._data_root,
            project_root=self._project_root,
            secret_store=self._secret_store,
        )

    def test_load_initializes_data_config_from_project_template(self) -> None:
        runtime_path = self._data_root / "config.yaml"
        self.assertFalse(runtime_path.exists())

        loaded = self._repo.load()

        self.assertTrue(runtime_path.exists())
        self.assertEqual(loaded.model.name, "doubao-seed-2-0-lite-260215")
        self.assertEqual(loaded.model.timeout_seconds, 60)
        self.assertEqual(loaded.extract.dpi, 150)
        self.assertEqual(loaded.extract.concurrency, 10)
        self.assertEqual(loaded.extract.max_retries, 3)
        self.assertIn("Markdown", loaded.extract.prompt)
        self.assertFalse(loaded.has_api_key)

    def test_save_and_reload_roundtrip(self) -> None:
        updated = RuntimeConfig(
            model=ModelConfig(
                name="doubao-seed-2-0-pro",
                timeout_seconds=90,
            ),
            extract=ExtractConfig(
                dpi=200,
                concurrency=6,
                max_retries=5,
                prompt="请提取并结构化输出。",
            ),
            has_api_key=False,
        )

        self._repo.save(updated)
        loaded = self._repo.load()

        self.assertEqual(loaded.model.name, "doubao-seed-2-0-pro")
        self.assertEqual(loaded.model.timeout_seconds, 90)
        self.assertEqual(loaded.extract.dpi, 200)
        self.assertEqual(loaded.extract.concurrency, 6)
        self.assertEqual(loaded.extract.max_retries, 5)
        self.assertEqual(loaded.extract.prompt, "请提取并结构化输出。")

    def test_load_rejects_invalid_config(self) -> None:
        bad_yaml = (
            "model:\n"
            '  name: "doubao-seed-2-0-lite-260215"\n'
            "  timeout_seconds: 60\n"
            "\n"
            "extract:\n"
            "  dpi: 150\n"
            "  concurrency: 0\n"
            "  max_retries: 3\n"
            '  prompt: "x"\n'
        )
        (self._data_root / "config.yaml").write_text(bad_yaml, encoding="utf-8")

        with self.assertRaises(AppError) as ctx:
            self._repo.load()
        self.assertEqual(ctx.exception.code, ErrorCode.CONFIG_INVALID)

    def test_public_runtime_config_only_exposes_has_api_key_flag(self) -> None:
        self._secret_store.set_api_key("secret-only-for-backend")

        loaded = self._repo.load()

        self.assertTrue(loaded.has_api_key)
        self.assertFalse(hasattr(loaded, "api_key"))


class TestFsSecretStore(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._data_root = Path(self._tmp) / "data"
        self._data_root.mkdir(parents=True, exist_ok=True)
        self._store = FsSecretStore(data_root=self._data_root)

    def test_has_api_key_when_only_secrets_json_exists(self) -> None:
        with _set_ark_api_key(None):
            self._store.set_api_key("secret-store-key")
            self.assertTrue(self._store.has_api_key())
            self.assertEqual(self._store.get_api_key(), "secret-store-key")
            self.assertEqual(self._store.require_api_key(), "secret-store-key")

    def test_has_api_key_when_only_env_var_exists(self) -> None:
        with _set_ark_api_key("env-only-key"):
            self.assertTrue(self._store.has_api_key())
            self.assertEqual(self._store.get_api_key(), "env-only-key")
            self.assertEqual(self._store.require_api_key(), "env-only-key")

    def test_secrets_json_has_higher_priority_than_env_var(self) -> None:
        with _set_ark_api_key("env-key"):
            self._store.set_api_key("secret-key")
            self.assertEqual(self._store.get_api_key(), "secret-key")
            self.assertEqual(self._store.require_api_key(), "secret-key")

    def test_missing_api_key_in_both_sources(self) -> None:
        with _set_ark_api_key(None):
            self.assertFalse(self._store.has_api_key())
            self.assertIsNone(self._store.get_api_key())
            with self.assertRaises(AppError) as ctx:
                self._store.require_api_key()
            self.assertEqual(ctx.exception.code, ErrorCode.CONFIG_MISSING_API_KEY)
