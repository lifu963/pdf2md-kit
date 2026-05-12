"""
Step 07: config-application 应用层测试

验收目标（严格对齐实施步骤）：
1. 合法配置可读取并映射为 PublicConfigView。
2. 非法 concurrency 被拒绝（CONFIG_INVALID）。
3. 非法 dpi 被拒绝（CONFIG_INVALID）。
4. 更新非敏感字段可持久化并返回公开视图。
5. 提交 api_key 时只写入 SecretStore，不回传明文。
6. 省略 api_key 时保持原值不变。
7. 对外返回永远不包含 api_key 明文字段。
"""

from __future__ import annotations

from dataclasses import asdict
from unittest import TestCase

from backend.config.application.commands import (
    ExtractConfigInput,
    ModelConfigInput,
    UpdateConfigCommand,
)
from backend.config.application.dto import PublicConfigView
from backend.config.application.queries import GetPublicConfigQuery
from backend.config.application import ConfigApplication
from backend.shared_kernel.contracts import ExtractConfig, ModelConfig, RuntimeConfig
from backend.shared_kernel.errors import AppError, ErrorCode


def _runtime_config(*, has_api_key: bool) -> RuntimeConfig:
    return RuntimeConfig(
        model=ModelConfig(
            name="doubao-seed-2-0-lite-260215",
            timeout_seconds=60,
        ),
        extract=ExtractConfig(
            dpi=150,
            concurrency=10,
            max_retries=3,
            prompt="从课件图片中提取文字内容，保持 Markdown 结构。",
        ),
        has_api_key=has_api_key,
    )


def _update_command(
    *,
    name: str = "doubao-seed-2-0-lite-260215",
    timeout_seconds: int = 60,
    dpi: int = 150,
    concurrency: int = 10,
    max_retries: int = 3,
    prompt: str = "从课件图片中提取文字内容，保持 Markdown 结构。",
    api_key: str | None = None,
) -> UpdateConfigCommand:
    return UpdateConfigCommand(
        model=ModelConfigInput(
            name=name,
            timeout_seconds=timeout_seconds,
        ),
        extract=ExtractConfigInput(
            dpi=dpi,
            concurrency=concurrency,
            max_retries=max_retries,
            prompt=prompt,
        ),
        api_key=api_key,
    )


class _FakeConfigRepository:
    def __init__(self, config: RuntimeConfig, template_config: RuntimeConfig | None = None) -> None:
        self.runtime_config = config
        self.template_config = template_config or config
        self.load_calls = 0
        self.save_calls = 0
        self.reset_calls = 0

    def load(self) -> RuntimeConfig:
        self.load_calls += 1
        return self.runtime_config

    def save(self, config: RuntimeConfig) -> RuntimeConfig:
        self.save_calls += 1
        self.runtime_config = config
        return self.runtime_config

    def reset_to_template(self) -> RuntimeConfig:
        self.reset_calls += 1
        self.runtime_config = RuntimeConfig(
            model=self.template_config.model,
            extract=self.template_config.extract,
            has_api_key=self.runtime_config.has_api_key,
        )
        return self.runtime_config


