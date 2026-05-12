"""Central dependency wiring for the HTTP API adapter."""

from __future__ import annotations

from importlib import import_module
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Callable

from backend.build.application import (
    BuildApplication as BuildApplicationService,
    SimpleMarkdownBuildPipeline,
)
from backend.config.application import ConfigApplication as ConfigApplicationService
from backend.config.ports import ConfigRepository, SecretStore
from backend.extraction.application import (
    ExtractionApplication as ExtractionApplicationService,
    SinglePagePreviewApplication,
)
from backend.extraction.ports import TaskScheduler, VisionExtractionGateway
from backend.job.application import JobApplication as JobApplicationService
from backend.job.ports import (
    ArtifactRepository,
    BuildApplication,
    EventPublisher,
    ExtractionApplication,
    JobRepository,
    PageRepository,
    PdfDocumentGateway,
    SourceDocumentStore,
)
from backend.shared_kernel.time import Clock, IdGenerator, SystemClock, Uuid4Generator
from backend.stream.application import StreamApplication as StreamApplicationService
from backend.stream.ports import EventLogRepository, LiveSubscriberHub


class ApiContainer:
    """Compose adapters and application services exactly once for HTTP usage."""

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        data_root: Path | None = None,
        workspace: Any | None = None,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
        job_repository: JobRepository | None = None,
        page_repository: PageRepository | None = None,
        source_store: SourceDocumentStore | None = None,
        artifact_repository: ArtifactRepository | None = None,
        event_log_repository: EventLogRepository | None = None,
        config_repository: ConfigRepository | None = None,
        secret_store: SecretStore | None = None,
        pdf_gateway: PdfDocumentGateway | None = None,
        vision_gateway: VisionExtractionGateway | None = None,
        task_scheduler: TaskScheduler | None = None,
        live_subscriber_hub: LiveSubscriberHub | None = None,
        event_publisher: EventPublisher | None = None,
        build_application: BuildApplication | None = None,
        extraction_application: ExtractionApplication | None = None,
        job_application: JobApplicationService | None = None,
        config_application: ConfigApplicationService | None = None,
        stream_application: StreamApplicationService | None = None,
        single_page_preview_application: SinglePagePreviewApplication | None = None,
        openai_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        resolved_project_root = Path(project_root).resolve() if project_root is not None else _default_project_root()
        resolved_data_root = (
            Path(data_root).resolve()
            if data_root is not None
            else (resolved_project_root / "data").resolve()
        )

        self.project_root = resolved_project_root
        self.data_root = resolved_data_root

        self._workspace_override = workspace
        self._clock_override = clock
        self._id_generator_override = id_generator
        self._job_repository_override = job_repository
        self._page_repository_override = page_repository
        self._source_store_override = source_store
        self._artifact_repository_override = artifact_repository
        self._event_log_repository_override = event_log_repository
        self._config_repository_override = config_repository
        self._secret_store_override = secret_store
        self._pdf_gateway_override = pdf_gateway
        self._vision_gateway_override = vision_gateway
        self._task_scheduler_override = task_scheduler
        self._live_subscriber_hub_override = live_subscriber_hub
        self._event_publisher_override = event_publisher
        self._build_application_override = build_application
        self._extraction_application_override = extraction_application
        self._job_application_override = job_application
        self._config_application_override = config_application
        self._stream_application_override = stream_application
        self._single_page_preview_application_override = single_page_preview_application
        self._openai_client_factory = openai_client_factory

    @cached_property
    def workspace(self) -> Any:
        if self._workspace_override is not None:
            return self._workspace_override
        workspace_cls = _load_attr("backend.infra.fs", "WorkspaceManager")
        return workspace_cls(data_root=self.data_root)

    @cached_property
    def clock(self) -> Clock:
        if self._clock_override is not None:
            return self._clock_override
        return SystemClock()

    @cached_property
    def id_generator(self) -> IdGenerator:
        if self._id_generator_override is not None:
            return self._id_generator_override
        return Uuid4Generator()

    @cached_property
    def job_repository(self) -> JobRepository:
        if self._job_repository_override is not None:
            return self._job_repository_override
        repository_cls = _load_attr("backend.infra.fs", "FsJobRepository")
        return repository_cls(workspace=self.workspace)

    @cached_property
    def page_repository(self) -> PageRepository:
        if self._page_repository_override is not None:
            return self._page_repository_override
        repository_cls = _load_attr("backend.infra.fs", "FsPageRepository")
        return repository_cls(workspace=self.workspace)

    @cached_property
    def source_store(self) -> SourceDocumentStore:
        if self._source_store_override is not None:
            return self._source_store_override
        store_cls = _load_attr("backend.infra.fs", "FsSourceDocumentStore")
        return store_cls(workspace=self.workspace)

    @cached_property
    def artifact_repository(self) -> ArtifactRepository:
        if self._artifact_repository_override is not None:
            return self._artifact_repository_override
        repository_cls = _load_attr("backend.infra.fs", "FsArtifactRepository")
        return repository_cls(workspace=self.workspace)

    @cached_property
    def event_log_repository(self) -> EventLogRepository:
        if self._event_log_repository_override is not None:
            return self._event_log_repository_override
        repository_cls = _load_attr("backend.infra.fs", "FsEventLogRepository")
        return repository_cls(workspace=self.workspace)

    @cached_property
    def secret_store(self) -> SecretStore:
        if self._secret_store_override is not None:
            return self._secret_store_override
        store_cls = _load_attr("backend.infra.fs", "FsSecretStore")
        return store_cls(data_root=self.data_root)

    @cached_property
    def config_repository(self) -> ConfigRepository:
        if self._config_repository_override is not None:
            return self._config_repository_override
        repository_cls = _load_attr("backend.infra.fs", "FsConfigRepository")
        return repository_cls(
            data_root=self.data_root,
            project_root=self.project_root,
            secret_store=self.secret_store,
        )

    @cached_property
    def pdf_gateway(self) -> PdfDocumentGateway:
        if self._pdf_gateway_override is not None:
            return self._pdf_gateway_override
        gateway_cls = _load_attr("backend.infra.pdf", "PymupdfPdfDocumentGateway")
        return gateway_cls()

    @cached_property
    def vision_gateway(self) -> VisionExtractionGateway:
        if self._vision_gateway_override is not None:
            return self._vision_gateway_override
        gateway_cls = _load_attr("backend.infra.llm", "OpenAIVisionExtractionGateway")
        if self._openai_client_factory is not None:
            return gateway_cls(client_factory=self._openai_client_factory)
        return gateway_cls()

    @cached_property
    def task_scheduler(self) -> TaskScheduler:
        if self._task_scheduler_override is not None:
            return self._task_scheduler_override
        scheduler_cls = _load_attr("backend.infra.task", "ThreadedTaskScheduler")
        return scheduler_cls()

    @cached_property
    def live_subscriber_hub(self) -> LiveSubscriberHub:
        if self._live_subscriber_hub_override is not None:
            return self._live_subscriber_hub_override
        hub_cls = _load_attr("backend.infra.stream", "InMemoryLiveSubscriberHub")
        return hub_cls()

    @cached_property
    def event_publisher(self) -> EventPublisher:
        if self._event_publisher_override is not None:
            return self._event_publisher_override
        publisher_cls = _load_attr("backend.infra.stream", "EventLogBackedEventPublisher")
        return publisher_cls(
            event_log_repository=self.event_log_repository,
            live_subscriber_hub=self.live_subscriber_hub,
        )

    @cached_property
    def build_application(self) -> BuildApplication:
        if self._build_application_override is not None:
            return self._build_application_override
        return BuildApplicationService(
            job_repository=self.job_repository,
            page_repository=self.page_repository,
            artifact_repository=self.artifact_repository,
            clock=self.clock,
            pipeline=SimpleMarkdownBuildPipeline(),
        )

    @cached_property
    def extraction_application(self) -> ExtractionApplication:
        if self._extraction_application_override is not None:
            return self._extraction_application_override
        return ExtractionApplicationService(
            job_repository=self.job_repository,
            page_repository=self.page_repository,
            source_store=self.source_store,
            config_repository=self.config_repository,
            secret_store=self.secret_store,
            pdf_gateway=self.pdf_gateway,
            vision_gateway=self.vision_gateway,
            event_publisher=self.event_publisher,
            task_scheduler=self.task_scheduler,
            clock=self.clock,
        )

    @cached_property
    def job_application(self) -> JobApplicationService:
        if self._job_application_override is not None:
            return self._job_application_override
        return JobApplicationService(
            job_repository=self.job_repository,
            page_repository=self.page_repository,
            source_store=self.source_store,
            build_application=self.build_application,
            extraction_application=self.extraction_application,
            pdf_gateway=self.pdf_gateway,
            secret_store=self.secret_store,
            clock=self.clock,
            id_generator=self.id_generator,
        )

    @cached_property
    def config_application(self) -> ConfigApplicationService:
        if self._config_application_override is not None:
            return self._config_application_override
        return ConfigApplicationService(
            config_repository=self.config_repository,
            secret_store=self.secret_store,
            vision_gateway=self.vision_gateway,
        )

    @cached_property
    def single_page_preview_application(self) -> SinglePagePreviewApplication:
        if self._single_page_preview_application_override is not None:
            return self._single_page_preview_application_override
        return SinglePagePreviewApplication(
            config_repository=self.config_repository,
            secret_store=self.secret_store,
            pdf_gateway=self.pdf_gateway,
            vision_gateway=self.vision_gateway,
        )

    @cached_property
    def stream_application(self) -> StreamApplicationService:
        if self._stream_application_override is not None:
            return self._stream_application_override
        return StreamApplicationService(
            event_log_repository=self.event_log_repository,
            live_subscriber_hub=self.live_subscriber_hub,
            job_repository=self.job_repository,
        )


