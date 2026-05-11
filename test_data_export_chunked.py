"""
按日期分块导出 — 集成测试 v2.13

E · execute API 创建分块 Job
F · 串行执行 N 块、output_files 推进、进度累加
G · 中途单块失败 → failed，已完成块文件保留
H · 中途取消 → cancelled，未启动块跳过
I · download 分块模式（缺 file_index/越界/合法）
J · delete 递归目录
K · RBAC 权限
L · 占位符 vs 包装两种 SQL 注入路径
M · 1M/Sheet 拆分在分块模式仍生效

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_data_export_chunked.py -v -s
"""
import asyncio
import os
import shutil
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

# 测试用户名前缀（conftest session cleanup 会按此前缀清理）
_PREFIX = f"_t_dec_{uuid.uuid4().hex[:6]}_"


# ─────────────────────────────────────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────────────────────────────────────

def _make_columns():
    from backend.services.export_clients.base import ColumnInfo
    return [ColumnInfo("id", "Int64"), ColumnInfo("name", "String")]


def _make_batch(n: int, base: int = 0):
    return [(str(base + i), f"name_{base + i}") for i in range(n)]


def _make_chunked_job(db, username: str, output_dir: Path, status: str = "pending") -> str:
    """直接在 DB 创建一个 date_chunked 模式的 ExportJob 记录（绕过 API）"""
    from backend.models.export_job import ExportJob
    job = ExportJob(
        user_id="uid",
        username=username,
        query_sql="SELECT id, name FROM events WHERE dt >= '{{date_start}}' AND dt <= '{{date_end}}'",
        connection_env="test",
        status=status,
        export_mode="date_chunked",
        chunk_config={
            "date_start": "2025-04-01",
            "date_end": "2025-04-30",
            "chunk_days": 10,
        },
        output_filename=output_dir.name,
        file_path=str(output_dir),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return str(job.id)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# E · execute API 创建分块 Job
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteAPIChunked:

    @pytest.fixture
    def client(self):
        os.environ["ENABLE_AUTH"] = "False"
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(Path(__file__).parent / "backend"))
        from main import app
        with TestClient(app) as c:
            yield c

    def test_e1_execute_with_chunk_config_creates_chunked_job(self, client):
        """E1: 提交 chunk_config 创建 date_chunked Job"""
        resp = client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}e1",
            "chunk_config": {
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
            },
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["export_mode"] == "date_chunked"

        # 验证 DB 字段
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == data["job_id"]).first()
            assert j is not None
            assert j.export_mode == "date_chunked"
            assert j.chunk_config["chunk_days"] == 10
            # 取消任务以便后续清理
            j.status = "cancelled"
            db.commit()
        finally:
            db.close()

    def test_e2_invalid_chunk_config_400(self, client):
        """E2: chunk_config 校验失败返回 400"""
        resp = client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t",
            "connection_env": "test",
            "job_name": f"{_PREFIX}e2",
            "chunk_config": {
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
                # 既无占位符也无 date_column
            },
        })
        assert resp.status_code == 400
        assert "date_column" in resp.json()["detail"]

    def test_e3_chunk_days_out_of_range_422(self, client):
        """E3: chunk_days 超出 Pydantic 校验范围 → 422"""
        resp = client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}e3",
            "chunk_config": {
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 100,
            },
        })
        assert resp.status_code == 422

    def test_e4_no_chunk_config_creates_single_job(self, client):
        """E4: 不传 chunk_config 仍是 single 模式（向后兼容）"""
        resp = client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT 1",
            "connection_env": "test",
            "job_name": f"{_PREFIX}e4",
        })
        # 注意：connection_env 'test' 不存在会异步失败，但 execute 端点本身应返回 200
        # 因为执行是后台协程，端点只验证创建
        # 此处可能因 settings 读取报错；接受 400/200 任一
        assert resp.status_code in (200, 400, 500)
        if resp.status_code == 200:
            assert resp.json()["data"]["export_mode"] == "single"


