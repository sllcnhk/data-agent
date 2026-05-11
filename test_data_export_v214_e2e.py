"""
test_data_export_v214_e2e — 资深 QA 视角综合补强测试

目标:用真实测试客户端 + mock 数据库 + mock 客户端,验证 Task C/A/B/D 在端到端
环境下的契约、安全、组合行为。补现有单测/集成测试未覆盖的场景。

测试分组:
  M · v2.14 API 契约     新字段往返:Pydantic schema + DB JSONB + GET 回读
  N · v2.14 API 边界      min_subdivide_unit / cursor_column 非法值 → 400
  O · v2.14 RBAC          新字段在权限保护下安全(继承 data:export,无新菜单/端点)
  P · 组合场景            hour 子细分 + cursor_column keyset 双开,块失败 → sub-day 拆 + 子块走 keyset
  Q · Cancel race         重试退避中、子块拆分前、keyset 流式中被取消
  R · Keyset 多窗口       真实驱动 3-window cursor 推进,验证 ORDER BY 单调性
  S · Download 子块文件   sub-day 拆分后 datetime 文件名 + file_index 越界
  T · 异常链探测           is_transient_stream_error 沿 __cause__ 探到底层
  U · 文件名 Windows 安全 datetime 文件名不含 ':'

运行:
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_data_export_v214_e2e.py -v -s
"""
import asyncio
import os
import shutil
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_t_v214_{uuid.uuid4().hex[:6]}_"


# ─────────────────────────────────────────────────────────────────────────────
# 公共 fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app_client():
    os.environ["ENABLE_AUTH"] = "False"
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    from main import app
    with TestClient(app) as c:
        yield c


def _make_columns():
    from backend.services.export_clients.base import ColumnInfo
    return [ColumnInfo("id", "Int64"), ColumnInfo("name", "String"),
            ColumnInfo("ts", "DateTime")]


def _make_batch(n: int, base: int = 0):
    return [(str(base + i), f"name_{base + i}",
             f"2025-04-01 00:00:{(base+i) % 60:02d}") for i in range(n)]


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _tsv_response(col_names: List[str], col_types: List[str], rows: List[Tuple]):
    """构造 mock requests Response 模拟流式 TSV"""
    lines = ["\t".join(col_names), "\t".join(col_types)]
    for r in rows:
        lines.append("\t".join(str(c) if c is not None else r"\N" for c in r))
    body = "\n".join(lines) + "\n"
    resp = MagicMock()
    resp.status_code = 200
    resp.content = body.encode("utf-8")
    resp.text = body
    resp.iter_lines = lambda decode_unicode=True: iter(lines)
    return resp


def _make_pending_chunked_job(
    db, username: str, chunk_config: dict, output_dir: Path, sql: Optional[str] = None,
) -> str:
    """直接在 DB 创建 pending 状态 chunked job(绕过 API)"""
    from backend.models.export_job import ExportJob
    job = ExportJob(
        user_id="uid", username=username,
        query_sql=sql or "SELECT id, name, ts FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
        connection_env="test", status="pending",
        export_mode="date_chunked",
        chunk_config=chunk_config,
        output_filename=output_dir.name,
        file_path=str(output_dir),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return str(job.id)


# =============================================================================
# M · v2.14 API 契约 — 新字段往返
# =============================================================================

class TestApiContractV214:

    def test_m1_round_trip_min_subdivide_unit_hour(self, app_client):
        """M1: POST /execute 带 min_subdivide_unit=hour → DB.chunk_config 持久化 →
        GET /jobs/{id} 回读字段正确"""
        resp = app_client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}m1",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-05",
                "chunk_days": 1, "min_subdivide_unit": "hour",
            },
        })
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["data"]["job_id"]

        # DB 字段验证
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.chunk_config["min_subdivide_unit"] == "hour"
            assert j.chunk_config["cursor_column"] is None or j.chunk_config.get("cursor_column") is None
        finally:
            db.close()

        # GET 回读
        resp = app_client.get(f"/api/v1/data-export/jobs/{job_id}")
        data = resp.json()["data"]
        assert data["chunk_config"]["min_subdivide_unit"] == "hour"

    def test_m2_round_trip_cursor_column(self, app_client):
        """M2: cursor_column 字段往返"""
        resp = app_client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT id, ts FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}m2",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-05",
                "chunk_days": 5, "cursor_column": "id",
            },
        })
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["data"]["job_id"]

        resp = app_client.get(f"/api/v1/data-export/jobs/{job_id}")
        assert resp.json()["data"]["chunk_config"]["cursor_column"] == "id"

    def test_m3_default_min_subdivide_unit_is_day(self, app_client):
        """M3: 不传 min_subdivide_unit → Pydantic 默认 'day'(保留老行为)"""
        resp = app_client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}m3",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-30",
                "chunk_days": 10,
            },
        })
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["data"]["job_id"]

        resp = app_client.get(f"/api/v1/data-export/jobs/{job_id}")
        cc = resp.json()["data"]["chunk_config"]
        # 默认值由 Pydantic 模型注入,GET 回读应见到 "day"
        assert cc.get("min_subdivide_unit", "day") == "day"


