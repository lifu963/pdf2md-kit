"""Port contracts used by flashcard (v2 placeholder) application."""

from __future__ import annotations

from typing import Protocol

from backend.config.ports import ConfigRepository, SecretStore
from backend.job.ports import ArtifactRepository, EventPublisher, JobRepository
from backend.shared_kernel.contracts import ModelConfig
from backend.shared_kernel.time import Clock


class TextGenerationGateway(Protocol):
    def generate_text(
        self,
        *,
        prompt: str,
        model: ModelConfig,
        api_key: str,
    ) -> str:
        """Generate text from LLM for flashcard step1/step2."""


__all__ = [
    "ArtifactRepository",
    "Clock",
    "ConfigRepository",
    "EventPublisher",
    "JobRepository",
    "SecretStore",
    "TextGenerationGateway",
]
