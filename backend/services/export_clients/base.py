"""
导出客户端抽象基类

定义统一接口，所有数据库客户端实现须继承此类。
"""
from abc import ABC, abstractmethod
from typing import Dict, Generator, List, NamedTuple, Tuple


class ColumnInfo(NamedTuple):
    name: str
    type: str  # 原始数据库类型字符串，如 "Int64", "String", "DateTime"


class BaseExportClient(ABC):
    """
    导出客户端抽象基类。

    子类须实现：
      - get_columns(sql) → List[ColumnInfo]
      - stream_batches(sql, batch_size) → Generator[List[Tuple], ...]
    """

    @abstractmethod
    def get_columns(self, sql: str) -> List[ColumnInfo]:
        """
        执行 SQL（LIMIT 0），返回列名与类型列表，不获取数据行。
        用于导出前预检列元信息。

        Returns:
            List[ColumnInfo(name, type)]

        Raises:
            RuntimeError: SQL 语法错误或连接失败
        """

    @abstractmethod
    def stream_batches(
        self,
        sql: str,
        batch_size: int = 50_000,
    ) -> Generator[List[Tuple], None, None]:
        """
        以流式方式执行 SQL，每次 yield 一批行，不将全部结果加载进内存。

        首次调用时建立连接并开始流式读取；
        每攒够 batch_size 行即 yield 一次；
        流结束时 yield 剩余不足 batch_size 的行（可能为空列表）。

        Args:
            sql:        SELECT 语句
            batch_size: 每批行数，默认 50,000

        Yields:
            List[Tuple] — 每批数据行，Tuple 元素顺序与 get_columns 一致

        Raises:
            RuntimeError: SQL 错误或连接失败
        """