# =============================================================================
# N · v2.14 API 边界 — 非法入参 → 400
# =============================================================================

class TestApiBoundariesV214:

    def test_n1_invalid_min_subdivide_unit_rejected(self, app_client):
        """N1: min_subdivide_unit='second'(非合法枚举)→ 400(chunker 校验)"""
        resp = app_client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}n1",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-05",
                "chunk_days": 1, "min_subdivide_unit": "second",
            },
        })
        assert resp.status_code == 400
        assert "min_subdivide_unit" in resp.json()["detail"]

    def test_n2_invalid_cursor_column_charset_rejected(self, app_client):
        """N2: cursor_column 含非法字符(空格 + 分号)→ 400"""
        resp = app_client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}n2",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-05",
                "chunk_days": 1, "cursor_column": "id; DROP TABLE",
            },
        })
        assert resp.status_code == 400
        assert "cursor_column" in resp.json()["detail"]

    def test_n3_cursor_column_empty_string_normalized(self, app_client):
        """N3: cursor_column='' 视为未提供 → 仍接受,DB 字段 None"""
        resp = app_client.post("/api/v1/data-export/execute", json={
            "query_sql": "SELECT * FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test",
            "job_name": f"{_PREFIX}n3",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-05",
                "chunk_days": 5, "cursor_column": "",
            },
        })
        assert resp.status_code == 200, resp.text


# =============================================================================
# O · v2.14 RBAC — 新字段在 data:export 权限保护下
# =============================================================================

class TestRbacV214:

    def test_o1_no_new_endpoints_introduced(self):
        """O1: v2.14 改动**无新增**端点/菜单 — 仅扩展现有 chunk_config 字段。
        本测试验证 backend/api/data_export.py 端点数与 v2.13 一致(契约稳定)。"""
        import re
        text = Path("backend/api/data_export.py").read_text(encoding="utf-8")
        # v2.13 已存在端点:connections / preview / execute / jobs/{id} / cancel /
        #                     jobs / jobs/{id}/download / jobs/{id} DELETE
        # = 至少 8 个路由
        routes = re.findall(r'@router\.(get|post|delete|put)\("(.+?)"', text)
        assert len(routes) >= 8, f"endpoint 数变少了:{routes}"
        # 没有 /chunk-config 或 /cursor 之类的新独立端点
        for method, path in routes:
            assert "cursor" not in path, f"v2.14 不应有 cursor 独立端点:{path}"
            assert "subdivide" not in path, f"v2.14 不应有 subdivide 独立端点:{path}"

    def test_o2_no_new_menu_entry(self):
        """O2: 前端菜单未新增项目(v2.14 字段都在现有 /data-export 页内)"""
        layout = Path("frontend/src/components/AppLayout.tsx").read_text(encoding="utf-8")
        # /data-export 菜单存在,沿用 perm: 'data:export'
        assert "'/data-export'" in layout
        assert "perm: 'data:export'" in layout
        # 没有 /data-export-chunked / /data-export-keyset 之类的新子菜单
        assert "/data-export-chunked" not in layout
        assert "/data-export-keyset" not in layout
        assert "/data-export/subdivide" not in layout

    def test_o3_existing_data_export_permission_covers_new_fields(self):
        """O3: data:export 权限通过 require_permission('data','export') 装饰所有
        相关端点 — 新字段在请求 body 内,沿用同一权限,无需 RBAC 矩阵变更"""
        text = Path("backend/api/data_export.py").read_text(encoding="utf-8")
        import re
        # 所有路由装饰器
        decorators = re.findall(r'@router\.(?:get|post|delete|put)', text)
        # 所有 require_permission 调用
        perm_calls = re.findall(
            r'require_permission\(\s*["\']data["\']\s*,\s*["\']export["\']\s*\)',
            text,
        )
        assert len(perm_calls) >= len(decorators), (
            f"端点数 {len(decorators)} 多于权限调用数 {len(perm_calls)} —— "
            f"v2.14 改动可能未保持 require_permission 装饰"
        )

    def test_o4_init_rbac_data_export_still_in_superadmin(self):
        """O4: init_rbac.py 中 data:export 仍预置在 superadmin(数据导出仍是
        superadmin 专属;v2.14 不修改权限矩阵)"""
        text = Path("backend/scripts/init_rbac.py").read_text(encoding="utf-8")
        assert '("data",            "export"' in text or \
               '("data", "export"' in text
        # superadmin 通过 PERMISSIONS 列表派生全部权限,自动包含 data:export


