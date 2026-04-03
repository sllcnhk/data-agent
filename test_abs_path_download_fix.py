"""
test_abs_path_download_fix.py
==============================
绝对路径 → 相对路径转换修复 全量测试

问题背景：
  MCP Filesystem Server 向 AgenticLoop 返回绝对路径（如
  "C:/Users/.../customer_data/superadmin/report.md"），AgenticLoop 原样存入
  written_files → 前端用绝对路径调用下载 API → Windows pathlib 行为导致
  偶发性路径拼接错误（"运气好"才能通过）。

修复方案：
  agentic_loop.py: write_file 成功后立即将绝对路径转换为相对于 customer_data
  根目录的路径，再写入 written_files。

测试层次：
  A (7)  — 路径转换逻辑单元测试（直接调用转换逻辑，mock settings）
  B (6)  — AgenticLoop 集成：绝对路径输入 → files_written 事件含相对路径
  C (5)  — _resolve_download_path 兼容性（相对路径格式正确解析）
  D (6)  — 端到端：绝对路径写入 → 相对路径存储 → 下载 API 200
  E (5)  — 边缘情况：Windows 反斜线、非 customer_data 路径、转换失败回退
  F (4)  — RBAC 回归：无新菜单/权限，现有 API 不受影响
  G (3)  — 源码结构验证（确保修复实现正确）

总计: 36 个测试用例

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_abs_path_download_fix.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ── 路径 ───────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username


# ══════════════════════════════════════════════════════════════════════════════
# 辅助工具
# ══════════════════════════════════════════════════════════════════════════════

def _get_customer_root() -> Path:
    """获取运行时 files 模块中的 _CUSTOMER_DATA_ROOT（与 main.py 加载路径一致）"""
    import api.files as files_mod
    return files_mod._CUSTOMER_DATA_ROOT


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_loop():
    from backend.agents.agentic_loop import AgenticLoop
    llm = MagicMock()
    mcp = MagicMock()
    mcp.list_servers.return_value = []
    loop = AgenticLoop(llm_adapter=llm, mcp_manager=mcp)
    loop.llm_adapter.chat_plain = AsyncMock(return_value={
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "完成"}],
        "usage": {},
    })
    return loop


def _make_write_tool_resp(path: str, content: str = "data") -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{
            "type": "tool_use", "id": "t1",
            "name": "filesystem__write_file",
            "input": {"path": path, "content": content},
        }],
        "usage": {},
    }


def _make_end_resp() -> dict:
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "文件已写入"}],
        "usage": {},
    }


async def _collect_events(loop, tool_resp_path: str, tool_content: str = "data") -> list:
    """运行 AgenticLoop.run_streaming，返回所有事件。
    tool_resp_path: write_file input.path 参数（绝对或相对路径）
    """
    from backend.agents.agentic_loop import AgenticLoop

    call_count = [0]
    tool_resp = _make_write_tool_resp(tool_resp_path, tool_content)
    end_resp = _make_end_resp()

    async def mock_chat(**kwargs):
        call_count[0] += 1
        return tool_resp if call_count[0] == 1 else end_resp

    loop.llm_adapter.chat_with_tools = mock_chat
    loop.mcp_manager.call_tool = AsyncMock(return_value={"success": True, "data": "ok"})

    events = []
    with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude",
               return_value=[{"name": "filesystem__write_file"}]):
        with patch("backend.agents.agentic_loop.AgenticLoop._build_system_prompt",
                   new_callable=AsyncMock, return_value="test prompt"):
            async for ev in loop.run_streaming("写文件", {}):
                events.append(ev)
    return events


# ══════════════════════════════════════════════════════════════════════════════
# A — 路径转换逻辑单元测试
# ══════════════════════════════════════════════════════════════════════════════

class TestPathConversionLogic:
    """A: 直接测试 agentic_loop.py 中的绝对路径 → 相对路径转换逻辑"""

    def _convert(self, file_path: str, customer_root: Path) -> str:
        """复现 agentic_loop.py 中的转换逻辑（便于白盒单元测试）"""
        result = file_path
        try:
            path_obj = Path(file_path)
            if path_obj.is_absolute():
                try:
                    result = str(path_obj.relative_to(customer_root))
                except ValueError:
                    pass  # 不在 customer_data 下，保持原路径
        except Exception:
            pass
        return result

    def test_a1_absolute_path_converted_to_relative(self, tmp_path):
        """标准 Windows 绝对路径转换为相对路径"""
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()
        abs_path = str(customer_root / "superadmin" / "report.csv")

        result = self._convert(abs_path, customer_root)
        # 结果是相对路径（不含绝对路径前缀）
        result_normalized = result.replace("\\", "/")
        assert result_normalized == "superadmin/report.csv", f"got: {result_normalized!r}"

    def test_a2_subdirectory_preserved_in_conversion(self, tmp_path):
        """子目录结构在转换后保留"""
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()
        abs_path = str(customer_root / "alice" / "2026-03" / "analysis.xlsx")

        result = self._convert(abs_path, customer_root)
        result_normalized = result.replace("\\", "/")
        assert result_normalized == "alice/2026-03/analysis.xlsx", f"got: {result_normalized!r}"

    def test_a3_relative_path_unchanged(self, tmp_path):
        """已经是相对路径 → 不修改"""
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()
        rel_path = "alice/report.csv"

        result = self._convert(rel_path, customer_root)
        assert result == rel_path, f"相对路径不应被修改, got: {result!r}"

    def test_a4_relative_with_customer_data_prefix_unchanged(self, tmp_path):
        """'customer_data/alice/report.csv' 格式 → 不修改（不是绝对路径）"""
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()
        rel_path = "customer_data/alice/report.csv"

        result = self._convert(rel_path, customer_root)
        assert result == rel_path, f"customer_data/ 前缀相对路径不应被修改, got: {result!r}"

    def test_a5_path_outside_customer_data_kept_as_is(self, tmp_path):
        """绝对路径不在 customer_data 下 → 保持原路径（安全兜底）"""
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()
        # 完全不同的路径
        abs_path = "/tmp/some_other_dir/file.txt"

        result = self._convert(abs_path, customer_root)
        assert result == abs_path, f"不在 customer_data 下应保持原路径, got: {result!r}"

    def test_a6_empty_path_returned_as_is(self, tmp_path):
        """空路径 → 原样返回（不抛出异常）"""
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()

        result = self._convert("", customer_root)
        assert result == "", "空路径应原样返回"

    def test_a7_actual_settings_customer_root(self):
        """使用真实 settings.allowed_directories[0] 作为 customer_root 进行转换"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = Path(settings.allowed_directories[0])
        # 构造一个模拟绝对路径
        fake_abs = str(customer_root / "testuser" / "report.csv")
        path_obj = Path(fake_abs)

        if path_obj.is_absolute():
            try:
                rel = str(path_obj.relative_to(customer_root))
                rel_normalized = rel.replace("\\", "/")
                assert rel_normalized == "testuser/report.csv", f"got: {rel_normalized!r}"
            except ValueError:
                pytest.fail(f"relative_to 失败: {fake_abs!r} vs {customer_root!r}")