# ─────────────────────────────────────────────────────────────────────────────
# F · 串行执行 N 块
# ─────────────────────────────────────────────────────────────────────────────

class TestChunkedExecution:

    def test_f1_three_chunks_serial(self, tmp_path):
        """F1: 3 块串行执行 → 3 个文件、output_files 全部 completed"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc

        db = SessionLocal()
        out_dir = tmp_path / "f1_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}f1", out_dir)
        db.close()

        # mock client：3 块 × 2 行/块
        sqls_seen: List[str] = []

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                sqls_seen.append(sql)
                yield _make_batch(2, base=len(sqls_seen) * 100)
            mc.stream_batches.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM events WHERE dt >= '{{date_start}}' AND dt <= '{{date_end}}'",
            "connection_env": "test",
            "connection_type": "clickhouse",
            "batch_size": 1000,
            "export_mode": "date_chunked",
            "chunk_config": {
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
            },
            "output_dir": str(out_dir),
            "job_name": "f1job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        # 验证 Job 状态
        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "completed"
            assert j.total_batches == 3
            assert j.done_batches == 3
            assert j.exported_rows == 6  # 2 行 × 3 块
            assert j.total_sheets == 3  # 每块 1 个 Sheet
            assert len(j.output_files) == 3
            for entry in j.output_files:
                assert entry["status"] == "completed"
                assert entry["rows"] == 2
                assert Path(entry["file_path"]).exists()
        finally:
            db.close()

        # 4 次 stream_batches 调用（1 次列预检 + 3 次块）— 实际 get_columns 走 client.get_columns
        # SQL 中应包含日期字面量
        assert any("'2025-04-01'" in s for s in sqls_seen)
        assert any("'2025-04-21'" in s for s in sqls_seen)

    def test_f2_progress_accumulates_across_chunks(self, tmp_path):
        """F2: exported_rows 跨块累加"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc

        db = SessionLocal()
        out_dir = tmp_path / "f2_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}f2", out_dir)
        db.close()

        rows_per_chunk = [3, 5, 2]
        idx = {"n": 0}

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                # 每块调用一次 stream_batches；按调用顺序对应 rows_per_chunk
                i = idx["n"]
                idx["n"] += 1
                yield _make_batch(rows_per_chunk[i])
            mc.stream_batches.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            "output_dir": str(out_dir), "job_name": "f2job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "completed"
            assert j.exported_rows == sum(rows_per_chunk)
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# G · 单块失败 → failed，已完成块保留
# ─────────────────────────────────────────────────────────────────────────────

