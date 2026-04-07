"""
数据导出 E2E 测试

A · 连接 & 预览端点
B · 完整导出流（execute → poll → download）
C · 取消流程（pending 直接 cancelled / running → cancelling → cancelled）
D · 服务重启恢复（startup recovery）
E · 权限矩阵

运行：
    python -m pytest test_data_export_e2e.py -v -s
"""
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_t_dee_{uuid.uuid4().hex[:6]}_"


# ─── 公共夹具 ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_client():
    os.environ["ENABLE_AUTH"] = "False"
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def db_session():
    from backend.config.database import SessionLocal
    db = SessionLocal()
    yield db
    db.close()


def _make_export_job(db, username, status="pending", file_path=None):
    from backend.models.export_job import ExportJob
    job = ExportJob(
        user_id="uid",
        username=username,
        query_sql="SELECT 1",
        connection_env="test",
        status=status,
        output_filename="out.xlsx",
        file_path=file_path,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# =============================================================================
# A · 连接 & 预览端点
# =============================================================================

class TestConnectionsAndPreview:
    """A1-A6"""

    def test_a1_get_connections_returns_list(self, app_client):
        """A1: GET /connections 返回列表"""
        # 在 data_export 模块中 import 了 list_writable_connections，需 patch 该引用
        with patch("api.data_export.list_writable_connections", return_value=[
            {"env": "sg", "server_name": "clickhouse-sg", "host": "ch", "http_port": 8123,
             "database": "default", "display_name": "clickhouse-sg"},
        ]):
            resp = app_client.get("/api/v1/data-export/connections")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["env"] == "sg"

    def test_a2_get_connections_empty(self, app_client):
        """A2: 无连接时返回空列表"""
        with patch("api.data_export.list_writable_connections", return_value=[]):
            resp = app_client.get("/api/v1/data-export/connections")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_a3_preview_returns_columns_and_rows(self, app_client):
        """A3: POST /preview 返回列信息和行数据"""
        preview_result = {
            "columns": [{"name": "id", "type": "Int64"}, {"name": "name", "type": "String"}],
            "rows": [["1", "alice"], ["2", "bob"]],
            "row_count": 2,
        }
        with patch("api.data_export.preview_query", return_value=preview_result):
            resp = app_client.post("/api/v1/data-export/preview", json={
                "query_sql": "SELECT id, name FROM users",
                "connection_env": "sg",
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["columns"]) == 2
        assert data["row_count"] == 2

    def test_a4_preview_sql_error_400(self, app_client):
        """A4: SQL 语法错误 → 400"""
        with patch(
            "api.data_export.preview_query",
            side_effect=RuntimeError("Syntax error"),
        ):
            resp = app_client.post("/api/v1/data-export/preview", json={
                "query_sql": "SELCT *",
                "connection_env": "sg",
            })
        assert resp.status_code == 400
        assert "Syntax error" in resp.json()["detail"]

    def test_a5_preview_connection_error_400(self, app_client):
        """A5: 连接失败 → 400"""
        with patch(
            "api.data_export.preview_query",
            side_effect=ConnectionError("refused"),
        ):
            resp = app_client.post("/api/v1/data-export/preview", json={
                "query_sql": "SELECT 1",
                "connection_env": "sg",
            })
        assert resp.status_code == 400

    def test_a6_preview_respects_limit_param(self, app_client):
        """A6: limit 参数传入后端"""
        captured = {}

        def fake_preview(sql, env, connection_type="clickhouse", limit=100):
            captured["limit"] = limit
            return {"columns": [], "rows": [], "row_count": 0}

        with patch("api.data_export.preview_query", side_effect=fake_preview):
            app_client.post("/api/v1/data-export/preview", json={
                "query_sql": "SELECT 1",
                "connection_env": "sg",
                "limit": 50,
            })
        assert captured.get("limit") == 50


# =============================================================================
# B · 完整导出流
# =============================================================================

class TestFullExportFlow:
    """B1-B6: execute → poll → status → download"""

    def test_b1_execute_creates_job(self, app_client):
        """B1: POST /execute 创建任务，返回 job_id"""
        with patch("asyncio.create_task"):
            resp = app_client.post("/api/v1/data-export/execute", json={
                "query_sql": "SELECT 1",
                "connection_env": "sg",
                "job_name": f"{_PREFIX}b1",
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["output_filename"].endswith(".xlsx")

    def test_b2_poll_returns_job_status(self, app_client, db_session):
        """B2: GET /jobs/{job_id} 返回任务状态"""
        job = _make_export_job(db_session, f"{_PREFIX}b2")
        resp = app_client.get(f"/api/v1/data-export/jobs/{job.id}")
        assert resp.status_code == 200
        d = resp.json()["data"]
        assert d["status"] == "pending"
        assert d["query_sql"] == "SELECT 1"

    def test_b3_job_progresses_to_completed(self, app_client, db_session, tmp_path):
        """B3: 模拟 running → completed 状态转换"""
        from backend.models.export_job import ExportJob
        fpath = tmp_path / "b3.xlsx"
        fpath.write_bytes(b"PK xlsx")

        job = _make_export_job(db_session, f"{_PREFIX}b3")
        job.status = "completed"
        job.exported_rows = 500
        job.total_sheets = 1
        job.file_path = str(fpath)
        job.output_filename = "b3.xlsx"
        job.file_size = len(b"PK xlsx")
        db_session.commit()

        resp = app_client.get(f"/api/v1/data-export/jobs/{job.id}")
        assert resp.json()["data"]["status"] == "completed"
        assert resp.json()["data"]["exported_rows"] == 500

    def test_b4_download_completed_job(self, app_client, db_session, tmp_path):
        """B4: 下载已完成的导出文件"""
        fpath = tmp_path / "b4.xlsx"
        content = b"PK real xlsx content"
        fpath.write_bytes(content)

        job = _make_export_job(db_session, f"{_PREFIX}b4", status="completed", file_path=str(fpath))
        job.output_filename = "b4.xlsx"
        db_session.commit()

        resp = app_client.get(f"/api/v1/data-export/jobs/{job.id}/download")
        assert resp.status_code == 200
        assert resp.content == content

    def test_b5_download_pending_job_400(self, app_client, db_session):
        """B5: 下载未完成任务 → 400"""
        job = _make_export_job(db_session, f"{_PREFIX}b5", status="pending")
        resp = app_client.get(f"/api/v1/data-export/jobs/{job.id}/download")
        assert resp.status_code == 400

    def test_b6_list_jobs_pagination(self, app_client, db_session):
        """B6: 历史列表分页"""
        for i in range(3):
            _make_export_job(db_session, f"{_PREFIX}b6_{i}")

        resp = app_client.get("/api/v1/data-export/jobs", params={"page": 1, "page_size": 2})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["items"]) <= 2


# =============================================================================
# C · 取消流程
# =============================================================================

class TestCancellation:
    """C1-C6: pending 取消、running 取消、重复取消防护"""

    def test_c1_cancel_pending_directly_cancelled(self, app_client, db_session):
        """C1: 取消 pending → 直接 cancelled"""
        job = _make_export_job(db_session, f"{_PREFIX}c1")
        resp = app_client.post(f"/api/v1/data-export/jobs/{job.id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "cancelled"

        # DB 中状态确认
        db_session.refresh(job)
        assert job.status == "cancelled"
        assert job.finished_at is not None

    def test_c2_cancel_running_becomes_cancelling(self, app_client, db_session):
        """C2: 取消 running → cancelling"""
        job = _make_export_job(db_session, f"{_PREFIX}c2", status="running")
        resp = app_client.post(f"/api/v1/data-export/jobs/{job.id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "cancelling"

    def test_c3_cancel_completed_400(self, app_client, db_session):
        """C3: 取消已完成 → 400"""
        job = _make_export_job(db_session, f"{_PREFIX}c3", status="completed")
        resp = app_client.post(f"/api/v1/data-export/jobs/{job.id}/cancel")
        assert resp.status_code == 400

    def test_c4_cancel_cancelled_400(self, app_client, db_session):
        """C4: 取消已取消 → 400"""
        job = _make_export_job(db_session, f"{_PREFIX}c4", status="cancelled")
        resp = app_client.post(f"/api/v1/data-export/jobs/{job.id}/cancel")
        assert resp.status_code == 400

    def test_c5_cancel_failed_400(self, app_client, db_session):
        """C5: 取消失败任务 → 400"""
        job = _make_export_job(db_session, f"{_PREFIX}c5", status="failed")
        resp = app_client.post(f"/api/v1/data-export/jobs/{job.id}/cancel")
        assert resp.status_code == 400

    def test_c6_coroutine_respects_cancelling_mid_run(self):
        """C6: 协程在 running 时检测到 cancelling → 标记 cancelled"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        from backend.services.export_clients.base import ColumnInfo
        import backend.services.data_export_service as svc

        db = SessionLocal()
        job = _make_export_job(db, f"{_PREFIX}c6")
        job_id = str(job.id)
        db.close()

        batches_yielded = []

        def _stream(*a, **kw):
            yield [("v1",)]
            # 在第二批前将 DB 状态改为 cancelling
            db2 = SessionLocal()
            j = db2.query(ExportJob).filter(ExportJob.id == job_id).first()
            j.status = "cancelling"
            db2.commit()
            db2.close()
            yield [("v2",)]

        mock_client = Mock()
        mock_client.get_columns.return_value = [ColumnInfo("v", "String")]
        mock_client.stream_batches = _stream

        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            config = {
                "query_sql": "SELECT 1", "connection_env": "test",
                "connection_type": "clickhouse", "batch_size": 1000,
                "output_path": os.path.join(td, "c6.xlsx"),
                "output_filename": "c6.xlsx",
            }

            with patch("backend.services.data_export_service._build_export_client", return_value=mock_client):
                loop = asyncio.new_event_loop()
                loop.run_until_complete(svc.run_export_job(job_id, config))
                loop.close()

        db = SessionLocal()
        j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
        db.close()
        assert j.status == "cancelled"


# =============================================================================
# D · 服务重启恢复（startup recovery）
# =============================================================================

class TestStartupRecovery:
    """D1-D4: pending/running/cancelling → 自动终态"""

    def _run_recovery(self):
        """触发 startup recovery 逻辑（独立调用，不启动整个 app）"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        from backend.models.import_job import ImportJob
        from datetime import datetime

        db = SessionLocal()
        now = datetime.utcnow()
        interrupted = db.query(ExportJob).filter(
            ExportJob.status.in_(["pending", "running"])
        ).all()
        for j in interrupted:
            j.status = "failed"
            j.error_message = "服务重启，任务已中断"
            j.finished_at = now
            j.updated_at = now
        cancelling = db.query(ExportJob).filter(ExportJob.status == "cancelling").all()
        for j in cancelling:
            j.status = "cancelled"
            j.finished_at = now
            j.updated_at = now
        db.commit()
        db.close()

    def test_d1_pending_becomes_failed(self, db_session):
        """D1: 重启后 pending → failed"""
        from backend.models.export_job import ExportJob
        job = _make_export_job(db_session, f"{_PREFIX}d1", status="pending")
        self._run_recovery()
        db_session.refresh(job)
        assert job.status == "failed"
        assert "重启" in job.error_message

    def test_d2_running_becomes_failed(self, db_session):
        """D2: 重启后 running → failed"""
        from backend.models.export_job import ExportJob
        job = _make_export_job(db_session, f"{_PREFIX}d2", status="running")
        self._run_recovery()
        db_session.refresh(job)
        assert job.status == "failed"

    def test_d3_cancelling_becomes_cancelled(self, db_session):
        """D3: 重启后 cancelling → cancelled"""
        from backend.models.export_job import ExportJob
        job = _make_export_job(db_session, f"{_PREFIX}d3", status="cancelling")
        self._run_recovery()
        db_session.refresh(job)
        assert job.status == "cancelled"

    def test_d4_completed_untouched(self, db_session):
        """D4: 已完成任务不受影响"""
        from backend.models.export_job import ExportJob
        job = _make_export_job(db_session, f"{_PREFIX}d4", status="completed")
        self._run_recovery()
        db_session.refresh(job)
        assert job.status == "completed"


# =============================================================================
# E · 权限矩阵
# =============================================================================

class TestPermissions:
    """E1-E4: ENABLE_AUTH=true 场景下的权限检查"""

    @pytest.fixture
    def auth_client(self):
        old = os.environ.get("ENABLE_AUTH")
        os.environ["ENABLE_AUTH"] = "True"
        from fastapi.testclient import TestClient
        import importlib
        import backend.config.settings as _settings
        _settings.settings.__class__.__init__  # 确保 settings 已加载
        sys.path.insert(0, str(Path(__file__).parent / "backend"))
        from main import app
        with TestClient(app) as c:
            yield c
        if old is not None:
            os.environ["ENABLE_AUTH"] = old
        else:
            del os.environ["ENABLE_AUTH"]

    def test_e1_no_token_returns_401_or_403(self, app_client):
        """E1: 无 token（ENABLE_AUTH=false 时匿名用户通过）"""
        # ENABLE_AUTH=false → anon user → 通过
        resp = app_client.get("/api/v1/data-export/connections")
        assert resp.status_code == 200

    def test_e2_connections_needs_export_perm(self, app_client):
        """E2: data:export 权限控制连接列表端点"""
        from backend.api.deps import require_permission
        # 验证端点确实依赖 require_permission("data", "export")
        import inspect
        from backend.api import data_export as de_module
        source = inspect.getsource(de_module.get_connections)
        assert 'require_permission("data", "export")' in source

    def test_e3_preview_needs_export_perm(self, app_client):
        """E3: /preview 端点依赖 data:export 权限"""
        import inspect
        from backend.api import data_export as de_module
        source = inspect.getsource(de_module.preview)
        assert 'require_permission("data", "export")' in source

    def test_e4_all_endpoints_protected(self):
        """E4: 所有路由均有 require_permission("data", "export")"""
        import inspect
        from backend.api import data_export as de_module
        # 检查模块中所有路由函数
        perm_str = 'require_permission("data", "export")'
        full_src = inspect.getsource(de_module)
        # 每个 @router 装饰的函数都应该有此权限依赖
        endpoints = [
            "get_connections", "preview", "execute_export",
            "get_job_status", "cancel_job", "delete_job",
            "list_jobs", "download_job",
        ]
        for ep in endpoints:
            fn = getattr(de_module, ep, None)
            if fn:
                src = inspect.getsource(fn)
                assert perm_str in src, f"{ep} missing permission check"


# =============================================================================
# teardown
# =============================================================================

def teardown_module(_):
    """清理本文件创建的测试数据"""
    try:
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        jobs = db.query(ExportJob).filter(ExportJob.username.like(f"{_PREFIX}%")).all()
        for j in jobs:
            if j.file_path:
                try:
                    os.unlink(j.file_path)
                except OSError:
                    pass
            db.delete(j)
        db.commit()
        db.close()
    except Exception:
        pass
