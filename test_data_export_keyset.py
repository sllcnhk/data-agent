"""
test_data_export_keyset — Task B 单测

覆盖:
  G · stream_batches_keyset 直接单测(HTTP mock)
  H · _run_single_export 在 cursor_column 提供时走 keyset 路径
  I · chunked export 集成:cursor_column + 块失败回退 → keyset 完成

运行:
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_data_export_keyset.py -v -s
"""
import os
import sys
import uuid
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

from backend.services.export_clients.clickhouse import ClickHouseExportClient  # noqa: E402


_PREFIX = f"_t_keyset_{uuid.uuid4().hex[:6]}_"


# ─────────────────────────────────────────────────────────────────────────────
# 通用 HTTP mock 助手
# ─────────────────────────────────────────────────────────────────────────────

def _make_tsv_response(col_names: List[str], col_types: List[str], rows: List[Tuple]):
    """构造一个 mock 的 requests Response,行为类似 stream=True 流式 TSV 响应"""
    lines = ["\t".join(col_names), "\t".join(col_types)]
    for r in rows:
        lines.append("\t".join(str(c) if c is not None else r"\N" for c in r))
    body = "\n".join(lines) + "\n"
    resp = MagicMock()
    resp.status_code = 200
    resp.content = body.encode("utf-8")
    resp.text = body
    # iter_lines stream 模式
    resp.iter_lines = lambda decode_unicode=True: iter(lines)
    return resp


def _new_client():
    return ClickHouseExportClient(
        host="localhost", port=8123, user="u", password="p", database="d",
    )


# ─────────────────────────────────────────────────────────────────────────────
# G · stream_batches_keyset 直接单测
# ─────────────────────────────────────────────────────────────────────────────

