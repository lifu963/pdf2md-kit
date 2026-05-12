"""
Step 11: pdf-adapter tests

Acceptance goals:
1. Count pages for a valid PDF.
2. Corrupted PDF maps to PDF_OPEN_FAILED.
3. Out-of-range page number is rejected.
4. Rendered page bytes are non-empty PNG data.
5. Render failures map to stable error semantics.
"""

from __future__ import annotations

import unittest
from unittest import mock

import fitz

from backend.infra.pdf.document_gateway import PymupdfPdfDocumentGateway
from backend.shared_kernel.errors import AppError, ErrorCode


def _build_pdf_bytes(*, total_pages: int) -> bytes:
    doc = fitz.open()
    try:
        for idx in range(total_pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {idx + 1}")
        return doc.tobytes()
    finally:
        doc.close()


class TestPdfAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = PymupdfPdfDocumentGateway()
        self.valid_pdf = _build_pdf_bytes(total_pages=3)

    def test_count_pages_with_valid_pdf(self) -> None:
        self.assertEqual(3, self.gateway.count_pages(self.valid_pdf))

    def test_count_pages_maps_corrupted_pdf_to_pdf_open_failed(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.gateway.count_pages(b"this is not a valid pdf file")
        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)

    def test_render_page_rejects_out_of_range_page_num(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.gateway.render_page(self.valid_pdf, page_num=4, dpi=150)
        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)

    def test_render_page_returns_non_empty_png_bytes(self) -> None:
        rendered = self.gateway.render_page(self.valid_pdf, page_num=2, dpi=150)
        self.assertGreater(len(rendered), 0)
        self.assertTrue(rendered.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_open_render_session_reuses_same_opened_document_across_pages(self) -> None:
        with mock.patch("backend.infra.pdf.document_gateway.fitz.open", wraps=fitz.open) as open_mock:
            session = self.gateway.open_render_session(self.valid_pdf)
            try:
                rendered_page_1 = session.render_page(page_num=1, dpi=150)
                rendered_page_2 = session.render_page(page_num=2, dpi=150)
            finally:
                session.close()

        self.assertEqual(1, open_mock.call_count)
        self.assertTrue(rendered_page_1.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(rendered_page_2.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_render_session_exposes_page_count_without_reopening_document(self) -> None:
        with mock.patch("backend.infra.pdf.document_gateway.fitz.open", wraps=fitz.open) as open_mock:
            session = self.gateway.open_render_session(self.valid_pdf)
            try:
                first_read = session.page_count
                second_read = session.page_count
                session.render_page(page_num=1, dpi=150)
            finally:
                session.close()

        self.assertEqual(3, first_read)
        self.assertEqual(3, second_read)
        self.assertEqual(1, open_mock.call_count)

    def test_render_page_maps_corrupted_pdf_to_pdf_open_failed(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self.gateway.render_page(b"broken-pdf", page_num=1, dpi=150)
        self.assertEqual(ErrorCode.PDF_OPEN_FAILED, ctx.exception.code)

