"""
ClickHouse MCP服务器实现

提供ClickHouse数据库的连接、查询、导出等功能
"""
import asyncio
import logging
import re
from typing import Dict, List, Any, Optional
from datetime import datetime, date, time
from decimal import Decimal
import uuid
import json
from clickhouse_driver import Client as ClickHouseClient
from clickhouse_driver import errors as ClickHouseError
from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient

logger = logging.getLogger(__name__)

# DDL keyword detection using word boundaries.
# Simple substring matching (e.g. "CREATE" in "create_time") causes false positives.
# \b ensures the keyword is a standalone word, not part of an identifier.
_DDL_KEYWORD_RE = re.compile(
    r'\b(DROP|TRUNCATE|ALTER|CREATE)\b',
    re.IGNORECASE,
)

from backend.mcp.base import BaseMCPServer, MCPResponse, format_query_result
from backend.config.settings import settings


def _to_json_safe(value: Any) -> Any:
    """将 ClickHouse 返回的特殊类型转换为 JSON 可序列化类型。

    注意：datetime 是 date 的子类，必须先检查 datetime。
    """
    # 时间类型 → ISO 8601 字符串
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    # Decimal → float（保留精度）
    if isinstance(value, Decimal):
        return float(value)
    # 二进制 → hex 字符串
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    # UUID → 字符串
    if isinstance(value, uuid.UUID):
        return str(value)
    # 列表/元组（ClickHouse Array 类型）→ 递归转换元素
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v) for v in value]
    # 其他非 JSON 原生类型 → 字符串兜底
    if not isinstance(value, (int, float, str, bool, type(None))):
        return str(value)
    return value