class _FakeSecretStore:
    def __init__(self, initial_key: str | None) -> None:
        self._api_key = initial_key
        self.set_calls: list[str] = []

    def has_api_key(self) -> bool:
        return self._api_key is not None and bool(self._api_key.strip())

    def get_api_key(self) -> str | None:
        return self._api_key

    def require_api_key(self) -> str:
        if not self.has_api_key():
            raise AppError(code=ErrorCode.CONFIG_MISSING_API_KEY)
        assert self._api_key is not None
        return self._api_key

    def set_api_key(self, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise AppError(code=ErrorCode.CONFIG_INVALID, message="api_key must not be empty")
        self._api_key = value
        self.set_calls.append(value)


class _FakeVisionGateway:
    def __init__(self, reply: str = "OK") -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    def test_connection(self, *, model: ModelConfig, api_key: str) -> str:
        self.calls.append(
            {
                "model": model,
                "api_key": api_key,
            }
        )
        return self.reply


class TestConfigApplication(TestCase):
    def setUp(self) -> None:
        self.repo = _FakeConfigRepository(_runtime_config(has_api_key=False))
        self.secret_store = _FakeSecretStore(initial_key=None)
        self.vision_gateway = _FakeVisionGateway()
        self.app = ConfigApplication(
            config_repository=self.repo,
            secret_store=self.secret_store,
            vision_gateway=self.vision_gateway,
        )

    def test_get_public_config_returns_valid_view(self) -> None:
        view = self.app.get_public_config(GetPublicConfigQuery())
        self.assertIsInstance(view, PublicConfigView)
        self.assertEqual(view.model.name, "doubao-seed-2-0-lite-260215")
        self.assertEqual(view.extract.dpi, 150)
        self.assertEqual(view.extract.concurrency, 10)
        self.assertFalse(view.has_api_key)

    def test_update_config_rejects_invalid_concurrency(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.app.update_config(_update_command(concurrency=0))

        self.assertEqual(ctx.exception.code, ErrorCode.CONFIG_INVALID)
        self.assertEqual(self.repo.save_calls, 0)
        self.assertEqual(self.secret_store.set_calls, [])

    def test_update_config_rejects_invalid_dpi(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.app.update_config(_update_command(dpi=0))

        self.assertEqual(ctx.exception.code, ErrorCode.CONFIG_INVALID)
        self.assertEqual(self.repo.save_calls, 0)
        self.assertEqual(self.secret_store.set_calls, [])

    def test_update_config_updates_non_sensitive_fields(self) -> None:
        updated = self.app.update_config(
            _update_command(
                name="doubao-seed-2-0-pro",
                dpi=200,
                prompt="请提取并输出更结构化的 Markdown。",
            )
        )

        self.assertEqual(self.repo.save_calls, 1)
        self.assertEqual(self.secret_store.set_calls, [])
        self.assertEqual(updated.model.name, "doubao-seed-2-0-pro")
        self.assertEqual(updated.extract.dpi, 200)
        self.assertEqual(updated.extract.prompt, "请提取并输出更结构化的 Markdown。")
        self.assertFalse(updated.has_api_key)

    def test_update_config_writes_api_key_without_returning_plaintext(self) -> None:
        updated = self.app.update_config(_update_command(api_key="new-secret-key"))

        self.assertEqual(self.secret_store.set_calls, ["new-secret-key"])
        self.assertTrue(updated.has_api_key)
        self.assertFalse(hasattr(updated, "api_key"))
        self.assertNotIn("new-secret-key", repr(updated))

    def test_update_config_omitting_api_key_keeps_existing_secret(self) -> None:
        repo = _FakeConfigRepository(_runtime_config(has_api_key=True))
        secret_store = _FakeSecretStore(initial_key="existing-secret")
        app = ConfigApplication(
            config_repository=repo,
            secret_store=secret_store,
            vision_gateway=_FakeVisionGateway(),
        )

        updated = app.update_config(_update_command(name="keep-secret"))

        self.assertEqual(secret_store.set_calls, [])
        self.assertEqual(secret_store.get_api_key(), "existing-secret")
        self.assertTrue(updated.has_api_key)

    def test_public_view_never_contains_api_key_plaintext(self) -> None:
        repo = _FakeConfigRepository(_runtime_config(has_api_key=True))
        secret_store = _FakeSecretStore(initial_key="super-secret-token")
        app = ConfigApplication(
            config_repository=repo,
            secret_store=secret_store,
            vision_gateway=_FakeVisionGateway(),
        )

        current = app.get_public_config(GetPublicConfigQuery())
        updated = app.update_config(_update_command(api_key="rotated-secret-token"))

        current_dict = asdict(current)
        updated_dict = asdict(updated)
        self.assertNotIn("api_key", current_dict)
        self.assertNotIn("api_key", updated_dict)
        self.assertNotIn("super-secret-token", repr(current))
        self.assertNotIn("rotated-secret-token", repr(updated))

    def test_reset_to_initial_config_restores_template_values_and_keeps_secret_visibility(self) -> None:
        template = _runtime_config(has_api_key=False)
        repo = _FakeConfigRepository(
            RuntimeConfig(
                model=ModelConfig(
                    name="doubao-seed-2-0-pro",
                    timeout_seconds=90,
                ),
                extract=ExtractConfig(
                    dpi=220,
                    concurrency=6,
                    max_retries=5,
                    prompt="请输出结构化 Markdown。",
                ),
                has_api_key=True,
            ),
            template_config=template,
        )
        secret_store = _FakeSecretStore(initial_key="existing-secret")
        app = ConfigApplication(
            config_repository=repo,
            secret_store=secret_store,
            vision_gateway=_FakeVisionGateway(),
        )

        restored = app.reset_to_initial_config()

        self.assertEqual(1, repo.reset_calls)
        self.assertEqual(template.model.name, restored.model.name)
        self.assertEqual(template.model.timeout_seconds, restored.model.timeout_seconds)
        self.assertEqual(template.extract.dpi, restored.extract.dpi)
        self.assertEqual(template.extract.prompt, restored.extract.prompt)
        self.assertTrue(restored.has_api_key)
        self.assertEqual(template.model.name, repo.runtime_config.model.name)

    def test_test_connection_uses_saved_model_and_secret_store_api_key(self) -> None:
        repo = _FakeConfigRepository(_runtime_config(has_api_key=True))
        secret_store = _FakeSecretStore(initial_key="test-secret")
        vision_gateway = _FakeVisionGateway(reply="OK from gateway")
        app = ConfigApplication(
            config_repository=repo,
            secret_store=secret_store,
            vision_gateway=vision_gateway,
        )

        result = app.test_connection()

        self.assertTrue(result.ok)
        self.assertEqual("LLM API 响应正常", result.message)
        self.assertEqual("OK from gateway", result.reply_preview)
        self.assertEqual(1, len(vision_gateway.calls))
        self.assertEqual("test-secret", vision_gateway.calls[0]["api_key"])
