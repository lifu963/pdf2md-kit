"""
Step 04: workspace-fs-adapter 基础能力集成测试

验收标准：
1. 同一 job_id 复用同一把锁；不同 job_id 互不影响。
2. 路径不会逃逸出 data/{job_id}（路径遍历保护）。
3. state.json 原子写——并发写不留半文件。
4. 损坏 state.json 映射为 STATE_CORRUPTED。
5. 源 PDF 元数据与只读句柄正确。
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from unittest import TestCase, mock

from backend.infra.fs.workspace import WorkspaceManager
from backend.infra.fs.job_repository import FsJobRepository
from backend.infra.fs.source_store import FsSourceDocumentStore
from backend.shared_kernel.errors import AppError, ErrorCode
from backend.shared_kernel.contracts import (
    JobAggregate,
    JobStatus,
    PageStatus,
)
from datetime import datetime, timezone


def _make_job(job_id: uuid.UUID, total_pages: int = 3) -> JobAggregate:
    now = datetime.now(timezone.utc)
    return JobAggregate(
        job_id=job_id,
        source_pdf_name="test.pdf",
        total_pages=total_pages,
        status=JobStatus.EXTRACTING,
        succeeded_pages=[],
        failed_pages=[],
        created_at=now,
        updated_at=now,
        version=1,
        last_error=None,
    )


class TestWorkspaceManagerPaths(TestCase):
    """WorkspaceManager 路径解析与逃逸防护测试。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._wm = WorkspaceManager(data_root=Path(self._tmp))
        self._job_id = uuid.uuid4()

    def test_job_dir_is_under_data_root(self) -> None:
        job_dir = self._wm.job_dir(self._job_id)
        # 必须在 data_root 之下
        self.assertTrue(str(job_dir).startswith(self._tmp))

    def test_job_dir_contains_job_id(self) -> None:
        job_dir = self._wm.job_dir(self._job_id)
        self.assertIn(str(self._job_id), str(job_dir))

    def test_state_path_under_job_dir(self) -> None:
        state_path = self._wm.state_path(self._job_id)
        job_dir = self._wm.job_dir(self._job_id)
        self.assertTrue(str(state_path).startswith(str(job_dir)))
        self.assertEqual(state_path.name, "state.json")

    def test_source_path_under_job_dir(self) -> None:
        source_path = self._wm.source_path(self._job_id)
        job_dir = self._wm.job_dir(self._job_id)
        self.assertTrue(str(source_path).startswith(str(job_dir)))
        self.assertEqual(source_path.name, "source.pdf")

    def test_ensure_job_dir_creates_directory(self) -> None:
        job_dir = self._wm.ensure_job_dir(self._job_id)
        self.assertTrue(job_dir.is_dir())

    def test_different_jobs_have_different_dirs(self) -> None:
        job_id_a = uuid.uuid4()
        job_id_b = uuid.uuid4()
        self.assertNotEqual(
            self._wm.job_dir(job_id_a),
            self._wm.job_dir(job_id_b),
        )


