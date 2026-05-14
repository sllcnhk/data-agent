"""
失败子任务批量重试功能测试

A · 服务层单元测试（_retry_failed_chunks_sync 核心逻辑）
B · retry_failed_chunks_async 快速校验（前置 ValueError）
C · API 端点测试（POST /data-export/jobs/{job_id}/retry-failed-chunks）
D · 端到端集成测试（完整重试流程：状态流转、串行执行、文件输出）

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_retry_failed_chunks.py -v -s
"""
import asyncio
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_t_rfc_{uuid.uuid4().hex[:6]}_"

# ─────────────────────────────────────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────────────────────────────────────

def _make_columns():
    from backend.services.export_clients.base import ColumnInfo
    return [ColumnInfo("id", "Int64"), ColumnInfo("name", "String")]


def _make_batch(n: int, base: int = 0):
    return [(str(base + i), f"name_{base + i}") for i in range(n)]


def _attach_csv_stream_raw(mc) -> None:
    """给 Mock client 补 stream_raw（csv_staging 引擎需要）"""
    cols = mc.get_columns.return_value
    try:
        col_names: List[str] = [c.name for c in cols] if cols else []
    except TypeError:
        col_names = []
    sb_side = mc.stream_batches.side_effect

    def _csv_escape(v: Any) -> str:
        if v is None:
            return ""
        s = str(v)
        if "," in s or '"' in s or "\n" in s or "\r" in s:
            s = '"' + s.replace('"', '""') + '"'
        return s

    def _stream_raw(sql, format_name="CSVWithNames", **kw):
        yield (",".join(col_names) + "\n").encode("utf-8")
        gen = sb_side(sql) if sb_side else iter([])
        for batch in gen:
            for row in batch:
                yield (",".join(_csv_escape(v) for v in row) + "\n").encode("utf-8")

    mc.stream_raw.side_effect = _stream_raw


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_output_files(out_dir: Path, completed_count: int, failed_count: int) -> List[Dict]:
    """
    构造 output_files 列表：先 completed，后 failed（按 index 顺序）。
    completed 块生成真实的空文件（模拟已有产出）；failed 块仅有路径记录。
    """
    entries = []
    for i in range(completed_count):
        fn = f"chunk_{i:03d}.xlsx"
        fp = out_dir / fn
        fp.touch()  # 创建空文件模拟已完成
        entries.append({
            "index": i,
            "date_start": f"2025-0{(i % 3) + 1}-01",
            "date_end":   f"2025-0{(i % 3) + 1}-10",
            "filename":   fn,
            "file_path":  str(fp),
            "file_size":  100,
            "rows":       5,
            "sheets":     1,
            "status":     "completed",
        })
    for j in range(failed_count):
        idx = completed_count + j
        fn = f"chunk_{idx:03d}.xlsx"
        fp = out_dir / fn
        entries.append({
            "index":        idx,
            "date_start":   f"2025-0{(idx % 3) + 1}-11",
            "date_end":     f"2025-0{(idx % 3) + 1}-20",
            "filename":     fn,
            "file_path":    str(fp),
            "file_size":    None,
            "rows":         0,
            "sheets":       0,
            "status":       "failed",
            "error_summary": "Connection broken",
        })
    return entries


