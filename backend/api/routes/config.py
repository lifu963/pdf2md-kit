"""HTTP routes for runtime config read/write."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_config_application
from backend.api.schemas import (
    UpdateConfigRequest,
    dump_http_model,
    to_public_config_response,
    to_test_connection_response,
    to_update_config_command,
)
from backend.config.application import ConfigApplication, GetPublicConfigQuery

router = APIRouter()


def resolve_config_application() -> ConfigApplication:
    return get_config_application()


@router.get("/config")
def get_public_config(
    config_application: ConfigApplication = Depends(resolve_config_application),
) -> dict[str, Any]:
    view = config_application.get_public_config(GetPublicConfigQuery())
    response = to_public_config_response(view)
    return dump_http_model(response, exclude_none=True)


@router.put("/config")
def update_config(
    request: UpdateConfigRequest,
    config_application: ConfigApplication = Depends(resolve_config_application),
) -> dict[str, Any]:
    command = to_update_config_command(request)
    updated = config_application.update_config(command)
    response = to_public_config_response(updated)
    return dump_http_model(response, exclude_none=True)


@router.post("/config/reset")
def reset_config(
    config_application: ConfigApplication = Depends(resolve_config_application),
) -> dict[str, Any]:
    restored = config_application.reset_to_initial_config()
    response = to_public_config_response(restored)
    return dump_http_model(response, exclude_none=True)


@router.post("/config/test-connection")
def test_connection(
    config_application: ConfigApplication = Depends(resolve_config_application),
) -> dict[str, Any]:
    result = config_application.test_connection()
    response = to_test_connection_response(result)
    return dump_http_model(response, exclude_none=True)


__all__ = ["resolve_config_application", "router"]