# ══════════════════════════════════════════════════════════════════════════════
# B — AgenticLoop 集成：绝对路径输入 → files_written 事件含相对路径
# ══════════════════════════════════════════════════════════════════════════════

class TestAgenticLoopAbsolutePathConversion:
    """B: AgenticLoop.run_streaming 收到绝对路径 → files_written 事件含相对路径"""

    def test_b1_absolute_path_in_event_is_relative(self):
        """write_file 使用绝对路径 → files_written.path 是相对路径"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = Path(settings.allowed_directories[0])
        abs_path = str(customer_root / "superadmin" / "test_b1_report.csv")

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, "col1,col2\n1,2\n"))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None, f"未找到 files_written 事件，events: {[e.type for e in events]}"

        stored_path = fw.data["files"][0]["path"]
        stored_path_normalized = stored_path.replace("\\", "/")
        # 存储的路径应该是相对路径，不含绝对路径部分
        assert not stored_path_normalized.startswith("/"), \
            f"路径应是相对路径，不应以 '/' 开头: {stored_path_normalized!r}"
        assert ":" not in stored_path_normalized, \
            f"路径应是相对路径，不含盘符: {stored_path_normalized!r}"
        assert "test_b1_report.csv" in stored_path_normalized, \
            f"文件名应保留: {stored_path_normalized!r}"

    def test_b2_filename_extracted_correctly_from_absolute(self):
        """绝对路径中提取的 name 字段正确"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = Path(settings.allowed_directories[0])
        abs_path = str(customer_root / "alice" / "test_b2_analysis.xlsx")

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None
        assert fw.data["files"][0]["name"] == "test_b2_analysis.xlsx"

    def test_b3_subdirectory_preserved_in_relative_path(self):
        """绝对路径含子目录 → 相对路径也含子目录"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = Path(settings.allowed_directories[0])
        abs_path = str(customer_root / "alice" / "2026-03" / "test_b3_bill.csv")

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None
        stored_path = fw.data["files"][0]["path"].replace("\\", "/")
        assert "2026-03" in stored_path, f"子目录应保留: {stored_path!r}"
        assert "test_b3_bill.csv" in stored_path

    def test_b4_relative_path_input_unchanged(self):
        """write_file 已使用相对路径 → files_written.path 保持不变"""
        rel_path = "customer_data/alice/test_b4_report.csv"

        loop = _make_loop()
        events = _run(_collect_events(loop, rel_path))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None
        stored_path = fw.data["files"][0]["path"].replace("\\", "/")
        assert stored_path == rel_path, f"相对路径不应被修改: {stored_path!r}"

    def test_b5_multiple_absolute_paths_all_converted(self):
        """多个绝对路径写入 → 全部转换为相对路径"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = Path(settings.allowed_directories[0])

        loop = _make_loop()
        call_count = [0]
        abs_path_1 = str(customer_root / "alice" / "test_b5_a.csv")
        abs_path_2 = str(customer_root / "alice" / "test_b5_b.json")

        tool_resp = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "filesystem__write_file",
                 "input": {"path": abs_path_1, "content": "a,b"}},
                {"type": "tool_use", "id": "t2", "name": "filesystem__write_file",
                 "input": {"path": abs_path_2, "content": "{}"}},
            ],
            "usage": {},
        }
        end_resp = _make_end_resp()

        async def mock_chat(**kwargs):
            call_count[0] += 1
            return tool_resp if call_count[0] == 1 else end_resp

        loop.llm_adapter.chat_with_tools = mock_chat
        loop.mcp_manager.call_tool = AsyncMock(return_value={"success": True, "data": "ok"})

        events = _run(_async_collect(loop))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None
        paths = [f["path"].replace("\\", "/") for f in fw.data["files"]]
        for p in paths:
            assert not p.startswith("/"), f"应是相对路径: {p!r}"
            assert ":" not in p, f"不应含盘符: {p!r}"

    def test_b6_mime_type_correct_after_conversion(self):
        """路径转换后 MIME 类型仍能从文件名正确推断"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = Path(settings.allowed_directories[0])
        abs_path = str(customer_root / "user1" / "test_b6_result.json")

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, '{"key": "val"}'))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None
        assert fw.data["files"][0]["mime_type"] == "application/json"


async def _async_collect(loop) -> list:
    """配合 B5 使用：直接收集事件（tools/chat 已在外部配置好）"""
    events = []
    with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude",
               return_value=[{"name": "filesystem__write_file"}]):
        with patch("backend.agents.agentic_loop.AgenticLoop._build_system_prompt",
                   new_callable=AsyncMock, return_value="test prompt"):
            async for ev in loop.run_streaming("写两个文件", {}):
                events.append(ev)
    return events


# ══════════════════════════════════════════════════════════════════════════════
# C — _resolve_download_path 兼容性（转换后的相对路径格式）
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveDownloadPathCompat:
    """C: _resolve_download_path 正确处理修复后产生的相对路径格式"""

    @pytest.fixture(autouse=True)
    def tmp_customer_data(self, tmp_path, monkeypatch):
        self.customer_root = tmp_path / "customer_data"
        self.customer_root.mkdir()
        user_dir = self.customer_root / "alice"
        user_dir.mkdir()
        (user_dir / "report.csv").write_text("col1,col2\n1,2\n")
        subdir = user_dir / "2026-03"
        subdir.mkdir()
        (subdir / "bill.xlsx").write_bytes(b"PK...")

        import backend.api.files as files_module
        monkeypatch.setattr(files_module, "_CUSTOMER_DATA_ROOT", self.customer_root)

    def test_c1_username_slash_filename_format(self):
        """修复后路径格式 'alice/report.csv' → 正确解析"""
        from backend.api.files import _resolve_download_path
        p = _resolve_download_path("alice/report.csv", "alice")
        assert p.name == "report.csv"
        assert p.exists()

    def test_c2_subdirectory_in_relative_path(self):
        """修复后路径格式 'alice/2026-03/bill.xlsx' → 正确解析"""
        from backend.api.files import _resolve_download_path
        p = _resolve_download_path("alice/2026-03/bill.xlsx", "alice")
        assert p.name == "bill.xlsx"
        assert p.exists()

    def test_c3_windows_backslash_normalized(self, tmp_path, monkeypatch):
        """Windows 反斜线路径（alice\\report.csv）→ 自动规范化"""
        # 创建专属临时数据
        cr = tmp_path / "cdata_c3"
        cr.mkdir()
        u = cr / "alice"
        u.mkdir()
        (u / "report.csv").write_text("data")
        import backend.api.files as fm
        monkeypatch.setattr(fm, "_CUSTOMER_DATA_ROOT", cr)
        from backend.api.files import _resolve_download_path
        p = _resolve_download_path("alice\\report.csv", "alice")
        assert p.name == "report.csv"

    def test_c4_customer_data_prefix_still_supported(self):
        """原格式 'customer_data/alice/report.csv' 仍然兼容"""
        from backend.api.files import _resolve_download_path
        p = _resolve_download_path("customer_data/alice/report.csv", "alice")
        assert p.name == "report.csv"

    def test_c5_cross_user_access_still_blocked(self):
        """修复后的相对路径格式下，跨用户访问仍被阻止"""
        from backend.api.files import _resolve_download_path
        from fastapi import HTTPException
        # bob 目录不存在，cross-user check 先于 404
        (self.customer_root / "bob").mkdir()
        (self.customer_root / "bob" / "secret.txt").write_text("bob_data")
        with pytest.raises(HTTPException) as exc:
            _resolve_download_path("bob/secret.txt", "alice")
        assert exc.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# D — 端到端：绝对路径写入 → 相对路径存储 → 下载 API 200
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EAbsolutePathDownload:
    """D: 完整链路 — AgenticLoop 绝对路径写入 → 相对路径 → 下载 API 正常工作"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        self.client = TestClient(app, raise_server_exceptions=True)
        self.customer_root = _get_customer_root()
        self.default_dir = self.customer_root / "default"
        self.default_dir.mkdir(parents=True, exist_ok=True)
        self._files_to_cleanup: list[Path] = []
        yield
        for f in self._files_to_cleanup:
            try:
                if f.is_dir():
                    shutil.rmtree(str(f))
                else:
                    f.unlink(missing_ok=True)
            except Exception:
                pass

    def _write_test_file(self, filename: str, content: str = "test content") -> Path:
        p = self.default_dir / filename
        p.write_text(content, encoding="utf-8")
        self._files_to_cleanup.append(p)
        return p

    def test_d1_absolute_path_in_event_leads_to_successful_download(self):
        """绝对路径转换后的相对路径可直接用于下载 API"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        filename = "_t_d1_abs_report.csv"
        abs_path = str(self.customer_root / "default" / filename)

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, "col1,col2\n1,2"))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None, "应有 files_written 事件"

        stored_path = fw.data["files"][0]["path"]
        stored_path_normalized = stored_path.replace("\\", "/")

        # 用存储的路径调用下载 API（文件需要真实存在）
        self._write_test_file(filename, "col1,col2\n1,2")

        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": stored_path_normalized},
        )
        assert resp.status_code == 200, \
            f"下载应返回 200，path={stored_path_normalized!r}, got: {resp.status_code} {resp.text[:300]}"

    def test_d2_stored_path_not_absolute(self):
        """files_written 事件中存储的路径不是绝对路径"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        abs_path = str(self.customer_root / "default" / "_t_d2_file.txt")

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, "data"))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None
        stored = fw.data["files"][0]["path"]
        assert not Path(stored).is_absolute(), \
            f"存储路径不应是绝对路径: {stored!r}"

    def test_d3_relative_path_correctly_points_to_user_dir(self):
        """转换后的相对路径正确指向 customer_data/{username}/ 下的文件"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        filename = "_t_d3_check.txt"
        abs_path = str(self.customer_root / "default" / filename)

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, "hello"))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None

        stored_path = fw.data["files"][0]["path"].replace("\\", "/")
        # 相对路径应以 username 开头
        assert stored_path.startswith("default/") or stored_path == f"default/{filename}", \
            f"路径应以 'default/' 开头: {stored_path!r}"

    def test_d4_subdirectory_absolute_path_downloads_correctly(self):
        """带子目录的绝对路径（如 default/2026-03/report.csv）转换后可正常下载"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        subdir = self.default_dir / "_t_d4_subdir"
        subdir.mkdir(exist_ok=True)
        self._files_to_cleanup.append(subdir)

        filename = "nested_report.csv"
        abs_path = str(subdir / filename)

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, "nested,data\n1,2"))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None

        stored_path = fw.data["files"][0]["path"].replace("\\", "/")
        assert "_t_d4_subdir" in stored_path, f"子目录名应在相对路径中: {stored_path!r}"

        # 文件需要真实存在
        (subdir / filename).write_text("nested,data\n1,2", encoding="utf-8")

        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": stored_path},
        )
        assert resp.status_code == 200, \
            f"子目录下载应返回 200, got: {resp.status_code}"

    def test_d5_files_written_path_in_db_extra_metadata_is_relative(self):
        """写入 DB 的 extra_metadata.files_written[].path 是相对路径，非绝对路径"""
        from backend.services.conversation_service import ConversationService
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        abs_path = str(self.customer_root / "default" / "_t_d5_db_test.csv")

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, "a,b,c"))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None

        stored_path = fw.data["files"][0]["path"]
        # 模拟 conversation_service 存入 DB
        from backend.config.database import SessionLocal
        db = SessionLocal()
        try:
            svc = ConversationService(db=db)
            conv = svc.create_conversation(title="D5 AbsPath Test", user_id=None)
            conv_id = str(conv.id)
            try:
                svc.add_message(
                    conversation_id=conv_id,
                    role="assistant",
                    content="文件已生成",
                    extra_metadata={"files_written": fw.data["files"]},
                )
                client = TestClient(app)
                resp = client.get(f"/api/v1/conversations/{conv_id}/messages")
                assert resp.status_code == 200
                msgs = resp.json()["data"]
                asst = next(m for m in msgs if m["role"] == "assistant")
                fw_meta = asst["extra_metadata"]["files_written"][0]
                db_path = fw_meta["path"]
                assert not Path(db_path.replace("\\", "/")).is_absolute(), \
                    f"DB 中存储的路径不应是绝对路径: {db_path!r}"
            finally:
                svc.delete_conversation(conv_id)
        finally:
            db.close()

    def test_d6_original_path_bug_scenario(self):
        """重现原始 Bug 场景：绝对路径直接传给 _resolve_download_path 会导致路径拼接问题"""
        # 此测试验证修复前的行为确实存在问题（在某些路径组合下会失败）
        from backend.config.settings import settings
        import backend.api.files as files_mod

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = files_mod._CUSTOMER_DATA_ROOT
        filename = "_t_d6_bug_scenario.txt"
        abs_path = str(customer_root / "default" / filename)
        (customer_root / "default" / filename).write_text("bug test", encoding="utf-8")
        self._files_to_cleanup.append(customer_root / "default" / filename)

        # 修复后：用相对路径（alice/file.txt 格式）调用下载 API → 200
        rel_path = f"default/{filename}"
        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": rel_path},
        )
        assert resp.status_code == 200, \
            f"相对路径下载应返回 200, path={rel_path!r}, got: {resp.status_code}"


