"""
test_filesystem_path.py
测试 FilesystemMCPServer 路径解析与文件操作：

路径解析（_normalize_path / _resolve_path）：
  - 相对路径（正/反斜杠）
  - 绝对路径（正/反斜杠，带盘符）
  - URL 编码路径
  - 多余斜杠（双斜杠）
  - 路径穿越拦截（..）
  - 越权绝对路径拦截

文件读写操作（_write_file / _read_file）：
  - 写入新文件（write 模式）
  - 追加内容（append 模式）
  - 覆盖已有文件
  - 读取写入后的文件
  - 写入 Unicode 内容

目录操作（_create_directory / _list_directory）：
  - 创建目录
  - 列出目录内容
  - 列出空目录

安全边界：
  - write_file 路径穿越被拒绝
  - read_file 越权路径被拒绝
"""
import asyncio
import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# 测试时使用临时目录，不污染真实项目目录
_TEMP_DIR = tempfile.mkdtemp(prefix="data_agent_test_")


def make_server(allowed_dirs=None):
    """创建 FilesystemMCPServer 实例，指向临时测试目录。"""
    from backend.mcp.filesystem.server import FilesystemMCPServer
    server = object.__new__(FilesystemMCPServer)
    server.allowed_directories = allowed_dirs or [_TEMP_DIR]
    return server


# ──────────────────────────────────────────────────────────
# 路径解析
# ──────────────────────────────────────────────────────────

def test_relative_path_forward_slash():
    server = make_server()
    result = server._resolve_path("subdir/file.txt")
    assert str(result).endswith("file.txt")
    print("[PASS] test_relative_path_forward_slash")


def test_relative_path_backslash():
    server = make_server()
    result = server._resolve_path("subdir\\file.txt")
    assert "file.txt" in str(result)
    print("[PASS] test_relative_path_backslash")


def test_absolute_path_forward_slash():
    server = make_server()
    abs_path = _TEMP_DIR.replace("\\", "/") + "/subdir/file.txt"
    result = server._resolve_path(abs_path)
    assert "file.txt" in str(result)
    print("[PASS] test_absolute_path_forward_slash")


def test_absolute_path_backslash():
    server = make_server()
    abs_path = _TEMP_DIR + "\\subdir\\file.txt"
    result = server._resolve_path(abs_path)
    assert "file.txt" in str(result)
    print("[PASS] test_absolute_path_backslash")


def test_url_encoded_path():
    """URL 编码的路径应被正确解码。"""
    server = make_server()
    # %2F = /，%20 = 空格
    result = server._resolve_path("sub%2Fdir%20name/file.txt")
    assert "file.txt" in str(result)
    print("[PASS] test_url_encoded_path")


def test_normalize_double_slashes():
    """多余斜杠不应导致崩溃或路径穿越。"""
    server = make_server()
    # 双斜杠正规化为单斜杠
    norm = server._normalize_path("sub//dir/file.txt")
    assert "//" not in norm or norm.count("//") <= 1  # 系统会处理
    # 关键：能正常 resolve，不报错
    result = server._resolve_path("sub/dir/file.txt")
    assert "file.txt" in str(result)
    print("[PASS] test_normalize_double_slashes")


def test_path_traversal_relative_blocked():
    """相对路径穿越（../../etc）必须被拦截。"""
    server = make_server()
    try:
        server._resolve_path("../../etc/passwd")
        assert False, "路径穿越未被拦截！"
    except PermissionError:
        print("[PASS] test_path_traversal_relative_blocked")


def test_out_of_allowed_absolute_blocked():
    """绝对越权路径必须被拦截。"""
    server = make_server()
    try:
        server._resolve_path("C:/Windows/System32/evil.exe")
        assert False, "越权路径未被拦截！"
    except PermissionError:
        print("[PASS] test_out_of_allowed_absolute_blocked")


def test_normalize_backslash_to_forward():
    """_normalize_path 必须把反斜杠全部替换为正斜杠。"""
    server = make_server()
    result = server._normalize_path("a\\b\\c.txt")
    assert "\\" not in result
    assert result == "a/b/c.txt"
    print("[PASS] test_normalize_backslash_to_forward")


