"""
ClickHouse 导出客户端

使用 HTTP 流式接口（FORMAT TabSeparatedWithNamesAndTypes）实现大数据量低内存导出。

原理：
  - ClickHouse HTTP 接口支持流式响应，服务端边查询边输出
  - 使用 requests(stream=True) 逐行消费，无需等待全部结果
  - 前两行是列名和列类型（制表符分隔），之后每行是一条数据
  - 本客户端在内存中只持有当前 batch_size 行，峰值内存 ≈ batch_size × 行宽

分批提取（规避 max_execution_time 估算拒绝）：
  - count_rows(sql)               → 预扫描总行数
  - stream_batches_chunked(...)   → LIMIT/OFFSET 窗口提取，每批独立 HTTP 请求
  - extra_settings                → 向 ClickHouse 传递 per-query 设置（如 max_execution_time=300）
    · 这是应用层覆盖，仅影响本次请求，不修改服务器配置
"""
import logging
import os
import socket
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

from backend.services.export_clients.base import BaseExportClient, ColumnInfo

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TCP Keepalive HTTPAdapter — OS 层心跳，防御网络/NAT/LB 切断长连接
# ─────────────────────────────────────────────────────────────────────────────
#
# HTTP-level 心跳（send_progress_in_http_headers）只在服务端有数据要送时才发头，
# 若服务端长时间内部计算无任何输出，HTTP 心跳也不发出 → LB 仍可能切断。
#
# TCP keepalive 是 OS 层机制：内核周期发送空 TCP 包，即使应用层完全静默，
# NAT/LB 也能看到 TCP 活动，避免空闲超时切断。
#
# 平台兼容：
#   - Linux: TCP_KEEPIDLE / TCP_KEEPINTVL / TCP_KEEPCNT 全部可用
#   - macOS: TCP_KEEPIDLE 别名为 TCP_KEEPALIVE，TCP_KEEPINTVL/TCP_KEEPCNT 也可用
#   - Windows: 仅 SO_KEEPALIVE 可用，间隔由系统注册表控制（默认 2 小时太长）

class _TCPKeepAliveAdapter(HTTPAdapter):
    """带 OS 层 TCP keepalive 的 HTTPAdapter，防止长连接因空闲被中间链路切断"""

    def init_poolmanager(self, *args, **kwargs):
        keepidle = int(os.getenv("CH_EXPORT_TCP_KEEPIDLE", "30"))
        keepintvl = int(os.getenv("CH_EXPORT_TCP_KEEPINTVL", "10"))
        keepcnt = int(os.getenv("CH_EXPORT_TCP_KEEPCNT", "6"))

        socket_options = [
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        ]
        # Linux / macOS 平台的精细 keepalive 控制（Windows 跳过）
        for opt_name, val in (
            ("TCP_KEEPIDLE", keepidle),
            ("TCP_KEEPINTVL", keepintvl),
            ("TCP_KEEPCNT", keepcnt),
        ):
            opt = getattr(socket, opt_name, None)
            if opt is not None:
                socket_options.append((socket.IPPROTO_TCP, opt, val))

        kwargs["socket_options"] = socket_options
        super().init_poolmanager(*args, **kwargs)


def _build_keepalive_session() -> requests.Session:
    """构造带 TCP keepalive 的 requests.Session"""
    session = requests.Session()
    adapter = _TCPKeepAliveAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# 模块级单例 Session（线程安全；requests Session 内部是线程安全的）
# 用于 stream_batches / count_rows / get_columns 的 HTTP 请求
_export_session: Optional[requests.Session] = None


def _get_export_session() -> requests.Session:
    """惰性初始化全局 Session（首次调用时构造）"""
    global _export_session
    if _export_session is None:
        if os.getenv("CH_EXPORT_TCP_KEEPALIVE", "1") == "0":
            # 关闭 keepalive 时回退到标准 Session
            _export_session = requests.Session()
        else:
            _export_session = _build_keepalive_session()
    return _export_session