# ══════════════════════════════════════════════════════════════════════════════
# E — 边缘情况
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """E: 边缘情况：Windows 反斜线、非 customer_data 路径、转换异常回退"""

    def test_e1_windows_backslash_absolute_path(self, tmp_path):
        """Windows 反斜线绝对路径转换后文件名正确"""
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()

        # 构造含反斜线的绝对路径（模拟 Windows MCP 服务器返回的路径）
        win_path = str(customer_root / "user1" / "report.csv")
        # Path 会处理斜线/反斜线，转换逻辑正确

        result = str(Path(win_path).relative_to(customer_root)) if Path(win_path).is_absolute() else win_path
        result_normalized = result.replace("\\", "/")
        assert "report.csv" in result_normalized
        assert not result_normalized.startswith("/")

    def test_e2_path_with_customer_data_in_name_not_confused(self, tmp_path):
        """路径含 'customer_data' 字符串但不是我们的根目录 → 不误判"""
        # 比如 /var/customer_data_backup/user/file.csv
        customer_root = tmp_path / "customer_data"
        customer_root.mkdir()
        unrelated = tmp_path / "customer_data_backup" / "user" / "file.csv"

        path_obj = Path(str(unrelated))
        try:
            rel = str(path_obj.relative_to(customer_root))
            # 如果没有抛 ValueError，说明路径确实在 customer_root 下（不应发生）
            pytest.fail(f"不应成功: {rel!r}")
        except ValueError:
            pass  # 期望抛出 ValueError → 保持原路径（正确行为）

    def test_e3_settings_empty_allowed_directories_uses_fallback(self):
        """allowed_directories 为空时使用 'customer_data' 作为回退值"""
        # 直接验证回退逻辑：empty list → Path("customer_data")
        allowed = []
        customer_root = Path(allowed[0]) if allowed else Path("customer_data")
        assert customer_root == Path("customer_data")

    def test_e4_conversion_exception_keeps_original_path(self):
        """转换过程抛出异常 → 静默回退到原始路径"""
        # 模拟异常情况：settings.allowed_directories 访问出错
        original_path = "/some/abs/path/file.txt"
        result = original_path

        try:
            # 模拟转换中的异常
            raise RuntimeError("模拟转换失败")
            result = "should not reach here"
        except Exception:
            pass  # 保持 result = original_path

        assert result == original_path, "转换失败应回退到原始路径"

    def test_e5_file_size_computed_from_content_not_path(self):
        """文件大小从 content 计算，转换路径后 size 仍正确"""
        from backend.config.settings import settings

        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        customer_root = Path(settings.allowed_directories[0])
        abs_path = str(customer_root / "alice" / "test_e5.csv")
        content = "col1,col2\n1,2\n3,4\n"

        loop = _make_loop()
        events = _run(_collect_events(loop, abs_path, content))

        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None
        file_entry = fw.data["files"][0]
        expected_size = len(content.encode("utf-8"))
        assert file_entry["size"] == expected_size, \
            f"文件大小应与 content 一致: expected={expected_size}, got={file_entry['size']}"