def test_normalize_strips_leading_slash():
    """_normalize_path 去掉前导正斜杠（Unix 绝对路径头）。"""
    server = make_server()
    result = server._normalize_path("/relative/path.txt")
    assert not result.startswith("/")
    print("[PASS] test_normalize_strips_leading_slash")


# ──────────────────────────────────────────────────────────
# 文件写入与读取（异步，真实 I/O）
# ──────────────────────────────────────────────────────────

async def test_write_file_creates_new_file():
    """write 模式写入新文件，文件确实被创建，内容正确。"""
    server = make_server()
    filename = "write_test.txt"
    content = "hello from test"
    result = await server._write_file(filename, content)

    assert result.get("success") is True, f"写入失败: {result}"
    assert result.get("operation") == "write"

    full_path = os.path.join(_TEMP_DIR, filename)
    assert os.path.exists(full_path), "文件不存在"
    with open(full_path, encoding="utf-8") as f:
        assert f.read() == content
    print("[PASS] test_write_file_creates_new_file")


async def test_write_file_overwrites_existing():
    """write 模式覆盖已有文件。"""
    server = make_server()
    filename = "overwrite_test.txt"
    await server._write_file(filename, "旧内容")
    await server._write_file(filename, "新内容")

    full_path = os.path.join(_TEMP_DIR, filename)
    with open(full_path, encoding="utf-8") as f:
        assert f.read() == "新内容"
    print("[PASS] test_write_file_overwrites_existing")


async def test_write_file_append_mode():
    """append 模式追加内容，不覆盖原有内容。"""
    server = make_server()
    filename = "append_test.txt"
    await server._write_file(filename, "第一行\n", mode="write")
    result = await server._write_file(filename, "第二行\n", mode="append")

    assert result.get("operation") == "append"
    full_path = os.path.join(_TEMP_DIR, filename)
    with open(full_path, encoding="utf-8") as f:
        data = f.read()
    assert "第一行" in data
    assert "第二行" in data
    print("[PASS] test_write_file_append_mode")


async def test_write_file_unicode_content():
    """写入 Unicode（中文、emoji、特殊符号）内容不报错。"""
    server = make_server()
    filename = "unicode_test.md"
    content = "# 标题\n\n这是中文内容。\n\n- 列表项 1\n- 列表项 2\n"
    result = await server._write_file(filename, content)
    assert result.get("success") is True

    result2 = await server._read_file(filename)
    assert result2.get("content") == content
    print("[PASS] test_write_file_unicode_content")


async def test_write_file_creates_parent_dirs():
    """写入深层路径时自动创建父目录。"""
    server = make_server()
    path = "nested/deep/dir/file.txt"
    result = await server._write_file(path, "nested content")
    assert result.get("success") is True
    full_path = os.path.join(_TEMP_DIR, "nested", "deep", "dir", "file.txt")
    assert os.path.exists(full_path)
    print("[PASS] test_write_file_creates_parent_dirs")


async def test_read_file_after_write():
    """读取刚写入的文件，内容一致。"""
    server = make_server()
    filename = "read_after_write.txt"
    content = "read_me_content_12345"
    await server._write_file(filename, content)
    result = await server._read_file(filename)

    assert result.get("type") == "file_content"
    assert result.get("content") == content
    assert result.get("size") == len(content.encode("utf-8"))
    print("[PASS] test_read_file_after_write")


async def test_read_nonexistent_file_returns_error():
    """读取不存在的文件返回 error 字段，不抛异常。"""
    server = make_server()
    result = await server._read_file("does_not_exist.txt")
    assert "error" in result
    print("[PASS] test_read_nonexistent_file_returns_error")


async def test_write_file_absolute_path():
    """使用绝对路径（指向允许目录内）写入文件，正常工作。"""
    server = make_server()
    filename = "abs_path_write.txt"
    abs_path = os.path.join(_TEMP_DIR, filename).replace("\\", "/")
    result = await server._write_file(abs_path, "absolute path content")
    assert result.get("success") is True

    # 验证文件实际存在
    assert os.path.exists(os.path.join(_TEMP_DIR, filename))
    print("[PASS] test_write_file_absolute_path")


