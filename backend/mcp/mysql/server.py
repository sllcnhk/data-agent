"""
MySQL MCP服务器实现

提供MySQL数据库的连接、查询、导出等功能
"""
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
import json
import pymysql
from pymysql.cursors import DictCursor

from backend.mcp.base import BaseMCPServer, MCPResponse
from backend.config.settings import settings


class MySQLMCPServer(BaseMCPServer):
    """MySQL MCP服务器"""

    def __init__(self, env: str = "prod"):
        """
        初始化MySQL MCP服务器

        Args:
            env: 环境名称 (prod, staging)
        """
        super().__init__(
            name=f"MySQL MCP Server ({env.upper()})",
            version="1.0.0"
        )
        self.env = env
        self.connection: Optional[pymysql.Connection] = None
        self.config = None

    async def initialize(self):
        """初始化服务器"""
        # 获取配置
        self.config = settings.get_mysql_config(self.env)

        # 创建连接
        self.connection = pymysql.connect(
            host=self.config["host"],
            port=self.config["port"],
            user=self.config["user"],
            password=self.config["password"],
            database=self.config["database"],
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            read_timeout=30,
            write_timeout=30
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
            description="执行MySQL SQL查询",
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
            description="获取MySQL服务器信息",
            input_schema={
                "type": "object",
                "properties": {}
            },
            callback=self._get_server_info
        )

        # 获取表索引信息
        self.register_tool(
            name="get_table_indexes",
            description="获取表的索引信息",
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
            callback=self._get_table_indexes
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
            uri=f"mysql://{self.env}/databases",
            name="数据库列表",
            description="MySQL服务器上的所有数据库",
            mime_type="application/json"
        )

        # 表列表资源
        self.register_resource(
            uri=f"mysql://{self.env}/tables",
            name="表列表",
            description="当前数据库中的所有表",
            mime_type="application/json"
        )

    def _execute_query(
        self,
        query: str,
        max_rows: int = 100
    ) -> Dict[str, Any]:
        """执行查询"""
        try:
            # 验证查询（简单的安全检查）
            query_upper = query.strip().upper()
            if any(keyword in query_upper for keyword in ["DROP", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE", "DELETE"]):
                return {
                    "error": "只允许执行SELECT查询",
                    "query": query
                }

            # 执行查询
            with self.connection.cursor() as cursor:
                cursor.execute(query)
                result = cursor.fetchall()

                # 获取列名
                columns = [desc[0] for desc in cursor.description]

                # 限制返回行数
                rows = result[:max_rows]

                return {
                    "type": "query_result",
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(result),
                    "displayed_rows": len(rows),
                    "query": query
                }

        except pymysql.Error as e:
            return {
                "error": str(e),
                "errno": e.args[0] if e.args else None,
                "query": query
            }
        except Exception as e:
            return {
                "error": f"查询执行失败: {str(e)}",
                "query": query
            }

    def _list_databases(self) -> Dict[str, Any]:
        """列出所有数据库"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SHOW DATABASES")
                databases = [row["Database"] for row in cursor.fetchall()]

                return {
                    "type": "database_list",
                    "databases": databases,
                    "current_database": self.config["database"]
                }
        except Exception as e:
            return {
                "error": f"获取数据库列表失败: {str(e)}"
            }

    def _list_tables(self, database: Optional[str] = None) -> Dict[str, Any]:
        """列出表"""
        try:
            if not database:
                database = self.config["database"]

            with self.connection.cursor() as cursor:
                query = f"SHOW TABLES FROM `{database}`"
                cursor.execute(query)
                tables = [list(row.values())[0] for row in cursor.fetchall()]

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

    def _describe_table(
        self,
        table: str,
        database: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取表结构"""
        try:
            if not database:
                database = self.config["database"]

            with self.connection.cursor() as cursor:
                query = f"DESCRIBE `{database}`.`{table}`"
                cursor.execute(query)
                result = cursor.fetchall()

                columns = []
                for row in result:
                    columns.append({
                        "field": row.get("Field"),
                        "type": row.get("Type"),
                        "null": row.get("Null"),
                        "key": row.get("Key"),
                        "default": row.get("Default"),
                        "extra": row.get("Extra")
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

    def _get_table_overview(
        self,
        table: str,
        database: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取表概览"""
        try:
            if not database:
                database = self.config["database"]

            with self.connection.cursor() as cursor:
                # 获取行数
                count_query = f"SELECT COUNT(*) as cnt FROM `{database}`.`{table}`"
                cursor.execute(count_query)
                row_count = cursor.fetchone()["cnt"]

                # 获取表信息
                info_query = f"""
                    SELECT
                        table_comment as comment,
                        engine as engine,
                        table_rows as estimated_rows,
                        data_length as data_bytes,
                        index_length as index_bytes,
                        create_time,
                        update_time
                    FROM information_schema.tables
                    WHERE table_schema = '{database}' AND table_name = '{table}'
                """
                cursor.execute(info_query)
                info = cursor.fetchone()

                overview = {
                    "type": "table_overview",
                    "database": database,
                    "table": table,
                    "row_count": row_count
                }

                if info:
                    # 计算总大小
                    total_bytes = (info.get("data_bytes", 0) or 0) + (info.get("index_bytes", 0) or 0)

                    overview.update({
                        "engine": info.get("engine"),
                        "comment": info.get("comment"),
                        "estimated_rows": info.get("estimated_rows"),
                        "data_size_bytes": info.get("data_bytes"),
                        "index_size_bytes": info.get("index_bytes"),
                        "total_size_bytes": total_bytes,
                        "create_time": info.get("create_time").isoformat() if info.get("create_time") else None,
                        "update_time": info.get("update_time").isoformat() if info.get("update_time") else None
                    })

                    # 格式化大小
                    if total_bytes:
                        overview["total_size"] = self._format_bytes(total_bytes)
                    if info.get("data_bytes"):
                        overview["data_size"] = self._format_bytes(info.get("data_bytes"))
                    if info.get("index_bytes"):
                        overview["index_size"] = self._format_bytes(info.get("index_bytes"))

                return overview
        except Exception as e:
            return {
                "error": f"获取表概览失败: {str(e)}"
            }

    def _test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1 as test")
                result = cursor.fetchone()

                return {
                    "type": "connection_test",
                    "status": "success",
                    "result": result["test"],
                    "host": self.config["host"],
                    "port": self.config["port"],
                    "database": self.config["database"],
                    "user": self.config["user"]
                }
        except Exception as e:
            return {
                "type": "connection_test",
                "status": "failed",
                "error": str(e),
                "host": self.config["host"],
                "port": self.config["port"],
                "database": self.config["database"]
            }

    def _get_server_info(self) -> Dict[str, Any]:
        """获取服务器信息"""
        try:
            with self.connection.cursor() as cursor:
                # 获取版本信息
                cursor.execute("SELECT VERSION() as version")
                version = cursor.fetchone()["version"]

                # 获取系统信息
                cursor.execute("""
                    SHOW VARIABLES WHERE Variable_name IN (
                        'max_connections',
                        'character_set_server',
                        'collation_server'
                    )
                """)
                variables = {row["Variable_name"]: row["Value"] for row in cursor.fetchall()}

                return {
                    "type": "server_info",
                    "server": {
                        "host": self.config["host"],
                        "port": self.config["port"],
                        "database": self.config["database"],
                        "version": version
                    },
                    "variables": variables,
                    "environment": self.env.upper()
                }
        except Exception as e:
            return {
                "error": f"获取服务器信息失败: {str(e)}"
            }

    def _get_table_indexes(
        self,
        table: str,
        database: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取表索引信息"""
        try:
            if not database:
                database = self.config["database"]

            with self.connection.cursor() as cursor:
                query = f"SHOW INDEX FROM `{database}`.`{table}`"
                cursor.execute(query)
                result = cursor.fetchall()

                indexes = {}
                for row in result:
                    key_name = row["Key_name"]
                    if key_name not in indexes:
                        indexes[key_name] = {
                            "name": key_name,
                            "unique": row["Non_unique"] == 0,
                            "columns": []
                        }

                    indexes[key_name]["columns"].append({
                        "column_name": row["Column_name"],
                        "seq_in_index": row["Seq_in_index"],
                        "collation": row["Collation"],
                        "sub_part": row["Sub_part"],
                        "nullable": row["Null"],
                        "index_type": row["Index_type"]
                    })

                return {
                    "type": "table_indexes",
                    "database": database,
                    "table": table,
                    "indexes": list(indexes.values()),
                    "index_count": len(indexes)
                }
        except Exception as e:
            return {
                "error": f"获取表索引失败: {str(e)}"
            }

    def _format_bytes(self, bytes_value: int) -> str:
        """格式化字节大小"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024.0:
                return f"{bytes_value:.2f} {unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.2f} PB"

    def _sample_table_data(
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
                    SELECT * FROM `{database}`.`{table}`
                    ORDER BY RAND()
                    LIMIT {limit}
                """
            elif sampling_method == "recent" and order_by:
                query = f"""
                    SELECT * FROM `{database}`.`{table}`
                    ORDER BY `{order_by}` DESC
                    LIMIT {limit}
                """
            else:  # top
                query = f"""
                    SELECT * FROM `{database}`.`{table}`
                    LIMIT {limit}
                """

            # 执行查询
            result = self._execute_query(query, max_rows=limit)

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
                result = self._list_databases()
                return MCPResponse(
                    success=True,
                    data=result
                )
            elif "tables" in uri:
                # 获取表列表
                result = self._list_tables()
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

def create_mysql_server(env: str = "prod") -> MySQLMCPServer:
    """
    创建MySQL MCP服务器实例

    Args:
        env: 环境 (prod, staging)

    Returns:
        MySQLMCPServer实例
    """
    return MySQLMCPServer(env=env)


# 示例用法

if __name__ == "__main__":
    import asyncio

    async def main():
        # 创建服务器
        server = create_mysql_server("prod")
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
