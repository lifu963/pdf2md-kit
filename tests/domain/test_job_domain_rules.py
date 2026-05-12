from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from backend.job.domain.models import JobAggregate, JobStatus, PageDocument, PageStatus
from backend.job.domain.rules import (
    create_job,
    discard_output,
    finish_build,
    mark_page_done,
    mark_page_failed,
    retry_page,
    save_output,
    save_page,
    start_build,
)
from backend.shared_kernel.errors import AppError, ErrorCode


class JobDomainRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job_id = uuid4()
        self.started_at = datetime(2026, 4, 6, 9, 0, tzinfo=timezone.utc)

    def _at(self, seconds: int) -> datetime:
        return self.started_at + timedelta(seconds=seconds)

    def _create_job_all_pages_terminal_with_failed_fixture(self) -> tuple[JobAggregate, list[PageDocument]]:
        job, pages = create_job(
            job_id=self.job_id,
            source_pdf_name="sample.pdf",
            total_pages=3,
            now=self._at(0),
        )
        job, pages[0] = mark_page_done(job, pages[0], "page-1", self._at(1))
        job, pages[1] = mark_page_failed(job, pages[1], "timeout", self._at(2))
        job, pages[2] = mark_page_done(job, pages[2], "page-3", self._at(3))
        return job, pages

    def _create_extracted_all_success_fixture(self) -> tuple[JobAggregate, list[PageDocument]]:
        job, pages = create_job(
            job_id=self.job_id,
            source_pdf_name="sample.pdf",
            total_pages=3,
            now=self._at(0),
        )
        job, pages[0] = mark_page_done(job, pages[0], "page-1", self._at(1))
        job, pages[1] = mark_page_done(job, pages[1], "page-2", self._at(2))
        job, pages[2] = mark_page_done(job, pages[2], "page-3", self._at(3))
        return job, pages

    def test_create_job_initializes_extracting_job_and_pending_pages(self) -> None:
        job, pages = create_job(
            job_id=self.job_id,
            source_pdf_name="sample.pdf",
            total_pages=3,
            now=self._at(0),
        )

        self.assertEqual(JobStatus.EXTRACTING, job.status)
        self.assertEqual(0, job.processed_count)
        self.assertEqual([], job.succeeded_pages)
        self.assertEqual([], job.failed_pages)
        self.assertEqual(3, len(pages))
        self.assertEqual([1, 2, 3], [page.page_num for page in pages])
        self.assertTrue(all(page.status == PageStatus.PENDING for page in pages))
        self.assertTrue(all(page.content is None for page in pages))
        self.assertTrue(all(page.error_message is None for page in pages))

    def test_single_page_done_updates_processed_count_without_finishing_job(self) -> None:
        job, pages = create_job(
            job_id=self.job_id,
            source_pdf_name="sample.pdf",
            total_pages=3,
            now=self._at(0),
        )

        next_job, next_page = mark_page_done(job, pages[0], "markdown-1", self._at(1))

        self.assertEqual(JobStatus.EXTRACTING, next_job.status)
        self.assertEqual(1, next_job.processed_count)
        self.assertEqual([1], next_job.succeeded_pages)
        self.assertEqual([], next_job.failed_pages)
        self.assertEqual(PageStatus.DONE, next_page.status)
        self.assertEqual("markdown-1", next_page.content)
        self.assertIsNone(next_page.error_message)

    def test_all_pages_processed_with_failure_stays_extracting(self) -> None:
        job, _pages = self._create_job_all_pages_terminal_with_failed_fixture()

        self.assertEqual(JobStatus.EXTRACTING, job.status)
        self.assertEqual(3, job.processed_count)
        self.assertEqual([1, 3], job.succeeded_pages)
        self.assertEqual([2], job.failed_pages)

    def test_retry_page_from_extracting_terminal_batch_rolls_back_processed_count(self) -> None:
        job, pages = self._create_job_all_pages_terminal_with_failed_fixture()

        next_job, retried_page = retry_page(job, pages[0], self._at(4))

        self.assertEqual(JobStatus.EXTRACTING, next_job.status)
        self.assertEqual(2, next_job.processed_count)
        self.assertEqual([3], next_job.succeeded_pages)
        self.assertEqual([2], next_job.failed_pages)
        self.assertEqual(PageStatus.EXTRACTING, retried_page.status)
        self.assertIsNone(retried_page.content)
        self.assertIsNone(retried_page.error_message)

    def test_build_is_only_allowed_in_extracted(self) -> None:
        job, pages = create_job(
            job_id=self.job_id,
            source_pdf_name="sample.pdf",
            total_pages=2,
            now=self._at(0),
        )
        with self.assertRaises(AppError) as conflict:
            start_build(job, self._at(1))
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, conflict.exception.code)

        extracted_job, pages[0] = mark_page_done(job, pages[0], "ok-1", self._at(2))
        extracted_job, pages[1] = mark_page_done(extracted_job, pages[1], "ok-2", self._at(3))
        self.assertEqual(JobStatus.EXTRACTED, extracted_job.status)

        building_job = start_build(extracted_job, self._at(4))
        self.assertEqual(JobStatus.BUILDING, building_job.status)

    def test_start_build_rejects_extracted_job_with_failed_pages(self) -> None:
        job, _pages = self._create_extracted_all_success_fixture()
        inconsistent = replace(job, failed_pages=[2])
        with self.assertRaises(AppError) as ctx:
            start_build(inconsistent, self._at(4))
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, ctx.exception.code)

    def test_ready_job_accepts_output_save_and_keeps_ready(self) -> None:
        job, _pages = self._create_extracted_all_success_fixture()
        building_job = start_build(job, self._at(4))
        ready_job = finish_build(building_job, self._at(5))

        touched_job = save_output(ready_job, self._at(6))
        self.assertEqual(JobStatus.READY, touched_job.status)

        with self.assertRaises(AppError) as conflict:
            save_output(job, self._at(7))
        self.assertEqual(ErrorCode.OUTPUT_EDIT_FORBIDDEN, conflict.exception.code)

    def test_ready_job_can_discard_output_and_return_to_extracted(self) -> None:
        job, _pages = self._create_extracted_all_success_fixture()
        ready_job = finish_build(start_build(job, self._at(4)), self._at(5))

        reopened_job = discard_output(ready_job, self._at(6))
        self.assertEqual(JobStatus.EXTRACTED, reopened_job.status)

        with self.assertRaises(AppError) as conflict:
            discard_output(job, self._at(7))
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, conflict.exception.code)

    def test_ready_job_freezes_page_edit_and_retry(self) -> None:
        job, pages = self._create_extracted_all_success_fixture()
        ready_job = finish_build(start_build(job, self._at(4)), self._at(5))

        with self.assertRaises(AppError) as save_page_conflict:
            save_page(ready_job, pages[0], "manual fix", self._at(6))
        self.assertEqual(ErrorCode.PAGE_EDIT_FORBIDDEN, save_page_conflict.exception.code)

        with self.assertRaises(AppError) as retry_conflict:
            retry_page(ready_job, pages[0], self._at(7))
        self.assertEqual(ErrorCode.PAGE_RETRY_FORBIDDEN, retry_conflict.exception.code)

    def test_building_job_rejects_page_writes_with_job_status_conflict(self) -> None:
        job, pages = self._create_extracted_all_success_fixture()
        building_job = start_build(job, self._at(4))

        with self.assertRaises(AppError) as save_page_conflict:
            save_page(building_job, pages[0], "manual", self._at(5))
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, save_page_conflict.exception.code)

        with self.assertRaises(AppError) as retry_conflict:
            retry_page(building_job, pages[0], self._at(6))
        self.assertEqual(ErrorCode.JOB_STATUS_CONFLICT, retry_conflict.exception.code)


if __name__ == "__main__":
    unittest.main()
