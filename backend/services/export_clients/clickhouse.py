"""
ClickHouse 导出客户端

使用 HTTP 流式接口（FORMAT TabSeparatedWithNamesAndTypes）实现大数据量低内存导出。

原理：
  - ClickHouse HTTP 接口支持流式响应，服务端边查询边输出
  - 使用 requests(stream=True) 逐行消费，无需等待全部结果
  - 前两行是列名和列类型（制表符分隔），之后每行是一条数据
  - 本客户端在内存中只持有当前 batch_size 行，峰值内存 ≈ batch_size × 行宽
"""
import logging
from typing import Dict, Generator, List, Optional, Tuple

import requests

from backend.services.export_clients.base import BaseExportClient, ColumnInfo

logger = logging.getLogger(__name__)


def _parse_tsv_cell(raw: str):
    """将 ClickHouse TabSeparated 单元格原始字符串还原为 Python 值。"""
    if raw == r"\N":
        return None
    # 手动处理转义（str.translate 不支持多字符键）
    result = (
        raw
        .replace("\\\\", "\x00BACKSLASH\x00")
        .replace("\\t", "\t")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\x00BACKSLASH\x00", "\\")
    )
    return result


class ClickHouseExportClient(BaseExportClient):
    """
    ClickHouse HTTP 流式导出客户端。

    使用 FORMAT TabSeparatedWithNamesAndTypes 流式读取查询结果：
      第 1 行：列名（制表符分隔）
      第 2 行：列类型（制表符分隔）
      第 3+ 行：数据行（制表符分隔）
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        timeout: int = 3600,  # 大查询默认 1 小时超时
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.timeout = timeout
        self._base_url = f"http://{host}:{port}/"

    def _base_params(self) -> Dict:
        return {
            "user": self.user,
            "password": self.password,
            "database": self.database,
        }

    def get_columns(self, sql: str) -> List[ColumnInfo]:
        """
        执行 LIMIT 0 查询，快速获取列元信息，不返回数据行。
        用于导出前预检列名/类型。
        """
        # 用 LIMIT 0 只取列信息，不拉数据
        stripped = sql.rstrip().rstrip(";")
        probe_sql = f"SELECT * FROM ({stripped}) AS _q LIMIT 0 FORMAT TabSeparatedWithNamesAndTypes"

        params = self._base_params()
        try:
            resp = requests.post(
                self._base_url,
                data=probe_sql.encode("utf-8"),
                params=params,
                timeout=30,
                stream=False,
            )
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"ClickHouse 连接失败 {self.host}:{self.port}: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"ClickHouse 错误 {resp.status_code}: {resp.text[:500]}"
            )

        lines = resp.text.splitlines()
        if len(lines) < 2:
            return []

        names = lines[0].split("\t")
        types = lines[1].split("\t")
        return [ColumnInfo(name=n, type=t) for n, t in zip(names, types)]

    def stream_batches(
        self,
        sql: str,
        batch_size: int = 50_000,
    ) -> Generator[List[Tuple], None, None]:
        """
        以 HTTP 流式方式执行 SQL，逐批 yield 数据行。

        内存特性：
          - 任意时刻内存中只有 ≤ batch_size 行
          - requests 流式读取，HTTP body 不全量缓存
        """
        stripped = sql.rstrip().rstrip(";")
        stream_sql = stripped + " FORMAT TabSeparatedWithNamesAndTypes"

        params = self._base_params()

        try:
            resp = requests.post(
                self._base_url,
                data=stream_sql.encode("utf-8"),
                params=params,
                timeout=self.timeout,
                stream=True,  # 关键：流式响应，不等待全量数据
            )
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"ClickHouse 连接失败 {self.host}:{self.port}: {exc}"
            ) from exc
        except requests.Timeout as exc:
            raise TimeoutError(
                f"ClickHouse 流式查询超时 {self.host}:{self.port}"
            ) from exc

        if resp.status_code != 200:
            body = resp.content.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"ClickHouse 错误 {resp.status_code}: {body}")

        line_iter = resp.iter_lines(decode_unicode=True)

        # 消费前两行（列名 + 列类型），不用于此处解析
        try:
            next(line_iter)  # 列名行
            next(line_iter)  # 列类型行
        except StopIteration:
            return  # 空结果（无行）

        batch: List[Tuple] = []
        for line in line_iter:
            if line == "":
                continue
            cells = line.split("\t")
            row = tuple(_parse_tsv_cell(c) for c in cells)
            batch.append(row)
            if len(batch) >= batch_size:
                yield batch
                batch = []

        if batch:
            yield batch
