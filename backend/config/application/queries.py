"""Config application query contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GetPublicConfigQuery:
    pass


__all__ = ["GetPublicConfigQuery"]