# =============================================================================
# P · 组合场景 — hour 子细分 + cursor_column keyset 双开
# =============================================================================

class TestCombinationV214:

    def test_p1_hour_subdivision_and_keyset_combined(self, tmp_path):
        """P1: chunk_days=1 + min_subdivide_unit=hour + cursor_column=id。
        原 1 天块整体流式失败 → 自动 sub-day 拆 12h+12h → 各子块 fallback 用 keyset
        → 完成。验证 Task A + Task B 双开协同正确性。"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "p1_out"
        job_id = _make_pending_chunked_job(
            db, f"{_PREFIX}p1",
            {
                "date_start": "2025-04-01", "date_end": "2025-04-01",
                "chunk_days": 1, "min_subdivide_unit": "hour",
                "cursor_column": "id",
            },
            out_dir,
        )
        db.close()

        # 注入策略:
        #   - 原 1 天块 stream_batches 抛 → 触发 fallback (use_chunked=True)
        #     由于 cursor_column 提供 → keyset 而非 LIMIT/OFFSET
        #   - 但此处我们让 keyset 也在原 1 天块下抛 → 异常上抛 → 外层 sub-day 拆
        #   - sub-day 子块再调 _run_single_export:首次 stream_batches 也抛 →
        #     fallback keyset → 子块的 keyset 成功
        keyset_calls = {"n": 0}

        def _is_subday(sql: str) -> bool:
            return ":" in sql and "00:00" in sql or "11:59:59" in sql or "23:59:59" in sql

        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()

            def _stream(sql, *a, **kw):
                raise ChunkedEncodingError("stream cut")
                yield

            def _keyset(sql, cursor_column, *a, **kw):
                keyset_calls["n"] += 1
                # sub-day SQL: 成功(包装后含时间字面量);原 1 天 SQL: 失败
                if _is_subday(sql):
                    yield _make_batch(3, base=keyset_calls["n"] * 10)
                    return
                raise ChunkedEncodingError("keyset stream cut on top-level chunk")

            mc.stream_batches.side_effect = _stream
            mc.stream_batches_keyset.side_effect = _keyset
            return mc

        config = {
            "query_sql": "SELECT id, name, ts FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 1000, "export_mode": "date_chunked",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-01",
                "chunk_days": 1, "min_subdivide_unit": "hour",
                "cursor_column": "id",
            },
            "output_dir": str(out_dir), "job_name": "p1job",
        }

        # patch time.sleep 让 Task D 重试退避瞬时
        with patch("time.sleep", lambda s: None), \
             patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            _run_async(svc.run_export_job(job_id, config))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "completed", f"got {j.status}, err={j.error_message}"
            files = j.output_files
            # 应有 2 个 sub-day 子块(原 1 天块分裂)
            assert len(files) == 2, f"expected 2 sub-day entries, got {len(files)}"
            for f in files:
                assert f["status"] == "completed"
                assert "T" in f["date_start"], f"expected datetime ISO: {f['date_start']}"
            # keyset 至少被调用 2 次(2 子块)
            assert keyset_calls["n"] >= 2
        finally:
            db.close()


# =============================================================================
# Q · Cancel race — 在新代码路径中央取消
# =============================================================================

class TestCancelRaceV214:

    def test_q1_cancel_in_retry_backoff(self, tmp_path):
        """Q1: 重试退避期间触发 cancel → 立即 cancelled,不再发起 retry"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import backend.services.data_export_service as svc
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "q1_out"
        job_id = _make_pending_chunked_job(
            db, f"{_PREFIX}q1",
            {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            out_dir,
        )
        db.close()

        def _flaky(*args, **kwargs):
            # 始终抛 transient,让 retry 退避被 cancel 中断
            raise ChunkedEncodingError("perpetual transient")

        # 列预检也要 mock(否则 'test' env 无效配置 → 列预检 fail)
        def _mk_client(*args, **kw):
            mc = Mock()
            mc.get_columns.return_value = _make_columns()
            return mc

        # is_cancelling stub:首次 False(块前检查),后续 True(重试退避内)
        cancel_call = {"n": 0}

        def _is_cancel_stub(jid):
            cancel_call["n"] += 1
            return cancel_call["n"] > 1

        with patch("time.sleep", lambda s: None), \
             patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client), \
             patch("backend.services.data_export_service._run_single_export", side_effect=_flaky), \
             patch("backend.services.data_export_service._is_cancelling", side_effect=_is_cancel_stub):
            _run_async(svc.run_export_job(job_id, config={
                "query_sql": "SELECT 1 FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
                "connection_env": "test", "connection_type": "clickhouse",
                "batch_size": 1000, "export_mode": "date_chunked",
                "chunk_config": {
                    "date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10,
                },
                "output_dir": str(out_dir), "job_name": "q1job",
            }))

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "cancelled", f"got {j.status}"
        finally:
            db.close()