@lru_cache(maxsize=1)
def get_api_container() -> ApiContainer:
    return ApiContainer()


def clear_api_container_cache() -> None:
    get_api_container.cache_clear()


def get_build_application(container: ApiContainer | None = None) -> BuildApplication:
    return _resolve_container(container).build_application


def get_extraction_application(container: ApiContainer | None = None) -> ExtractionApplication:
    return _resolve_container(container).extraction_application


def get_job_application(container: ApiContainer | None = None) -> JobApplicationService:
    return _resolve_container(container).job_application


def get_config_application(container: ApiContainer | None = None) -> ConfigApplicationService:
    return _resolve_container(container).config_application


def get_stream_application(container: ApiContainer | None = None) -> StreamApplicationService:
    return _resolve_container(container).stream_application


def get_single_page_preview_application(
    container: ApiContainer | None = None,
) -> SinglePagePreviewApplication:
    return _resolve_container(container).single_page_preview_application


def get_source_store(container: ApiContainer | None = None) -> SourceDocumentStore:
    return _resolve_container(container).source_store


def _resolve_container(container: ApiContainer | None) -> ApiContainer:
    if container is not None:
        return container
    return get_api_container()


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_attr(module_name: str, attr_name: str) -> Any:
    module = import_module(module_name)
    return getattr(module, attr_name)


__all__ = [
    "ApiContainer",
    "clear_api_container_cache",
    "get_api_container",
    "get_build_application",
    "get_config_application",
    "get_extraction_application",
    "get_job_application",
    "get_single_page_preview_application",
    "get_source_store",
    "get_stream_application",
]
