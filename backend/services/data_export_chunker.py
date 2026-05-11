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
from typing import List, Literal, Optional, Tuple


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


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

InjectMode = Literal["placeholder", "wrapper"]


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

    返回：
      - days == 1：返回 [(start, end)]（不可再分，单天块）
      - days == 2：返回 [(start, start), (end, end)]
      - days > 2：返回 [(start, mid), (mid+1, end)]，前半向下取整

    示例：
      subdivide_date_range(2026-01-01, 2026-01-05) → [(01-01, 01-02), (01-03, 01-05)]
      subdivide_date_range(2026-01-01, 2026-01-02) → [(01-01, 01-01), (01-02, 01-02)]
      subdivide_date_range(2026-01-01, 2026-01-01) → [(01-01, 01-01)]  # 单天，无法再分

    Raises:
        ValueError: start > end
    """
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start 和 end 必须是 date 类型")
    if start > end:
        raise ValueError(f"起始日期 {start} 不能晚于结束日期 {end}")

    days = (end - start).days + 1
    if days == 1:
        return [(start, end)]

    half = days // 2
    mid = start + timedelta(days=half - 1)
    return [(start, mid), (mid + timedelta(days=1), end)]


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


def inject_date_filter(
    sql: str,
    date_column: Optional[str],
    chunk_start: date,
    chunk_end: date,
) -> Tuple[str, InjectMode]:
    """
    把日期过滤注入用户 SQL，返回 (注入后 SQL, 注入模式)。

    判定顺序：
      1. SQL 含 {{date_start}} 与 {{date_end}} → 替换为字面量字符串（placeholder 模式）
      2. 否则要求 date_column 非空 → 包装子查询并加 BETWEEN（wrapper 模式）

    chunk_start / chunk_end 序列化为 ISO 'YYYY-MM-DD'。
    ClickHouse 可隐式把字符串转为 Date 类型（与 String/DateTime 列也兼容）。

    Raises:
        ValueError: 既无占位符又无 date_column；或 date_column 含非法字符
    """
    if has_placeholders(sql):
        # 占位符模式：直接替换字符串字面量（带单引号）
        s = sql.replace(_PLACEHOLDER_START, chunk_start.isoformat())
        s = s.replace(_PLACEHOLDER_END, chunk_end.isoformat())
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
        f" WHERE _chunk_q.{date_column} >= '{chunk_start.isoformat()}'"
        f" AND _chunk_q.{date_column} <= '{chunk_end.isoformat()}'"
    )
    return wrapped, "wrapper"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 文件名构造
# ─────────────────────────────────────────────────────────────────────────────

def _safe_segment(s: str) -> str:
    """把任意字符串转为文件名安全段（替换非法字符为下划线，去除首尾下划线）"""
    cleaned = _FILENAME_UNSAFE_RE.sub("_", s).strip("_")
    return cleaned or "export"


def build_chunk_filename(
    job_name: str,
    chunk_start: date,
    chunk_end: date,
    extension: str = "xlsx",
) -> str:
    """
    生成单个分块文件名：
      {safe_job_name}_{YYYYMMDD}_to_{YYYYMMDD}.{ext}

    job_name 安全过滤后截断到 _MAX_JOB_NAME_LEN 字符；空则用 'export'。
    """
    base = _safe_segment(job_name)[:_MAX_JOB_NAME_LEN] if job_name else "export"
    s = chunk_start.strftime("%Y%m%d")
    e = chunk_end.strftime("%Y%m%d")
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


def validate_chunk_config(raw: dict, sql: str) -> NormalizedChunkConfig:
    """
    校验前端传入的 chunk_config dict，并结合 SQL 决定注入模式。

    Args:
        raw: {date_column?, date_start, date_end, chunk_days}
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

    mode: InjectMode = "placeholder" if use_placeholder else "wrapper"

    return NormalizedChunkConfig(
        date_column=date_column,
        date_start=date_start,
        date_end=date_end,
        chunk_days=chunk_days,
        mode=mode,
    )
