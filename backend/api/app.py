"""FastAPI application factory for the HTTP API adapter."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from backend.api.dependencies import (
    ApiContainer,
    get_config_application,
    get_job_application,
    get_single_page_preview_application,
    get_source_store,
    get_stream_application,
)
from backend.api.routes.config import (
    resolve_config_application,
    router as config_router,
)
from backend.api.routes.extraction import (
    resolve_single_page_preview_application,
    router as extraction_router,
)
from backend.api.routes.jobs import (
    resolve_job_application,
    resolve_source_store,
    resolve_stream_application,
    router as jobs_router,
)
from backend.api.schemas import dump_http_model, map_error_to_http_response
from backend.shared_kernel.errors import AppError


_DEFAULT_SPA_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PDF Knowledge Base</title>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
"""


def create_api_app(*, container: ApiContainer | None = None) -> FastAPI:
    """Create FastAPI app and optionally bind a fixed dependency container."""
    app = FastAPI()
    project_root = container.project_root if container is not None else Path(__file__).resolve().parents[2]
    _prepare_runtime_environment(project_root)
    spa_index_html = _load_spa_index_html(project_root)

    app.include_router(config_router, prefix="/api")
    app.include_router(extraction_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    if container is not None:
        app.dependency_overrides[resolve_config_application] = lambda: get_config_application(container)
        app.dependency_overrides[resolve_job_application] = lambda: get_job_application(container)
        app.dependency_overrides[resolve_stream_application] = lambda: get_stream_application(container)
        app.dependency_overrides[resolve_source_store] = lambda: get_source_store(container)
        app.dependency_overrides[resolve_single_page_preview_application] = (
            lambda: get_single_page_preview_application(container)
        )

    @app.get("/", include_in_schema=False)
    async def spa_root() -> HTMLResponse:
        return HTMLResponse(content=spa_index_html)

    @app.get("/jobs/{_job_id}", include_in_schema=False)
    async def spa_job(_job_id: str) -> HTMLResponse:
        return HTMLResponse(content=spa_index_html)

    @app.exception_handler(AppError)
    async def _handle_app_error(_request, error: AppError) -> JSONResponse:  # type: ignore[no-untyped-def]
        status_code, response = map_error_to_http_response(error)
        return JSONResponse(status_code=status_code, content=dump_http_model(response))

    return app


def _load_spa_index_html(project_root: Path) -> str:
    candidates = [
        project_root / "frontend" / "index.html",
        project_root / "frontend" / "dist" / "index.html",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            continue
    return _DEFAULT_SPA_HTML


def _prepare_runtime_environment(project_root: Path) -> None:
    """Align API runtime env with extraction script behavior."""
    dotenv_path = project_root / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path=dotenv_path, override=False)

    # ARK API is a domestic endpoint; proxy forwarding is often unavailable.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        os.environ.pop(var, None)


__all__ = ["create_api_app"]
