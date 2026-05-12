"""
FsConfigRepository: 管理 data/config.yaml 的初始化、读取、校验与保存。

规则：
- data/config.yaml 缺失时，使用项目根目录 config.yaml 初始化。
- 读取时把配置映射为 RuntimeConfig（仅含 has_api_key，不含明文）。
- 保存时只写非敏感配置字段，不写 API Key。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from backend.config.ports import SecretStore
from backend.infra.fs.error_mapping import raise_persistence_error
from backend.shared_kernel.contracts import ExtractConfig, ModelConfig, RuntimeConfig
from backend.shared_kernel.errors import AppError, ErrorCode


class FsConfigRepository:
    """文件系统版 ConfigRepository，运行时真源为 data/config.yaml。"""

    def __init__(
        self,
        data_root: Path,
        project_root: Path,
        secret_store: SecretStore | None = None,
    ) -> None:
        self._data_root = data_root.resolve()
        self._project_root = project_root.resolve()
        self._runtime_path = self._data_root / "config.yaml"
        self._template_path = self._project_root / "config.yaml"
        self._secret_store = secret_store
        self._lock = Lock()

    def load(self) -> RuntimeConfig:
        """读取运行时配置；若缺失则从项目模板初始化。"""
        with self._lock:
            config_data = self._load_or_init_raw_config()
        return _to_runtime_config(config_data, has_api_key=self._has_api_key())

    def save(self, config: RuntimeConfig) -> RuntimeConfig:
        """保存配置到 data/config.yaml，并返回持久化后的 RuntimeConfig。"""
        _validate_runtime_config(config)
        payload = _dump_runtime_config(config)

        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise_persistence_error("failed to prepare data root for config", exc)
        with self._lock:
            try:
                _atomic_write_text(self._runtime_path, payload)
            except OSError as exc:
                raise_persistence_error("failed to write data/config.yaml", exc)

        reloaded = self.load()
        return RuntimeConfig(
            model=reloaded.model,
            extract=reloaded.extract,
            has_api_key=self._has_api_key(),
        )

    def reset_to_template(self) -> RuntimeConfig:
        """使用项目模板覆盖运行时配置，并返回恢复后的 RuntimeConfig。"""
        template_text = _read_text(self._template_path, missing_as_invalid=True)
        template_data = _parse_config_text(template_text)
        payload = _dump_runtime_mapping(template_data)

        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise_persistence_error("failed to prepare data root for config reset", exc)
        with self._lock:
            try:
                _atomic_write_text(self._runtime_path, payload)
            except OSError as exc:
                raise_persistence_error("failed to reset data/config.yaml from template", exc)

        return self.load()

    def _load_or_init_raw_config(self) -> dict[str, dict[str, Any]]:
        if not self._runtime_path.exists():
            template_text = _read_text(self._template_path, missing_as_invalid=True)
            template_data = _parse_config_text(template_text)
            try:
                self._data_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise_persistence_error("failed to prepare data root for config init", exc)
            try:
                _atomic_write_text(self._runtime_path, _dump_runtime_mapping(template_data))
            except OSError as exc:
                raise_persistence_error("failed to initialize data/config.yaml", exc)
            return template_data

        runtime_text = _read_text(self._runtime_path, missing_as_invalid=False)
        return _parse_config_text(runtime_text)

    def _has_api_key(self) -> bool:
        if self._secret_store is None:
            return False
        return self._secret_store.has_api_key()


def _read_text(path: Path, *, missing_as_invalid: bool) -> str:
    if not path.exists() and missing_as_invalid:
        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message=f"config template not found: {path}",
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AppError(
            code=ErrorCode.PERSISTENCE_ERROR,
            message=f"failed to read config file: {exc}",
        ) from exc


def _parse_config_text(raw: str) -> dict[str, dict[str, Any]]:
    text = raw.strip()
    if not text:
        raise AppError(code=ErrorCode.CONFIG_INVALID, message="config file is empty")

    try:
        parsed_json = json.loads(text)
    except json.JSONDecodeError:
        data = _parse_simple_yaml(raw)
    else:
        data = parsed_json

    if not isinstance(data, dict):
        raise AppError(code=ErrorCode.CONFIG_INVALID, message="config must be an object")

    normalized = _normalize_mapping(data)
    _validate_mapping(normalized)
    return normalized


def _parse_simple_yaml(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_top: str | None = None
    block_target: tuple[str, str] | None = None
    block_lines: list[str] = []
    block_indent = 4

    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if block_target is not None:
            if stripped == "":
                block_lines.append("")
                index += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent >= block_indent:
                block_lines.append(line[block_indent:])
                index += 1
                continue
            top_key, key = block_target
            result[top_key][key] = "\n".join(block_lines).rstrip("\n")
            block_target = None
            block_lines = []
            continue

        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            if not stripped.endswith(":"):
                raise AppError(
                    code=ErrorCode.CONFIG_INVALID,
                    message=f"invalid top-level config line: {line}",
                )
            current_top = stripped[:-1].strip()
            if not current_top:
                raise AppError(code=ErrorCode.CONFIG_INVALID, message="empty top-level key")
            result.setdefault(current_top, {})
            if not isinstance(result[current_top], dict):
                raise AppError(code=ErrorCode.CONFIG_INVALID, message="invalid mapping value")
            index += 1
            continue

        if indent == 2:
            if current_top is None:
                raise AppError(
                    code=ErrorCode.CONFIG_INVALID,
                    message=f"orphan nested key: {line}",
                )
            if ":" not in stripped:
                raise AppError(
                    code=ErrorCode.CONFIG_INVALID,
                    message=f"invalid nested config line: {line}",
                )
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if value == "|":
                block_target = (current_top, key)
                block_lines = []
                index += 1
                continue
            result[current_top][key] = _parse_scalar(value)
            index += 1
            continue

        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message=f"unsupported indentation in config: {line}",
        )

    if block_target is not None:
        top_key, key = block_target
        result[top_key][key] = "\n".join(block_lines).rstrip("\n")

    return result


def _parse_scalar(raw: str) -> Any:
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        body = raw[1:-1]
        return body.replace('\\"', '"').replace("\\\\", "\\")
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1]

    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    try:
        return int(raw)
    except ValueError:
        return raw


def _normalize_mapping(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    model_obj = data.get("model")
    extract_obj = data.get("extract")
    if not isinstance(model_obj, Mapping) or not isinstance(extract_obj, Mapping):
        raise AppError(code=ErrorCode.CONFIG_INVALID, message="model/extract mapping required")

    timeout_value = model_obj.get("timeout_seconds", model_obj.get("timeout"))
    normalized_model = {
        "name": model_obj.get("name"),
        "timeout_seconds": timeout_value,
    }
    normalized_extract = {
        "dpi": extract_obj.get("dpi"),
        "concurrency": extract_obj.get("concurrency"),
        "max_retries": extract_obj.get("max_retries"),
        "prompt": extract_obj.get("prompt"),
    }
    return {"model": normalized_model, "extract": normalized_extract}


def _validate_mapping(data: dict[str, dict[str, Any]]) -> None:
    model = data["model"]
    extract = data["extract"]

    _require_non_empty_str(model.get("name"), "model.name")
    _require_positive_int(model.get("timeout_seconds"), "model.timeout_seconds")

    _require_positive_int(extract.get("dpi"), "extract.dpi")
    _require_positive_int(extract.get("concurrency"), "extract.concurrency")
    _require_non_negative_int(extract.get("max_retries"), "extract.max_retries")
    _require_non_empty_str(extract.get("prompt"), "extract.prompt")


def _to_runtime_config(data: dict[str, dict[str, Any]], *, has_api_key: bool) -> RuntimeConfig:
    model = data["model"]
    extract = data["extract"]
    return RuntimeConfig(
        model=ModelConfig(
            name=str(model["name"]).strip(),
            timeout_seconds=int(model["timeout_seconds"]),
        ),
        extract=ExtractConfig(
            dpi=int(extract["dpi"]),
            concurrency=int(extract["concurrency"]),
            max_retries=int(extract["max_retries"]),
            prompt=str(extract["prompt"]),
        ),
        has_api_key=has_api_key,
    )


def _validate_runtime_config(config: RuntimeConfig) -> None:
    _validate_mapping(
        {
            "model": {
                "name": config.model.name,
                "timeout_seconds": config.model.timeout_seconds,
            },
            "extract": {
                "dpi": config.extract.dpi,
                "concurrency": config.extract.concurrency,
                "max_retries": config.extract.max_retries,
                "prompt": config.extract.prompt,
            },
        }
    )


def _dump_runtime_config(config: RuntimeConfig) -> str:
    mapping = {
        "model": {
            "name": config.model.name,
            "timeout_seconds": config.model.timeout_seconds,
        },
        "extract": {
            "dpi": config.extract.dpi,
            "concurrency": config.extract.concurrency,
            "max_retries": config.extract.max_retries,
            "prompt": config.extract.prompt,
        },
    }
    return _dump_runtime_mapping(mapping)


def _dump_runtime_mapping(mapping: Mapping[str, Mapping[str, Any]]) -> str:
    extract_prompt_lines = _split_block_lines(mapping["extract"]["prompt"])
    lines = [
        "model:",
        f'  name: "{_escape_yaml_double_quoted(str(mapping["model"]["name"]))}"',
        f"  timeout_seconds: {int(mapping['model']['timeout_seconds'])}",
        "",
        "extract:",
        f"  dpi: {int(mapping['extract']['dpi'])}",
        f"  concurrency: {int(mapping['extract']['concurrency'])}",
        f"  max_retries: {int(mapping['extract']['max_retries'])}",
        "  prompt: |",
    ]
    lines.extend(f"    {line}" for line in extract_prompt_lines)
    return "\n".join(lines) + "\n"


def _split_block_lines(value: Any) -> list[str]:
    return str(value).splitlines() or [""]


def _escape_yaml_double_quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _require_non_empty_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AppError(code=ErrorCode.CONFIG_INVALID, message=f"{field} must be string")
    if not value.strip():
        raise AppError(code=ErrorCode.CONFIG_INVALID, message=f"{field} must not be empty")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int):
        raise AppError(code=ErrorCode.CONFIG_INVALID, message=f"{field} must be integer")
    if value <= 0:
        raise AppError(code=ErrorCode.CONFIG_INVALID, message=f"{field} must be > 0")
    return value


def _require_non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int):
        raise AppError(code=ErrorCode.CONFIG_INVALID, message=f"{field} must be integer")
    if value < 0:
        raise AppError(code=ErrorCode.CONFIG_INVALID, message=f"{field} must be >= 0")
    return value


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


__all__ = ["FsConfigRepository"]
