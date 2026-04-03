"""
MCP管理器

统一管理所有MCP服务器实例
"""
from typing import Dict, List, Optional, Any
import asyncio
import logging
from backend.mcp.base import BaseMCPServer
from backend.mcp.clickhouse import ClickHouseMCPServer
from backend.mcp.mysql import MySQLMCPServer
from backend.mcp.filesystem import FilesystemMCPServer
from backend.mcp.lark import LarkMCPServer
from backend.config.settings import settings

logger = logging.getLogger(__name__)


class MCPServerManager:
    """MCP服务器管理器"""

    def __init__(self):
        self.servers: Dict[str, BaseMCPServer] = {}
        self.server_configs: Dict[str, Dict[str, Any]] = {}

    async def initialize_all(self):
        """
        初始化所有服务器。

        ClickHouse 注册策略：
          - admin 连接（clickhouse-{env}）：host 非空时注册
          - readonly 连接（clickhouse-{env}-ro）：has_readonly_credentials(env) 为 True 时注册

        各服务器独立初始化，单个服务器失败不影响其他服务器注册。
        """
        if settings.enable_mcp_clickhouse:
            # 动态发现所有已配置的 ClickHouse 环境（idn/sg/mx 及任意新增 env）
            for env in settings.get_all_clickhouse_envs():
                cfg = settings.get_clickhouse_config(env, level="admin")
                if cfg["host"]:
                    try:
                        await self.create_clickhouse_server(env, level="admin")
                    except Exception as e:
                        logger.warning("[MCPManager] Failed to register clickhouse-%s (admin): %s", env, e)
                if settings.has_readonly_credentials(env):
                    try:
                        await self.create_clickhouse_server(env, level="readonly")
                    except Exception as e:
                        logger.warning("[MCPManager] Failed to register clickhouse-%s-ro: %s", env, e)

        if settings.enable_mcp_mysql:
            for env in ("prod", "staging"):
                try:
                    mysql_cfg = settings.get_mysql_config(env)
                    if not mysql_cfg.get("host"):
                        logger.info("[MCPManager] Skipping mysql-%s: host not configured", env)
                        continue
                    await self.create_mysql_server(env)
                except Exception as e:
                    logger.warning("[MCPManager] Failed to register mysql-%s: %s", env, e)

        if settings.enable_mcp_filesystem:
            try:
                await self.create_filesystem_server()
            except Exception as e:
                logger.warning("[MCPManager] Failed to register filesystem: %s", e)

        if settings.enable_mcp_lark:
            try:
                await self.create_lark_server()
            except Exception as e:
                logger.warning("[MCPManager] Failed to register lark: %s", e)

        # 启动汇总：帮助快速确认哪些服务器注册成功
        registered = sorted(self.servers.keys())
        logger.info(
            "[MCPManager] Initialization complete: %d server(s) registered: %s",
            len(registered),
            ", ".join(registered) if registered else "(none)",
        )

    async def create_clickhouse_server(
        self, env: str = "idn", level: str = "admin"
    ) -> ClickHouseMCPServer:
        """
        创建 ClickHouse 服务器。

        Args:
            env:   环境名称（idn | sg | mx）
            level: 连接权限级别（"admin" | "readonly"）
                   admin   → server_name = clickhouse-{env}
                   readonly → server_name = clickhouse-{env}-ro
        """
        # 服务器命名使用纯连字符格式（env 中的下划线转为连字符），
        # 确保 tool_formatter 的 replace("-","_") + replace("_","-") 往返一致。
        # 例：env="sg_azure" → server_name="clickhouse-sg-azure"（而非 clickhouse-sg_azure）
        server_env = env.replace("_", "-")
        server_name = f"clickhouse-{server_env}" if level == "admin" else f"clickhouse-{server_env}-ro"

        if server_name in self.servers:
            return self.servers[server_name]

        server = ClickHouseMCPServer(env=env, level=level)  # env 保持原始形式供配置查找
        await server.initialize()

        self.servers[server_name] = server
        self.server_configs[server_name] = {
            "type": "clickhouse",
            "env": env,
            "level": level,
        }
        logger.info("[MCPManager] Registered %s (%s) [env=%s]", server_name, level, env)

        return server

    async def create_mysql_server(self, env: str = "prod") -> MySQLMCPServer:
        """创建MySQL服务器"""
        server_name = f"mysql-{env}"

        if server_name in self.servers:
            return self.servers[server_name]

        server = MySQLMCPServer(env=env)
        await server.initialize()

        self.servers[server_name] = server
        self.server_configs[server_name] = {
            "type": "mysql",
            "env": env
        }

        return server

    async def create_filesystem_server(self) -> FilesystemMCPServer:
        """创建Filesystem服务器"""
        server_name = "filesystem"

        if server_name in self.servers:
            return self.servers[server_name]

        server = FilesystemMCPServer()
        await server.initialize()

        self.servers[server_name] = server
        self.server_configs[server_name] = {
            "type": "filesystem"
        }

        return server

    async def create_lark_server(self) -> LarkMCPServer:
        """创建Lark服务器"""
        server_name = "lark"

        if server_name in self.servers:
            return self.servers[server_name]

        server = LarkMCPServer()
        await server.initialize()

        self.servers[server_name] = server
        self.server_configs[server_name] = {
            "type": "lark"
        }

        return server

    def get_server(self, name: str) -> Optional[BaseMCPServer]:
        """获取服务器实例"""
        return self.servers.get(name)

    def list_servers(self) -> List[Dict[str, Any]]:
        """列出所有服务器"""
        result = []
        for name, server in self.servers.items():
            result.append({
                "name": name,
                "type": self.server_configs[name]["type"],
                "version": server.version,
                "tool_count": len(server.tools),
                "resource_count": len(server.resources),
                "prompt_count": len(server.prompts)
            })
        return result

    async def get_server_info(self, name: str) -> Optional[Dict[str, Any]]:
        """获取服务器详细信息"""
        server = self.get_server(name)
        if not server:
            return None

        return await server.get_server_info()

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """调用服务器工具"""
        server = self.get_server(server_name)
        if not server:
            return None

        result = await server.call_tool(tool_name, arguments)
        return result.to_dict()

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """获取所有工具列表"""
        all_tools = []
        for name, server in self.servers.items():
            tools = server.get_tools_list()
            for tool in tools:
                tool["server"] = name
                all_tools.append(tool)
        return all_tools

    def get_all_resources(self) -> List[Dict[str, Any]]:
        """获取所有资源列表"""
        all_resources = []
        for name, server in self.servers.items():
            resources = server.get_resources_list()
            for resource in resources:
                resource["server"] = name
                all_resources.append(resource)
        return all_resources

    async def shutdown(self):
        """关闭所有服务器"""
        # 目前MCP服务器不需要特殊关闭逻辑
        # 如果有网络连接或资源需要清理，在这里处理
        pass


