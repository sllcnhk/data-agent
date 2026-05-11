"""
按日期分块导出 — 纯逻辑工具

设计目标：
  - 无 IO、无副作用、无外部依赖（仅标准库）
  - 单测覆盖率 100%
  - 与 data_export_service 解耦，便于复用与重构

核心能力：
  1. split_date_range          — 把闭区间日期切成 chunk_days 长度的子区间
  2. inject_date_filter        — 把日期过滤注入用户 SQL（占位符 / 包装子查询双模式）
  3. build_chunk_filename      — 生成单块输出文件名（安全、可读）
  4. validate_chunk_config     — 入参校验（抛 ValueError）

日期注入双模式说明：
  - 占位符模式（推荐）：用户 SQL 含 {{date_start}} / {{date_end}} → 字符串替换
        优点：谓词由 ClickHouse 直接下推到源表，性能最佳
        要求：用户须自行写 WHERE date_col >= '{{date_start}}' AND date_col <= '{{date_end}}'
  - 包装子查询模式（兜底）：用户 SQL 不含占位符 → 包装为
        SELECT * FROM (user_sql) AS _t WHERE _t.{date_column} BETWEEN '...' AND '...'
        优点：用户无需修改 SQL；缺点：复杂查询（GROUP BY/JOIN）无法谓词下推
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Literal, Optional, Tuple, Union


# 占位符（大小写敏感，仅匹配标准形式）
_PLACEHOLDER_START = "{{date_start}}"
_PLACEHOLDER_END = "{{date_end}}"

# 文件名安全字符（保留中文、字母数字、连字符、下划线）
_FILENAME_UNSAFE_RE = re.compile(r"[^\w\-一-鿿]+", re.UNICODE)

# 标识符（列名）安全字符 — ClickHouse 标识符规则：[a-zA-Z_][a-zA-Z0-9_]*
# 严格白名单防 SQL 注入
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# chunk_days 取值上下限（与 UI 约束一致）
MIN_CHUNK_DAYS = 1
MAX_CHUNK_DAYS = 90

# 文件名 job_name 部分最大长度
_MAX_JOB_NAME_LEN = 50

# 子块再细分最小粒度的合法值（用户 opt-in）
SubdivideUnit = Literal["day", "hour", "minute"]
_VALID_SUBDIVIDE_UNITS = ("day", "hour", "minute")

# 不同 min_subdivide_unit 对应的「最小可拆分窗口」（秒）
# 拆出来的两半各 < 阈值时,停止再分（保护对 Date 列的无效细分循环）
_UNIT_FLOOR_SECONDS = {
    "day": 86400,    # 1 day - 由 days_in_chunk > 1 判断,这里其实不参与 sub-day
    "hour": 3600,    # 1 hour
    "minute": 60,    # 1 minute
}


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

InjectMode = Literal["placeholder", "wrapper"]

# date 或 datetime；endpoint 类型在分裂中可能从 date 升级到 datetime
RangeEndpoint = Union[date, datetime]


def _is_pure_date(v: RangeEndpoint) -> bool:
    """判断是否「纯 date」（非 datetime；isinstance 检查需注意 datetime 是 date 子类）"""
    return isinstance(v, date) and not isinstance(v, datetime)


@dataclass(frozen=True)
class DateChunk:
    """单个日期切片（闭区间 [start, end]）"""
    index: int
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


# ─────────────────────────────────────────────────────────────────────────────
# 1. 日期切分
# ─────────────────────────────────────────────────────────────────────────────

def subdivide_date_range(start: date, end: date) -> List[Tuple[date, date]]:
    """
    把闭区间 [start, end] 对半分裂为最多 2 个子区间。
    用于「自动子块分裂」：当某块流式断开时，把失败块的日期范围一分为二递归重试。

    本函数等价于 `subdivide_range(start, end, min_unit="day")`,保留作为薄包装
    保证现有调用方零改动。仅处理 date 类型,不下钻到 sub-day。

    返回：
      - days == 1：返回 [(start, end)]（不可再分，单天块）
      - days == 2：返回 [(start, start), (end, end)]
      - days > 2：返回 [(start, mid), (mid+1, end)]，前半向下取整

    Raises:
        ValueError: start/end 非 date 类型；start > end
    """
    if not _is_pure_date(start) or not _is_pure_date(end):
        raise ValueError("start 和 end 必须是 date 类型（非 datetime）")
    result = subdivide_range(start, end, min_unit="day")
    # 类型收窄：min_unit=day 下不会返回 datetime
    return [(s, e) for s, e in result]  # type: ignore[misc]


def subdivide_range(
    start: RangeEndpoint,
    end: RangeEndpoint,
    min_unit: SubdivideUnit = "day",
) -> List[Tuple[RangeEndpoint, RangeEndpoint]]:
    """
    通用闭区间对半分裂,支持 date 与 datetime 端点 + 用户控制最小细分粒度。

    端点类型规则:
      - 两端均为 date：按整天对半（旧 subdivide_date_range 行为）
        · days > 1:  普通对半
        · days == 1: 若 min_unit ∈ {hour, minute} → 提升为 datetime
                     [start 00:00:00, start 23:59:59] 后再二分；否则不可再分
      - 两端均为 datetime:按时间对半
        · 拆出来的两半各 ≥ _UNIT_FLOOR_SECONDS[min_unit] 时才拆,否则不可再分
      - 类型混合（一端 date 一端 datetime）：拒绝

    返回:
      - 不可再分 → [(start, end)]
      - 可拆 → [(start, mid), (mid_next, end)] 共 2 项

    Raises:
        ValueError: 类型错误；start > end；min_unit 非法
    """
    if min_unit not in _VALID_SUBDIVIDE_UNITS:
        raise ValueError(
            f"min_unit 必须是 {_VALID_SUBDIVIDE_UNITS} 之一,收到 {min_unit!r}"
        )

    s_is_dt = isinstance(start, datetime)
    e_is_dt = isinstance(end, datetime)
    s_is_date = _is_pure_date(start)
    e_is_date = _is_pure_date(end)

    if (s_is_dt and e_is_date) or (s_is_date and e_is_dt):
        raise ValueError(
            "start/end 类型必须一致（同为 date 或同为 datetime）"
        )

    if not (s_is_dt or s_is_date) or not (e_is_dt or e_is_date):
        raise ValueError("start/end 必须是 date 或 datetime 类型")

    if start > end:
        raise ValueError(f"起始 {start} 不能晚于结束 {end}")

    # 分支 1：两端 date —— 按天对半
    if s_is_date and e_is_date:
        days = (end - start).days + 1
        if days > 1:
            half = days // 2
            mid = start + timedelta(days=half - 1)
            return [(start, mid), (mid + timedelta(days=1), end)]
        # days == 1
        if min_unit == "day":
            return [(start, end)]
        # 升级到 datetime 后再分
        s_dt = datetime.combine(start, datetime.min.time())  # 00:00:00
        e_dt = datetime.combine(end, datetime.max.time().replace(microsecond=0))  # 23:59:59
        return subdivide_range(s_dt, e_dt, min_unit=min_unit)

    # 分支 2：两端 datetime —— 按时间对半,但拆出两半须各 ≥ floor
    total_seconds = (end - start).total_seconds() + 1  # 闭区间含两端,粗略 +1 秒
    floor = _UNIT_FLOOR_SECONDS[min_unit]
    # 拆完后两半的最小那一份 ≈ total_seconds // 2,若 < floor 则不再拆
    if total_seconds < 2 * floor:
        return [(start, end)]

    half_seconds = total_seconds // 2
    # 取整到「秒」避免 microsecond 噪音渗入文件名
    mid = start + timedelta(seconds=int(half_seconds) - 1)
    next_start = mid + timedelta(seconds=1)
    return [(start, mid), (next_start, end)]


def split_date_range(
    start: date,
    end: date,
    chunk_days: int,
) -> List[DateChunk]:
    """
    把闭区间 [start, end] 按 chunk_days 切成连续不重叠的子区间。
    最后一块自适应缩短（不超出 end）。

    示例：
        split_date_range(date(2025,4,1), date(2025,4,30), 10)
        → [DateChunk(0, 04-01, 04-10), DateChunk(1, 04-11, 04-20), DateChunk(2, 04-21, 04-30)]

        split_date_range(date(2025,4,1), date(2025,4,25), 10)
        → [DateChunk(0, 04-01, 04-10), DateChunk(1, 04-11, 04-20), DateChunk(2, 04-21, 04-25)]

    Raises:
        ValueError: start > end 或 chunk_days < 1 或 chunk_days > MAX_CHUNK_DAYS
    """
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start 和 end 必须是 date 类型")
    if start > end:
        raise ValueError(f"起始日期 {start} 不能晚于结束日期 {end}")
    if chunk_days < MIN_CHUNK_DAYS:
        raise ValueError(f"chunk_days 须 >= {MIN_CHUNK_DAYS}（当前 {chunk_days}）")
    if chunk_days > MAX_CHUNK_DAYS:
        raise ValueError(f"chunk_days 须 <= {MAX_CHUNK_DAYS}（当前 {chunk_days}）")

    chunks: List[DateChunk] = []
    cur = start
    idx = 0
    delta = timedelta(days=chunk_days - 1)  # 每块跨度 chunk_days 天，含起始日

    while cur <= end:
        chunk_end = cur + delta
        if chunk_end > end:
            chunk_end = end
        chunks.append(DateChunk(index=idx, start=cur, end=chunk_end))
        cur = chunk_end + timedelta(days=1)
        idx += 1

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# 2. 日期过滤注入
# ─────────────────────────────────────────────────────────────────────────────

def has_placeholders(sql: str) -> bool:
    """检测 SQL 中是否含有 {{date_start}} 与 {{date_end}} 双占位符"""
    return _PLACEHOLDER_START in sql and _PLACEHOLDER_END in sql


def has_partial_placeholders(sql: str) -> bool:
    """
    检测 SQL 中是否仅含一个占位符（XOR）。
    单占位符是危险状态：若用户提供 date_column 走 wrapper 路径，未替换的占位符
    字面量会被原样送给 ClickHouse 触发语法错误。校验层须在创建 Job 前拒绝。
    """
    has_start = _PLACEHOLDER_START in sql
    has_end = _PLACEHOLDER_END in sql
    return has_start != has_end


def _format_range_literal(v: RangeEndpoint) -> str:
    """把 date 或 datetime 端点序列化为 ClickHouse SQL 字面量值（不含外层引号）。
    date  → 'YYYY-MM-DD'
    datetime → 'YYYY-MM-DD HH:MM:SS'（截到秒,丢弃微秒,与 ClickHouse DateTime 类型对齐）"""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if _is_pure_date(v):
        return v.isoformat()
    raise ValueError(f"不支持的端点类型: {type(v).__name__}")


def inject_date_filter(
    sql: str,
    date_column: Optional[str],
    chunk_start: RangeEndpoint,
    chunk_end: RangeEndpoint,
) -> Tuple[str, InjectMode]:
    """
    把日期/时间过滤注入用户 SQL，返回 (注入后 SQL, 注入模式)。

    判定顺序：
      1. SQL 含 {{date_start}} 与 {{date_end}} → 替换为字面量字符串（placeholder 模式）
      2. 否则要求 date_column 非空 → 包装子查询并加 BETWEEN（wrapper 模式）

    端点序列化：
      - date   → 'YYYY-MM-DD'
      - datetime → 'YYYY-MM-DD HH:MM:SS'（用于 sub-day 再细分）
    ClickHouse 可隐式把字符串转为 Date/DateTime,使两种字面量与各自列类型兼容。

    Raises:
        ValueError: start/end 类型不一致；既无占位符又无 date_column；
                    date_column 含非法字符
    """
    s_is_dt = isinstance(chunk_start, datetime)
    e_is_dt = isinstance(chunk_end, datetime)
    if s_is_dt != e_is_dt:
        raise ValueError("chunk_start 和 chunk_end 类型必须一致（同为 date 或同为 datetime）")

    start_lit = _format_range_literal(chunk_start)
    end_lit = _format_range_literal(chunk_end)

    if has_placeholders(sql):
        # 占位符模式：直接替换字符串字面量（带单引号）
        s = sql.replace(_PLACEHOLDER_START, start_lit)
        s = s.replace(_PLACEHOLDER_END, end_lit)
        return s, "placeholder"

    # 包装模式
    if not date_column:
        raise ValueError(
            "SQL 不含 {{date_start}}/{{date_end}} 占位符时，必须提供 date_column"
        )
    if not _IDENT_RE.match(date_column):
        raise ValueError(
            f"date_column 含非法字符（仅允许字母/数字/下划线，须以字母或下划线起首）: {date_column!r}"
        )

    stripped = sql.rstrip().rstrip(";")
    wrapped = (
        f"SELECT * FROM ({stripped}) AS _chunk_q"
        f" WHERE _chunk_q.{date_column} >= '{start_lit}'"
        f" AND _chunk_q.{date_column} <= '{end_lit}'"
    )
    return wrapped, "wrapper"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 文件名构造
# ─────────────────────────────────────────────────────────────────────────────

def _safe_segment(s: str) -> str:
    """把任意字符串转为文件名安全段（替换非法字符为下划线，去除首尾下划线）"""
    cleaned = _FILENAME_UNSAFE_RE.sub("_", s).strip("_")
    return cleaned or "export"


def _filename_timestamp(v: RangeEndpoint) -> str:
    """端点序列化为文件名片段:
       date     → YYYYMMDD
       datetime → YYYYMMDDTHHMMSS（T 分隔避免破坏可读性,文件系统全平台安全）"""
    if isinstance(v, datetime):
        return v.strftime("%Y%m%dT%H%M%S")
    if _is_pure_date(v):
        return v.strftime("%Y%m%d")
    raise ValueError(f"不支持的端点类型: {type(v).__name__}")


def build_chunk_filename(
    job_name: str,
    chunk_start: RangeEndpoint,
    chunk_end: RangeEndpoint,
    extension: str = "xlsx",
) -> str:
    """
    生成单个分块文件名：
      date 端点:     {safe_job_name}_{YYYYMMDD}_to_{YYYYMMDD}.{ext}
      datetime 端点: {safe_job_name}_{YYYYMMDDTHHMMSS}_to_{YYYYMMDDTHHMMSS}.{ext}

    job_name 安全过滤后截断到 _MAX_JOB_NAME_LEN 字符；空则用 'export'。
    """
    base = _safe_segment(job_name)[:_MAX_JOB_NAME_LEN] if job_name else "export"
    s = _filename_timestamp(chunk_start)
    e = _filename_timestamp(chunk_end)
    return f"{base}_{s}_to_{e}.{extension}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. 配置校验
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iso_date(value) -> date:
    """把 'YYYY-MM-DD' 字符串或 date 实例转为 date；其他类型/格式抛 ValueError"""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"日期格式必须为 'YYYY-MM-DD'，收到 {value!r}") from exc
    raise ValueError(f"日期类型不受支持: {type(value).__name__}")


@dataclass(frozen=True)
class NormalizedChunkConfig:
    """校验后的标准化 chunk 配置"""
    date_column: Optional[str]
    date_start: date
    date_end: date
    chunk_days: int
    mode: InjectMode  # 根据 SQL 自动决定
    min_subdivide_unit: SubdivideUnit = "day"  # 用户 opt-in:sub-day 再细分粒度
    cursor_column: Optional[str] = None        # 用户 opt-in:键集分页游标列（Task B 启用）


def validate_chunk_config(raw: dict, sql: str) -> NormalizedChunkConfig:
    """
    校验前端传入的 chunk_config dict，并结合 SQL 决定注入模式。

    Args:
        raw: {date_column?, date_start, date_end, chunk_days, min_subdivide_unit?, cursor_column?}
        sql: 用户 SQL，用于探测占位符

    Returns:
        NormalizedChunkConfig

    Raises:
        ValueError: 字段缺失/类型错误/范围错误/SQL 既无占位符也无 date_column
    """
    if not isinstance(raw, dict):
        raise ValueError("chunk_config 必须是对象")

    # 防御：单占位符 SQL — 包装路径会把未替换的占位符送给 ClickHouse 触发语法错
    if has_partial_placeholders(sql):
        raise ValueError(
            "SQL 中 {{date_start}} 与 {{date_end}} 必须成对出现；"
            "仅写一个会导致包装路径下未替换的占位符被送往 ClickHouse 触发语法错误"
        )

    date_start = _parse_iso_date(raw.get("date_start"))
    date_end = _parse_iso_date(raw.get("date_end"))

    if date_start > date_end:
        raise ValueError(f"date_start ({date_start}) 不能晚于 date_end ({date_end})")

    chunk_days = raw.get("chunk_days", 10)
    if not isinstance(chunk_days, int) or isinstance(chunk_days, bool):
        raise ValueError(f"chunk_days 必须是整数（收到 {type(chunk_days).__name__}）")
    if chunk_days < MIN_CHUNK_DAYS or chunk_days > MAX_CHUNK_DAYS:
        raise ValueError(
            f"chunk_days 须在 [{MIN_CHUNK_DAYS}, {MAX_CHUNK_DAYS}] 区间内（收到 {chunk_days}）"
        )

    date_column = raw.get("date_column") or None
    if date_column is not None:
        if not isinstance(date_column, str):
            raise ValueError("date_column 必须是字符串")
        date_column = date_column.strip() or None

    use_placeholder = has_placeholders(sql)
    if not use_placeholder and not date_column:
        raise ValueError(
            "SQL 不含 {{date_start}}/{{date_end}} 占位符时必须提供 date_column"
        )
    if date_column is not None and not _IDENT_RE.match(date_column):
        raise ValueError(
            f"date_column 含非法字符（仅允许字母/数字/下划线）: {date_column!r}"
        )

    # min_subdivide_unit:用户 opt-in,默认 "day"(老行为)
    min_subdivide_unit = raw.get("min_subdivide_unit", "day") or "day"
    if min_subdivide_unit not in _VALID_SUBDIVIDE_UNITS:
        raise ValueError(
            f"min_subdivide_unit 必须是 {_VALID_SUBDIVIDE_UNITS} 之一,收到 {min_subdivide_unit!r}"
        )

    # cursor_column:用户 opt-in,默认 None;非空时同 date_column 一样走标识符白名单
    cursor_column = raw.get("cursor_column") or None
    if cursor_column is not None:
        if not isinstance(cursor_column, str):
            raise ValueError("cursor_column 必须是字符串")
        cursor_column = cursor_column.strip() or None
    if cursor_column is not None and not _IDENT_RE.match(cursor_column):
        raise ValueError(
            f"cursor_column 含非法字符（仅允许字母/数字/下划线）: {cursor_column!r}"
        )

    mode: InjectMode = "placeholder" if use_placeholder else "wrapper"

    return NormalizedChunkConfig(
        date_column=date_column,
        date_start=date_start,
        date_end=date_end,
        chunk_days=chunk_days,
        mode=mode,
        min_subdivide_unit=min_subdivide_unit,
        cursor_column=cursor_column,
    )
