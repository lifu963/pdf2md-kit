"""PDF gateway adapter based on PyMuPDF."""

from __future__ import annotations

import fitz
import threading

from backend.shared_kernel.errors import AppError, ErrorCode

_PDF_POINTS_PER_INCH = 72.0


class PymupdfPdfDocumentGateway:
    """Open PDF bytes, count pages, and render one page to PNG bytes."""

    def count_pages(self, pdf_bytes: bytes) -> int:
        doc = _open_pdf_document(pdf_bytes)
        try:
            return doc.page_count
        finally:
            doc.close()

    def render_page(self, pdf_bytes: bytes, page_num: int, dpi: int) -> bytes:
        session = self.open_render_session(pdf_bytes)
        try:
            return session.render_page(page_num=page_num, dpi=dpi)
        finally:
            session.close()

    def open_render_session(self, pdf_bytes: bytes) -> "_PymupdfPdfRenderSession":
        return _PymupdfPdfRenderSession(pdf_bytes=pdf_bytes)


class _PymupdfPdfRenderSession:
    def __init__(self, *, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self._document: fitz.Document | None = None
        self._owner_thread_id: int | None = None

    @property
    def page_count(self) -> int:
        return self._ensure_document().page_count

    def render_page(self, page_num: int, dpi: int) -> bytes:
        if dpi <= 0:
            raise AppError(
                code=ErrorCode.PDF_OPEN_FAILED,
                message="dpi must be positive",
                details={"dpi": dpi},
            )

        doc = self._ensure_document()
        try:
            total_pages = doc.page_count
            if page_num < 1 or page_num > total_pages:
                raise AppError(
                    code=ErrorCode.PDF_OPEN_FAILED,
                    message="page number out of range",
                    details={"page_num": page_num, "total_pages": total_pages},
                )

            page = doc.load_page(page_num - 1)
            scale = dpi / _PDF_POINTS_PER_INCH
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            rendered = pix.tobytes("png")
            if not rendered:
                raise AppError(
                    code=ErrorCode.PDF_OPEN_FAILED,
                    message="rendered page is empty",
                    details={"page_num": page_num, "dpi": dpi},
                )
            return rendered
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                code=ErrorCode.PDF_OPEN_FAILED,
                message=f"failed to render page: {exc}",
                details={"page_num": page_num, "dpi": dpi},
            ) from exc

    def close(self) -> None:
        if self._document is not None:
            self._document.close()
            self._document = None
            self._owner_thread_id = None

    def _ensure_document(self) -> fitz.Document:
        current_thread_id = threading.get_ident()
        if self._document is None:
            self._document = _open_pdf_document(self._pdf_bytes)
            self._owner_thread_id = current_thread_id
            return self._document

        if self._owner_thread_id != current_thread_id:
            raise AppError(
                code=ErrorCode.PDF_OPEN_FAILED,
                message="pdf render session cannot be used across multiple threads",
            )

        return self._document


def _open_pdf_document(pdf_bytes: bytes) -> fitz.Document:
    if not pdf_bytes:
        raise AppError(
            code=ErrorCode.PDF_OPEN_FAILED,
            message="pdf bytes are empty",
        )

    try:
        return fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise AppError(
            code=ErrorCode.PDF_OPEN_FAILED,
            message=f"failed to open pdf: {exc}",
        ) from exc


__all__ = ["PymupdfPdfDocumentGateway"]