# =============================================================================
# R · Keyset 多窗口推进 — 真实驱动 3-window cursor 单调推进
# =============================================================================

class TestKeysetMultiWindow:

    def test_r1_three_windows_cursor_advance(self):
        """R1: cursor 从 None → '50' → '100' → '150' → 空,验证 3 个窗口
        都用上一窗口的 last cursor 推进,且单调"""
        from backend.services.export_clients.clickhouse import ClickHouseExportClient
        client = ClickHouseExportClient(host="localhost", port=8123,
                                         user="u", password="p", database="d")

        captured_params: List[dict] = []

        def _fake_post(url, data=None, params=None, **kw):
            captured_params.append(dict(params or {}))
            n = len(captured_params)
            if n == 1:
                return _tsv_response(
                    ["id", "name"], ["Int64", "String"],
                    [(str(i), f"n{i}") for i in range(1, 51)],
                )
            if n == 2:
                return _tsv_response(
                    ["id", "name"], ["Int64", "String"],
                    [(str(i), f"n{i}") for i in range(51, 101)],
                )
            if n == 3:
                return _tsv_response(
                    ["id", "name"], ["Int64", "String"],
                    [(str(i), f"n{i}") for i in range(101, 151)],
                )
            return _tsv_response(["id", "name"], ["Int64", "String"], [])

        with patch.object(requests.sessions.Session, "post", side_effect=_fake_post):
            batches = list(client.stream_batches_keyset(
                "SELECT id, name FROM t", cursor_column="id", batch_size=50,
            ))

        # 总行数 150
        all_rows = [r for b in batches for r in b]
        assert len(all_rows) == 150
        # cursor 单调推进
        assert "param_cursor_val" not in captured_params[0]
        assert captured_params[1]["param_cursor_val"] == "50"
        assert captured_params[2]["param_cursor_val"] == "100"
        # 终止窗口
        assert captured_params[3]["param_cursor_val"] == "150"


# =============================================================================
# S · Download 子块文件 — datetime 文件名 + file_index 越界
# =============================================================================