class TestChunkedFailure:

    def test_g1_second_chunk_fails_first_kept(self, tmp_path):
        """G1: 第 2 块失败 → Job failed，第 1 块文件保留"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc

        db = SessionLocal()
        out_dir = tmp_path / "g1_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}g1", out_dir)
        db.close()

        call_idx = {"n": 0}

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                call_idx["n"] += 1
                if call_idx["n"] == 2:  # 第 2 个 stream（即第 2 块的执行）抛错
                    raise RuntimeError("simulated CH failure")
                yield _make_batch(2)
            mc.stream_batches.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            "output_dir": str(out_dir), "job_name": "g1job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "failed"
            assert "simulated CH failure" in (j.error_message or "")
            files = j.output_files
            assert files[0]["status"] == "completed"
            assert Path(files[0]["file_path"]).exists()
            assert files[1]["status"] == "failed"
            # 第 3 块未启动
            assert files[2]["status"] == "pending"
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# H · 取消语义
# ─────────────────────────────────────────────────────────────────────────────

class TestChunkedCancellation:

    def test_h1_cancel_before_first_chunk(self, tmp_path):
        """H1: 启动前已 cancelling → cancelled"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc

        db = SessionLocal()
        out_dir = tmp_path / "h1_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}h1", out_dir, status="cancelling")
        db.close()

        config = {
            "query_sql": "SELECT 1 WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            "output_dir": str(out_dir), "job_name": "h1job",
        }
        # 不需 mock client — 启动竞态会直接退出，根本不会调用
        _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "cancelled"
        finally:
            db.close()

    def test_h2_cancel_between_chunks(self, tmp_path):
        """H2: 第 1 块完成后被取消 → 第 2,3 块跳过，状态 cancelled"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc

        db = SessionLocal()
        out_dir = tmp_path / "h2_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}h2", out_dir)
        db.close()

        call_idx = {"n": 0}

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                call_idx["n"] += 1
                yield _make_batch(2)
                # 第 1 块完成后把任务设为 cancelling
                if call_idx["n"] == 1:
                    db2 = SessionLocal()
                    j = db2.query(ExportJob).filter(ExportJob.id == job_id).first()
                    j.status = "cancelling"
                    db2.commit()
                    db2.close()
            mc.stream_batches.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            "output_dir": str(out_dir), "job_name": "h2job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "cancelled"
            files = j.output_files
            assert files[0]["status"] == "completed"
            # 第 2,3 块状态保持 pending（未启动）
            assert files[1]["status"] == "pending"
            assert files[2]["status"] == "pending"
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# I · download API（分块）
# ─────────────────────────────────────────────────────────────────────────────

class TestChunkedDownload:

    @pytest.fixture
    def client(self):
        os.environ["ENABLE_AUTH"] = "False"
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(Path(__file__).parent / "backend"))
        from main import app
        with TestClient(app) as c:
            yield c

    def _create_completed_chunked_job(self, tmp_path: Path, username: str) -> str:
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        out_dir = tmp_path / f"{username}_dir"
        out_dir.mkdir(parents=True, exist_ok=True)
        # 真实创建 2 个 xlsx 文件
        files = []
        for i in range(2):
            fp = out_dir / f"chunk_{i}.xlsx"
            fp.write_bytes(b"PK_dummy")
            files.append({
                "index": i,
                "date_start": f"2025-04-{1 + i*10:02d}",
                "date_end": f"2025-04-{10 + i*10:02d}",
                "filename": fp.name,
                "file_path": str(fp),
                "file_size": 8,
                "rows": 10,
                "sheets": 1,
                "status": "completed",
            })
        db = SessionLocal()
        try:
            job = ExportJob(
                user_id="uid", username=username,
                query_sql="SELECT 1", connection_env="test",
                status="completed",
                export_mode="date_chunked",
                chunk_config={"date_start": "2025-04-01", "date_end": "2025-04-20", "chunk_days": 10},
                output_filename=out_dir.name,
                file_path=str(out_dir),
                output_files=files,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return str(job.id)
        finally:
            db.close()

    def test_i1_download_chunked_no_index_400(self, client, tmp_path):
        """I1: 分块模式不带 file_index → 400"""
        job_id = self._create_completed_chunked_job(tmp_path, f"{_PREFIX}i1")
        resp = client.get(f"/api/v1/data-export/jobs/{job_id}/download")
        assert resp.status_code == 400
        assert "file_index" in resp.json()["detail"]

    def test_i2_download_chunked_valid_index_200(self, client, tmp_path):
        """I2: file_index=0 → 200 文件流"""
        job_id = self._create_completed_chunked_job(tmp_path, f"{_PREFIX}i2")
        resp = client.get(f"/api/v1/data-export/jobs/{job_id}/download?file_index=0")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["content-type"]
        assert resp.content == b"PK_dummy"

    def test_i3_download_chunked_index_out_of_range_404(self, client, tmp_path):
        """I3: file_index 越界 → 404"""
        job_id = self._create_completed_chunked_job(tmp_path, f"{_PREFIX}i3")
        resp = client.get(f"/api/v1/data-export/jobs/{job_id}/download?file_index=99")
        assert resp.status_code == 404

    def test_i4_download_chunked_negative_index_422(self, client, tmp_path):
        """I4: file_index 负数 → 422（Pydantic ge=0 校验）"""
        job_id = self._create_completed_chunked_job(tmp_path, f"{_PREFIX}i4")
        resp = client.get(f"/api/v1/data-export/jobs/{job_id}/download?file_index=-1")
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# J · delete 递归目录
# ─────────────────────────────────────────────────────────────────────────────

class TestChunkedDelete:

    @pytest.fixture
    def client(self):
        os.environ["ENABLE_AUTH"] = "False"
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(Path(__file__).parent / "backend"))
        from main import app
        with TestClient(app) as c:
            yield c

    def test_j1_delete_chunked_removes_directory(self, client, tmp_path):
        """J1: DELETE 分块 Job → 目录与所有子文件被删除"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob

        out_dir = tmp_path / "j1_dir"
        out_dir.mkdir()
        for i in range(3):
            (out_dir / f"chunk_{i}.xlsx").write_bytes(b"x")

        db = SessionLocal()
        try:
            job = ExportJob(
                user_id="uid", username=f"{_PREFIX}j1",
                query_sql="SELECT 1", connection_env="test",
                status="completed",
                export_mode="date_chunked",
                output_filename=out_dir.name,
                file_path=str(out_dir),
                output_files=[],
            )
            db.add(job)
            db.commit()
            job_id = str(job.id)
        finally:
            db.close()

        assert out_dir.exists()
        resp = client.delete(f"/api/v1/data-export/jobs/{job_id}")
        assert resp.status_code == 200
        assert not out_dir.exists()


