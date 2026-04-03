"""
ClickHouse HTTP Client

轻量级 HTTP 协议客户端，用于 TCP（9000）不可达时的回退连接。
接口与 clickhouse_driver.Client.execute() 兼容，无需额外安装依赖。

ClickHouse 原生 HTTP 接口（默认端口 8123）：
  POST http://host:8123/
  Body: SQL 语句（UTF-8）
  Params: user, password, database, 以及任意 ClickHouse settings
  使用 FORMAT JSONCompact 获取结构化结果（含列名/类型）
"""
import re
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

logger = logging.getLogger(__name__)

# 匹配查询末尾已有 FORMAT 子句（避免重复追加）
_FORMAT_RE = re.compile(r'\bFORMAT\s+\w+\s*$', re.IGNORECASE)

# 需要返回数据集的语句前缀
_SELECT_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH", "EXISTS")


class ClickHouseHTTPClient:
    """
    ClickHouse HTTP 协议客户端（端口 8123）。

    对外接口与 clickhouse_driver.Client 兼容：
        .execute(query, with_column_types=False, settings=None)

    返回格式与 clickhouse_driver 一致：
        - with_column_types=False: List[tuple]
        - with_column_types=True:  (List[tuple], List[(col_name, col_type)])

    不支持流式查询、INSERT VALUES、SSL——满足 MCP 查询场景即可。
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        timeout: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.timeout = timeout
        self._base_url = f"http://{host}:{port}/"
        self._session = requests.Session()

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _is_select_like(self, query: str) -> bool:
        """判断查询是否会返回数据集（需要追加 FORMAT）。"""
        return query.strip().upper().startswith(_SELECT_PREFIXES)

    def _build_exec_query(self, query: str, is_select: bool) -> str:
        """为 SELECT-like 查询追加 FORMAT JSONCompact（若尚未指定）。"""
        if not is_select:
            return query
        if _FORMAT_RE.search(query):
            return query  # 已有 FORMAT，不重复添加
        return query.rstrip().rstrip(";") + " FORMAT JSONCompact"

    # ── 主接口 ────────────────────────────────────────────────────────────

    def execute(
        self,
        query: str,
        with_column_types: bool = False,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Union[List[Tuple], Tuple[List[Tuple], List[Tuple]]]:
        """
        执行 SQL 查询，返回与 clickhouse_driver 相同的格式。

        Args:
            query:             SQL 语句
            with_column_types: True → 返回 (rows, col_types)；False → 只返回 rows
            settings:          ClickHouse 查询级 settings（dict），透传为 HTTP 参数

        Returns:
            with_column_types=False: List[tuple]
            with_column_types=True:  (List[tuple], List[(name, type)])

        Raises:
            ConnectionError: HTTP 连接失败
            TimeoutError:    请求超时
            RuntimeError:    HTTP 非 200 响应（ClickHouse 查询错误）
        """
        is_select = self._is_select_like(query)
        exec_query = self._build_exec_query(query, is_select)

        # HTTP 参数：认证 + 数据库 + 用户自定义 settings
        params: Dict[str, Any] = {
            "user": self.user,
            "password": self.password,
            "database": self.database,
        }
        if settings:
            # ClickHouse HTTP 接口接受 settings 键值对作为 URL 参数
            params.update(settings)

        try:
            resp = self._session.post(
                self._base_url,
                data=exec_query.encode("utf-8"),
                params=params,
                timeout=self.timeout,
            )
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"ClickHouse HTTP 连接失败 {self.host}:{self.port}: {exc}"
            ) from exc
        except requests.Timeout as exc:
            raise TimeoutError(
                f"ClickHouse HTTP 超时 {self.host}:{self.port}"
            ) from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"ClickHouse HTTP 错误 {resp.status_code} "
                f"({self.host}:{self.port}): {resp.text[:500]}"
            )

        # 非 SELECT 类语句没有返回数据（如 SET、USE 等）
        if not is_select:
            return ([], []) if with_column_types else []

        # 解析 JSONCompact 响应
        result = resp.json()
        meta: List[Dict] = result.get("meta", [])
        data: List[List] = result.get("data", [])

        col_types: List[Tuple[str, str]] = [
            (col["name"], col["type"]) for col in meta
        ]
        rows: List[Tuple] = [tuple(row) for row in data]

        if with_column_types:
            return rows, col_types
        return rows