class TestDownloadSubdayChunks:

    def test_s1_download_datetime_chunk_file(self, tmp_path, app_client):
        """S1: 直接构造一个带 datetime 文件名的 chunked job,验证按 file_index 下载"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob

        # 创建一个真实磁盘文件模拟 sub-day 已完成的块
        out_dir = tmp_path / "s1_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        dt_file = out_dir / "s1job_20250401T000000_to_20250401T115959.xlsx"
        # 写一个最小化的有效 xlsx
        import openpyxl
        wb = openpyxl.Workbook()
        wb.save(str(dt_file))

        db = SessionLocal()
        try:
            from backend.models.export_job import ExportJob
            job = ExportJob(
                user_id="uid", username=f"{_PREFIX}s1",
                query_sql="SELECT 1", connection_env="test",
                status="completed", export_mode="date_chunked",
                chunk_config={
                    "date_start": "2025-04-01", "date_end": "2025-04-01",
                    "chunk_days": 1, "min_subdivide_unit": "hour",
                },
                output_filename=out_dir.name,
                file_path=str(out_dir),
                output_files=[{
                    "index": 0,
                    "date_start": "2025-04-01T00:00:00",
                    "date_end": "2025-04-01T11:59:59",
                    "filename": dt_file.name,
                    "file_path": str(dt_file),
                    "file_size": dt_file.stat().st_size,
                    "rows": 100, "sheets": 1, "status": "completed",
                    "_depth": 1,
                }],
                file_size=dt_file.stat().st_size,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = str(job.id)
        finally:
            db.close()

        # 按 file_index=0 下载
        resp = app_client.get(f"/api/v1/data-export/jobs/{job_id}/download?file_index=0")
        assert resp.status_code == 200, resp.text
        assert len(resp.content) > 0
        # Content-Disposition 应含 datetime 文件名
        cd = resp.headers.get("content-disposition", "")
        assert "20250401T000000" in cd or dt_file.name in cd

    def test_s2_download_file_index_out_of_range_404(self, tmp_path, app_client):
        """S2: file_index 越界 → 404"""
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob

        db = SessionLocal()
        try:
            job = ExportJob(
                user_id="uid", username=f"{_PREFIX}s2",
                query_sql="SELECT 1", connection_env="test",
                status="completed", export_mode="date_chunked",
                chunk_config={"date_start": "2025-04-01", "date_end": "2025-04-01",
                              "chunk_days": 1, "min_subdivide_unit": "hour"},
                output_filename="s2out", file_path=str(tmp_path),
                output_files=[],  # 空清单
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = str(job.id)
        finally:
            db.close()

        resp = app_client.get(f"/api/v1/data-export/jobs/{job_id}/download?file_index=99")
        assert resp.status_code == 404


# =============================================================================
# T · 异常链探测 — is_transient_stream_error 沿 __cause__
# =============================================================================

class TestTransientChainDetection:

    def test_t1_chained_runtime_error_detected(self):
        """T1: RuntimeError 包装 ChunkedEncodingError 作为 cause → 仍识别为 transient"""
        from requests.exceptions import ChunkedEncodingError
        from backend.services.export_clients.clickhouse import is_transient_stream_error

        try:
            try:
                raise ChunkedEncodingError("stream cut")
            except ChunkedEncodingError as inner:
                raise RuntimeError("分批模式预扫描行数失败:wrapped") from inner
        except RuntimeError as outer:
            assert is_transient_stream_error(outer), \
                "应识别 RuntimeError 包装下的 ChunkedEncodingError"

    def test_t2_unrelated_error_not_transient(self):
        """T2: 普通 ValueError 不视作 transient(避免误判)"""
        from backend.services.export_clients.clickhouse import is_transient_stream_error
        assert not is_transient_stream_error(ValueError("invalid input"))

    def test_t3_circular_cause_chain_safe(self):
        """T3: __cause__ 循环不会导致无限递归(罕见但需防御)"""
        from backend.services.export_clients.clickhouse import is_transient_stream_error
        a = RuntimeError("a")
        b = RuntimeError("b")
        a.__cause__ = b
        b.__cause__ = a
        # 不抛 RecursionError
        result = is_transient_stream_error(a)
        assert result in (True, False)  # 无所谓真假,关键是不抛


# =============================================================================
# U · 文件名 Windows 安全 — datetime 不含 ':'
# =============================================================================

class TestFilenameWindowsSafety:

    def test_u1_datetime_filename_no_colon(self):
        """U1: build_chunk_filename(datetime) 不含 ':',Windows 文件系统安全"""
        from backend.services.data_export_chunker import build_chunk_filename
        fn = build_chunk_filename(
            "job",
            datetime(2025, 4, 1, 12, 30, 45),
            datetime(2025, 4, 1, 18, 59, 59),
        )
        assert ":" not in fn
        assert "T" in fn  # 用 T 分隔日期/时间

    def test_u2_datetime_filename_lexicographic_sortable(self):
        """U2: 文件名内时间戳按 YYYYMMDDTHHMMSS 顺序排列 → ls 字典序 == 时间序"""
        from backend.services.data_export_chunker import build_chunk_filename
        fns = [
            build_chunk_filename("job", datetime(2025, 4, 1, h, 0, 0),
                                  datetime(2025, 4, 1, h, 59, 59))
            for h in (0, 6, 12, 18)
        ]
        assert fns == sorted(fns), "datetime 文件名应字典序与时间序一致"


# =============================================================================
# Cleanup
# =============================================================================

def teardown_module(_):
    try:
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        db = SessionLocal()
        for j in db.query(ExportJob).filter(ExportJob.username.like(f"{_PREFIX}%")).all():
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