class TestStreamBatchesKeyset:

    def test_g1_first_window_no_where_clause(self):
        """G1: 首次窗口 SQL 应含 ORDER BY + LIMIT,不含 WHERE cursor"""
        client = _new_client()
        captured_sqls: List[str] = []
        captured_params: List[dict] = []

        def _fake_post(url, data=None, params=None, **kw):
            captured_sqls.append(data.decode("utf-8"))
            captured_params.append(dict(params or {}))
            # 第一次返回 1 行 + 第二次返回空(终止)
            if len(captured_sqls) == 1:
                return _make_tsv_response(
                    ["id", "name"], ["Int64", "String"], [("1", "alice")],
                )
            return _make_tsv_response(["id", "name"], ["Int64", "String"], [])

        with patch.object(
            requests.sessions.Session, "post", side_effect=_fake_post,
        ):
            batches = list(client.stream_batches_keyset(
                "SELECT id, name FROM t", cursor_column="id", batch_size=100,
            ))

        assert len(batches) == 1
        assert batches[0] == [("1", "alice")]
        # 第一个 SQL:含 ORDER BY,无 WHERE。cursor 列统一用反引号包裹(v2.14.1
        # 支持含空格/中文的别名列名)
        first_sql = captured_sqls[0]
        assert "ORDER BY `id`" in first_sql
        assert "LIMIT 100" in first_sql
        assert "WHERE" not in first_sql
        # 第一次 params 不含 cursor_val
        assert "param_cursor_val" not in captured_params[0]

    def test_g2_subsequent_window_uses_cursor(self):
        """G2: 后续窗口 SQL 应含 WHERE cursor > {cursor_val:String} ORDER BY + LIMIT,
        URL 参数含 param_cursor_val=<last_value>"""
        client = _new_client()
        captured_sqls: List[str] = []
        captured_params: List[dict] = []

        def _fake_post(url, data=None, params=None, **kw):
            captured_sqls.append(data.decode("utf-8"))
            captured_params.append(dict(params or {}))
            n = len(captured_sqls)
            if n == 1:
                return _make_tsv_response(
                    ["id", "name"], ["Int64", "String"],
                    [("1", "a"), ("2", "b")],
                )
            if n == 2:
                return _make_tsv_response(
                    ["id", "name"], ["Int64", "String"],
                    [("3", "c")],
                )
            return _make_tsv_response(["id", "name"], ["Int64", "String"], [])

        with patch.object(
            requests.sessions.Session, "post", side_effect=_fake_post,
        ):
            batches = list(client.stream_batches_keyset(
                "SELECT id, name FROM t", cursor_column="id", batch_size=100,
            ))

        # 收到 2 个窗口的数据
        assert sum(len(b) for b in batches) == 3
        # 第二次 SQL 应含 WHERE `id` > {cursor_val:String}(反引号包裹)
        second_sql = captured_sqls[1]
        assert "WHERE `id` > {cursor_val:String}" in second_sql
        assert captured_params[1]["param_cursor_val"] == "2"

    def test_g3_empty_first_window_terminates(self):
        """G3: 首次窗口直接空 → 立即终止,不发后续请求"""
        client = _new_client()
        call_count = {"n": 0}

        def _fake_post(url, data=None, params=None, **kw):
            call_count["n"] += 1
            return _make_tsv_response(["id"], ["Int64"], [])

        with patch.object(
            requests.sessions.Session, "post", side_effect=_fake_post,
        ):
            batches = list(client.stream_batches_keyset(
                "SELECT id FROM t", cursor_column="id", batch_size=100,
            ))

        assert batches == []
        assert call_count["n"] == 1  # 仅发了一次请求

    def test_g4_invalid_cursor_column_rejected(self):
        """G4: cursor_column 非合法标识符 → ValueError(前置校验,防绕过)"""
        client = _new_client()
        with pytest.raises(ValueError, match="cursor_column"):
            list(client.stream_batches_keyset(
                "SELECT id FROM t", cursor_column="id; DROP TABLE", batch_size=100,
            ))

    def test_g5_duplicate_cursor_deadloop_detected(self):
        """G5: 两个相邻窗口 last cursor 完全相等(死循环) → RuntimeError fast-fail。
        注意:不严格检查 `>`,因数字字符串字典序 ≠ 数值序("50" > "100" 字典序),
        会误伤合法 cursor 列。只防真正死循环(==)。"""
        client = _new_client()
        seq = iter([
            _make_tsv_response(["id"], ["Int64"], [("5",)]),  # last=5
            _make_tsv_response(["id"], ["Int64"], [("5",)]),  # 死循环:== last
        ])

        def _fake_post(url, data=None, params=None, **kw):
            return next(seq)

        with patch.object(
            requests.sessions.Session, "post", side_effect=_fake_post,
        ):
            with pytest.raises(RuntimeError, match="死循环"):
                list(client.stream_batches_keyset(
                    "SELECT id FROM t", cursor_column="id", batch_size=100,
                ))

    def test_g7_cursor_column_with_space_uses_backtick_in_sql(self):
        """G7(v2.14.1): cursor_column='Call ID'(含空格,用户 SELECT 别名)→ SQL
        必须用反引号包裹 \`Call ID\`,且 col_names.index 用裸字符串 'Call ID' 查找成功"""
        client = _new_client()
        captured_sqls: List[str] = []

        def _fake_post(url, data=None, params=None, **kw):
            captured_sqls.append(data.decode("utf-8"))
            n = len(captured_sqls)
            if n == 1:
                return _make_tsv_response(
                    ["Call ID", "Name"], ["String", "String"],
                    [("CALL_1", "alice"), ("CALL_2", "bob")],
                )
            if n == 2:
                return _make_tsv_response(
                    ["Call ID", "Name"], ["String", "String"],
                    [("CALL_3", "carol")],
                )
            return _make_tsv_response(["Call ID", "Name"], ["String", "String"], [])

        with patch.object(requests.sessions.Session, "post", side_effect=_fake_post):
            batches = list(client.stream_batches_keyset(
                "SELECT cr.call_record_id as `Call ID`, cr.name as Name FROM t",
                cursor_column="Call ID", batch_size=100,
            ))

        # 数据正常推进
        all_rows = [r for b in batches for r in b]
        assert len(all_rows) == 3
        # 首窗口 SQL 含 ORDER BY `Call ID`(反引号包裹含空格列名)
        assert "ORDER BY `Call ID`" in captured_sqls[0]
        # 后续窗口含 WHERE `Call ID` >(同样反引号包裹)
        assert "WHERE `Call ID` > {cursor_val:String}" in captured_sqls[1]

    def test_g8_cursor_column_backtick_input_stripped(self):
        """G8(v2.14.1): cursor_column='`id`'(用户带反引号填)→ strip 后等价于 'id'"""
        client = _new_client()
        captured_sqls: List[str] = []

        def _fake_post(url, data=None, params=None, **kw):
            captured_sqls.append(data.decode("utf-8"))
            n = len(captured_sqls)
            if n == 1:
                return _make_tsv_response(["id"], ["Int64"], [("1",)])
            return _make_tsv_response(["id"], ["Int64"], [])

        with patch.object(requests.sessions.Session, "post", side_effect=_fake_post):
            list(client.stream_batches_keyset(
                "SELECT id FROM t", cursor_column="`id`", batch_size=100,
            ))

        assert "ORDER BY `id`" in captured_sqls[0]

    def test_g6_cursor_column_not_in_select_raises(self):
        """G6: SELECT * 结果不含 cursor_column 列 → RuntimeError(用户 SQL SELECT 子集)"""
        client = _new_client()

        def _fake_post(url, data=None, params=None, **kw):
            # 返回的列名不含 'id'(用户 SQL SELECT name FROM t)
            return _make_tsv_response(["name"], ["String"], [("alice",)])

        with patch.object(
            requests.sessions.Session, "post", side_effect=_fake_post,
        ):
            with pytest.raises(RuntimeError, match="未在 SELECT"):
                list(client.stream_batches_keyset(
                    "SELECT name FROM t", cursor_column="id", batch_size=100,
                ))


