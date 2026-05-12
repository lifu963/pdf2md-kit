from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
FRONTEND_ROOT = ROOT / "frontend" / "src"
DATA_ROOT = ROOT / "data"

EXPECTED_DIRECTORIES = [
    ROOT / "backend",
    ROOT / "backend" / "shared_kernel",
    ROOT / "backend" / "job",
    ROOT / "backend" / "job" / "domain",
    ROOT / "backend" / "job" / "application",
    ROOT / "backend" / "extraction",
    ROOT / "backend" / "extraction" / "application",
    ROOT / "backend" / "build",
    ROOT / "backend" / "build" / "application",
    ROOT / "backend" / "flashcard",
    ROOT / "backend" / "flashcard" / "application",
    ROOT / "backend" / "config",
    ROOT / "backend" / "config" / "application",
    ROOT / "backend" / "stream",
    ROOT / "backend" / "stream" / "application",
    ROOT / "backend" / "infra",
    ROOT / "backend" / "infra" / "fs",
    ROOT / "backend" / "infra" / "pdf",
    ROOT / "backend" / "infra" / "llm",
    ROOT / "backend" / "infra" / "stream",
    ROOT / "backend" / "infra" / "task",
    ROOT / "backend" / "api",
    ROOT / "backend" / "api" / "routes",
    ROOT / "backend" / "api" / "schemas",
    ROOT / "frontend",
    ROOT / "frontend" / "src",
    ROOT / "frontend" / "src" / "shared",
    ROOT / "frontend" / "src" / "shared" / "api",
    ROOT / "frontend" / "src" / "shared" / "stream",
    ROOT / "frontend" / "src" / "shared" / "types",
    ROOT / "frontend" / "src" / "modules",
    ROOT / "frontend" / "src" / "modules" / "job-workspace",
    ROOT / "frontend" / "src" / "modules" / "pdf-preview",
    ROOT / "frontend" / "src" / "modules" / "page-editor",
    ROOT / "frontend" / "src" / "modules" / "output-editor",
    ROOT / "frontend" / "src" / "modules" / "config-panel",
    ROOT / "data",
    ROOT / "tests",
    ROOT / "tests" / "architecture",
]

EXPECTED_FILES = [
    ROOT / "backend" / "__init__.py",
    ROOT / "backend" / "shared_kernel" / "__init__.py",
    ROOT / "backend" / "shared_kernel" / "errors.py",
    ROOT / "backend" / "shared_kernel" / "types.py",
    ROOT / "backend" / "shared_kernel" / "time.py",
    ROOT / "backend" / "job" / "__init__.py",
    ROOT / "backend" / "job" / "domain" / "__init__.py",
    ROOT / "backend" / "job" / "domain" / "models.py",
    ROOT / "backend" / "job" / "domain" / "rules.py",
    ROOT / "backend" / "job" / "application" / "__init__.py",
    ROOT / "backend" / "job" / "application" / "commands.py",
    ROOT / "backend" / "job" / "application" / "queries.py",
    ROOT / "backend" / "job" / "application" / "dto.py",
    ROOT / "backend" / "job" / "ports.py",
    ROOT / "backend" / "extraction" / "__init__.py",
    ROOT / "backend" / "extraction" / "application" / "__init__.py",
    ROOT / "backend" / "extraction" / "application" / "commands.py",
    ROOT / "backend" / "extraction" / "application" / "queries.py",
    ROOT / "backend" / "extraction" / "application" / "dto.py",
    ROOT / "backend" / "extraction" / "ports.py",
    ROOT / "backend" / "build" / "__init__.py",
    ROOT / "backend" / "build" / "application" / "__init__.py",
    ROOT / "backend" / "build" / "application" / "commands.py",
    ROOT / "backend" / "build" / "application" / "queries.py",
    ROOT / "backend" / "build" / "application" / "dto.py",
    ROOT / "backend" / "build" / "pipeline.py",
    ROOT / "backend" / "build" / "ports.py",
    ROOT / "backend" / "flashcard" / "__init__.py",
    ROOT / "backend" / "flashcard" / "application" / "__init__.py",
    ROOT / "backend" / "flashcard" / "application" / "commands.py",
    ROOT / "backend" / "flashcard" / "application" / "queries.py",
    ROOT / "backend" / "flashcard" / "application" / "dto.py",
    ROOT / "backend" / "flashcard" / "ports.py",
    ROOT / "backend" / "config" / "__init__.py",
    ROOT / "backend" / "config" / "application" / "__init__.py",
    ROOT / "backend" / "config" / "application" / "commands.py",
    ROOT / "backend" / "config" / "application" / "queries.py",
    ROOT / "backend" / "config" / "application" / "dto.py",
    ROOT / "backend" / "config" / "ports.py",
    ROOT / "backend" / "stream" / "__init__.py",
    ROOT / "backend" / "stream" / "application" / "__init__.py",
    ROOT / "backend" / "stream" / "application" / "commands.py",
    ROOT / "backend" / "stream" / "application" / "queries.py",
    ROOT / "backend" / "stream" / "application" / "dto.py",
    ROOT / "backend" / "stream" / "ports.py",
    ROOT / "backend" / "infra" / "__init__.py",
    ROOT / "backend" / "infra" / "fs" / "__init__.py",
    ROOT / "backend" / "infra" / "pdf" / "__init__.py",
    ROOT / "backend" / "infra" / "llm" / "__init__.py",
    ROOT / "backend" / "infra" / "stream" / "__init__.py",
    ROOT / "backend" / "infra" / "task" / "__init__.py",
    ROOT / "backend" / "api" / "__init__.py",
    ROOT / "backend" / "api" / "dependencies.py",
    ROOT / "backend" / "api" / "routes" / "__init__.py",
    ROOT / "backend" / "api" / "schemas" / "__init__.py",
    ROOT / "frontend" / "src" / "shared" / "api" / "index.ts",
    ROOT / "frontend" / "src" / "shared" / "stream" / "index.ts",
    ROOT / "frontend" / "src" / "shared" / "types" / "index.ts",
    ROOT / "frontend" / "src" / "modules" / "job-workspace" / "index.ts",
    ROOT / "frontend" / "src" / "modules" / "pdf-preview" / "index.ts",
    ROOT / "frontend" / "src" / "modules" / "page-editor" / "index.ts",
    ROOT / "frontend" / "src" / "modules" / "output-editor" / "index.ts",
    ROOT / "frontend" / "src" / "modules" / "config-panel" / "index.ts",
    ROOT / "data" / ".gitkeep",
]

