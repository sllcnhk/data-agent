"""
按日期分块导出 — 端到端测试 v2.13

N · 前端字段 → API 契约：execute(chunk_config) 完整提交链路
O · output_files JSON 在 GET /jobs/{id} 中正确返回
P · 分块导出全流程：execute → 等待完成 → 列出 output_files → 下载第 0 个文件
Q · 单文件 vs 分块混合任务列表显示
R · 分块模式 SQL 占位符路径 e2e

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_data_export_chunked_e2e.py -v -s
"""
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_t_decE_{uuid.uuid4().hex[:6]}_"


# ─── 公共夹具 ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_client():
    os.environ["ENABLE_AUTH"] = "False"
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    from main import app
    with TestClient(app) as c:
        yield c


def _make_columns():
    from backend.services.export_clients.base import ColumnInfo
    return [ColumnInfo("id", "Int64"), ColumnInfo("name", "String")]


def _make_batch(n: int, base: int = 0):
    return [(str(base + i), f"name_{base + i}") for i in range(n)]


def _wait_until_done(app_client, job_id: str, timeout: float = 10.0):
    """轮询直到任务进入终态（completed/failed/cancelled）"""
    start = time.monotonic()
    last = None
    while time.monotonic() - start < timeout:
        resp = app_client.get(f"/api/v1/data-export/jobs/{job_id}")
        if resp.status_code != 200:
            return None
        last = resp.json()["data"]
        if last["status"] in ("completed", "failed", "cancelled"):
            return last
        time.sleep(0.1)
    return last


# =============================================================================
# N · 前端字段 → API 契约
# =============================================================================

class TestFrontendFieldContract:
    """N1-N3：模拟前端表单提交格式"""

    def test_n1_frontend_payload_shape(self, app_client):
        """N1: 前端 RangePicker.format('YYYY-MM-DD') + chunk_days + 占位符 SQL"""
        payload = {
            "query_sql": "SELECT * FROM events WHERE dt >= '{{date_start}}' AND dt <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}n1",
            "batch_size": 50000,
            "chunk_config": {
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
                "date_column": None,
            },
        }
        resp = app_client.post("/api/v1/data-export/execute", json=payload)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["export_mode"] == "date_chunked"
        assert "job_id" in data

        # 立即取消防止后台执行污染
        app_client.post(f"/api/v1/data-export/jobs/{data['job_id']}/cancel")

    def test_n2_wrapper_mode_payload_with_date_column(self, app_client):
        """N2: 包装模式 — date_column 必填"""
        payload = {
            "query_sql": "SELECT id, name FROM events",
            "connection_env": "test",
            "job_name": f"{_PREFIX}n2",
            "chunk_config": {
                "date_column": "event_date",
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
            },
        }
        resp = app_client.post("/api/v1/data-export/execute", json=payload)
        assert resp.status_code == 200
        app_client.post(f"/api/v1/data-export/jobs/{resp.json()['data']['job_id']}/cancel")

    def test_n3_wrapper_mode_missing_column_400(self, app_client):
        """N3: 包装模式缺 date_column → 400 显示具体错误"""
        payload = {
            "query_sql": "SELECT * FROM events",
            "connection_env": "test",
            "job_name": f"{_PREFIX}n3",
            "chunk_config": {
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
            },
        }
        resp = app_client.post("/api/v1/data-export/execute", json=payload)
        assert resp.status_code == 400
        assert "date_column" in resp.json()["detail"]


# =============================================================================
# O · output_files JSON 经 GET /jobs/{id} 透传
# =============================================================================