# ─────────────────────────────────────────────────────────────────────────────
# H · _run_single_export 选路 — cursor_column → keyset
# ─────────────────────────────────────────────────────────────────────────────

class TestRunSingleExportKeysetRouting:

    def test_h1_cursor_column_routes_to_keyset(self, tmp_path):
        """H1: _run_single_export 在 use_chunked=True + cursor_column 提供时
        调用 stream_batches_keyset 而不是 stream_batches_chunked"""
        import backend.services.data_export_service as svc
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob

        # 创建 pending Job
        db = SessionLocal()
        job = ExportJob(
            user_id="uid", username=f"{_PREFIX}h1",
            query_sql="SELECT id, name FROM t", connection_env="test",
            status="pending", export_mode="single",
            output_filename="h1.xlsx", file_path=str(tmp_path / "h1.xlsx"),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        from requests.exceptions import ChunkedEncodingError

        def _stream(sql, **kwargs):
            # 第一次直接抛 transient → 触发 use_chunked
            raise ChunkedEncodingError("simulated")
            yield

        keyset_called = {"yes": False}

        def _keyset(sql, cursor_column, **kwargs):
            keyset_called["yes"] = True
            assert cursor_column == "id"
            yield [("1", "a"), ("2", "b")]

        def _mk_client(*args, **kw):
            from backend.services.export_clients.base import ColumnInfo
            mc = Mock()
            mc.get_columns.return_value = [ColumnInfo("id", "Int64"), ColumnInfo("name", "String")]
            mc.stream_batches.side_effect = _stream
            mc.stream_batches_keyset.side_effect = _keyset
            return mc

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            result = svc._run_single_export(
                job_id=job_id,
                sql="SELECT id, name FROM t",
                env="test", conn_type="clickhouse",
                batch_size=100,
                output_path=str(tmp_path / "h1.xlsx"),
                cursor_column="id",
            )

        assert keyset_called["yes"], "expected stream_batches_keyset called when cursor_column set"
        assert result["exported_rows"] == 2

    def test_h2_no_cursor_column_uses_legacy_chunked(self, tmp_path):
        """H2: 无 cursor_column → fallback 仍用 stream_batches_chunked(老路径,零回归)"""
        import backend.services.data_export_service as svc
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        job = ExportJob(
            user_id="uid", username=f"{_PREFIX}h2",
            query_sql="SELECT id FROM t", connection_env="test",
            status="pending", export_mode="single",
            output_filename="h2.xlsx", file_path=str(tmp_path / "h2.xlsx"),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        def _stream(sql, **kwargs):
            raise ChunkedEncodingError("simulated")
            yield

        chunked_called = {"yes": False}

        def _chunked(sql, **kwargs):
            chunked_called["yes"] = True
            yield [("1",)]

        def _mk_client(*args, **kw):
            from backend.services.export_clients.base import ColumnInfo
            mc = Mock()
            mc.get_columns.return_value = [ColumnInfo("id", "Int64")]
            mc.stream_batches.side_effect = _stream
            mc.count_rows.return_value = 1
            mc.stream_batches_chunked.side_effect = _chunked
            return mc

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            result = svc._run_single_export(
                job_id=job_id,
                sql="SELECT id FROM t",
                env="test", conn_type="clickhouse",
                batch_size=100,
                output_path=str(tmp_path / "h2.xlsx"),
                # 不传 cursor_column
            )

        assert chunked_called["yes"], "expected stream_batches_chunked called as legacy fallback"
        assert result["exported_rows"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# I · chunked job 集成 — cursor_column + 块失败 → keyset 完成
# ─────────────────────────────────────────────────────────────────────────────

class TestChunkedKeysetIntegration:

    def test_i1_chunked_job_cursor_column_used_on_fallback(self, tmp_path):
        """I1: chunked job 配 cursor_column,第一块流式断开 → fallback 走 keyset 完成"""
        import asyncio
        import backend.services.data_export_service as svc
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        from requests.exceptions import ChunkedEncodingError

        db = SessionLocal()
        out_dir = tmp_path / "i1_out"
        job = ExportJob(
            user_id="uid", username=f"{_PREFIX}i1",
            query_sql="SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            connection_env="test", status="pending",
            export_mode="date_chunked",
            chunk_config={
                "date_start": "2025-04-01", "date_end": "2025-04-05",
                "chunk_days": 5, "cursor_column": "id",
            },
            output_filename=out_dir.name,
            file_path=str(out_dir),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = str(job.id)
        db.close()

        keyset_calls = {"n": 0}

        def _mk_client(*args, **kw):
            from backend.services.export_clients.base import ColumnInfo
            mc = Mock()
            mc.get_columns.return_value = [
                ColumnInfo("id", "Int64"), ColumnInfo("name", "String"),
            ]

            def _stream(sql, **kwargs):
                # 整块流式直接抛 → 触发 keyset fallback
                raise ChunkedEncodingError("simulated stream disconnect")
                yield

            def _keyset(sql, cursor_column, **kwargs):
                keyset_calls["n"] += 1
                assert cursor_column == "id"
                yield [("1", "a"), ("2", "b"), ("3", "c")]

            mc.stream_batches.side_effect = _stream
            mc.stream_batches_keyset.side_effect = _keyset
            return mc

        config = {
            "query_sql": "SELECT id, name FROM t WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'",
            "connection_env": "test", "connection_type": "clickhouse",
            "batch_size": 100, "export_mode": "date_chunked",
            "chunk_config": {
                "date_start": "2025-04-01", "date_end": "2025-04-05",
                "chunk_days": 5, "cursor_column": "id",
            },
            "output_dir": str(out_dir), "job_name": "i1job",
        }

        with patch("backend.services.data_export_service._build_export_client", side_effect=_mk_client):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(svc.run_export_job(job_id, config))
            finally:
                loop.close()

        db = SessionLocal()
        try:
            j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            assert j.status == "completed", f"got {j.status}, err={j.error_message}"
            assert keyset_calls["n"] >= 1
            assert j.exported_rows == 3
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────

def teardown_module(module):
    """删除测试创建的 Job"""
    try:
        from backend.config.database import SessionLocal
        from backend.models.export_job import ExportJob
        import shutil
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
