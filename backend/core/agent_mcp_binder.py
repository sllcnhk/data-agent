"""
Agent MCP Binder

按 .claude/agent_config.yaml 的声明为每类 Agent 提供
权限受限的 FilteredMCPManager，实现凭据级安全隔离。

关键类
-------
FilteredMCPManager
    包装 MCPServerManager，只暴露白名单内的服务器。
    接口与 MCPServerManager 完全兼容：
      .servers / .server_configs / call_tool /
      list_servers / get_server / get_all_tools

AgentMCPBinder
    解析 .claude/agent_config.yaml，按 agent_type 返回
    对应的 FilteredMCPManager。

设计原则
--------
- 非 ClickHouse 服务器（filesystem / lark / mysql-*）始终加入
  所有 Agent 的允许集合，config 仅控制 ClickHouse 访问级别。
- clickhouse_envs: all（或未指定）→ 包含所有已注册的 CH 服务器，
  无需在 agent_config.yaml 中逐一列出新增环境。
- 只读凭据未配置时，自动降级到 admin 连接并输出 WARNING 日志。
- config 文件不存在时输出 WARNING 并允许访问所有服务器（向后兼容）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = ".claude/agent_config.yaml"


# ──────────────────────────────────────────────────────────
# FilteredMCPManager
# ──────────────────────────────────────────────────────────


class FilteredMCPManager:
    """
    MCPServerManager 的轻量视图：只暴露 allowed_servers 中的服务器。

    接口与 MCPServerManager 兼容，可直接替换传入各 Agent。
    """

    def __init__(self, base, allowed_servers: FrozenSet[str]) -> None:
        """
        Args:
            base:            原始 MCPServerManager 实例
            allowed_servers: 本 Agent 可访问的服务器名称集合
        """
        self._base = base
        self._allowed = allowed_servers

    # ── 兼容 MCPServerManager 的属性 ──────────────────────

    @property
    def servers(self) -> Dict[str, Any]:
        return {
            name: srv
            for name, srv in self._base.servers.items()
            if name in self._allowed
        }

    @property
    def server_configs(self) -> Dict[str, Any]:
        return {
            name: cfg
            for name, cfg in self._base.server_configs.items()
            if name in self._allowed
        }

    # ── 方法 ──────────────────────────────────────────────

    def get_server(self, name: str):
        if name not in self._allowed:
            logger.warning(
                "[FilteredMCPManager] Access denied: server '%s' not in allowed set %s",
                name,
                sorted(self._allowed),
            )
            return None
        return self._base.get_server(name)

    def list_servers(self) -> List[Dict[str, Any]]:
        return [s for s in self._base.list_servers() if s["name"] in self._allowed]

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if server_name not in self._allowed:
            logger.error(
                "[FilteredMCPManager] Blocked tool call '%s.%s' — "
                "server not in allowed set for this agent",
                server_name,
                tool_name,
            )
            return {
                "success": False,
                "error": f"服务器 '{server_name}' 未授权给当前 Agent",
            }
        return await self._base.call_tool(server_name, tool_name, arguments)

    def get_all_tools(self) -> List[Dict[str, Any]]:
        return [
            t
            for t in self._base.get_all_tools()
            if t.get("server") in self._allowed
        ]

    def get_all_resources(self) -> List[Dict[str, Any]]:
        return [
            r
            for r in self._base.get_all_resources()
            if r.get("server") in self._allowed
        ]


# ──────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────


def _extract_envs_from_manager(mcp_manager) -> List[str]:
    """
    从已注册的服务器名中提取唯一的 ClickHouse env 名称列表。

    clickhouse-idn     → idn
    clickhouse-idn-ro  → idn  (去重)
    clickhouse-sg      → sg
    """
    seen: set = set()
    envs: List[str] = []
    for name in mcp_manager.servers:
        if not name.startswith("clickhouse-"):
            continue
        base = name[len("clickhouse-"):]
        env = base[:-len("-ro")] if base.endswith("-ro") else base
        if env not in seen:
            seen.add(env)
            envs.append(env)
    return sorted(envs)


# ──────────────────────────────────────────────────────────
# AgentMCPBinder
# ──────────────────────────────────────────────────────────


class AgentMCPBinder:
    """
    读取 .claude/agent_config.yaml，将 agent_type 映射到
    对应的 FilteredMCPManager。

    绑定规则
    --------
    - clickhouse_connection=admin   → 使用 clickhouse-{env}
    - clickhouse_connection=readonly → 优先使用 clickhouse-{env}-ro；
      若 ro 未注册，则降级 admin + 输出 WARNING
    - 非 ClickHouse 服务器（filesystem / lark / mysql-* 等）
      始终纳入所有 Agent 的允许集合
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        if config_path is None:
            # 相对于项目根目录（backend/core/ 的三级上级）
            project_root = Path(__file__).parent.parent.parent
            config_path = str(project_root / _DEFAULT_CONFIG_PATH)

        self._config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        if not self._config_path.exists():
            logger.warning(
                "[AgentMCPBinder] Config file not found: %s — "
                "all agents will have access to all servers",
                self._config_path,
            )
            self._config = {}
            return

        with open(self._config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._config = data.get("agents", {})
        logger.info(
            "[AgentMCPBinder] Loaded config from %s: %d agent types defined",
            self._config_path,
            len(self._config),
        )

    # 内置默认值，当 agent_config.yaml 未配置 max_iterations 时生效
    _DEFAULT_MAX_ITERATIONS: Dict[str, int] = {
        "etl_engineer": 15,
        "analyst": 30,
        "general": 20,
    }

    def get_max_iterations(self, agent_type: str) -> int:
        """返回 agent_type 对应的最大推理轮次（从 yaml 读取，缺省用内置值）。"""
        agent_cfg = self._config.get(agent_type, {})
        default = self._DEFAULT_MAX_ITERATIONS.get(agent_type, 20)
        return int(agent_cfg.get("max_iterations", default))

    def get_filtered_manager(
        self,
        agent_type: str,
        mcp_manager,
    ) -> FilteredMCPManager:
        """
        为 agent_type 构建并返回 FilteredMCPManager。

        允许集合 = ClickHouse 服务器（按 config 解析）
                  + 所有非 ClickHouse 服务器

        Args:
            agent_type:  Agent 类型字符串（etl_engineer / analyst / general / …）
            mcp_manager: 全量 MCPServerManager 实例

        Returns:
            FilteredMCPManager with resolved allowed_servers
        """
        agent_cfg = self._config.get(agent_type, {})
        connection_level: str = agent_cfg.get("clickhouse_connection", "readonly")
        envs_raw = agent_cfg.get("clickhouse_envs", "all")

        # clickhouse_envs: all（或未指定）→ 自动发现所有已注册的 CH 环境
        if envs_raw in ("all", ["all"]) or not envs_raw:
            envs = _extract_envs_from_manager(mcp_manager)
        else:
            envs = list(envs_raw) if not isinstance(envs_raw, list) else envs_raw

        # 解析允许的 ClickHouse 服务器名
        allowed_ch: set = set()
        for env in envs:
            if connection_level == "admin":
                server_name = f"clickhouse-{env}"
                if server_name in mcp_manager.servers:
                    allowed_ch.add(server_name)
            else:  # readonly
                ro_name = f"clickhouse-{env}-ro"
                if ro_name in mcp_manager.servers:
                    allowed_ch.add(ro_name)
                else:
                    # 降级：ro 未注册，回退到 admin 连接
                    admin_name = f"clickhouse-{env}"
                    if admin_name in mcp_manager.servers:
                        logger.warning(
                            "[AgentMCPBinder] Agent '%s' requested readonly "
                            "connection for env '%s', but '%s' is not registered. "
                            "Falling back to admin connection '%s'.",
                            agent_type,
                            env,
                            ro_name,
                            admin_name,
                        )
                        allowed_ch.add(admin_name)
                    # 两者都不存在时忽略该 env（ClickHouse 不可用）

        # 非 ClickHouse 服务器：默认全部可访问，但可通过
        # excluded_non_ch_servers 按 agent 排除特定服务器
        excluded_non_ch: set = set(agent_cfg.get("excluded_non_ch_servers", []))
        non_ch_names = {
            name
            for name in mcp_manager.servers
            if not name.startswith("clickhouse-") and name not in excluded_non_ch
        }
        if excluded_non_ch:
            logger.info(
                "[AgentMCPBinder] Agent '%s' excluded non-CH servers: %s",
                agent_type,
                sorted(excluded_non_ch),
            )

        allowed = frozenset(allowed_ch | non_ch_names)

        logger.info(
            "[AgentMCPBinder] Agent '%s' allowed servers: %s",
            agent_type,
            sorted(allowed),
        )

        filtered = FilteredMCPManager(base=mcp_manager, allowed_servers=allowed)

        # 当 filesystem 服务器可访问时，叠加目录级写权限代理
        if "filesystem" in allowed:
            from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
            from backend.config.settings import settings
            return FilesystemPermissionProxy(
                base=filtered,
                write_allowed_dirs=settings.filesystem_write_allowed_dirs,
                read_allowed_dirs=settings.allowed_directories,
            )

        return filtered