# 全局MCP管理器实例
_mcp_manager: Optional[MCPServerManager] = None


def get_mcp_manager() -> MCPServerManager:
    """获取全局MCP管理器实例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPServerManager()
    return _mcp_manager


async def initialize_mcp_servers():
    """初始化所有MCP服务器"""
    manager = get_mcp_manager()
    await manager.initialize_all()
    return manager


# 便捷函数

async def get_clickhouse_server(env: str = "idn") -> Optional[ClickHouseMCPServer]:
    """获取ClickHouse服务器（env 中的下划线自动转为连字符，与注册名一致）"""
    manager = get_mcp_manager()
    return manager.get_server(f"clickhouse-{env.replace('_', '-')}")


async def get_mysql_server(env: str = "prod") -> Optional[MySQLMCPServer]:
    """获取MySQL服务器"""
    manager = get_mcp_manager()
    return manager.get_server(f"mysql-{env}")


async def get_filesystem_server() -> Optional[FilesystemMCPServer]:
    """获取Filesystem服务器"""
    manager = get_mcp_manager()
    return manager.get_server("filesystem")


async def get_lark_server() -> Optional[LarkMCPServer]:
    """获取Lark服务器"""
    manager = get_mcp_manager()
    return manager.get_server("lark")


# 示例用法

if __name__ == "__main__":
    async def main():
        # 初始化所有服务器
        manager = await initialize_mcp_servers()

        # 列出所有服务器
        servers = manager.list_servers()
        print("已启动的MCP服务器:")
        for server in servers:
            print(f"  - {server['name']} ({server['type']}) v{server['version']}")

        # 获取所有工具
        tools = manager.get_all_tools()
        print(f"\n总共有 {len(tools)} 个工具")

        # 测试文件系统工具
        if "filesystem" in manager.servers:
            result = await manager.call_tool("filesystem", "list_allowed_directories", {})
            print(f"\n允许的目录: {result}")

        # 关闭服务器
        await manager.shutdown()

    asyncio.run(main())
