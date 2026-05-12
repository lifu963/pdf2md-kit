"""Application service for one-shot, task-less single-page preview extraction."""

from __future__ import annotations

from backend.extraction.application.dto import SinglePagePreviewResult
from backend.extraction.ports import (
    ConfigRepository,
    PdfDocumentGateway,
    SecretStore,
    VisionExtractionGateway,
)
from backend.shared_kernel.errors import AppError, ErrorCode


class SinglePagePreviewApplication:
    """
    Run one synchronous extraction against a single page of an uploaded PDF.

    This deliberately does NOT touch any job-context ports (jobs / pages /
    source store / events / scheduler). It reads the exact same runtime
    config and API key used by the real extraction pipeline so the preview
    faithfully represents what the real extraction would produce.
    """

    def __init__(
        self,
        *,
        config_repository: ConfigRepository,
        secret_store: SecretStore,
        pdf_gateway: PdfDocumentGateway,
        vision_gateway: VisionExtractionGateway,
    ) -> None:
        self._config_repository = config_repository
        self._secret_store = secret_store
        self._pdf_gateway = pdf_gateway
        self._vision_gateway = vision_gateway

    def preview_page(
        self,
        *,
        pdf_bytes: bytes,
        page_num: int,
    ) -> SinglePagePreviewResult:
        if page_num < 1:
            raise AppError(
                code=ErrorCode.PAGE_NOT_FOUND,
                message="page number must be >= 1",
                details={"page_num": page_num},
            )

        runtime = self._config_repository.load()
        api_key = self._secret_store.require_api_key()

        image_bytes = self._render_page(
            pdf_bytes=pdf_bytes,
            page_num=page_num,
            dpi=runtime.extract.dpi,
        )

        content = self._vision_gateway.extract_markdown(
            image_bytes=image_bytes,
            prompt=runtime.extract.prompt,
            model=runtime.model,
            api_key=api_key,
            max_retries=runtime.extract.max_retries,
            page_num=page_num,
        )

        return SinglePagePreviewResult(page_num=page_num, content=content)

    def _render_page(
        self,
        *,
        pdf_bytes: bytes,
        page_num: int,
        dpi: int,
    ) -> bytes:
        session = self._pdf_gateway.open_render_session(pdf_bytes)
        try:
            try:
                total_pages = session.page_count
            except AppError:
                raise
            except Exception as exc:
                raise AppError(
                    code=ErrorCode.PDF_OPEN_FAILED,
                    message=f"failed to open pdf: {exc}",
                    details={"page_num": page_num},
                ) from exc

            if page_num > total_pages:
                raise AppError(
                    code=ErrorCode.PAGE_NOT_FOUND,
                    message="page number out of range",
                    details={"page_num": page_num, "total_pages": total_pages},
                )

            try:
                return session.render_page(page_num=page_num, dpi=dpi)
            except AppError:
                raise
            except Exception as exc:
                raise AppError(
                    code=ErrorCode.PDF_OPEN_FAILED,
                    message=f"failed to render page: {exc}",
                    details={"page_num": page_num, "dpi": dpi},
                ) from exc
        finally:
            try:
                session.close()
            except Exception:
                pass


__all__ = ["SinglePagePreviewApplication"]
