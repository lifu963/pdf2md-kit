"""
FsSecretStore: 管理 data/secrets.json 与环境变量 ARK_API_KEY 的 API Key 读取优先级。

规则：
- 优先读取 data/secrets.json。
- 若 secrets.json 不存在，则回退到 ARK_API_KEY。
- 对外只提供 has/get/require 语义，调用方决定是否展示 has_api_key。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from backend.infra.fs.error_mapping import raise_persistence_error
from backend.shared_kernel.errors import AppError, ErrorCode


class FsSecretStore:
    """文件系统版 SecretStore，优先级 secrets.json > ARK_API_KEY。"""

    def __init__(self, data_root: Path, env_var_name: str = "ARK_API_KEY") -> None:
        self._data_root = data_root.resolve()
        self._secrets_path = self._data_root / "secrets.json"
        self._env_var_name = env_var_name
        self._lock = Lock()

    def has_api_key(self) -> bool:
        return self.get_api_key() is not None

    def get_api_key(self) -> str | None:
        key = self._get_api_key_from_file()
        if key is not None:
            return key
        return self._get_api_key_from_env()

    def require_api_key(self) -> str:
        key = self.get_api_key()
        if key is None:
            raise AppError(code=ErrorCode.CONFIG_MISSING_API_KEY)
        return key

    def set_api_key(self, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise AppError(
                code=ErrorCode.CONFIG_INVALID,
                message="api_key must be a non-empty string",
            )

        payload = json.dumps({"api_key": value}, ensure_ascii=False, indent=2)
        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise_persistence_error("failed to prepare data root for secrets", exc)
        with self._lock:
            try:
                _atomic_write_text(self._secrets_path, payload)
            except OSError as exc:
                raise_persistence_error("failed to write data/secrets.json", exc)

    def _get_api_key_from_file(self) -> str | None:
        if not self._secrets_path.exists():
            return None

        try:
            raw = self._secrets_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except OSError as exc:
            raise_persistence_error("failed to read data/secrets.json", exc)
        except json.JSONDecodeError as exc:
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message=f"secrets.json corrupted: {exc}",
            ) from exc

        if not isinstance(data, dict):
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message="secrets.json must be a JSON object",
            )

        value = data.get("api_key")
        if value is None:
            return None
        if not isinstance(value, str):
            raise AppError(
                code=ErrorCode.STATE_CORRUPTED,
                message="secrets.json api_key must be a string",
            )
        stripped = value.strip()
        return stripped or None

    def _get_api_key_from_env(self) -> str | None:
        value = os.getenv(self._env_var_name)
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


__all__ = ["FsSecretStore"]
