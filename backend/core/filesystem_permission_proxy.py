"""
FilesystemPermissionProxy
=========================
目录级文件写入权限代理。

架构位置
--------
    AgenticLoop
      -> FilesystemPermissionProxy      (本模块: 目录级写权限控制)
          -> FilteredMCPManager         (服务器级访问控制)
              -> MCPServerManager
                  -> FilesystemMCPServer (allowed_dirs: customer_data/ + .claude/skills/)

职责
----
- 拦截对 "filesystem" 服务器的写类工具调用
  (write_file / create_directory / delete)
- 解析路径参数，判断目标路径是否在允许写入目录内
- 允许: 目标路径在 write_allowed_dirs 内 -> 透传给底层 FilteredMCPManager
- 拒绝: 目标路径在 write_allowed_dirs 外 -> 返回明确拒绝消息（不抛出异常）
- 读类工具 (read_file / list_directory / search_files 等): 无条件透传

路径解析
--------
镜像 FilesystemMCPServer._normalize_path() + _resolve_path() 的两步逻辑：
  1. 绝对路径（含盘符）: 直接 resolve()，检查是否在 read_allowed_dirs 内
  2. 相对路径: 在 read_allowed_dirs 中逐一拼接，取第一个合法结果

注意: 解析不依赖文件是否存在（写入前文件可能尚不存在）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional
from urllib.parse import unquote

logger = logging.getLogger(__name__)

# 写类工具名称集合 — 只有这些工具才受目录级写权限约束
_WRITE_TOOLS: FrozenSet[str] = frozenset({"write_file", "create_directory", "delete"})

# filesystem MCP 服务器注册名
_FILESYSTEM_SERVER = "filesystem"


class FilesystemPermissionProxy:
    """
    目录级文件写入权限代理。

    包装 FilteredMCPManager（或任何兼容 MCP 管理器），在 filesystem 服务器的
    写类工具调用上叠加目录级白名单检查。读类工具调用无条件透传。

    接口与 FilteredMCPManager / MCPServerManager 完全兼容：
        .servers / .server_configs / call_tool /
        list_servers / get_server / get_all_tools / get_all_resources

    Args:
        base:               底层 MCP 管理器（FilteredMCPManager 实例）
        write_allowed_dirs: 允许写入的目录列表（绝对或相对路径，内部会 resolve()）
        read_allowed_dirs:  可读目录列表（与 FilesystemMCPServer.allowed_directories 相同）
                            用于将相对路径参数解析为绝对路径
    """

    def __init__(
        self,
        base,
        write_allowed_dirs: List[str],
        read_allowed_dirs: List[str],
    ) -> None:
        self._base = base
        # resolve() 一次，后续所有检查使用 resolved 路径
        self._write_allowed: List[Path] = [Path(d).resolve() for d in write_allowed_dirs]
        self._read_allowed: List[Path] = [Path(d).resolve() for d in read_allowed_dirs]

        logger.info(
            "[FilesystemPermissionProxy] Initialized. "
            "write_allowed=%s  read_allowed=%s",
            [str(p) for p in self._write_allowed],
            [str(p) for p in self._read_allowed],
        )

    # ── 兼容属性（透传给底层管理器）────────────────────────────────────────

    @property
    def _allowed(self) -> FrozenSet[str]:
        """透传 FilteredMCPManager._allowed，供测试和调试使用。"""
        return self._base._allowed

    @property
    def servers(self) -> Dict[str, Any]:
        return self._base.servers

    @property
    def server_configs(self) -> Dict[str, Any]:
        return self._base.server_configs

    def get_server(self, name: str):
        return self._base.get_server(name)

    def list_servers(self) -> List[Dict[str, Any]]:
        return self._base.list_servers()

    def get_all_tools(self) -> List[Dict[str, Any]]:
        return self._base.get_all_tools()

    def get_all_resources(self) -> List[Dict[str, Any]]:
        return self._base.get_all_resources()

    # ── 核心拦截逻辑 ────────────────────────────────────────────────────────

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        拦截 filesystem 写类工具调用，进行目录级权限检查；其他工具直接透传。

        检查顺序（写类工具）：
          1. _is_write_allowed()：路径是否在写权限白名单内
             - 含 Fix-2：检测技能路径被错误路由到 customer_data/ 下
          2. _check_skills_user_subdir()：技能路径必须包含用户名子目录层（Fix-4）

        Returns:
            底层 call_tool 结果，或拒绝字典 {"success": False, "error": "..."}
        """
        if server_name == _FILESYSTEM_SERVER and tool_name in _WRITE_TOOLS:
            path_arg: str = arguments.get("path", "")

            # Check 1: directory-level write permission (Fix-2 included)
            if not self._is_write_allowed(path_arg):
                logger.warning(
                    "[FilesystemPermissionProxy] Blocked '%s' on path='%s'. "
                    "write_allowed=%s",
                    tool_name,
                    path_arg,
                    [str(d) for d in self._write_allowed],
                )
                # Fix-3: correct path template with username layer
                skills_user_dir = self._get_skills_user_dir()
                skills_hint = (
                    f"  • 用户技能文件（SKILL.md）→ {skills_user_dir}/{{用户名}}/{{skill-name}}.md\n"
                    if skills_user_dir
                    else "  • 用户技能文件（SKILL.md）→ .claude/skills/user/{用户名}/{skill-name}.md\n"
                )
                return {
                    "success": False,
                    "error": (
                        f"权限拒绝: 不允许在路径 '{path_arg}' 执行写操作。\n"
                        f"允许写入的目录: {[str(d) for d in self._write_allowed]}\n"
                        "正确的写入路径规则:\n"
                        "  • 数据文件（CSV/JSON/SQL/分析结果）→ customer_data/{用户名}/ 目录\n"
                        f"{skills_hint}"
                        "  • 不允许写入 .claude/skills/system/ 或 .claude/skills/project/\n"
                        "  • 不允许写入项目源代码目录（backend/ / frontend/）"
                    ),
                }

            # Check 2: Fix-4 — skills/user/ 写入必须包含用户名子目录层
            subdir_error = self._check_skills_user_subdir(path_arg, tool_name)
            if subdir_error:
                return {"success": False, "error": subdir_error}

        return await self._base.call_tool(server_name, tool_name, arguments)

    # ── 路径解析（镜像 FilesystemMCPServer 逻辑）───────────────────────────

    def _is_write_allowed(self, path: str) -> bool:
        """
        判断路径是否在允许写入目录下。

        Returns True 当且仅当：
          1. 解析后的绝对路径是某个 write_allowed 目录的子路径。
          2. Fix-2: 路径不是技能路径被错误路由到 customer_data/ 下的情况。
             （即：路径在 customer_data/ 下，但同时含有 ".claude" 段，说明 LLM
               用错了根目录，将 .claude/skills/... 拼在了 customer_data/ 之后）
        """
        resolved = self._resolve_for_check(path)
        if resolved is None:
            # 路径超出 read_allowed_dirs：底层服务器本身也会拒绝
            return False

        # Fix-2: 检测 customer_data/.claude/... 错误路由
        # 如果解析路径在 customer_data/ 下且路径中含有 ".claude" 目录段，
        # 说明 LLM 把技能路径错误地拼在了数据根目录下
        resolved_parts = resolved.parts  # 跨平台安全拆分
        if ".claude" in resolved_parts:
            # 检查 .claude 是否出现在 customer_data 之后（即在 customer_data 子目录中）
            customer_data_roots = [
                d for d in self._write_allowed
                if "customer_data" in str(d).replace("\\", "/")
            ]
            for cdr in customer_data_roots:
                try:
                    resolved.relative_to(cdr)
                    # resolved 在 customer_data/ 下且含有 .claude 段 → 错误路由
                    logger.warning(
                        "[FilesystemPermissionProxy] Fix-2 blocked: path '%s' looks like "
                        "a skills file incorrectly routed under customer_data/. "
                        "LLM should use the .claude/skills root instead.",
                        resolved,
                    )
                    return False
                except ValueError:
                    continue

        for allowed_dir in self._write_allowed:
            try:
                resolved.relative_to(allowed_dir)
                return True
            except ValueError:
                continue
        return False

    def _get_skills_user_dir(self) -> str:
        """
        从 write_allowed 列表中找到 .claude/skills/user 根目录的字符串表示。
        用于生成报错提示信息。
        """
        for d in self._write_allowed:
            d_str = str(d).replace("\\", "/")
            if ".claude" in d_str:
                return d_str
        return ""

    def _check_skills_user_subdir(self, path: str, tool_name: str = "write_file") -> Optional[str]:
        """
        Fix-4: 若写入路径在 .claude/skills/user/ 下，验证路径层级要求：
          - create_directory: 允许深度 >= 1（创建 user/{username}/ 目录本身合法）
          - write_file / delete: 要求深度 >= 2（文件必须在 user/{username}/{skill}.md）

        Returns:
            None  — 检查通过（路径合法，或路径不在 skills/user/ 下）
            str   — 错误消息（检查不通过时返回，由 call_tool 封装为拒绝响应）
        """
        # 找到 write_allowed 中的 skills/user 根
        skills_user_roots = [
            d for d in self._write_allowed
            if ".claude" in str(d).replace("\\", "/")
        ]
        if not skills_user_roots:
            return None  # 没有配置 skills 写目录，跳过此检查

        resolved = self._resolve_for_check(path)
        if resolved is None:
            return None  # 路径已在 _is_write_allowed 阶段被拒，此处跳过

        # create_directory 允许仅有用户名层（深度 1），write_file/delete 要求文件层（深度 2）
        min_depth = 1 if tool_name == "create_directory" else 2

        for skills_user_root in skills_user_roots:
            try:
                rel = resolved.relative_to(skills_user_root)
            except ValueError:
                continue

            # rel 是相对于 skills/user/ 的路径：
            #   "alice/"        → parts = ("alice",)       → depth 1
            #   "alice/x.md"   → parts = ("alice","x.md") → depth 2
            if len(rel.parts) < min_depth:
                logger.warning(
                    "[FilesystemPermissionProxy] Fix-4 blocked: path '%s' (tool=%s) "
                    "writes directly to skills/user/ root without username subdirectory. "
                    "Expected depth >= %d, got: user/%s",
                    resolved,
                    tool_name,
                    min_depth,
                    rel,
                )
                return (
                    f"权限拒绝: 技能文件不能直接写入 user/ 根目录（路径: '{path}'）。\n"
                    f"必须包含用户名子目录层，正确格式：\n"
                    f"  {skills_user_root}/{{用户名}}/{{skill-name}}.md\n"
                    f"例如：{skills_user_root}/alice/my-skill.md"
                )
            # 通过：rel.parts[0] 是用户名目录
            return None

        return None

    def _resolve_for_check(self, path: str) -> Optional[Path]:
        """
        将 path 解析为绝对 Path，与 FilesystemMCPServer._resolve_path() 逻辑一致。

        不检查文件是否存在（写入操作往往在文件创建前执行）。
        路径不在 read_allowed_dirs 内时返回 None。
        """
        # 1. 规范化（mirror _normalize_path）
        normalized = unquote(path).replace("\\", "/").lstrip("/")

        candidate = Path(normalized)

        # 2. 绝对路径处理（含盘符，如 C:/... 或 /abs/...）
        if candidate.is_absolute():
            try:
                resolved = candidate.resolve()
            except OSError:
                return None
            for allowed in self._read_allowed:
                try:
                    resolved.relative_to(allowed)
                    return resolved
                except ValueError:
                    continue
            return None

        # 3. 相对路径: 在 read_allowed_dirs 中逐一拼接
        for allowed in self._read_allowed:
            try:
                full = (allowed / normalized).resolve()
                full.relative_to(allowed)  # 边界检查，防止 ../.. 逃逸
                return full
            except (ValueError, OSError):
                continue

        return None