BACKEND_SMOKE_MODULES = [
    "backend.shared_kernel.errors",
    "backend.shared_kernel.types",
    "backend.shared_kernel.time",
    "backend.job.domain.models",
    "backend.job.domain.rules",
    "backend.job.application.commands",
    "backend.job.application.queries",
    "backend.job.application.dto",
    "backend.job.ports",
    "backend.extraction.application",
    "backend.extraction.application.commands",
    "backend.extraction.application.queries",
    "backend.extraction.application.dto",
    "backend.extraction.ports",
    "backend.build.application.commands",
    "backend.build.application.queries",
    "backend.build.application.dto",
    "backend.build.pipeline",
    "backend.build.ports",
    "backend.flashcard.application.commands",
    "backend.flashcard.application.queries",
    "backend.flashcard.application.dto",
    "backend.flashcard.ports",
    "backend.config.application.commands",
    "backend.config.application.queries",
    "backend.config.application.dto",
    "backend.config.ports",
    "backend.stream.application.commands",
    "backend.stream.application.queries",
    "backend.stream.application.dto",
    "backend.stream.ports",
    "backend.api.dependencies",
]

FRONTEND_SMOKE_MODULES = [
    "frontend/src/shared/api/index.ts",
    "frontend/src/shared/stream/index.ts",
    "frontend/src/shared/types/index.ts",
    "frontend/src/modules/job-workspace/index.ts",
    "frontend/src/modules/pdf-preview/index.ts",
    "frontend/src/modules/page-editor/index.ts",
    "frontend/src/modules/output-editor/index.ts",
    "frontend/src/modules/config-panel/index.ts",
]

DISALLOWED_DOMAIN_IMPORT_ROOTS = {
    "fastapi",
    "starlette",
    "pydantic",
    "openai",
    "fitz",
    "pymupdf",
    "requests",
    "httpx",
    "aiofiles",
    "pathlib",
    "os",
    "subprocess",
    "socket",
    "tempfile",
    "shutil",
}

DISALLOWED_TOP_LEVEL_CALLS = {
    "open",
}

DISALLOWED_TOP_LEVEL_ATTRIBUTES = {
    "Path.open",
    "Path.read_text",
    "Path.write_text",
    "Path.mkdir",
}

ALLOWED_CONTRACT_FILES = {"commands", "queries", "dto"}


def _module_name_from_path(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _python_source_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _frontend_source_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx"}
    )