# ─────────────────────────────────────────────────────────────────────────────
# 默认导出查询的 HTTP 保活/流式设置
# ─────────────────────────────────────────────────────────────────────────────
#
# 解决跨境/云上 LB/NAT/代理在长查询服务端处理期间因「连接空闲」切断流式响应的问题。
#
# 典型故障模式（v2.13 实测）：
#   - 用户 SQL 含 decrypt/JSONExtract/arrayMap 等高 CPU 操作
#   - ClickHouse 服务端先内部计算数分钟，HTTP 连接期间无字节流动
#   - 中间链路（云 LB / NAT / 反向代理）默认 ~5 分钟空闲超时切断连接
#   - 客户端读到 ChunkedEncodingError("Response ended prematurely") 或 IncompleteRead
#
# 解决方案（ClickHouse 原生支持）：
#   send_progress_in_http_headers=1      → 服务端周期发送 X-ClickHouse-Progress HTTP 头
#                                          作为应用层心跳，让 LB/NAT 认为连接活跃
#   http_headers_progress_interval_ms=10000 → 心跳间隔 10 秒（默认 100ms 太频繁）
#   wait_end_of_query=0                  → 不等服务端缓冲完所有结果再发，立即流式发送
#
# 用户可通过 stream_batches 的 extra_settings 参数覆盖这些默认值（后传入优先级更高）。
# 也可通过环境变量 CH_EXPORT_HTTP_KEEPALIVE=0 在某些不兼容的场景关闭（保留逃生口）。

def _build_default_streaming_settings() -> Dict[str, str]:
    """构造默认流式查询设置（值为 str，因为 HTTP URL 参数都是字符串）"""
    if os.getenv("CH_EXPORT_HTTP_KEEPALIVE", "1") == "0":
        return {}
    return {
        "send_progress_in_http_headers": "1",
        "http_headers_progress_interval_ms": os.getenv(
            "CH_EXPORT_PROGRESS_INTERVAL_MS", "10000",
        ),
        "wait_end_of_query": "0",
    }


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


def is_ch_timeout_estimate_error(exc: Exception) -> bool:
    """
    判断是否为 ClickHouse Code 160: ESTIMATED_EXECUTION_TIMEOUT_EXCEEDED。

    该错误在查询执行前由 ClickHouse 优化器抛出：
    当估计执行时间 > max_execution_time 时直接拒绝，不返回任何数据。
    """
    msg = str(exc)
    return "Code: 160" in msg or "ESTIMATED_EXECUTION_TIMEOUT_EXCEEDED" in msg