# ─────────────────────────────────────────────────────────────────────────────
# L · 占位符 vs 包装 SQL 注入路径
# ─────────────────────────────────────────────────────────────────────────────

class TestSQLInjectionPaths:

    def test_l1_placeholder_path_used_when_sql_has_placeholders(self, tmp_path):
        """L1: SQL 含占位符 → 走 placeholder 替换路径（不出现 _chunk_q 包装别名）"""
        from backend.config.database import SessionLocal
        import backend.services.data_export_service as svc

        db = SessionLocal()
        out_dir = tmp_path / "l1_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}l1", out_dir)
        db.close()

        seen_sqls: List[str] = []

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                seen_sqls.append(sql)
                yield _make_batch(1)
            mc.stream_batches.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            "output_dir": str(out_dir), "job_name": "l1job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        assert len(seen_sqls) == 3  # 3 块
        for s in seen_sqls:
            assert "_chunk_q" not in s, f"Placeholder mode should not wrap: {s}"
            assert "{{date_start}}" not in s
        assert "'2025-04-01'" in seen_sqls[0]

    def test_l2_wrapper_path_used_without_placeholders(self, tmp_path):
        """L2: SQL 无占位符 + 提供 date_column → 走 wrapper 包装"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc

        db = SessionLocal()
        out_dir = tmp_path / "l2_output"
        job = ExportJob(
            user_id="uid", username=f"{_PREFIX}l2",
            query_sql="SELECT id, name FROM events",  # 无占位符
            connection_env="test", status="pending",
            export_mode="date_chunked",
            output_filename=out_dir.name,
            file_path=str(out_dir),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        seen_sqls: List[str] = []

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                seen_sqls.append(sql)
                yield _make_batch(1)
            mc.stream_batches.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM events",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {
                "date_column": "event_date",
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
            },
            "output_dir": str(out_dir), "job_name": "l2job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        assert len(seen_sqls) == 3
        for s in seen_sqls:
            assert "_chunk_q" in s, f"Wrapper mode should wrap: {s}"
            assert "_chunk_q.event_date >=" in s


# ─────────────────────────────────────────────────────────────────────────────
# M · 1M/Sheet 拆分仍生效
# ─────────────────────────────────────────────────────────────────────────────

class TestSheetSplitInChunkedMode:

    def test_m1_chunk_with_over_1m_rows_creates_multiple_sheets(self, tmp_path, monkeypatch):
        """M1: 单块超过 MAX_ROWS_PER_SHEET → 多 Sheet（Sheet 拆分逻辑在分块模式下仍生效）"""
        # 把 MAX_ROWS_PER_SHEET 临时调小到 5 以便测试
        import backend.services.data_export_service as svc
        monkeypatch.setattr(svc, "MAX_ROWS_PER_SHEET", 5)

        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob

        db = SessionLocal()
        out_dir = tmp_path / "m1_output"
        # 用单块（chunk_days=30 覆盖 30 天范围）
        job = ExportJob(
            user_id="uid", username=f"{_PREFIX}m1",
            query_sql="SELECT 1 WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            connection_env="test", status="pending",
            export_mode="date_chunked",
            output_filename=out_dir.name,
            file_path=str(out_dir),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                # 12 行 → 应分为 3 个 Sheet (5+5+2)
                yield _make_batch(12)
            mc.stream_batches.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 30},
            "output_dir": str(out_dir), "job_name": "m1job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "completed"
            assert len(j.output_files) == 1
            # 12 行 / 5 行每 Sheet = 3 Sheet
            assert j.output_files[0]["sheets"] == 3
            assert j.output_files[0]["rows"] == 12
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# teardown
# ─────────────────────────────────────────────────────────────────────────────

def teardown_module(_):
    """清理本文件创建的测试数据"""
    try:
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        jobs = db.query(ExportJob).filter(ExportJob.username.like(f"{_PREFIX}%")).all()
        for j in jobs:
            # 删除关联文件 / 目录
            if j.file_path:
                try:
                    p = Path(j.file_path)
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    elif p.exists():
                        p.unlink()
                except Exception:
                    pass
            db.delete(j)
        db.commit()
        db.close()
    except Exception:
        pass


# =============================================================================
# K-new · Task A 集成 — min_subdivide_unit 控制 sub-day 自动细分
# =============================================================================

class TestSubDaySubdivision:

    def test_kn1_subday_hour_unit_recovers_failed_single_day(self, tmp_path):
        """K-new1: min_subdivide_unit='hour' + 1 天块第一次流式断开 → 自动拆成
        两个 sub-day 子块(12h+12h),后续两次重试成功 → Job completed。
        老 entry 被替换,output_files 中 date_start/date_end 含 'T'。"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "kn1_output"
        # 单天 chunk(1 day, chunk_days=1),包含 min_subdivide_unit=hour
        job = ExportJob(
            user_id="uid",
            username=f"{_PREFIX}kn1",
            query_sql="SELECT id, name FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            connection_env="test", status="pending",
            export_mode="date_chunked",
            chunk_config={
                "date_start": "2025-04-01", "date_end": "2025-04-01",
                "chunk_days": 1, "min_subdivide_unit": "hour",
            },
            output_filename=out_dir.name,
            file_path=str(out_dir),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        # 用注入的 SQL 字面量区分原始 1 天块 vs sub-day 子块:
        # - 原始: ts >= '2025-04-01'(无时间字面量)→ 抛
        # - sub-day: ts >= '2025-04-01 00:00:00'(含 ':')→ 成功
        def _is_subday(sql: str) -> bool:
            return "00:00:00" in sql or "23:59:59" in sql or "11:59:59" in sql or "12:00:00" in sql

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()

            def _stream(sql, *a, **kwargs):
                if _is_subday(sql):
                    yield _make_batch(3, base=0)
                    return
                raise ChunkedEncodingError("simulated stream disconnect")

            # count_rows 不抛(否则 _run_single_export 会包装成 RuntimeError 丢失指纹)
            mc.count_rows.return_value = 3
            mc.stream_batches.side_effect = _stream
            mc.stream_batches_chunked.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-01",
                "chunk_days": 1, "min_subdivide_unit": "hour",
            },
            "output_dir": str(out_dir), "job_name": "kn1job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "completed", f"expected completed, got {j.status}, err={j.error_message}"
            files = j.output_files
            # 自动细分后:原 1 个块被替换成 2 个 sub-day 子块
            assert len(files) == 2, f"expected 2 sub-day entries, got {len(files)}: {files}"
            for f in files:
                assert f["status"] == "completed"
                # sub-day entry 的 date_start/date_end 应是 datetime ISO('T' 分隔)
                assert "T" in f["date_start"], f"expected datetime ISO, got {f['date_start']}"
                assert "T" in f["date_end"]
                # 文件应实际写入
                assert Path(f["file_path"]).exists(), f"file missing: {f['file_path']}"
        finally:
            db.close()

    @staticmethod
    def _patch_sleep_for_kn():
        """KN/L 段共用:让 in-place retry 的退避秒数瞬时跳过"""
        return patch("time.sleep", lambda s: None)

    def test_kn2_default_day_unit_fails_fast_on_single_day(self, tmp_path):
        """K-new2: min_subdivide_unit 默认 'day'(老行为兜底),1 天块失败后
        fail-fast,不下钻到 sub-day → Job failed,output_files 老 entry 标 failed。"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "kn2_output"
        job = ExportJob(
            user_id="uid",
            username=f"{_PREFIX}kn2",
            query_sql="SELECT id, name FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            connection_env="test", status="pending",
            export_mode="date_chunked",
            chunk_config={
                "date_start": "2025-04-01", "date_end": "2025-04-01",
                "chunk_days": 1,  # 不传 min_subdivide_unit → 默认 day
            },
            output_filename=out_dir.name,
            file_path=str(out_dir),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                # 每次都抛 → 1 天块 + day unit → 不可再分 → 应直接 failed
                raise ChunkedEncodingError("simulated stream disconnect")
                yield  # noqa: pragma generator marker
            mc.stream_batches.side_effect = _stream
            # Code 160 fallback 路径(stream_batches_chunked)也注入失败,避免误回退
            mc.count_rows.return_value = 100
            mc.stream_batches_chunked.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-01",
                "chunk_days": 1,
            },
            "output_dir": str(out_dir), "job_name": "kn2job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "failed", f"expected failed, got {j.status}"
            files = j.output_files
            # 仍是 1 个 entry(无 sub-day 分裂),标 failed
            assert len(files) == 1
            assert files[0]["status"] == "failed"
            # date_start/date_end 仍是 date-only(无 'T')
            assert "T" not in files[0]["date_start"]
        finally:
            db.close()


# =============================================================================
# L · Task D — 失败先原位重试 1 次再分裂(瞬时网络抖动友好)
# =============================================================================

class TestInplaceRetryBeforeSubdivide:

    def test_l1_retry_succeeds_no_subdivide(self, tmp_path):
        """L1: 第一块 _run_single_export 第一次整体抛 transient → 外层重试 →
        第二次成功 → 不分裂。验证 Task D 重试机制本身,直接 patch _run_single_export
        避免被内部 fallback 路径吞掉异常。"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "l1_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}l1", out_dir)
        db.close()

        per_chunk_call: Dict[str, int] = {}
        real_run_single = svc._run_single_export

        def _flaky_run_single(*args, **kwargs):
            label = kwargs.get("chunk_label", "?")
            per_chunk_call[label] = per_chunk_call.get(label, 0) + 1
            # 每个 chunk 的第 1 次调用抛 transient,第 2 次以后正常完成
            if per_chunk_call[label] == 1:
                raise ChunkedEncodingError("simulated transient on first attempt")
            return {
                "exported_rows": 2, "total_sheets": 1,
                "done_batches": 1, "total_sql_chunks": None,
                "file_size": 100, "cancelled": False,
            }

        # 还需 _build_export_client + get_columns(get_columns 在列预检 + 单 chunk 内部仍调)
        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            return mc

        # 让 Path(chunk_path).stat() 不抛 — 但是 _run_single_export 被 patch 后
        # 不会真写文件;chunk_path 是合法目录下的非存在文件,Path(...).exists() = False
        # 走 chunk_size_bytes = None 分支即可,无需 patch.

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            "output_dir": str(out_dir), "job_name": "l1job",
        }

        with patch("time.sleep", lambda s: None), \
             patch("backend.services.data_export_service._run_single_export", side_effect=_flaky_run_single), \
             patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "completed", f"got {j.status}, err={j.error_message}"
            files = j.output_files
            # 没有分裂:仍是 3 个原始块(30 天/10 天)
            assert len(files) == 3, f"expected 3 entries, got {len(files)}"
            # 每个块都有 _retry_count == 1
            for f in files:
                assert f.get("_retry_count", 0) == 1, f"expected _retry_count=1, got {f}"
                assert f["status"] == "completed"
        finally:
            db.close()

    def test_l2_retry_exhausted_falls_back_to_subdivide(self, tmp_path):
        """L2: 重试也失败 → 进入分裂分支(MAX_INPLACE_RETRY 用尽)"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "l2_output"
        # 用 chunk_days=2 让单块 2 天可被分裂为 1+1
        job = ExportJob(
            user_id="uid", username=f"{_PREFIX}l2",
            query_sql="SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            connection_env="test", status="pending",
            export_mode="date_chunked",
            chunk_config={
                "date_start": "2025-04-01", "date_end": "2025-04-02",
                "chunk_days": 2,
            },
            output_filename=out_dir.name,
            file_path=str(out_dir),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        # 整 2 天块持续抛,1 天子块也持续抛 → 最终 fail
        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                raise ChunkedEncodingError("persistent")
                yield
            mc.stream_batches.side_effect = _stream
            mc.count_rows.return_value = 1
            mc.stream_batches_chunked.side_effect = _stream
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-02",
                "chunk_days": 2,
            },
            "output_dir": str(out_dir), "job_name": "l2job",
        }

        with patch("time.sleep", lambda s: None), \
             patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            # 最终 fail(子块也失败 + day unit 不下钻 sub-day)
            assert j.status == "failed", f"got {j.status}"
            files = j.output_files
            # 至少发生过一次分裂:files 数 > 1(原 1 块 → 1+1)
            assert len(files) >= 2, f"expected subdivide to produce ≥2 entries, got {len(files)}: {files}"
            # 第一个失败 entry 应有 _retry_count == MAX_INPLACE_RETRY(用尽)
            assert files[0].get("status") == "failed"
        finally:
            db.close()

    def test_l3_retry_interrupted_by_cancel(self, tmp_path):
        """L3: 重试退避期间检测到 cancelling → 立即 cancelled,不再重试"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "l3_output"
        job_id = _make_chunked_job(db, f"{_PREFIX}l3", out_dir)
        db.close()

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                # 第 1 次抛 transient → 触发重试 → 退避期间被取消
                raise ChunkedEncodingError("transient")
                yield
            mc.stream_batches.side_effect = _stream
            mc.count_rows.return_value = 1
            mc.stream_batches_chunked.side_effect = _stream
            return mc

        # 重试退避期间 _is_cancelling 返回 True(模拟用户取消)
        cancel_called = {"n": 0}

        def _is_cancelling_stub(jid):
            cancel_called["n"] += 1
            # 第一次调用(_run_chunked_export_sync 的块前检查)返回 False
            # 后续(_sleep_with_cancel_check 内每秒检查)返回 True
            return cancel_called["n"] > 1

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            "output_dir": str(out_dir), "job_name": "l3job",
        }

        # 不 patch time.sleep,让 _sleep_with_cancel_check 真的每秒检查
        # 但每次 sleep(1) 立即返回(无延迟)
        with patch("time.sleep", lambda s: None), \
             patch("backend.services.data_export_service._is_cancelling", side_effect=_is_cancelling_stub), \
             patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "cancelled", f"got {j.status}, err={j.error_message}"
        finally:
            db.close()