class TestWorkspaceManagerLock(TestCase):
    """同一 job_id 锁共享；不同 job_id 锁独立。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._wm = WorkspaceManager(data_root=Path(self._tmp))

    def test_same_job_id_returns_same_lock(self) -> None:
        job_id = uuid.uuid4()
        lock1 = self._wm.get_lock(job_id)
        lock2 = self._wm.get_lock(job_id)
        self.assertIs(lock1, lock2)

    def test_different_job_ids_return_different_locks(self) -> None:
        lock_a = self._wm.get_lock(uuid.uuid4())
        lock_b = self._wm.get_lock(uuid.uuid4())
        self.assertIsNot(lock_a, lock_b)

    def test_lock_is_reentrant_safe_across_threads(self) -> None:
        """两个线程对同一 job_id 竞争锁，只有一个能同时持有。"""
        job_id = uuid.uuid4()
        lock = self._wm.get_lock(job_id)
        results: list[str] = []
        barrier = threading.Barrier(2)

        def worker(name: str) -> None:
            barrier.wait()
            with lock:
                results.append(f"{name}_enter")
                results.append(f"{name}_exit")

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 每个线程都完成了 enter/exit，且不交叉
        self.assertEqual(len(results), 4)
        # 验证临界区不交叉：每个 enter 后紧跟对应 exit
        for i in range(0, 4, 2):
            prefix = results[i].split("_")[0]
            self.assertEqual(results[i + 1], f"{prefix}_exit")


class TestFsJobRepositoryStateJson(TestCase):
    """FsJobRepository 的 state.json 原子读写测试。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._wm = WorkspaceManager(data_root=Path(self._tmp))
        self._repo = FsJobRepository(workspace=self._wm)
        self._job_id = uuid.uuid4()

    def test_save_and_get_roundtrip(self) -> None:
        job = _make_job(self._job_id)
        self._repo.save(job)
        loaded = self._repo.get(self._job_id)

        self.assertEqual(loaded.job_id, job.job_id)
        self.assertEqual(loaded.source_pdf_name, job.source_pdf_name)
        self.assertEqual(loaded.total_pages, job.total_pages)
        self.assertEqual(loaded.status, job.status)
        self.assertEqual(loaded.version, job.version)

    def test_exists_returns_false_when_missing(self) -> None:
        self.assertFalse(self._repo.exists(uuid.uuid4()))

    def test_exists_returns_true_after_save(self) -> None:
        job = _make_job(self._job_id)
        self._repo.save(job)
        self.assertTrue(self._repo.exists(self._job_id))

    def test_get_raises_job_not_found_when_missing(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self._repo.get(uuid.uuid4())
        self.assertEqual(ctx.exception.code, ErrorCode.JOB_NOT_FOUND)

    def test_state_json_no_half_file_on_concurrent_write(self) -> None:
        """并发写同一 job_id 后 state.json 是完整有效 JSON。"""
        job = _make_job(self._job_id, total_pages=10)
        self._wm.ensure_job_dir(self._job_id)

        errors: list[Exception] = []

        def writer() -> None:
            try:
                self._repo.save(job)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # 读取必须可解析
        state_path = self._wm.state_path(self._job_id)
        raw = state_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        self.assertIn("job_id", parsed)

    def test_corrupted_state_json_raises_state_corrupted(self) -> None:
        self._wm.ensure_job_dir(self._job_id)
        state_path = self._wm.state_path(self._job_id)
        state_path.write_text("NOT_VALID_JSON{{{", encoding="utf-8")

        with self.assertRaises(AppError) as ctx:
            self._repo.get(self._job_id)
        self.assertEqual(ctx.exception.code, ErrorCode.STATE_CORRUPTED)

    def test_save_overwrites_previous_state(self) -> None:
        job = _make_job(self._job_id)
        self._repo.save(job)

        job.status = JobStatus.EXTRACTED
        job.version = 2
        self._repo.save(job)

        loaded = self._repo.get(self._job_id)
        self.assertEqual(loaded.status, JobStatus.EXTRACTED)
        self.assertEqual(loaded.version, 2)

    def test_job_id_field_in_state_json(self) -> None:
        job = _make_job(self._job_id)
        self._repo.save(job)
        state_path = self._wm.state_path(self._job_id)
        data = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["job_id"], str(self._job_id))

    def test_list_all_reuses_cached_job_for_unchanged_state_file(self) -> None:
        job = _make_job(self._job_id)
        self._repo.save(job)
        self._repo.list_all()

        state_path = self._wm.state_path(self._job_id)
        original_read_text = Path.read_text

        def _guard_state_read(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if path == state_path:
                raise AssertionError("list_all should reuse cached state without re-reading state.json")
            return original_read_text(path, *args, **kwargs)

        with mock.patch(
            "pathlib.Path.read_text",
            autospec=True,
            side_effect=_guard_state_read,
        ):
            jobs = self._repo.list_all()

        self.assertEqual(1, len(jobs))
        self.assertEqual(self._job_id, jobs[0].job_id)

    def test_list_all_refreshes_cache_when_state_file_changes(self) -> None:
        job = _make_job(self._job_id)
        self._repo.save(job)
        first_jobs = self._repo.list_all()
        self.assertEqual(JobStatus.EXTRACTING, first_jobs[0].status)

        state_path = self._wm.state_path(self._job_id)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        raw["status"] = JobStatus.EXTRACTED.value
        raw["updated_at"] = datetime.now(timezone.utc).isoformat()
        state_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        refreshed_jobs = self._repo.list_all()
        self.assertEqual(JobStatus.EXTRACTED, refreshed_jobs[0].status)

    def test_list_all_holds_job_lock_while_refreshing_cache(self) -> None:
        original_job = _make_job(self._job_id)
        self._repo.save(original_job)

        # 先人为改动 state.json，确保 list_all 走到“重读并刷新缓存”分支。
        state_path = self._wm.state_path(self._job_id)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        raw["updated_at"] = datetime.now(timezone.utc).isoformat()
        state_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.utime(state_path, None)

        load_started = threading.Event()
        allow_list_continue = threading.Event()
        save_started = threading.Event()
        list_errors: list[Exception] = []
        save_errors: list[Exception] = []

        original_load = getattr(self._repo, "_load_job_from_state_path")

        def _delayed_load(path: Path) -> JobAggregate:
            loaded = original_load(path)
            load_started.set()
            allow_list_continue.wait(timeout=2)
            return loaded

        updated_job = _make_job(self._job_id)
        updated_job.status = JobStatus.EXTRACTED
        updated_job.version = original_job.version + 1
        updated_job.updated_at = datetime.now(timezone.utc)

        with mock.patch.object(
            self._repo,
            "_load_job_from_state_path",
            side_effect=_delayed_load,
        ):

            def _run_list() -> None:
                try:
                    self._repo.list_all()
                except Exception as exc:  # pylint: disable=broad-exception-caught  # pragma: no cover - assert via list_errors
                    list_errors.append(exc)

            def _run_save() -> None:
                save_started.set()
                try:
                    self._repo.save(updated_job)
                except Exception as exc:  # pylint: disable=broad-exception-caught  # pragma: no cover - assert via save_errors
                    save_errors.append(exc)

            list_thread = threading.Thread(target=_run_list)
            list_thread.start()
            self.assertTrue(load_started.wait(timeout=2), "list_all 未进入 state 重读分支")

            save_thread = threading.Thread(target=_run_save)
            save_thread.start()
            self.assertTrue(save_started.wait(timeout=1), "save 线程未启动")
            save_thread.join(timeout=0.2)
            self.assertTrue(
                save_thread.is_alive(),
                "list_all 刷新缓存期间应持有同一把 per-job 锁，防止 save 并发穿透",
            )

            allow_list_continue.set()
            list_thread.join(timeout=2)
            save_thread.join(timeout=2)

        self.assertFalse(list_thread.is_alive(), "list_all 线程未按预期结束")
        self.assertFalse(save_thread.is_alive(), "save 线程未按预期结束")
        self.assertEqual([], list_errors)
        self.assertEqual([], save_errors)

        latest_jobs = self._repo.list_all()
        self.assertEqual(1, len(latest_jobs))
        self.assertEqual(JobStatus.EXTRACTED, latest_jobs[0].status)
        self.assertEqual(updated_job.version, latest_jobs[0].version)


class TestFsSourceDocumentStore(TestCase):
    """FsSourceDocumentStore 元数据与只读句柄测试。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._wm = WorkspaceManager(data_root=Path(self._tmp))
        self._store = FsSourceDocumentStore(workspace=self._wm)
        self._job_id = uuid.uuid4()

    def test_save_source_returns_correct_metadata(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake content"
        ref = self._store.save_source(self._job_id, "my_document.pdf", pdf_bytes)

        self.assertEqual(ref.job_id, self._job_id)
        self.assertEqual(ref.filename, "my_document.pdf")
        self.assertEqual(ref.content_type, "application/pdf")
        self.assertEqual(ref.size_bytes, len(pdf_bytes))

    def test_save_source_creates_file_on_disk(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake"
        self._store.save_source(self._job_id, "doc.pdf", pdf_bytes)
        source_path = self._wm.source_path(self._job_id)
        self.assertTrue(source_path.exists())
        self.assertEqual(source_path.read_bytes(), pdf_bytes)

    def test_get_source_returns_correct_metadata(self) -> None:
        pdf_bytes = b"%PDF-1.4 hello"
        self._store.save_source(self._job_id, "hello.pdf", pdf_bytes)
        ref = self._store.get_source(self._job_id)

        self.assertEqual(ref.job_id, self._job_id)
        self.assertEqual(ref.filename, "hello.pdf")
        self.assertEqual(ref.size_bytes, len(pdf_bytes))
        self.assertEqual(ref.content_type, "application/pdf")

    def test_get_source_raises_job_not_found_when_missing(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self._store.get_source(uuid.uuid4())
        self.assertEqual(ctx.exception.code, ErrorCode.JOB_NOT_FOUND)

    def test_open_read_returns_readable_stream(self) -> None:
        pdf_bytes = b"%PDF-1.4 stream"
        self._store.save_source(self._job_id, "stream.pdf", pdf_bytes)
        handle = self._store.open_read(self._job_id)
        try:
            data = handle.read()
            self.assertEqual(data, pdf_bytes)
        finally:
            handle.close()

    def test_open_read_raises_job_not_found_when_missing(self) -> None:
        with self.assertRaises(AppError) as ctx:
            self._store.open_read(uuid.uuid4())
        self.assertEqual(ctx.exception.code, ErrorCode.JOB_NOT_FOUND)

    def test_relative_path_stays_within_job_dir(self) -> None:
        pdf_bytes = b"%PDF-1.4 safe"
        ref = self._store.save_source(self._job_id, "safe.pdf", pdf_bytes)
        # relative_path 不应包含路径逃逸符
        self.assertNotIn("..", ref.relative_path)
        # 拼回去仍在 data_root 之下
        abs_path = Path(self._tmp) / ref.relative_path
        self.assertTrue(str(abs_path).startswith(self._tmp))

    def test_save_source_overwrites_existing_file(self) -> None:
        self._store.save_source(self._job_id, "v1.pdf", b"version1")
        self._store.save_source(self._job_id, "v2.pdf", b"version2")
        source_path = self._wm.source_path(self._job_id)
        self.assertEqual(source_path.read_bytes(), b"version2")