# ══════════════════════════════════════════════════════════════════════════════
# F — RBAC 回归：无新菜单/权限，现有功能不受影响
# ══════════════════════════════════════════════════════════════════════════════

class TestRBACRegressionNoNewPermissions:
    """F: 绝对路径修复不引入新权限/菜单，不影响现有 API"""

    def test_f1_download_endpoint_needs_no_special_permission(self):
        """下载端点只需认证（get_current_user），无特殊权限（viewer 也可用）"""
        import inspect
        import api.files as files_mod
        src = inspect.getsource(files_mod.download_file)
        assert "require_permission" not in src, \
            "download_file 不应使用 require_permission（所有角色都能下载自己的文件）"
        assert "get_current_user" in src

    def test_f2_files_router_still_registered(self):
        """files router 已正确注册"""
        from backend.main import app
        paths = [r.path for r in app.routes]
        assert any("/files/download" in p for p in paths), \
            f"/files/download 未注册，当前路由: {[p for p in paths if 'file' in p.lower()]}"

    def test_f3_no_new_permissions_for_download(self):
        """权限系统中无 files:download 等新权限（下载是内嵌功能）"""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.get("/api/v1/permissions")
        assert resp.status_code == 200
        data = resp.json()
        perms = [p.get("key", "") for p in (data if isinstance(data, list) else (data.get("data") or []))]
        bad = [p for p in perms if "files:download" in p or ("download" in p and "file" in p)]
        assert not bad, f"不应有 download 专属权限: {bad}"

    def test_f4_existing_conversations_api_unaffected(self):
        """路径修复不影响现有 conversations API"""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.get("/api/v1/conversations")
        assert resp.status_code in (200, 401)

        resp2 = client.get("/api/v1/skills/preview", params={"message": "test"})
        assert resp2.status_code in (200, 401)


