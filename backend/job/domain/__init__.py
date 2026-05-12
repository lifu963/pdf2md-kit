"""Job domain package."""

from backend.job.domain.rules import (
    create_job,
    discard_output,
    fail_build,
    fail_job,
    finish_build,
    mark_page_done,
    mark_page_failed,
    retry_page,
    save_output,
    save_page,
    start_build,
)

__all__ = [
    "create_job",
    "discard_output",
    "fail_build",
    "fail_job",
    "finish_build",
    "mark_page_done",
    "mark_page_failed",
    "retry_page",
    "save_output",
    "save_page",
    "start_build",
]
