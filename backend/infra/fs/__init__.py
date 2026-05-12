"""Workspace file-system adapter: per-job + config/secrets 持久化。"""

from backend.infra.fs.artifact_repository import FsArtifactRepository
from backend.infra.fs.config_repository import FsConfigRepository
from backend.infra.fs.event_log_repository import FsEventLogRepository
from backend.infra.fs.workspace import WorkspaceManager
from backend.infra.fs.job_repository import FsJobRepository
from backend.infra.fs.page_repository import FsPageRepository
from backend.infra.fs.secret_store import FsSecretStore
from backend.infra.fs.source_store import FsSourceDocumentStore

__all__ = [
    "FsArtifactRepository",
    "FsConfigRepository",
    "FsEventLogRepository",
    "FsJobRepository",
    "FsPageRepository",
    "FsSecretStore",
    "FsSourceDocumentStore",
    "WorkspaceManager",
]
