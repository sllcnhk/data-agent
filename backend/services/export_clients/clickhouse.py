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
import sys
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

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
#   - Linux:   TCP_KEEPIDLE / TCP_KEEPINTVL / TCP_KEEPCNT 全部可用,走 socket_options(setsockopt)
#   - macOS:   TCP_KEEPIDLE 别名为 TCP_KEEPALIVE，KEEPINTVL/KEEPCNT 同上,走 socket_options
#   - Windows: setsockopt 拿不到 TCP_KEEPIDLE 等常量,改在 connect() 之后用
#              SIO_KEEPALIVE_VALS ioctl 设置(实现:自定义 HTTPConnection 子类)

def _get_keepalive_params() -> Tuple[int, int, int]:
    """读取环境变量，返回 (idle_sec, intvl_sec, cnt)"""
    idle = int(os.getenv("CH_EXPORT_TCP_KEEPIDLE", "30"))
    intvl = int(os.getenv("CH_EXPORT_TCP_KEEPINTVL", "10"))
    cnt = int(os.getenv("CH_EXPORT_TCP_KEEPCNT", "6"))
    return idle, intvl, cnt


def _apply_win_keepalive(sock) -> None:
    """
    Windows 专用:在 connect() 完成后,用 SIO_KEEPALIVE_VALS ioctl 设置 keepalive 间隔。
    Windows 不支持 setsockopt(TCP_KEEPIDLE/...),只能走 WSAIoctl。
    单位:毫秒;重试次数固定 10 次,无法通过 ioctl 调整。
    失败仅记 warn,不影响连接建立(降级为系统默认 ~2 小时心跳)。
    """
    idle, intvl, _ = _get_keepalive_params()
    try:
        sock.ioctl(
            socket.SIO_KEEPALIVE_VALS,
            (1, idle * 1000, intvl * 1000),
        )
    except Exception as exc:
        logger.warning(
            "Windows SIO_KEEPALIVE_VALS ioctl 失败(连接仍可用,但心跳间隔将"
            "回退到系统默认 ~2 小时): %s", exc,
        )


class _WinKeepAliveHTTPConnection(HTTPConnection):
    """Windows 专用 HTTPConnection:connect() 后用 ioctl 设置 keepalive 心跳"""

    def connect(self):
        super().connect()
        if self.sock is not None:
            _apply_win_keepalive(self.sock)


class _WinKeepAliveHTTPSConnection(HTTPSConnection):
    """同上,HTTPS 变体"""

    def connect(self):
        super().connect()
        if self.sock is not None:
            _apply_win_keepalive(self.sock)


class _WinKeepAliveHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _WinKeepAliveHTTPConnection


class _WinKeepAliveHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _WinKeepAliveHTTPSConnection


class _TCPKeepAliveAdapter(HTTPAdapter):
    """
    带 OS 层 TCP keepalive 的 HTTPAdapter，防止长连接因空闲被中间链路切断。

    Linux/macOS:走 socket_options(setsockopt) 在 connect 前设置参数。
    Windows:setsockopt 拿不到 TCP_KEEPIDLE 等常量,改注册自定义 ConnectionPool 类,
            在 HTTPConnection.connect() 完成后用 SIO_KEEPALIVE_VALS ioctl 设置。
    """

    def init_poolmanager(self, *args, **kwargs):
        idle, intvl, cnt = _get_keepalive_params()

        socket_options = [
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        ]
        # Linux / macOS 平台的精细 keepalive 控制
        for opt_name, val in (
            ("TCP_KEEPIDLE", idle),
            ("TCP_KEEPINTVL", intvl),
            ("TCP_KEEPCNT", cnt),
        ):
            opt = getattr(socket, opt_name, None)
            if opt is not None:
                socket_options.append((socket.IPPROTO_TCP, opt, val))

        kwargs["socket_options"] = socket_options
        super().init_poolmanager(*args, **kwargs)

        # Windows:setsockopt 拿不到 TCP_KEEPIDLE 等常量,改在 connect 后用 ioctl
        if sys.platform == "win32":
            self.poolmanager.pool_classes_by_scheme = {
                "http": _WinKeepAliveHTTPConnectionPool,
                "https": _WinKeepAliveHTTPSConnectionPool,
            }
            logger.info(
                "TCP keepalive: win32 平台使用 SIO_KEEPALIVE_VALS ioctl "
                "(idle=%ds, intvl=%ds; KEEPCNT 在 Windows 上由系统固定为 10 次)",
                idle, intvl,
            )


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