class ClickHouseMCPServer(BaseMCPServer):
    """ClickHouse MCP服务器"""

    def __init__(self, env: str = "idn", level: str = "admin"):
        """
        初始化ClickHouse MCP服务器

        Args:
            env:   环境名称 (idn, sg, mx)
            level: 连接权限级别 ("admin" 高权限 | "readonly" 只读)
        """
        level_label = "Admin" if level == "admin" else "ReadOnly"
        super().__init__(
            name=f"ClickHouse MCP Server ({env.upper()} {level_label})",
            version="1.0.0"
        )
        self.env = env
        self.level = level
        self.client: Optional[ClickHouseClient] = None
        self.config = None
        self._protocol: str = "native"  # "native"（TCP）或 "http"

    async def initialize(self):
        """
        初始化服务器：优先 TCP（port，默认 9000），失败则回退 HTTP（http_port，默认 8123）。

        TCP 探测使用 5 秒快速超时，确保服务启动不被长时间阻塞。
        一旦确定协议，后续所有查询都使用同一 client 实例。
        """
        self.config = settings.get_clickhouse_config(self.env, level=self.level)

        host = self.config["host"]
        tcp_port = self.config["port"]          # 默认 9000
        http_port = self.config["http_port"]    # 默认 8123

        # ── 1. 尝试 TCP 连接 ──────────────────────────────────────────────
        tcp_client = ClickHouseClient(
            host=host,
            port=tcp_port,
            user=self.config["user"],
            password=self.config["password"],
            database=self.config["database"],
            connect_timeout=5,       # 快速失败，避免阻塞启动
            send_receive_timeout=10,
            # use_numpy removed: causes inhomogeneous-array errors on Array/Tuple/Nested columns
        )

        try:
            tcp_client.execute("SELECT 1")  # 实际触发 TCP 握手
            self.client = tcp_client
            self._protocol = "native"
            logger.info(
                "[ClickHouseMCP] %s(%s) connected via TCP port %d",
                self.env.upper(), self.level, tcp_port,
            )
        except Exception as tcp_err:
            # ── 2. TCP 失败 → 回退 HTTP ───────────────────────────────────
            logger.warning(
                "[ClickHouseMCP] %s(%s) TCP port %d unavailable (%s), "
                "falling back to HTTP port %d",
                self.env.upper(), self.level, tcp_port, tcp_err, http_port,
            )
            http_client = ClickHouseHTTPClient(
                host=host,
                port=http_port,
                user=self.config["user"],
                password=self.config["password"],
                database=self.config["database"],
            )
            # 验证 HTTP 也可正常连接（此处抛出则让上层 manager 捕获并跳过注册）
            http_client.execute("SELECT 1")
            self.client = http_client
            self._protocol = "http"
            logger.info(
                "[ClickHouseMCP] %s(%s) connected via HTTP port %d (fallback)",
                self.env.upper(), self.level, http_port,
            )

        # 注册工具
        self._register_tools()

        # 注册资源
        self._register_resources()

    def _register_tools(self):
        """注册工具"""

        # 查询工具
        self.register_tool(
            name="query",
            description="执行ClickHouse SQL查询",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL查询语句"
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "最大返回行数",
                        "default": 100
                    }
                },
                "required": ["query"]
            },
            callback=self._execute_query
        )

        # 查询数据库列表
        self.register_tool(
            name="list_databases",
            description="列出所有数据库",
            input_schema={
                "type": "object",
                "properties": {}
            },
            callback=self._list_databases
        )

        # 查询表列表
        self.register_tool(
            name="list_tables",
            description="列出指定数据库中的所有表",
            input_schema={
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "数据库名称（可选，默认当前数据库）"
                    }
                }
            },
            callback=self._list_tables
        )

        # 获取表结构
        self.register_tool(
            name="describe_table",
            description="获取表的结构信息",
            input_schema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "表名"
                    },
                    "database": {
                        "type": "string",
                        "description": "数据库名称（可选）"
                    }
                },
                "required": ["table"]
            },
            callback=self._describe_table
        )

        # 批量获取多个表结构（一次调用替代多次 describe_table，节省推理轮次）
        self.register_tool(
            name="batch_describe_tables",
            description=(
                "批量获取多张表的结构信息（最多30张）。"
                "当需要了解多个表的字段时，优先使用此工具，"
                "可将多次 describe_table 调用合并为一次，大幅减少推理轮次消耗。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "表名列表（最多30个）"
                    },
                    "database": {
                        "type": "string",
                        "description": "数据库名称（可选，默认当前数据库）"
                    }
                },
                "required": ["tables"]
            },
            callback=self._batch_describe_tables
        )

        # 获取表数据概览
        self.register_tool(
            name="get_table_overview",
            description="获取表的数据概览（行数、大小等）",
            input_schema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "表名"
                    },
                    "database": {
                        "type": "string",
                        "description": "数据库名称（可选）"
                    }
                },
                "required": ["table"]
            },
            callback=self._get_table_overview
        )

        # 检查连接
        self.register_tool(
            name="test_connection",
            description="测试数据库连接",
            input_schema={
                "type": "object",
                "properties": {}
            },
            callback=self._test_connection
        )

        # 获取服务器信息
        self.register_tool(
            name="get_server_info",
            description="获取ClickHouse服务器信息",
            input_schema={
                "type": "object",
                "properties": {}
            },
            callback=self._get_server_info
        )

        # 数据采样工具
        self.register_tool(
            name="sample_table_data",
            description="获取表的示例数据(最多1000行),用于理解数据内容",
            input_schema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "表名"
                    },
                    "database": {
                        "type": "string",
                        "description": "数据库名称（可选）"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回行数（默认1000，最大1000）",
                        "default": 1000
                    },
                    "sampling_method": {
                        "type": "string",
                        "description": "采样方法: top(前N行), random(随机采样), recent(最新数据,需要order_by参数)",
                        "enum": ["top", "random", "recent"],
                        "default": "top"
                    },
                    "order_by": {
                        "type": "string",
                        "description": "排序字段(仅在sampling_method=recent时使用)"
                    }
                },
                "required": ["table"]
            },
            callback=self._sample_table_data
        )

    def _register_resources(self):
        """注册资源"""

        # 数据库列表资源
        self.register_resource(
            uri=f"clickhouse://{self.env}/databases",
            name="数据库列表",
            description="ClickHouse服务器上的所有数据库",
            mime_type="application/json"
        )

        # 表列表资源
        self.register_resource(
            uri=f"clickhouse://{self.env}/tables",
            name="表列表",
            description="当前数据库中的所有表",
            mime_type="application/json"
        )

    async def _execute_query(
        self,
        query: str,
        max_rows: int = 100
    ) -> Dict[str, Any]:
        """执行查询"""
        try:
            # 安全检查：拒绝 DDL 语句（使用词边界正则，避免误匹配列名如 create_time）
            if _DDL_KEYWORD_RE.search(query):
                return {
                    "error": "不允许执行DDL操作",
                    "query": query
                }

            # 执行查询
            result = self.client.execute(
                query,
                with_column_types=True,
                settings={"max_result_rows": max_rows, "result_overflow_mode": "break"}
            )

            # 格式化结果
            if result:
                rows, columns = result

                # 转换列信息
                column_names = [col[0] for col in columns]
                column_types = [col[1] for col in columns]

                # 转换数据（使用 _to_json_safe 处理所有 ClickHouse 特殊类型）
                data = []
                for row in rows:
                    row_dict = {}
                    for i, col_name in enumerate(column_names):
                        row_dict[col_name] = _to_json_safe(row[i])
                    data.append(row_dict)

                return {
                    "type": "query_result",
                    "columns": column_names,
                    "column_types": [str(t) for t in column_types],
                    "rows": data,
                    "row_count": len(data),
                    "query": query
                }
            else:
                return {
                    "type": "query_result",
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "query": query
                }

        except ClickHouseError.Error as e:
            return {
                "error": str(e),
                "code": e.code if hasattr(e, "code") else None,
                "query": query
            }
        except Exception as e:
            return {
                "error": f"查询执行失败: {str(e)}",
                "query": query
            }

    async def _list_databases(self) -> Dict[str, Any]:
        """列出所有数据库"""
        try:
            query = "SHOW DATABASES"
            result = self.client.execute(query)
            databases = [row[0] for row in result]

            return {
                "type": "database_list",
                "databases": databases,
                "current_database": self.config["database"]
            }
        except Exception as e:
            return {
                "error": f"获取数据库列表失败: {str(e)}"
            }

    async def _list_tables(self, database: Optional[str] = None) -> Dict[str, Any]:
        """列出表"""
        try:
            if not database:
                database = self.config["database"]

            query = f"SHOW TABLES FROM {database}"
            result = self.client.execute(query)
            tables = [row[0] for row in result]

            return {
                "type": "table_list",
                "database": database,
                "tables": tables,
                "table_count": len(tables)
            }
        except Exception as e:
            return {
                "error": f"获取表列表失败: {str(e)}"
            }

    async def _describe_table(
        self,
        table: str,
        database: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取表结构"""
        try:
            if not database:
                database = self.config["database"]

            query = f"DESCRIBE {database}.{table}"
            result = self.client.execute(query)

            columns = []
            for row in result:
                columns.append({
                    "name": row[0],
                    "type": row[1],
                    "default_type": row[2] if len(row) > 2 else None,
                    "default_expression": row[3] if len(row) > 3 else None,
                    "comment": row[4] if len(row) > 4 else None,
                    "codec": row[5] if len(row) > 5 else None,
                    "ttl": row[6] if len(row) > 6 else None
                })

            return {
                "type": "table_schema",
                "database": database,
                "table": table,
                "columns": columns,
                "column_count": len(columns)
            }
        except Exception as e:
            return {
                "error": f"获取表结构失败: {str(e)}"
            }

    async def _batch_describe_tables(
        self,
        tables: List[str],
        database: Optional[str] = None
    ) -> Dict[str, Any]:
        """批量获取多个表的结构，一次调用替代多次 describe_table，节省推理轮次。

        最多支持 30 张表；超出部分自动截断并在 truncated 字段告知。
        """
        if not database:
            database = self.config["database"]

        MAX_TABLES = 30
        truncated = len(tables) > MAX_TABLES
        target = tables[:MAX_TABLES]

        schemas: Dict[str, Any] = {}
        for tbl in target:
            schemas[tbl] = await self._describe_table(tbl, database)

        return {
            "type": "batch_table_schemas",
            "database": database,
            "table_count": len(schemas),
            "schemas": schemas,
            "truncated": truncated,
            "truncated_message": (
                f"只返回了前 {MAX_TABLES} 张表，共 {len(tables)} 张。"
                if truncated else None
            ),
        }

    async def _get_table_overview(
        self,
        table: str,
        database: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取表概览（兼容不同 ClickHouse 版本）"""
        try:
            if not database:
                database = self.config["database"]

            overview: Dict[str, Any] = {
                "type": "table_overview",
                "database": database,
                "table": table,
            }

            # 精确行数（COUNT(*) 始终可用）
            count_query = f"SELECT COUNT(*) FROM {database}.{table}"
            try:
                overview["row_count"] = self.client.execute(count_query)[0][0]
            except Exception:
                overview["row_count"] = None

            # 一次查询获取引擎、大小、估算行数
            # total_rows 可能为 NULL (MaterializedView 等引擎不支持)
            # 旧版 ClickHouse (<20.x) 可能没有 total_rows — 用 try/except 兜底
            meta_query = f"""
                SELECT
                    engine,
                    formatReadableSize(total_bytes) AS size,
                    total_bytes,
                    total_rows
                FROM system.tables
                WHERE database = '{database}' AND name = '{table}'
            """
            try:
                meta = self.client.execute(meta_query)
                if meta:
                    engine, size, size_bytes, total_rows = meta[0]
                    overview["engine"] = engine
                    overview["size"] = size
                    overview["size_bytes"] = int(size_bytes) if size_bytes is not None else None
                    overview["total_rows_estimate"] = int(total_rows) if total_rows is not None else None
            except Exception:
                # 降级：仅查 engine（所有版本均支持）
                try:
                    eng = self.client.execute(
                        f"SELECT engine FROM system.tables "
                        f"WHERE database = '{database}' AND name = '{table}'"
                    )
                    if eng:
                        overview["engine"] = eng[0][0]
                except Exception:
                    pass

            return overview
        except Exception as e:
            return {
                "error": f"获取表概览失败: {str(e)}"
            }

    async def _test_connection(self) -> Dict[str, Any]:
        """测试连接（同时报告当前使用的协议）"""
        try:
            result = self.client.execute("SELECT 1 as test")
            active_port = (
                self.config["http_port"]
                if self._protocol == "http"
                else self.config["port"]
            )
            return {
                "type": "connection_test",
                "status": "success",
                "result": result[0][0],
                "host": self.config["host"],
                "port": active_port,
                "protocol": self._protocol,   # "native"（TCP）或 "http"
                "database": self.config["database"],
                "user": self.config["user"],
            }
        except Exception as e:
            return {
                "type": "connection_test",
                "status": "failed",
                "error": str(e),
                "host": self.config["host"],
                "port": self.config["port"],
                "http_port": self.config.get("http_port"),
                "protocol": self._protocol,
                "database": self.config["database"],
            }

    async def _get_server_info(self) -> Dict[str, Any]:
        """获取服务器信息"""
        try:
            # 获取版本信息
            version_query = "SELECT version()"
            version = self.client.execute(version_query)[0][0]

            # 获取系统信息
            system_query = """
                SELECT
                    name,
                    value
                FROM system.settings
                WHERE name IN ('max_execution_time', 'max_memory_usage', 'max_result_rows')
            """
            settings_info = self.client.execute(system_query)

            settings_dict = {row[0]: row[1] for row in settings_info}

            return {
                "type": "server_info",
                "server": {
                    "host": self.config["host"],
                    "port": self.config["port"],
                    "database": self.config["database"],
                    "version": version
                },
                "settings": settings_dict,
                "environment": self.env.upper()
            }
        except Exception as e:
            return {
                "error": f"获取服务器信息失败: {str(e)}"
            }

    async def _sample_table_data(
        self,
        table: str,
        database: Optional[str] = None,
        limit: int = 1000,
        sampling_method: str = "top",
        order_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取表的示例数据

        Args:
            table: 表名
            database: 数据库名
            limit: 最多返回行数 (默认1000)
            sampling_method: 采样方法 (top, random, recent)
            order_by: 排序字段 (仅在recent方法时使用)

        Returns:
            包含列信息和示例数据的字典
        """
        try:
            if not database:
                database = self.config["database"]

            # 强制最大1000行
            limit = min(limit, 1000)

            # 根据采样方法构建查询
            if sampling_method == "random":
                query = f"""
                    SELECT * FROM {database}.{table}
                    ORDER BY rand()
                    LIMIT {limit}
                """
            elif sampling_method == "recent" and order_by:
                query = f"""
                    SELECT * FROM {database}.{table}
                    ORDER BY {order_by} DESC
                    LIMIT {limit}
                """
            else:  # top
                query = f"""
                    SELECT * FROM {database}.{table}
                    LIMIT {limit}
                """

            # 执行查询
            result = await self._execute_query(query, max_rows=limit)

            # 如果查询成功,添加采样信息
            if "error" not in result:
                result["sampling_info"] = {
                    "method": sampling_method,
                    "requested_limit": limit,
                    "actual_rows": result.get("row_count", 0),
                    "order_by": order_by if sampling_method == "recent" else None
                }
                result["type"] = "sample_data"

            return result

        except Exception as e:
            return {
                "error": f"数据采样失败: {str(e)}",
                "table": table,
                "database": database
            }

    async def get_resource_content(self, uri: str) -> MCPResponse:
        """获取资源内容"""
        try:
            if "databases" in uri:
                # 获取数据库列表
                result = await self._list_databases()
                return MCPResponse(
                    success=True,
                    data=result
                )
            elif "tables" in uri:
                # 获取表列表
                result = await self._list_tables()
                return MCPResponse(
                    success=True,
                    data=result
                )
            else:
                return MCPResponse(
                    success=False,
                    error=f"未知的资源: {uri}"
                )
        except Exception as e:
            return MCPResponse(
                success=False,
                error=str(e)
            )


# 工厂函数

def create_clickhouse_server(env: str = "idn") -> ClickHouseMCPServer:
    """
    创建ClickHouse MCP服务器实例

    Args:
        env: 环境 (idn, sg, mx)

    Returns:
        ClickHouseMCPServer实例
    """
    return ClickHouseMCPServer(env=env)


# 示例用法

if __name__ == "__main__":
    import asyncio

    async def main():
        # 创建服务器
        server = create_clickhouse_server("idn")
        await server.initialize()

        # 获取服务器信息
        info = await server.get_server_info()
        print(json.dumps(info, indent=2, ensure_ascii=False))

        # 测试连接
        result = await server.call_tool("test_connection", {})
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

        # 列出数据库
        result = await server.call_tool("list_databases", {})
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    # asyncio.run(main())