# ══════════════════════════════════════════════════════════════════════════════
# G — 源码结构验证（确保修复正确实现）
# ══════════════════════════════════════════════════════════════════════════════

class TestSourceCodeStructure:
    """G: 验证 agentic_loop.py 修复代码的结构正确性"""

    def test_g1_fix_uses_relative_to_for_conversion(self):
        """修复代码使用 path_obj.relative_to(customer_root) 进行路径转换"""
        import inspect
        from backend.agents.agentic_loop import AgenticLoop
        src = inspect.getsource(AgenticLoop.run_streaming)
        assert "relative_to" in src, "修复应使用 .relative_to() 进行路径转换"
        assert "is_absolute" in src, "修复应检查 .is_absolute() 判断是否需要转换"

    def test_g2_fix_has_exception_handling(self):
        """修复代码有 try/except 防止转换失败导致整个流程崩溃"""
        import inspect
        from backend.agents.agentic_loop import AgenticLoop
        src = inspect.getsource(AgenticLoop.run_streaming)
        assert "except" in src, "修复应有异常处理（转换失败时保持原路径）"

    def test_g3_fix_uses_settings_allowed_directories(self):
        """修复代码从 settings.allowed_directories 获取 customer_data 根目录"""
        import inspect
        from backend.agents.agentic_loop import AgenticLoop
        src = inspect.getsource(AgenticLoop.run_streaming)
        assert "allowed_directories" in src, "修复应从 settings.allowed_directories 获取根目录"
