"""
test_system_prompt.py
验证 AgenticLoop._build_system_prompt() 的 filesystem 指引注入逻辑：
  - 有 filesystem 服务器时注入文件操作规则
  - 无 filesystem / 仅有 clickhouse 时不注入
  - 自定义 system_prompt 被正确保留
  - 无任何服务器时 tools_info 为空
  - 指引内容包含关键指令（write_file、相对路径说明等）
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.agents.agentic_loop import AgenticLoop


# ──────────────────────────────────────────────────────────
# Mock 依赖
# ──────────────────────────────────────────────────────────

class _MockMCPBase:
    servers = {}

    async def call_tool(self, *a, **kw):
        return {"success": True, "data": ""}


class MockMCPNoServers(_MockMCPBase):
    def list_servers(self):
        return []


class MockMCPClickhouseOnly(_MockMCPBase):
    def list_servers(self):
        return [
            {"name": "clickhouse-idn", "type": "clickhouse", "tool_count": 17},
        ]


class MockMCPWithFilesystem(_MockMCPBase):
    def list_servers(self):
        return [
            {"name": "clickhouse-idn", "type": "clickhouse", "tool_count": 17},
            {"name": "Filesystem MCP Server", "type": "filesystem", "tool_count": 8},
        ]


class MockMCPFilesystemOnly(_MockMCPBase):
    def list_servers(self):
        return [
            {"name": "Filesystem MCP Server", "type": "filesystem", "tool_count": 8},
        ]


class _FakeFilesystemServer:
    """模拟 FilesystemMCPServer，带 allowed_directories 属性。"""
    allowed_directories = ["/data/project", "/tmp/sandbox"]


class MockMCPWithFilesystemDirs(_MockMCPBase):
    """带真实 allowed_directories 的 filesystem mock。"""
    servers = {"filesystem": _FakeFilesystemServer()}

    def list_servers(self):
        return [
            {"name": "Filesystem MCP Server", "type": "filesystem", "tool_count": 8},
        ]


class MockLLMAdapter:
    async def chat_plain(self, *a, **kw):
        return {"stop_reason": "end_turn", "content": []}

    async def chat_with_tools(self, *a, **kw):
        return {"stop_reason": "end_turn", "content": []}


def _build_prompt(mcp_manager, system_prompt="", message=""):
    """直接调用 _build_system_prompt 辅助函数。"""
    loop = AgenticLoop(
        llm_adapter=MockLLMAdapter(),
        mcp_manager=mcp_manager,
    )
    context = {"history": [], "system_prompt": system_prompt}
    return loop._build_system_prompt(context, message=message)


# ──────────────────────────────────────────────────────────
# 测试用例
# ──────────────────────────────────────────────────────────

def test_no_servers_no_tools_info():
    """无任何 MCP 服务器时，提示词不包含工具信息区块。"""
    prompt = _build_prompt(MockMCPNoServers())
    assert "可用数据工具" not in prompt
    assert "filesystem__write_file" not in prompt
    print("[PASS] test_no_servers_no_tools_info")


def test_clickhouse_only_no_filesystem_guidance():
    """仅有 clickhouse 服务器时，不应注入文件操作规则。"""
    prompt = _build_prompt(MockMCPClickhouseOnly())
    assert "可用数据工具" in prompt
    assert "filesystem__write_file" not in prompt
    assert "文件操作规则" not in prompt
    print("[PASS] test_clickhouse_only_no_filesystem_guidance")


def test_filesystem_server_injects_guidance():
    """有 filesystem 服务器时，必须注入写文件指引。"""
    prompt = _build_prompt(MockMCPWithFilesystem())
    assert "filesystem__write_file" in prompt, "必须包含 write_file 工具名"
    assert "文件操作规则" in prompt
    print("[PASS] test_filesystem_server_injects_guidance")


def test_filesystem_guidance_key_instructions():
    """注入的指引必须包含路径格式说明和行为规则。"""
    prompt = _build_prompt(MockMCPWithFilesystem(), message="写个文件")
    # 相对路径说明
    assert "相对路径" in prompt or "backend/skills" in prompt, \
        "指引应说明使用相对路径"
    # 禁止直接文字输出
    assert "不是在回复中直接输出" in prompt or "而不是" in prompt, \
        "指引应说明不能直接文字输出"
    # 写完后告知用户
    assert "告知用户" in prompt or "路径及字节数" in prompt, \
        "指引应要求写完后告知用户"
    print("[PASS] test_filesystem_guidance_key_instructions")


def test_filesystem_only_also_injects_guidance():
    """仅有 filesystem（无 clickhouse）时也注入指引。"""
    prompt = _build_prompt(MockMCPFilesystemOnly())
    assert "filesystem__write_file" in prompt
    print("[PASS] test_filesystem_only_also_injects_guidance")


def test_custom_system_prompt_preserved():
    """context 中提供的自定义 system_prompt 必须保留在最终提示词中。"""
    custom = "你是专属助手，只回答关于 ClickHouse 的问题。"
    prompt = _build_prompt(MockMCPNoServers(), system_prompt=custom)
    assert custom in prompt, f"自定义 system_prompt 应保留，实际: {prompt[:200]}"
    print("[PASS] test_custom_system_prompt_preserved")


def test_default_prompt_used_when_no_system_prompt():
    """未提供 system_prompt 时，使用内置的默认数据分析助手提示词。"""
    prompt = _build_prompt(MockMCPNoServers(), system_prompt="")
    assert "数据分析助手" in prompt or "ETL" in prompt, \
        f"应使用默认提示词，实际: {prompt[:200]}"
    print("[PASS] test_default_prompt_used_when_no_system_prompt")


def test_servers_listed_in_prompt():
    """有 MCP 服务器时，服务器名称和工具数量列入提示词。"""
    prompt = _build_prompt(MockMCPWithFilesystem())
    assert "clickhouse-idn" in prompt
    assert "Filesystem MCP Server" in prompt
    assert "17" in prompt   # clickhouse tool count
    assert "8"  in prompt   # filesystem tool count
    print("[PASS] test_servers_listed_in_prompt")


def test_filesystem_guidance_includes_allowed_dirs():
    """当 filesystem 服务器有 allowed_directories 时，目录路径应注入提示词，避免 LLM 探索。"""
    prompt = _build_prompt(MockMCPWithFilesystemDirs())
    assert "/data/project" in prompt or "/tmp/sandbox" in prompt, \
        f"提示词应包含允许的目录路径，实际: {prompt[:400]}"
    print("[PASS] test_filesystem_guidance_includes_allowed_dirs")


def test_filesystem_guidance_direct_write_instruction():
    """提示词应包含'无需先调用 list_allowed_directories'指令，防止 LLM 浪费迭代次数探索目录。"""
    prompt = _build_prompt(MockMCPWithFilesystem())
    assert "list_allowed_directories" in prompt, \
        "提示词应说明不需要先调用 list_allowed_directories"
    assert "无需先调用" in prompt or "直接调用" in prompt, \
        "提示词应明确指示直接调用 write_file"
    print("[PASS] test_filesystem_guidance_direct_write_instruction")


def test_filesystem_guidance_not_duplicated():
    """filesystem 指引只出现一次，不被重复注入。"""
    prompt = _build_prompt(MockMCPWithFilesystem())
    count = prompt.count("文件操作规则")
    assert count == 1, f"文件操作规则应只出现1次，实际出现 {count} 次"
    print("[PASS] test_filesystem_guidance_not_duplicated")


# ──────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────

def run_all():
    tests = [
        test_no_servers_no_tools_info,
        test_clickhouse_only_no_filesystem_guidance,
        test_filesystem_server_injects_guidance,
        test_filesystem_guidance_key_instructions,
        test_filesystem_only_also_injects_guidance,
        test_custom_system_prompt_preserved,
        test_default_prompt_used_when_no_system_prompt,
        test_servers_listed_in_prompt,
        test_filesystem_guidance_not_duplicated,
        test_filesystem_guidance_includes_allowed_dirs,
        test_filesystem_guidance_direct_write_instruction,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过, {failed} 失败 / 共 {len(tests)} 个测试")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