class TestJobStatusOutputFiles:
    """O1-O2"""

    def test_o1_get_status_includes_export_mode(self, app_client):
        """O1: GET /jobs/{id} 返回 export_mode 字段"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        try:
            j = ExportJob(
                user_id="uid", username=f"{_PREFIX}o1",
                query_sql="SELECT 1", connection_env="test",
                status="pending",
                export_mode="date_chunked",
                output_files=[{
                    "index": 0, "date_start": "2025-04-01", "date_end": "2025-04-10",
                    "filename": "x_0.xlsx", "file_path": "/x/x_0.xlsx",
                    "file_size": None, "rows": 0, "sheets": 0, "status": "pending",
                }],
            )
            db.add(j)
            db.commit()
            jid = str(j.id)
        finally:
            db.close()

        resp = app_client.get(f"/api/v1/data-export/jobs/{jid}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["export_mode"] == "date_chunked"
        assert isinstance(data["output_files"], list)
        assert len(data["output_files"]) == 1
        assert data["output_files"][0]["filename"] == "x_0.xlsx"

    def test_o2_legacy_single_job_export_mode_default(self, app_client):
        """O2: 旧单文件 Job → export_mode='single'，output_files=null"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        try:
            j = ExportJob(
                user_id="uid", username=f"{_PREFIX}o2",
                query_sql="SELECT 1", connection_env="test",
                status="completed",
                # 不显式设置 export_mode/output_files，验证默认行为
            )
            db.add(j)
            db.commit()
            jid = str(j.id)
        finally:
            db.close()

        resp = app_client.get(f"/api/v1/data-export/jobs/{jid}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["export_mode"] == "single"
        assert data["output_files"] is None


# =============================================================================
# P · 分块导出全流程：execute → poll → download chunk 0
# =============================================================================

class TestFullChunkedFlow:
    """P1-P2"""

    def test_p1_full_flow_execute_to_download(self, app_client, tmp_path, monkeypatch):
        """P1: execute → poll completed → output_files 完整 → 下载第 0 个文件"""
        # 把导出根目录指向 tmp_path 以隔离测试文件
        from backend.api import data_export as api_module
        monkeypatch.setattr(api_module, "_CUSTOMER_DATA_ROOT", tmp_path)

        # mock ClickHouse 客户端
        sqls_seen: List[str] = []

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                sqls_seen.append(sql)
                yield _make_batch(3)
            mc.stream_batches.side_effect = _stream
            return mc

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            payload = {
                "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
                "connection_env": "test",
                "job_name": f"{_PREFIX}p1",
                "chunk_config": {
                    "date_start": "2025-04-01",
                    "date_end": "2025-04-20",
                    "chunk_days": 10,
                },
            }
            resp = app_client.post("/api/v1/data-export/execute", json=payload)
            assert resp.status_code == 200
            jid = resp.json()["data"]["job_id"]

            final = _wait_until_done(app_client, jid, timeout=15.0)
            assert final is not None, "Job did not finish in time"
            assert final["status"] == "completed", f"Job state: {final}"
            assert final["export_mode"] == "date_chunked"
            assert len(final["output_files"]) == 2  # 04-01~04-10, 04-11~04-20
            assert all(f["status"] == "completed" for f in final["output_files"])
            assert final["exported_rows"] == 6  # 3 × 2 块

        # 下载第 0 个文件
        dl = app_client.get(f"/api/v1/data-export/jobs/{jid}/download?file_index=0")
        assert dl.status_code == 200
        assert "spreadsheetml" in dl.headers["content-type"]
        assert len(dl.content) > 0  # 真实 xlsx 内容

        # 下载第 1 个文件
        dl1 = app_client.get(f"/api/v1/data-export/jobs/{jid}/download?file_index=1")
        assert dl1.status_code == 200

        # 下载越界
        dl_bad = app_client.get(f"/api/v1/data-export/jobs/{jid}/download?file_index=99")
        assert dl_bad.status_code == 404

        # 不带 file_index → 400
        dl_none = app_client.get(f"/api/v1/data-export/jobs/{jid}/download")
        assert dl_none.status_code == 400

    def test_p2_full_flow_delete_removes_directory(self, app_client, tmp_path, monkeypatch):
        """P2: 完成后 DELETE → 目录及其下所有 xlsx 被清理"""
        from backend.api import data_export as api_module
        monkeypatch.setattr(api_module, "_CUSTOMER_DATA_ROOT", tmp_path)

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                yield _make_batch(2)
            mc.stream_batches.side_effect = _stream
            return mc

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            resp = app_client.post("/api/v1/data-export/execute", json={
                "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
                "connection_env": "test",
                "job_name": f"{_PREFIX}p2",
                "chunk_config": {
                    "date_start": "2025-04-01",
                    "date_end": "2025-04-10",
                    "chunk_days": 10,
                },
            })
            jid = resp.json()["data"]["job_id"]
            final = _wait_until_done(app_client, jid, timeout=10.0)
            assert final["status"] == "completed"

        target_dir = Path(final["output_files"][0]["file_path"]).parent
        assert target_dir.exists()
        # 内含真实 xlsx 文件
        xlsx_files = list(target_dir.glob("*.xlsx"))
        assert len(xlsx_files) >= 1

        # 删除任务
        del_resp = app_client.delete(f"/api/v1/data-export/jobs/{jid}")
        assert del_resp.status_code == 200
        assert not target_dir.exists(), "Chunked output directory should be recursively removed"


# =============================================================================
# Q · 任务列表显示混合模式
# =============================================================================

class TestJobListMixedModes:
    """Q1: 单文件 + 分块 Job 同时出现，列表不报错"""

    def test_q1_list_jobs_returns_mixed_modes(self, app_client):
        """Q1: GET /jobs 同时包含 single 和 date_chunked 任务"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        try:
            j1 = ExportJob(
                user_id="uid", username=f"{_PREFIX}q1a",
                query_sql="SELECT 1", connection_env="test",
                status="completed",
                export_mode="single",
            )
            j2 = ExportJob(
                user_id="uid", username=f"{_PREFIX}q1b",
                query_sql="SELECT 1", connection_env="test",
                status="completed",
                export_mode="date_chunked",
                output_files=[{
                    "index": 0, "date_start": "2025-04-01", "date_end": "2025-04-10",
                    "filename": "x.xlsx", "file_path": "/x.xlsx",
                    "file_size": 1024, "rows": 100, "sheets": 1, "status": "completed",
                }],
            )
            db.add_all([j1, j2])
            db.commit()
        finally:
            db.close()

        resp = app_client.get("/api/v1/data-export/jobs?page=1&page_size=50")
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        modes = {item.get("export_mode") for item in items}
        # 至少包含两种模式
        assert "single" in modes
        assert "date_chunked" in modes


# =============================================================================
# R · 分块模式占位符路径 e2e（验证生成的 SQL 含日期字面量）
# =============================================================================

class TestPlaceholderPathE2E:
    """R1: 占位符路径下，service 调用 ClickHouse 的 SQL 应含 ISO 日期字面量"""

    def test_r1_placeholder_substitution_e2e(self, app_client, tmp_path, monkeypatch):
        from backend.api import data_export as api_module
        monkeypatch.setattr(api_module, "_CUSTOMER_DATA_ROOT", tmp_path)

        seen: List[str] = []

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            def _stream(sql, **kwargs):
                seen.append(sql)
                yield _make_batch(1)
            mc.stream_batches.side_effect = _stream
            return mc

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            resp = app_client.post("/api/v1/data-export/execute", json={
                "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
                "connection_env": "test",
                "job_name": f"{_PREFIX}r1",
                "chunk_config": {
                    "date_start": "2025-04-01",
                    "date_end": "2025-04-30",
                    "chunk_days": 10,
                },
            })
            jid = resp.json()["data"]["job_id"]
            final = _wait_until_done(app_client, jid, timeout=15.0)
            assert final["status"] == "completed"

        # 应有 3 个块的 SQL 调用，每个含不同日期字面量
        assert len(seen) == 3
        assert any("'2025-04-01'" in s and "'2025-04-10'" in s for s in seen)
        assert any("'2025-04-11'" in s and "'2025-04-20'" in s for s in seen)
        assert any("'2025-04-21'" in s and "'2025-04-30'" in s for s in seen)
        # 占位符模式不应出现包装别名
        for s in seen:
            assert "_chunk_q" not in s
            assert "{{" not in s


# =============================================================================
# teardown
# =============================================================================

def teardown_module(_):
    try:
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        jobs = db.query(ExportJob).filter(ExportJob.username.like(f"{_PREFIX}%")).all()
        for j in jobs:
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