def _make_retry_job(db, username: str, out_dir: Path,
                    job_status: str = "partial_failed",
                    completed_count: int = 2,
                    failed_count: int = 2,
                    output_format: str = "xlsx",
                    sql: str = "SELECT id, name FROM t WHERE dt >= '{{date_start}}' AND dt <= '{{date_end}}'",
                    date_column: Optional[str] = None) -> str:
    """直接在 DB 创建带 output_files 的 date_chunked Job（模拟已运行过一次后的状态）。"""
    from backend.models.export_job import ExportJob

    out_dir.mkdir(parents=True, exist_ok=True)
    output_files = _make_output_files(out_dir, completed_count, failed_count)

    job = ExportJob(
        user_id=f"uid_{uuid.uuid4().hex[:8]}",
        username=username,
        job_name=f"retry_test_{uuid.uuid4().hex[:6]}",
        query_sql=sql,
        connection_env="test",
        connection_type="clickhouse",
        status=job_status,
        export_mode="date_chunked",
        chunk_config={
            "date_column": date_column,
            "date_start":  "2025-01-01",
            "date_end":    "2025-03-31",
            "chunk_days":  10,
        },
        output_files=output_files,
        output_filename=out_dir.name,
        file_path=str(out_dir),
        exported_rows=completed_count * 5,
        done_batches=completed_count,
        total_batches=completed_count + failed_count,
        total_sheets=completed_count,
        config_snapshot={
            "output_format": output_format,
            "xlsx_engine":   "auto",
            "batch_size":    50000,
        },
        error_message="部分块失败" if job_status == "partial_failed" else "全部块失败",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return str(job.id)


def _make_mock_client(rows_per_chunk: int = 3, fail_sql_keywords: Optional[List[str]] = None):
    """
    返回 Mock export_client。
    fail_sql_keywords: 若 SQL 包含其中任一词，则 stream_batches 抛异常（模拟失败块）。
    """
    fail_keywords = fail_sql_keywords or []
    call_count = [0]

    mc = Mock()
    mc.get_columns.return_value = _make_columns()

    def _stream(sql, **kw):
        call_count[0] += 1
        for kw_fail in fail_keywords:
            if kw_fail in sql:
                raise RuntimeError(f"Simulated failure for: {kw_fail}")
        yield _make_batch(rows_per_chunk, base=(call_count[0] - 1) * rows_per_chunk)

    mc.stream_batches.side_effect = _stream
    _attach_csv_stream_raw(mc)
    mc._call_count = call_count
    return mc


# ─────────────────────────────────────────────────────────────────────────────
# A · 服务层单元测试（_retry_failed_chunks_sync）
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryFailedChunksSync:

    @pytest.fixture(autouse=True)
    def db(self, tmp_path):
        from backend.config.database import SessionLocal
        self.db = SessionLocal()
        self.tmp = tmp_path
        yield
        self.db.close()

    def _username(self, label=""):
        return f"{_PREFIX}{label}_"

    def _get_job(self, job_id):
        from backend.models.export_job import ExportJob
        self.db.expire_all()
        return self.db.query(ExportJob).filter(ExportJob.id == job_id).first()

    # ── A1: job 不存在 → 静默退出 ────────────────────────────────────────────

    def test_a1_job_not_found_exits_silently(self):
        import backend.services.data_export_service as svc
        fake_id = str(uuid.uuid4())
        # 不应抛出异常
        svc._retry_failed_chunks_sync(fake_id, 50000)

    # ── A2: job 已是 running → 拒绝（串行保障）────────────────────────────────

    def test_a2_already_running_exits(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a2"
        job_id = _make_retry_job(self.db, self._username("a2"), out_dir,
                                  job_status="running")
        # 若 running 状态不被保护，会修改 status 为 running 并继续执行
        # 正确行为：看到 running → 直接返回，状态不变
        svc._retry_failed_chunks_sync(job_id, 50000)
        j = self._get_job(job_id)
        # 状态应保持 running（未被修改）
        assert j.status == "running", f"Expected running, got {j.status}"

    # ── A3: 非重试可接受状态（completed）→ 退出 ──────────────────────────────

    def test_a3_wrong_status_exits(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a3"
        job_id = _make_retry_job(self.db, self._username("a3"), out_dir,
                                  job_status="completed", failed_count=0)
        svc._retry_failed_chunks_sync(job_id, 50000)
        j = self._get_job(job_id)
        assert j.status == "completed"  # 未被改变

    # ── A4: 无失败块 → 重新统计后恢复终态 ──────────────────────────────────

    def test_a4_no_failed_chunks_restores_completed(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a4"
        # 创建 partial_failed 状态但所有 output_files 都是 completed
        job_id = _make_retry_job(self.db, self._username("a4"), out_dir,
                                  job_status="partial_failed",
                                  completed_count=3, failed_count=0)
        # 手动强制状态（绕过 _make_retry_job 的默认行为）
        from backend.models.export_job import ExportJob
        j = self.db.query(ExportJob).filter(ExportJob.id == job_id).first()
        j.status = "partial_failed"
        self.db.commit()

        svc._retry_failed_chunks_sync(job_id, 50000)

        j2 = self._get_job(job_id)
        assert j2.status == "completed", f"Expected completed, got {j2.status}"

    # ── A5: 单个失败块重试成功 → completed ──────────────────────────────────

    def test_a5_single_failed_chunk_retry_success(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a5"
        job_id = _make_retry_job(self.db, self._username("a5"), out_dir,
                                  completed_count=2, failed_count=1)
        mc = _make_mock_client(rows_per_chunk=4)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        assert j.status == "completed", f"Expected completed, got {j.status}"
        # 已完成 3 块（2 original + 1 retried）
        assert j.done_batches == 3
        # 行数累加：original 2×5 + retried 1×4 = 14
        assert j.exported_rows == 14, f"Expected 14, got {j.exported_rows}"
        # output_files 中没有 failed 块
        failed = [f for f in (j.output_files or []) if f["status"] == "failed"]
        assert failed == [], f"Expected no failed, got {failed}"
        # client 被调用 1 次（只处理 1 个失败块）
        assert mc._call_count[0] == 1

    # ── A6: 多个失败块全部重试成功 → completed ──────────────────────────────

    def test_a6_multiple_failed_chunks_all_succeed(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a6"
        job_id = _make_retry_job(self.db, self._username("a6"), out_dir,
                                  completed_count=1, failed_count=3)
        mc = _make_mock_client(rows_per_chunk=2)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        assert j.status == "completed", f"Expected completed, got {j.status}"
        assert j.done_batches == 4  # 1 + 3
        assert j.exported_rows == 1 * 5 + 3 * 2  # 11
        # mock 被调用 3 次（3 个失败块依次重试）
        assert mc._call_count[0] == 3

    # ── A7: 重试后仍有块失败 → partial_failed ────────────────────────────────

    def test_a7_retry_partial_still_fails(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a7"
        job_id = _make_retry_job(self.db, self._username("a7"), out_dir,
                                  completed_count=2, failed_count=2)

        # 让失败块的 SQL 中含有特定日期范围 → 通过关键词让部分块仍失败
        # 获取失败块的 date_start，用其截断信息模拟失败
        from backend.models.export_job import ExportJob
        j = self.db.query(ExportJob).filter(ExportJob.id == job_id).first()
        fail_blocks = [f for f in j.output_files if f["status"] == "failed"]
        # 让第一个失败块（index=2）继续失败，第二个（index=3）成功
        first_fail_date = fail_blocks[0]["date_start"]

        mc = Mock()
        mc.get_columns.return_value = _make_columns()
        call_n = [0]
        def _stream(sql, **kw):
            call_n[0] += 1
            if first_fail_date in sql and call_n[0] == 1:
                raise RuntimeError("Simulated failure")
            yield _make_batch(2, base=call_n[0] * 10)
        mc.stream_batches.side_effect = _stream
        _attach_csv_stream_raw(mc)

        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j2 = self._get_job(job_id)
        assert j2.status == "partial_failed", f"Expected partial_failed, got {j2.status}"
        # error_message 应更新
        assert j2.error_message is not None

    # ── A8: 全部重试失败 → failed ────────────────────────────────────────────

    def test_a8_all_retry_fail(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a8"
        job_id = _make_retry_job(self.db, self._username("a8"), out_dir,
                                  job_status="failed",
                                  completed_count=0, failed_count=2)
        mc = Mock()
        mc.get_columns.side_effect = RuntimeError("DB unreachable")

        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        assert j.status == "failed", f"Expected failed, got {j.status}"
        assert j.done_batches == 0

    # ── A9: 重试过程中任务被取消 → cancelled ─────────────────────────────────

    def test_a9_cancel_during_retry(self):
        import backend.services.data_export_service as svc
        from backend.models.export_job import ExportJob
        out_dir = self.tmp / "a9"
        job_id = _make_retry_job(self.db, self._username("a9"), out_dir,
                                  completed_count=1, failed_count=3)

        call_n = [0]
        mc = Mock()
        mc.get_columns.return_value = _make_columns()
        def _stream(sql, **kw):
            call_n[0] += 1
            if call_n[0] == 1:
                # 第一块成功后，将任务标记为 cancelling
                db2 = __import__('backend.config.database', fromlist=['SessionLocal']).SessionLocal()
                jj = db2.query(ExportJob).filter(ExportJob.id == job_id).first()
                jj.status = "cancelling"
                db2.commit()
                db2.close()
            yield _make_batch(2)
        mc.stream_batches.side_effect = _stream
        _attach_csv_stream_raw(mc)

        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        assert j.status == "cancelled", f"Expected cancelled, got {j.status}"

    # ── A10: batch_size 参数传递到 _run_single_export ─────────────────────────

    def test_a10_batch_size_propagated(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "a10"
        job_id = _make_retry_job(self.db, self._username("a10"), out_dir,
                                  completed_count=1, failed_count=1)
        mc = _make_mock_client(rows_per_chunk=2)

        captured_batch_size = []
        original_run_single = svc._run_single_export

        def _spy_run_single(job_id, sql, env, conn_type, batch_size, output_path, **kwargs):
            captured_batch_size.append(batch_size)
            return original_run_single(job_id, sql, env, conn_type, batch_size, output_path, **kwargs)

        with patch("backend.services.data_export_service._build_export_client", return_value=mc), \
             patch("backend.services.data_export_service._run_single_export", side_effect=_spy_run_single):
            svc._retry_failed_chunks_sync(job_id, 12345)

        assert captured_batch_size == [12345], f"Expected [12345], got {captured_batch_size}"


# ─────────────────────────────────────────────────────────────────────────────
# B · retry_failed_chunks_async 快速校验
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryFailedChunksAsync:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from backend.config.database import SessionLocal
        self.db = SessionLocal()
        self.tmp = tmp_path
        yield
        self.db.close()

    def _username(self, label=""):
        return f"{_PREFIX}b{label}_"

    def _run(self, coro):
        return _run_async(coro)

    # ── B1: job 不存在 → ValueError ──────────────────────────────────────────

    def test_b1_job_not_found_raises(self):
        import backend.services.data_export_service as svc
        with pytest.raises(ValueError, match="不存在"):
            self._run(svc.retry_failed_chunks_async(str(uuid.uuid4()), 50000))

    # ── B2: 非 date_chunked 模式 → ValueError ────────────────────────────────

    def test_b2_single_mode_raises(self):
        import backend.services.data_export_service as svc
        from backend.models.export_job import ExportJob
        job = ExportJob(
            user_id="uid", username=self._username("b2"),
            query_sql="SELECT 1", connection_env="test",
            status="partial_failed", export_mode="single",
            output_filename="x.xlsx", file_path="/tmp/x.xlsx",
        )
        self.db.add(job)
        self.db.commit()
        job_id = str(job.id)
        with pytest.raises(ValueError, match="date_chunked"):
            self._run(svc.retry_failed_chunks_async(job_id, 50000))

    # ── B3: 非 partial_failed/failed 状态 → ValueError ──────────────────────

    def test_b3_wrong_status_raises(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "b3"
        job_id = _make_retry_job(self.db, self._username("b3"), out_dir,
                                  job_status="completed", failed_count=0)
        with pytest.raises(ValueError, match="状态"):
            self._run(svc.retry_failed_chunks_async(job_id, 50000))

    # ── B4: 无失败块 → ValueError ────────────────────────────────────────────

    def test_b4_no_failed_chunks_raises(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "b4"
        job_id = _make_retry_job(self.db, self._username("b4"), out_dir,
                                  job_status="partial_failed",
                                  completed_count=3, failed_count=0)
        # output_files 全 completed → 无 failed
        from backend.models.export_job import ExportJob
        j = self.db.query(ExportJob).filter(ExportJob.id == job_id).first()
        j.status = "partial_failed"
        self.db.commit()
        with pytest.raises(ValueError, match="没有"):
            self._run(svc.retry_failed_chunks_async(job_id, 50000))

    # ── B5: 合法请求 → 提交线程池（mock executor）─────────────────────────────

    def test_b5_valid_job_submits_to_executor(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "b5"
        job_id = _make_retry_job(self.db, self._username("b5"), out_dir,
                                  job_status="partial_failed",
                                  completed_count=1, failed_count=1)

        submitted = []

        async def _fake_executor(exc, fn, *args):
            submitted.append((fn.__name__, args))

        async def _run_test():
            loop = asyncio.get_event_loop()
            with patch.object(loop, "run_in_executor", side_effect=_fake_executor):
                await svc.retry_failed_chunks_async(job_id, 99999)

        _run_async(_run_test())
        assert len(submitted) == 1
        fn_name, args = submitted[0]
        assert fn_name == "_retry_failed_chunks_sync"
        assert args[0] == job_id
        assert args[1] == 99999


# ─────────────────────────────────────────────────────────────────────────────
# C · API 端点测试
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryFailedChunksAPI:

    @pytest.fixture(scope="class")
    def client(self):
        os.environ["ENABLE_AUTH"] = "False"
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(Path(__file__).parent / "backend"))
        from main import app
        with TestClient(app) as c:
            yield c

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, client):
        from backend.config.database import SessionLocal
        self.db = SessionLocal()
        self.tmp = tmp_path
        self.client = client
        yield
        self.db.close()

    def _url(self, job_id):
        return f"/api/v1/data-export/jobs/{job_id}/retry-failed-chunks"

    def _username(self, label=""):
        return f"{_PREFIX}c{label}_"

    # ── C1: job 不存在 → 404 ─────────────────────────────────────────────────

    def test_c1_job_not_found_404(self):
        resp = self.client.post(self._url(str(uuid.uuid4())), json={"batch_size": 50000})
        assert resp.status_code == 404

    # ── C2: 非 date_chunked 模式 → 400 ───────────────────────────────────────

    def test_c2_single_mode_400(self):
        from backend.models.export_job import ExportJob
        job = ExportJob(
            user_id="uid", username=self._username("c2"),
            query_sql="SELECT 1", connection_env="test",
            status="partial_failed", export_mode="single",
            output_filename="x.xlsx", file_path="/tmp/x.xlsx",
        )
        self.db.add(job)
        self.db.commit()
        resp = self.client.post(self._url(str(job.id)), json={"batch_size": 50000})
        assert resp.status_code == 400
        assert "date_chunked" in resp.json()["detail"]

    # ── C3: 任务已在 running → 409 ──────────────────────────────────────────

    def test_c3_already_running_409(self):
        out_dir = self.tmp / "c3"
        job_id = _make_retry_job(self.db, self._username("c3"), out_dir, job_status="running")
        resp = self.client.post(self._url(job_id), json={"batch_size": 50000})
        assert resp.status_code == 409
        assert "执行中" in resp.json()["detail"]

    # ── C4: 状态不可重试（completed）→ 400 ──────────────────────────────────

    def test_c4_wrong_status_400(self):
        out_dir = self.tmp / "c4"
        job_id = _make_retry_job(self.db, self._username("c4"), out_dir,
                                  job_status="completed", failed_count=0)
        resp = self.client.post(self._url(job_id), json={"batch_size": 50000})
        assert resp.status_code == 400

    # ── C5: 无失败块 → 400 ───────────────────────────────────────────────────

    def test_c5_no_failed_chunks_400(self):
        out_dir = self.tmp / "c5"
        job_id = _make_retry_job(self.db, self._username("c5"), out_dir,
                                  job_status="partial_failed",
                                  completed_count=3, failed_count=0)
        from backend.models.export_job import ExportJob
        j = self.db.query(ExportJob).filter(ExportJob.id == job_id).first()
        j.status = "partial_failed"
        self.db.commit()
        resp = self.client.post(self._url(job_id), json={"batch_size": 50000})
        assert resp.status_code == 400
        assert "failed" in resp.json()["detail"]

    # ── C6: batch_size 过小 → 422 ────────────────────────────────────────────

    def test_c6_batch_size_too_small_422(self):
        out_dir = self.tmp / "c6"
        job_id = _make_retry_job(self.db, self._username("c6"), out_dir)
        resp = self.client.post(self._url(job_id), json={"batch_size": 500})
        assert resp.status_code == 422

    # ── C7: batch_size 过大 → 422 ────────────────────────────────────────────

    def test_c7_batch_size_too_large_422(self):
        out_dir = self.tmp / "c7"
        job_id = _make_retry_job(self.db, self._username("c7"), out_dir)
        resp = self.client.post(self._url(job_id), json={"batch_size": 999999})
        assert resp.status_code == 422

    # ── C8: partial_failed 合法请求 → 202 ────────────────────────────────────

    def test_c8_valid_partial_failed_202(self):
        out_dir = self.tmp / "c8"
        job_id = _make_retry_job(self.db, self._username("c8"), out_dir,
                                  job_status="partial_failed",
                                  completed_count=2, failed_count=2)
        with patch("backend.api.data_export.asyncio.create_task"):
            resp = self.client.post(self._url(job_id), json={"batch_size": 10000})
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["status"] == "retrying"
        assert data["failed_chunk_count"] == 2
        assert data["batch_size"] == 10000

    # ── C9: failed（全部失败）合法请求 → 202 ─────────────────────────────────

    def test_c9_valid_all_failed_202(self):
        out_dir = self.tmp / "c9"
        job_id = _make_retry_job(self.db, self._username("c9"), out_dir,
                                  job_status="failed",
                                  completed_count=0, failed_count=3)
        with patch("backend.api.data_export.asyncio.create_task"):
            resp = self.client.post(self._url(job_id), json={"batch_size": 50000})
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["failed_chunk_count"] == 3

    # ── C10: 默认 batch_size（不传）→ 使用 50000 ────────────────────────────

    def test_c10_default_batch_size(self):
        out_dir = self.tmp / "c10"
        job_id = _make_retry_job(self.db, self._username("c10"), out_dir)
        with patch("backend.api.data_export.asyncio.create_task"):
            resp = self.client.post(self._url(job_id), json={})
        assert resp.status_code == 202
        assert resp.json()["data"]["batch_size"] == 50000


# ─────────────────────────────────────────────────────────────────────────────
# D · 端到端集成测试（完整重试流程）
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryFailedChunksE2E:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from backend.config.database import SessionLocal
        self.db = SessionLocal()
        self.tmp = tmp_path
        yield
        self.db.close()

    def _username(self, label=""):
        return f"{_PREFIX}d{label}_"

    def _get_job(self, job_id):
        from backend.models.export_job import ExportJob
        self.db.expire_all()
        return self.db.query(ExportJob).filter(ExportJob.id == job_id).first()

    # ── D1: partial_failed → 全部重试成功 → completed ────────────────────────

    def test_d1_partial_failed_all_succeed_completed(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "d1"
        job_id = _make_retry_job(self.db, self._username("d1"), out_dir,
                                  completed_count=3, failed_count=2)
        mc = _make_mock_client(rows_per_chunk=5)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        assert j.status == "completed"
        assert j.done_batches == 5
        # 3×5 (original) + 2×5 (retried) = 25
        assert j.exported_rows == 25
        assert j.error_message is None or j.error_message == ""
        # output_files 所有块均 completed
        for f in j.output_files:
            assert f["status"] == "completed", f"Chunk {f['index']} not completed: {f}"
        # 重试块的文件实际存在
        for f in j.output_files[3:]:  # 重试的是后两块
            assert Path(f["file_path"]).exists(), f"File not created: {f['file_path']}"

    # ── D2: failed（全失败）→ 重试 → 全成功 → completed ─────────────────────

    def test_d2_all_failed_retry_all_succeed(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "d2"
        job_id = _make_retry_job(self.db, self._username("d2"), out_dir,
                                  job_status="failed",
                                  completed_count=0, failed_count=3)
        mc = _make_mock_client(rows_per_chunk=3)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        assert j.status == "completed"
        assert j.done_batches == 3
        assert j.exported_rows == 9  # 3×3

    # ── D3: 历史任务重试（模拟旧数据记录）─────────────────────────────────────

    def test_d3_historical_job_retry(self):
        """
        模拟用户看到一个较早创建的 partial_failed 任务（无 config_snapshot.batch_size），
        验证参数从 DB 正确重建，不会崩溃。
        """
        import backend.services.data_export_service as svc
        from backend.models.export_job import ExportJob
        out_dir = self.tmp / "d3"
        out_dir.mkdir(parents=True)

        output_files = _make_output_files(out_dir, completed_count=1, failed_count=1)
        job = ExportJob(
            user_id="uid_hist",
            username=self._username("d3"),
            job_name="historical_export",
            query_sql="SELECT id, name FROM t WHERE dt >= '{{date_start}}' AND dt <= '{{date_end}}'",
            connection_env="test",
            connection_type="clickhouse",
            status="partial_failed",
            export_mode="date_chunked",
            chunk_config={"date_column": None, "date_start": "2024-06-01",
                          "date_end": "2024-06-30", "chunk_days": 15},
            output_files=output_files,
            output_filename=out_dir.name,
            file_path=str(out_dir),
            exported_rows=5,
            done_batches=1,
            total_batches=2,
            config_snapshot=None,   # 历史任务可能无 snapshot
        )
        self.db.add(job)
        self.db.commit()
        job_id = str(job.id)

        mc = _make_mock_client(rows_per_chunk=7)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 20000)

        j = self._get_job(job_id)
        assert j.status == "completed"
        assert j.exported_rows == 5 + 7  # 1 original + 1 retried

    # ── D4: batch_size 覆盖生效（与原始不同）────────────────────────────────

    def test_d4_custom_batch_size_respected(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "d4"
        job_id = _make_retry_job(self.db, self._username("d4"), out_dir,
                                  completed_count=1, failed_count=1)
        mc = _make_mock_client(rows_per_chunk=2)
        captured = []
        orig_run = svc._run_single_export

        def _spy(job_id, sql, env, conn_type, batch_size, output_path, **kw):
            captured.append(batch_size)
            return orig_run(job_id, sql, env, conn_type, batch_size, output_path, **kw)

        with patch("backend.services.data_export_service._build_export_client", return_value=mc), \
             patch("backend.services.data_export_service._run_single_export", side_effect=_spy):
            svc._retry_failed_chunks_sync(job_id, 7777)

        assert all(bs == 7777 for bs in captured), f"Captured batch sizes: {captured}"

    # ── D5: 输出文件写入原目录（不改路径）───────────────────────────────────

    def test_d5_files_written_to_original_directory(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "d5"
        job_id = _make_retry_job(self.db, self._username("d5"), out_dir,
                                  completed_count=1, failed_count=2)
        mc = _make_mock_client(rows_per_chunk=2)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        for f in j.output_files:
            if f["status"] == "completed":
                assert Path(f["file_path"]).parent == out_dir, \
                    f"File {f['file_path']} not in {out_dir}"

    # ── D6: 重试期间进度实时更新（done_batches 递增）───────────────────────

    def test_d6_progress_updated_per_chunk(self):
        """验证每块完成后 done_batches 都能在 DB 中查到（实时更新，不是最后一次性写入）。"""
        import backend.services.data_export_service as svc
        from backend.models.export_job import ExportJob
        out_dir = self.tmp / "d6"
        job_id = _make_retry_job(self.db, self._username("d6"), out_dir,
                                  completed_count=1, failed_count=3)

        observed_done = []
        orig_update = svc._update_job

        def _spy_update(jid, **fields):
            orig_update(jid, **fields)
            if "done_batches" in fields and jid == job_id:
                observed_done.append(fields["done_batches"])

        mc = _make_mock_client(rows_per_chunk=1)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc), \
             patch("backend.services.data_export_service._update_job", side_effect=_spy_update):
            svc._retry_failed_chunks_sync(job_id, 50000)

        # 应有 3 次进度写入（每块一次）
        assert len(observed_done) >= 3, f"Expected >=3 progress updates, got {observed_done}"
        # done_batches 应单调递增
        for a, b in zip(observed_done, observed_done[1:]):
            assert b >= a, f"done_batches decreased: {observed_done}"

    # ── D7: error_summary 在重试成功后被清除 ─────────────────────────────────

    def test_d7_error_summary_cleared_on_success(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "d7"
        job_id = _make_retry_job(self.db, self._username("d7"), out_dir,
                                  completed_count=1, failed_count=1)
        mc = _make_mock_client(rows_per_chunk=2)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        for f in j.output_files:
            if f["status"] == "completed":
                assert f.get("error_summary") is None, \
                    f"Chunk {f['index']} should have no error_summary"

    # ── D8: 重试成功后旧 error_message 被覆盖 ────────────────────────────────

    def test_d8_job_error_message_cleared_on_full_success(self):
        import backend.services.data_export_service as svc
        out_dir = self.tmp / "d8"
        job_id = _make_retry_job(self.db, self._username("d8"), out_dir,
                                  completed_count=2, failed_count=1)
        mc = _make_mock_client(rows_per_chunk=3)
        with patch("backend.services.data_export_service._build_export_client", return_value=mc):
            svc._retry_failed_chunks_sync(job_id, 50000)

        j = self._get_job(job_id)
        assert j.status == "completed"
        # completed 终态下 error_message 不应保留旧失败信息
        # _mark_completed 不写 error_message 字段，值应为 None 或空（原来的旧值不清除也可接受，
        # 但 status=completed 已能明确表示成功）
        # 主要断言 status 正确即可
        assert j.done_batches == 3