def is_transient_stream_error(exc: Exception) -> bool:
    """
    判断是否为「流式响应中途断开」类瞬时错误，可通过 LIMIT/OFFSET 回退恢复。

    触发场景：
      - 跨境网络抖动切断长连接
      - ClickHouse 服务端 OOM/abort 主动关闭流
      - 中间代理/LB 的空闲连接超时
      - urllib3.exceptions.ProtocolError / requests.exceptions.ChunkedEncodingError
      - http.client.IncompleteRead

    回退策略：每个 LIMIT/OFFSET 窗口是独立 HTTP 请求，短小、独立连接，
    重试时大概率成功。
    """
    # 走 try/except 避免运行时还要 import requests/urllib3
    try:
        import requests.exceptions as _re
        import urllib3.exceptions as _u3e
        from http.client import IncompleteRead

        if isinstance(exc, (_re.ChunkedEncodingError,
                            _re.ConnectionError,
                            _u3e.ProtocolError,
                            IncompleteRead)):
            return True
    except ImportError:
        pass

    # 兜底：通过错误消息匹配
    msg = str(exc)
    fingerprints = (
        "Connection broken",
        "IncompleteRead",
        "ProtocolError",
        "ChunkedEncodingError",
        "Connection aborted",
        "Connection reset",
        "Response ended prematurely",  # urllib3 ProtocolError 在 chunked 编码末尾断开时的消息
        "Read timed out",
        "Remote end closed connection",
    )
    return any(fp in msg for fp in fingerprints)


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
            resp = _get_export_session().post(
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

    def count_rows(self, sql: str, timeout: int = 300) -> int:
        """
        预扫描总行数：SELECT count() FROM ({sql})。

        用于分批提取前计算分批数量，进而更新任务进度条。
        timeout 默认 300s（与 EXPORT_QUERY_MAX_EXECUTION_TIME 默认值对齐）。
        """
        stripped = sql.rstrip().rstrip(";")
        count_sql = f"SELECT count() FROM ({stripped}) AS _cnt_q"
        params = {
            **self._base_params(),
            **_build_default_streaming_settings(),  # 保活心跳：count() 在大表上也可能跑很久
            "max_execution_time": timeout,
        }
        try:
            resp = _get_export_session().post(
                self._base_url,
                data=count_sql.encode("utf-8"),
                params=params,
                timeout=timeout + 30,   # HTTP 超时稍大于 CH 超时，留出网络余量
            )
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"ClickHouse 连接失败 {self.host}:{self.port}: {exc}"
            ) from exc
        except requests.Timeout as exc:
            raise TimeoutError(
                f"count_rows 超时（>{timeout}s）: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"count_rows 失败 {resp.status_code}: {resp.text[:300]}"
            )

        return int(resp.text.strip())

    def stream_batches(
        self,
        sql: str,
        batch_size: int = 50_000,
        extra_settings: Optional[Dict[str, Any]] = None,
    ) -> Generator[List[Tuple], None, None]:
        """
        以 HTTP 流式方式执行 SQL，逐批 yield 数据行。

        默认注入的保活设置（防止跨境/云上 LB 切断流式响应）：
          - send_progress_in_http_headers=1         应用层心跳
          - http_headers_progress_interval_ms=10000 心跳间隔
          - wait_end_of_query=0                     立即流式发送
        可通过 extra_settings 覆盖；也可通过环境变量 CH_EXPORT_HTTP_KEEPALIVE=0 全局关闭。

        参数：
          extra_settings  — 附加到 HTTP URL 参数的 ClickHouse per-query 设置，
                            例如 {"max_execution_time": 300}。
                            这是应用层操作，不修改服务器配置，仅对本次请求生效。
                            优先级高于默认保活设置。

        内存特性：
          - 任意时刻内存中只有 ≤ batch_size 行
          - requests 流式读取，HTTP body 不全量缓存
        """
        stripped = sql.rstrip().rstrip(";")
        stream_sql = stripped + " FORMAT TabSeparatedWithNamesAndTypes"

        params = self._base_params()
        # 1. 先注入默认保活设置
        params.update(_build_default_streaming_settings())
        # 2. 调用方传入的 extra_settings 优先级更高，可覆盖默认
        if extra_settings:
            params.update(extra_settings)

        try:
            resp = _get_export_session().post(
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

    def stream_batches_chunked(
        self,
        sql: str,
        chunk_size: int,
        total_rows: int,
        batch_size: int = 50_000,
        extra_settings: Optional[Dict[str, Any]] = None,
    ) -> Generator[List[Tuple], None, None]:
        """
        LIMIT/OFFSET 分批提取，规避 ClickHouse max_execution_time 估算拒绝（Code 160）。

        工作原理：
          将原始 SQL 包装为多个 LIMIT/OFFSET 窗口查询，每个窗口独立 HTTP 请求。
          配合 extra_settings={"max_execution_time": N} 实现应用层 per-query 超时覆盖。

        注意事项（ClickHouse LIMIT/OFFSET 特性）：
          - LIMIT N OFFSET M 在 ClickHouse 中实际扫描 M+N 行（无跳过优化）
          - 对后期分批（大 OFFSET），扫描量接近全表，因此必须同时设置宽松的
            max_execution_time（通过 extra_settings），否则后期分批仍可能超时
          - 与光标分页（WHERE pk > last_pk）相比，本方案适用于任意 SQL；
            光标分页需要已知主键列，适合未来针对特定场景的进一步优化

        接口与 stream_batches 相同（yield List[Tuple]），可透明替换。
        调用方可通过 exported_rows // chunk_size 估算已完成的 SQL 分批数。

        参数：
          chunk_size    — 每个 SQL 分批的行数（LIMIT 值）
          total_rows    — 预扫描总行数（来自 count_rows()）
          extra_settings — 同 stream_batches.extra_settings
        """
        stripped = sql.rstrip().rstrip(";")
        offset = 0
        chunk_idx = 0

        while offset < total_rows:
            remaining = total_rows - offset
            current_limit = min(chunk_size, remaining)

            chunk_sql = (
                f"SELECT * FROM ({stripped}) AS _chunk_{chunk_idx}"
                f" LIMIT {current_limit} OFFSET {offset}"
            )
            logger.debug(
                "ChunkedExtract chunk=%d offset=%d limit=%d (total=%d)",
                chunk_idx, offset, current_limit, total_rows,
            )

            yield from self.stream_batches(
                chunk_sql,
                batch_size=batch_size,
                extra_settings=extra_settings,
            )

            offset += chunk_size
            chunk_idx += 1