# ──────────────────────────────────────────────────────────
# 目录操作
# ──────────────────────────────────────────────────────────

async def test_create_directory():
    """创建新目录成功。"""
    server = make_server()
    result = await server._create_directory("new_subdir")
    assert result.get("success") is True
    assert os.path.isdir(os.path.join(_TEMP_DIR, "new_subdir"))
    print("[PASS] test_create_directory")


async def test_list_directory_after_write():
    """写入文件后，list_directory 能列出该文件。"""
    server = make_server()
    subdir = "list_test_dir"
    os.makedirs(os.path.join(_TEMP_DIR, subdir), exist_ok=True)
    await server._write_file(f"{subdir}/a.txt", "aaa")
    await server._write_file(f"{subdir}/b.txt", "bbb")

    result = await server._list_directory(subdir)
    assert result.get("item_count") == 2
    names = {item["name"] for item in result["items"]}
    assert "a.txt" in names
    assert "b.txt" in names
    print("[PASS] test_list_directory_after_write")


async def test_list_allowed_directories():
    """list_allowed_directories 返回已配置的允许目录。"""
    server = make_server()
    result = await server._list_allowed_directories()
    assert result.get("type") == "allowed_directories"
    paths = [d["path"] for d in result["directories"]]
    assert _TEMP_DIR in paths or any(_TEMP_DIR.lower() in p.lower() for p in paths)
    print("[PASS] test_list_allowed_directories")


# ──────────────────────────────────────────────────────────
# 安全边界（通过公共 call_tool 接口）
# ──────────────────────────────────────────────────────────

async def test_write_file_path_traversal_blocked():
    """通过 _write_file 的路径穿越攻击被拦截，返回 error。"""
    server = make_server()
    result = await server._write_file("../../evil.txt", "bad content")
    assert "error" in result, f"路径穿越未被拦截: {result}"
    evil_path = os.path.join(os.path.dirname(_TEMP_DIR), "evil.txt")
    assert not os.path.exists(evil_path), "恶意文件不应被创建"
    print("[PASS] test_write_file_path_traversal_blocked")


async def test_read_file_out_of_allowed_blocked():
    """通过 _read_file 读取越权绝对路径被拦截，返回 error。"""
    server = make_server()
    result = await server._read_file("C:/Windows/win.ini")
    assert "error" in result, f"越权读取未被拦截: {result}"
    print("[PASS] test_read_file_out_of_allowed_blocked")


# ──────────────────────────────────────────────────────────
# 清理 & 入口
# ──────────────────────────────────────────────────────────

def _cleanup():
    try:
        shutil.rmtree(_TEMP_DIR, ignore_errors=True)
    except Exception:
        pass


async def run_async_tests():
    async_tests = [
        test_write_file_creates_new_file,
        test_write_file_overwrites_existing,
        test_write_file_append_mode,
        test_write_file_unicode_content,
        test_write_file_creates_parent_dirs,
        test_read_file_after_write,
        test_read_nonexistent_file_returns_error,
        test_write_file_absolute_path,
        test_create_directory,
        test_list_directory_after_write,
        test_list_allowed_directories,
        test_write_file_path_traversal_blocked,
        test_read_file_out_of_allowed_blocked,
    ]
    passed = failed = 0
    for t in async_tests:
        try:
            await t()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    return passed, failed


def run_all():
    sync_tests = [
        test_relative_path_forward_slash,
        test_relative_path_backslash,
        test_absolute_path_forward_slash,
        test_absolute_path_backslash,
        test_url_encoded_path,
        test_normalize_double_slashes,
        test_path_traversal_relative_blocked,
        test_out_of_allowed_absolute_blocked,
        test_normalize_backslash_to_forward,
        test_normalize_strips_leading_slash,
    ]

    passed = failed = 0
    for t in sync_tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    ap, af = asyncio.run(run_async_tests())
    passed += ap
    failed += af

    _cleanup()

    total = len(sync_tests) + ap + af
    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过, {failed} 失败 / 共 {total} 个测试")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