def is_transient_stream_error(exc: Exception, _seen: Optional[set] = None) -> bool:
    """
    判断是否为「流式响应中途断开」类瞬时错误，可通过 LIMIT/OFFSET 回退恢复。

    触发场景：
      - 跨境网络抖动切断长连接
      - ClickHouse 服务端 OOM/abort 主动关闭流
      - 中间代理/LB 的空闲连接超时
      - urllib3.exceptions.ProtocolError / requests.exceptions.ChunkedEncodingError
      - http.client.IncompleteRead

    递归检查 `__cause__` / `__context__`（v2.14 修复:`_run_single_export` 把
    count_rows 异常包装为 RuntimeError 时,raise ... from cnt_err 保留了 chain;
    本函数沿 chain 探测以识别底层 transient 性,使外层 retry 正常触发）。

    回退策略：每个 LIMIT/OFFSET 窗口或 keyset 窗口是独立 HTTP 请求,
    短小、独立连接,重试时大概率成功。
    """
    # 防止异常链环路无限递归(罕见但可能)
    if _seen is None:
        _seen = set()
    if id(exc) in _seen:
        return False
    _seen.add(id(exc))

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
    if any(fp in msg for fp in fingerprints):
        return True

    # v2.14:沿异常链探测包装层底下的原始 transient 错误
    for chained in (getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        if chained is not None and is_transient_stream_error(chained, _seen):
            return True
    return False


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

    def stream_batches_keyset(
        self,
        sql: str,
        cursor_column: str,
        batch_size: int = 50_000,
        extra_settings: Optional[Dict[str, Any]] = None,
    ) -> Generator[List[Tuple], None, None]:
        """
        键集分页（keyset pagination / cursor pagination）替代 LIMIT/OFFSET。

        每个窗口是一个独立 HTTP 请求,WHERE 子句用上一窗口尾行的 cursor 值推进:
            首次:  SELECT * FROM (sql) AS _q ORDER BY {cursor} LIMIT N
            后续:  SELECT * FROM (sql) AS _q
                     WHERE {cursor} > {last_value:String}
                     ORDER BY {cursor} LIMIT N

        相对 LIMIT/OFFSET 的优势:
          - **正确性**: ClickHouse 并行扫描下,LIMIT N OFFSET M 在无 ORDER BY 时
            两个窗口可能返回部分重叠/漏行;keyset 用 ORDER BY + WHERE 推进确保
            互斥连续。
          - **性能**: LIMIT/OFFSET 每窗口都扫 M+N 行,后期等于全表扫;keyset 每
            窗口从 cursor > last 起扫,与窗口位置无关。亿级行后期窗口可数量级加速。

        约束:
          - 用户 SQL 不能含影响 ORDER BY 的 GROUP BY/DISTINCT 等聚合;否则结果
            行不在原表行集合中,cursor 推进语义无效。
          - cursor 列需单调可排序;tie 值(相同 cursor)的若干行会被 strict `>` 跳过。
            为完全无丢失,用户应选「主键列 + 不可重复」;时间戳列存在并发同微秒插入
            时可能丢极少数行(可接受 trade-off,或换主键列)。

        ClickHouse HTTP 参数化语法:
          - URL 参数 param_cursor_val=<value> + SQL 内 {cursor_val:String}
          - String 类型 + 隐式转换,兼容数字/日期/时间戳列
          - 避免手动转义,杜绝 SQL 注入

        参数:
          cursor_column — 已在调用前由 chunker 通过 `_IDENT_RE` 校验,可直接拼接
          batch_size    — 每窗口最大行数(LIMIT 值)
          extra_settings — 同 stream_batches.extra_settings
        """
        # 防御:cursor_column 仍校验一次(防被绕过)。
        # 策略与 chunker 一致:strip 反引号 + 允许字母/数字/下划线/空格/中文,
        # 拼接 SQL 时统一反引号包裹。
        import re
        cursor_column = cursor_column.strip().strip("`").strip()
        if not re.match(r"^[A-Za-z_一-鿿][A-Za-z0-9_ 一-鿿]*$", cursor_column):
            raise ValueError(
                f"cursor_column 含非法字符(允许:字母/数字/下划线/空格/中文): {cursor_column!r}"
            )
        # 反引号包裹版本用于 SQL 拼接(防空格/中文列名解析失败);
        # 裸字符串版本用于 col_names.index() 查找(TSV header 返回的列名不含反引号)
        cursor_quoted = f"`{cursor_column}`"

        stripped = sql.rstrip().rstrip(";")
        last_cursor: Optional[str] = None
        window_idx = 0

        while True:
            if last_cursor is None:
                window_sql = (
                    f"SELECT * FROM ({stripped}) AS _ks_q"
                    f" ORDER BY {cursor_quoted}"
                    f" LIMIT {batch_size}"
                )
                extra_params: Dict[str, str] = {}
            else:
                window_sql = (
                    f"SELECT * FROM ({stripped}) AS _ks_q"
                    f" WHERE {cursor_quoted} > {{cursor_val:String}}"
                    f" ORDER BY {cursor_quoted}"
                    f" LIMIT {batch_size}"
                )
                extra_params = {"param_cursor_val": last_cursor}

            logger.debug(
                "KeysetExtract window=%d cursor=%s",
                window_idx, last_cursor,
            )

            # 找 cursor 列在结果中的索引(每个窗口都重新探测,因为 SELECT * 顺序由 CH 决定)
            # 实现:发一个独立 HTTP 请求(用 _request_keyset_window 助手),
            #       同时拿到列名 + 数据
            rows_in_window = []
            cursor_col_idx: Optional[int] = None

            for batch_rows, col_names in self._iter_keyset_window(
                window_sql, batch_size=batch_size,
                extra_settings=extra_settings,
                extra_url_params=extra_params,
            ):
                if cursor_col_idx is None and col_names is not None:
                    try:
                        cursor_col_idx = col_names.index(cursor_column)
                    except ValueError:
                        raise RuntimeError(
                            f"cursor_column {cursor_column!r} 未在 SELECT * 结果列"
                            f"({col_names!r})中找到 — 用户 SQL 可能 SELECT 了子集"
                            f"或聚合改名,无法用作 keyset"
                        )
                rows_in_window.extend(batch_rows)
                yield batch_rows

            if not rows_in_window:
                # 本窗口空 → 数据已耗尽,正常终止
                return

            # 校验:本窗口最大 cursor 不能 == 上轮(死循环)。
            # 注意:严格 `>` 比较会误伤,因为 cursor 通过 TSV 字符串返回,
            # 数值 "50" / "100" 字典序与数值序相反。我们只检测「完全相等」死循环,
            # 让 ClickHouse 自己用 WHERE cursor > last_value 保证推进。
            # cursor 列含 NULL 或排序非确定的责任由用户承担(文档警告)。
            new_cursor = rows_in_window[-1][cursor_col_idx] if cursor_col_idx is not None else None
            if new_cursor is None:
                raise RuntimeError("keyset cursor 解析失败:末行 cursor 列值为 None")
            if last_cursor is not None and str(new_cursor) == str(last_cursor):
                raise RuntimeError(
                    f"keyset cursor 死循环(last==new=={new_cursor!r})"
                    f" — cursor_column {cursor_column!r} 可能含重复值,"
                    f"请改用主键列或时间戳+ID 复合列"
                )
            last_cursor = str(new_cursor)
            window_idx += 1

    def _iter_keyset_window(
        self,
        sql: str,
        batch_size: int = 50_000,
        extra_settings: Optional[Dict[str, Any]] = None,
        extra_url_params: Optional[Dict[str, str]] = None,
    ) -> Generator[Tuple[List[Tuple], Optional[List[str]]], None, None]:
        """
        发一次 HTTP 请求,逐批 yield (rows, col_names)。
        col_names 仅在第一批 yield 时非 None(用于上层定位 cursor 列索引)。
        本方法是 stream_batches 的变体:同样的流式 TSV 解析,但额外回传列名。
        """
        stream_sql = sql.rstrip().rstrip(";") + " FORMAT TabSeparatedWithNamesAndTypes"
        params = self._base_params()
        params.update(_build_default_streaming_settings())
        if extra_settings:
            params.update(extra_settings)
        if extra_url_params:
            params.update(extra_url_params)

        try:
            resp = _get_export_session().post(
                self._base_url,
                data=stream_sql.encode("utf-8"),
                params=params,
                timeout=self.timeout,
                stream=True,
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
        col_names: Optional[List[str]] = None
        try:
            names_line = next(line_iter)
            next(line_iter)  # 列类型行(丢弃)
            col_names = names_line.split("\t")
        except StopIteration:
            return

        batch: List[Tuple] = []
        emitted_first = False
        for line in line_iter:
            if line == "":
                continue
            cells = line.split("\t")
            row = tuple(_parse_tsv_cell(c) for c in cells)
            batch.append(row)
            if len(batch) >= batch_size:
                yield (batch, col_names if not emitted_first else None)
                emitted_first = True
                batch = []

        if batch:
            yield (batch, col_names if not emitted_first else None)
