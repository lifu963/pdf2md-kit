"""Simple build pipeline that only concatenates extracted pages."""

from __future__ import annotations

from backend.shared_kernel.contracts import BuildMergeMode
from backend.shared_kernel.errors import AppError, ErrorCode


class SimpleMarkdownBuildPipeline:
    """Build output markdown without any LLM post-processing."""

    def build_output_content(
        self,
        *,
        page_contents: list[str],
        merge_mode: BuildMergeMode,
    ) -> str:
        normalized_sections = [_normalize_page_content(content) for content in page_contents]
        if not any(normalized_sections):
            raise AppError(
                code=ErrorCode.JOB_STATUS_CONFLICT,
                message="build output is empty",
            )

        if merge_mode == BuildMergeMode.DIRECT:
            return "\n\n".join(section for section in normalized_sections if section)

        if merge_mode == BuildMergeMode.SEPARATOR:
            return _join_blocks(normalized_sections, separator="---")

        if merge_mode == BuildMergeMode.SEPARATOR_WITH_PAGE_NUMBER:
            numbered_blocks = [
                _page_block(page_num=index, content=content)
                for index, content in enumerate(normalized_sections, start=1)
            ]
            return "\n\n".join(numbered_blocks)

        raise AppError(
            code=ErrorCode.JOB_STATUS_CONFLICT,
            message="unsupported build merge mode",
            details={"merge_mode": getattr(merge_mode, "value", str(merge_mode))},
        )


def _normalize_page_content(content: str) -> str:
    return content.replace("\r\n", "\n").strip()


def _join_blocks(blocks: list[str], *, separator: str) -> str:
    return f"\n\n{separator}\n\n".join(blocks)


def _page_block(*, page_num: int, content: str) -> str:
    marker = f"--- 第 {page_num} 页 ---"
    if not content:
        return marker
    return f"{marker}\n\n{content}"


__all__ = ["SimpleMarkdownBuildPipeline"]