def _resolve_imported_modules(source_module: str, node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    if not isinstance(node, ast.ImportFrom):
        return []

    if node.module == "__future__":
        return []

    if node.level == 0:
        if node.module is None:
            return []
        return [node.module]

    package_parts = source_module.split(".")[:-1]
    prefix = package_parts[: len(package_parts) - (node.level - 1)]
    if node.module:
        return [".".join(prefix + node.module.split("."))]

    return [".".join(prefix)]


def _top_level_attribute_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        parent = _top_level_attribute_name(node.value)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"

    return None


def _is_allowed_cross_module_import(source_module: str, imported_module: str) -> bool:
    if not imported_module.startswith("backend."):
        return True

    source_parts = source_module.split(".")
    target_parts = imported_module.split(".")

    if len(source_parts) < 2 or len(target_parts) < 2:
        return True

    source_top = source_parts[1]
    target_top = target_parts[1]

    if source_top == target_top:
        return True

    if target_top == "shared_kernel":
        return True

    if len(target_parts) >= 3 and target_parts[2] == "ports":
        return True

    if len(target_parts) == 3 and target_parts[2] == "application":
        return True

    if len(target_parts) == 4 and target_parts[2] == "application" and target_parts[3] in ALLOWED_CONTRACT_FILES:
        return True

    return False


class StructureTests(unittest.TestCase):
    def test_expected_directories_exist(self) -> None:
        missing = [str(path.relative_to(ROOT)) for path in EXPECTED_DIRECTORIES if not path.is_dir()]
        self.assertEqual([], missing, f"缺少目录骨架: {missing}")

    def test_expected_files_exist(self) -> None:
        missing = [str(path.relative_to(ROOT)) for path in EXPECTED_FILES if not path.is_file()]
        self.assertEqual([], missing, f"缺少空模块文件: {missing}")


class ImportSmokeTests(unittest.TestCase):
    def test_backend_modules_import_without_write_or_network_side_effects(self) -> None:
        script = """
import importlib
import json
import sys

modules = json.loads(sys.argv[1])
violations = []

def hook(event, args):
    if event == "open":
        path = str(args[0]) if args else "<unknown>"
        mode = str(args[1]) if len(args) > 1 else ""
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            violations.append(f"write during import: {path} [{mode}]")
    elif event in {"socket.connect", "subprocess.Popen", "os.system", "os.startfile", "os.mkdir", "os.remove", "os.rename", "os.rmdir"}:
        violations.append(f"{event}: {args!r}")

sys.addaudithook(hook)
sys.dont_write_bytecode = True

for module_name in modules:
    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)

if violations:
    raise SystemExit("\\n".join(violations))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, json.dumps(BACKEND_SMOKE_MODULES)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0,
            result.returncode,
            "后端空模块导入存在副作用或导入失败:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}",
        )

    def test_frontend_modules_import_without_network_side_effects(self) -> None:
        script = f"""
import path from "node:path";
import {{ pathToFileURL }} from "node:url";

const modules = {json.dumps(FRONTEND_SMOKE_MODULES)};
globalThis.fetch = () => {{
  throw new Error("fetch should not run during module import");
}};
globalThis.EventSource = class {{
  constructor() {{
    throw new Error("EventSource should not be constructed during module import");
  }}
}};

for (const modulePath of modules) {{
  await import(pathToFileURL(path.resolve(modulePath)).href);
}}
"""
        result = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0,
            result.returncode,
            "前端空模块导入失败，或在导入阶段触发了网络副作用:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}",
        )


class BoundaryTests(unittest.TestCase):
    def test_domain_layer_does_not_depend_on_frameworks_or_external_io(self) -> None:
        violations: list[str] = []

        for path in sorted((BACKEND_ROOT / "job" / "domain").rglob("*.py")):
            module_name = _module_name_from_path(path)
            tree = ast.parse(_read_text(path), filename=str(path))

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for imported_module in _resolve_imported_modules(module_name, node):
                        root_name = imported_module.split(".")[0]
                        if root_name in DISALLOWED_DOMAIN_IMPORT_ROOTS:
                            violations.append(
                                f"{path.relative_to(ROOT)} imports forbidden dependency {imported_module}"
                            )

                if isinstance(node, ast.Call):
                    name = _top_level_attribute_name(node.func)
                    if name in DISALLOWED_TOP_LEVEL_CALLS or name in DISALLOWED_TOP_LEVEL_ATTRIBUTES:
                        violations.append(
                            f"{path.relative_to(ROOT)} uses forbidden IO call {name}"
                        )

        self.assertEqual([], violations, "领域层出现框架/外部 IO 依赖:\n" + "\n".join(violations))

    def test_backend_cross_module_imports_only_go_through_contracts_ports_or_shared_kernel(self) -> None:
        violations: list[str] = []

        for path in _python_source_files(BACKEND_ROOT):
            module_name = _module_name_from_path(path)
            tree = ast.parse(_read_text(path), filename=str(path))

            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue

                for imported_module in _resolve_imported_modules(module_name, node):
                    if not _is_allowed_cross_module_import(module_name, imported_module):
                        violations.append(
                            f"{path.relative_to(ROOT)} illegally imports {imported_module}"
                        )

        self.assertEqual([], violations, "后端跨模块导入越过契约层:\n" + "\n".join(violations))

    def test_frontend_business_modules_do_not_call_fetch_or_eventsource_directly(self) -> None:
        violations: list[str] = []

        for path in _frontend_source_files(FRONTEND_ROOT / "modules"):
            content = _read_text(path)
            if "fetch(" in content or "fetch (" in content or "EventSource(" in content or "new EventSource(" in content:
                violations.append(f"{path.relative_to(ROOT)} contains a direct network call")

        self.assertEqual([], violations, "前端业务模块直连网络:\n" + "\n".join(violations))

