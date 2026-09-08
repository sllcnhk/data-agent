"""
Excel 导入原子性实测 —— 针对 THAI 环境真实 ClickHouse

背景
────
data-agent 的 Excel 导入按批（每批 N 行）通过 HTTP 往 ClickHouse 发
`INSERT ... FORMAT TabSeparated`（见 backend/services/data_import_service.py:536
调用 backend/mcp/clickhouse/http_client.py:200 的 insert_tsv）。

我们要给导入加「遇到 ClickHouse Code 202 (TOO_MANY_SIMULTANEOUS_QUERIES)
时重发同一批」的重试能力。重试安全的前提是：
    ★ 一条 INSERT 语句失败时，这一批数据 0 行入库（全有或全无）★
否则重试会产生重复数据。本文件用真实 ClickHouse 实测证明（或推翻）这个前提。

⚠️ 这是集成测试，需要真连 THAI 生产 ClickHouse，不能离线跑。
   所有用例带 `integration` 标记（本项目没有 pytest.ini / setup.cfg /
   pyproject.toml，marker 无处注册，故用模块级 pytestmark；运行时会出现
   `PytestUnknownMarkWarning`，属预期现象 —— 按要求不修改项目配置文件）。

运行方式（必须走 pytest，否则根 conftest.py 的清理钩子不触发）：
    d:/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest \
        test_import_atomicity_thai.py -v -s

安全约定
────────
- 连接信息只从项目根 `.env` 读取（CLICKHOUSE_THAI_*），不新建配置文件。
- 本文件自己发出的校验查询（CREATE / SELECT count / DROP）一律用
  HTTP header `X-ClickHouse-User` / `X-ClickHouse-Key` 传凭据，
  密码不进 URL、不进日志、不进异常消息。
- 被测对象 ClickHouseHTTPClient 目前把 user/password 放在 URL params 里，
  这是既有代码，本测试不修改它；但所有打印/断言消息都经 `_redact()`
  过滤，确保密码不会因异常文本外泄。
- 临时表名 zz_test_atomicity_<unix_ts>，建在 default 库，
  fixture 的 finally 阶段无条件 DROP。
"""
import os
import random
import re
import string
import time
from pathlib import Path

import pytest
import requests

from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient

# 集成测试标记：需要真实 ClickHouse 连接
# （marker 未在项目配置中注册 —— 见模块 docstring 说明，不改项目配置）
pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────────
# .env 读取 + 凭据脱敏
# ─────────────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent


def _load_thai_config():
    """从项目根 .env 读取 THAI 连接配置（不写入 os.environ）。"""
    env_path = _ROOT / ".env"
    if not env_path.is_file():
        pytest.skip(f".env not found at {env_path}")

    values = {}
    with env_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key.startswith("CLICKHOUSE_THAI_"):
                values[key] = val

    required = [
        "CLICKHOUSE_THAI_HOST",
        "CLICKHOUSE_THAI_HTTP_PORT",
        "CLICKHOUSE_THAI_USER",
        "CLICKHOUSE_THAI_PASSWORD",
    ]
    missing = [k for k in required if not values.get(k)]
    if missing:
        pytest.skip(f"THAI ClickHouse config incomplete in .env: {missing}")

    return {
        "host": values["CLICKHOUSE_THAI_HOST"],
        "http_port": int(values["CLICKHOUSE_THAI_HTTP_PORT"]),
        "user": values["CLICKHOUSE_THAI_USER"],
        "password": values["CLICKHOUSE_THAI_PASSWORD"],
        # 临时表统一建在 default 库（已验证 wizadmin 有 CREATE/INSERT/DROP 权限）
        "database": "default",
        "env_database": values.get("CLICKHOUSE_THAI_DATABASE", ""),
    }


_CFG = None


def _cfg():
    global _CFG
    if _CFG is None:
        _CFG = _load_thai_config()
    return _CFG


def _redact(text) -> str:
    """
    从任意待打印/待断言的文本里剔除密码与 URL 中的凭据参数。
    密码绝不出现在日志、异常消息或任何产物里。
    """
    s = str(text)
    pwd = _cfg().get("password") or ""
    if pwd:
        s = s.replace(pwd, "***REDACTED***")
    # 兜底：URL query 里的 password=/user= 形式
    s = re.sub(r"(?i)(password=)[^&\s\"']+", r"\1***REDACTED***", s)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# 校验通道：HTTP header 传凭据（CREATE / SELECT count / DROP 走这里）
# ─────────────────────────────────────────────────────────────────────────────

_verify_session = requests.Session()


def _ch_exec(sql: str, timeout: int = 60) -> str:
    """
    执行一条 SQL，凭据走 HTTP header（X-ClickHouse-User / X-ClickHouse-Key）。
    返回响应文本（已脱敏）。非 200 抛 AssertionError（消息也已脱敏）。
    """
    cfg = _cfg()
    url = "http://{host}:{port}/".format(host=cfg["host"], port=cfg["http_port"])
    resp = _verify_session.post(
        url,
        data=sql.encode("utf-8"),
        headers={
            "X-ClickHouse-User": cfg["user"],
            "X-ClickHouse-Key": cfg["password"],
            "X-ClickHouse-Database": cfg["database"],
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise AssertionError(
            "verify-channel SQL failed (HTTP {code}): {body}".format(
                code=resp.status_code, body=_redact(resp.text[:800])
            )
        )
    return resp.text


def _count(table: str) -> int:
    """SELECT count() FROM default.<table>，走 header 校验通道。"""
    raw = _ch_exec(
        "SELECT count() FROM `default`.`{t}` FORMAT JSONCompact".format(t=table)
    )
    import json

    return int(json.loads(raw)["data"][0][0])


def _ascii(text) -> str:
    """
    转成纯 ASCII 后再打印 —— Windows 控制台是 GBK，非 ASCII 会变乱码
    （被测客户端的异常消息里带中文，如「ClickHouse INSERT 失败」）。
    """
    return str(text).encode("ascii", "replace").decode("ascii")


def _extract_ch_code(text: str) -> str:
    """从 ClickHouse 错误文本里抽出 `Code: NNN`。"""
    m = re.search(r"Code:\s*(\d+)", str(text))
    return m.group(1) if m else "<not found>"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ch_client():
    """被测对象：项目自己的 ClickHouseHTTPClient，timeout=60 与导入服务一致。"""
    cfg = _cfg()
    # 连通性预检
    _ch_exec("SELECT 1")
    return ClickHouseHTTPClient(
        host=cfg["host"],
        port=cfg["http_port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        timeout=60,   # 与 backend/services/data_import_service.py:171 一致
    )


@pytest.fixture(scope="module")
def temp_table():
    """
    创建临时表，yield 表名，无论测试结果如何都在 teardown 阶段 DROP。
    表名 zz_test_atomicity_<unix_ts>，建在 THAI 的 default 库。
    """
    table = "zz_test_atomicity_{ts}".format(ts=int(time.time()))
    ddl = (
        "CREATE TABLE `default`.`{t}` ("
        "  id UInt32,"
        "  name String,"
        "  dt Date"
        ") ENGINE = MergeTree ORDER BY id"
    ).format(t=table)

    try:
        _ch_exec(ddl)
        print("\n[setup] created THAI default.{t}".format(t=table))
        yield table
    finally:
        # 断言失败、异常、KeyboardInterrupt 都会走到这里
        try:
            _ch_exec("DROP TABLE IF EXISTS `default`.`{t}`".format(t=table))
            print("\n[teardown] dropped THAI default.{t}".format(t=table))
        except Exception as exc:      # noqa: BLE001 - teardown 不能掩盖测试失败
            print("\n[teardown] !! DROP FAILED for {t}: {e}".format(
                t=table, e=_redact(exc)))


# ─────────────────────────────────────────────────────────────────────────────
# 数据构造
# ─────────────────────────────────────────────────────────────────────────────

_DT = "2026-09-08"


def _good_rows(n, id_start=1, name_len=None):
    """生成 n 行合法数据 (id, name, dt)。name_len 指定时填充随机文本。"""
    rows = []
    alphabet = string.ascii_letters + string.digits
    for i in range(n):
        if name_len:
            name = "".join(random.choice(alphabet) for _ in range(name_len))
        else:
            name = "row_{i}".format(i=id_start + i)
        rows.append((id_start + i, name, _DT))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# test 1：基线
# ─────────────────────────────────────────────────────────────────────────────


def test_01_baseline_insert_1000_rows(ch_client, temp_table):
    """写入 1000 行合法数据 → count() 必须等于 1000。"""
    rows = _good_rows(1000, id_start=1)
    t0 = time.time()
    ch_client.insert_tsv("default", temp_table, rows)
    elapsed = time.time() - t0

    cnt = _count(temp_table)
    print("\n[test 1] baseline insert: 1000 rows in {e:.2f}s -> count()={c}".format(
        e=elapsed, c=cnt))
    assert cnt == 1000, "baseline insert should land exactly 1000 rows, got {c}".format(c=cnt)


# ─────────────────────────────────────────────────────────────────────────────
# test 2：失败 INSERT 的原子性（核心）
# ─────────────────────────────────────────────────────────────────────────────


def test_02_failed_insert_is_atomic(ch_client, temp_table):
    """
    核心用例：5000 行里第 4500 行的 id 塞非法值（UInt32 解析必然失败）。

    断言：
      1. insert_tsv 抛出异常
      2. 表里行数与插入前完全一致（失败的 5000 行一行都没进去）

    第 4500 行的位置是故意选的 —— 让报错发生在解析中后段。若 ClickHouse
    是「边解析边提交」，前 4499 行会留在表里，本用例就会失败，
    说明「Code 202 重发同一批」的重试方案不安全。
    """
    before = _count(temp_table)
    print("\n[test 2] count() before bad insert = {c}".format(c=before))
    # 断言消息保持纯 ASCII（Windows 控制台约定：测试输出不用非 ASCII）
    assert before == 1000, (
        "test 2 depends on test 1's 1000-row baseline, found {c} rows -- "
        "run the whole module in file order".format(c=before)
    )

    rows = _good_rows(5000, id_start=10001)
    # 第 4500 行（1-based）→ 索引 4499，id 列塞字符串，UInt32 解析必失败
    bad_index = 4499
    orig = rows[bad_index]
    rows[bad_index] = ("not_a_number", orig[1], orig[2])
    print("[test 2] row #{n} (1-based) id -> 'not_a_number'".format(n=bad_index + 1))

    with pytest.raises(RuntimeError) as excinfo:
        ch_client.insert_tsv("default", temp_table, rows)

    err_text = _redact(excinfo.value)
    code = _extract_ch_code(err_text)
    print("[test 2] insert_tsv raised RuntimeError as expected")
    print("[test 2] ClickHouse error code = Code: {c}".format(c=code))
    print("[test 2] error text (redacted, ascii-safe, first 400 chars):\n         {t}".format(
        t=_ascii(err_text[:400]).replace("\n", "\n         ")))

    after = _count(temp_table)
    print("[test 2] count() after bad insert = {c} (expected {b})".format(
        c=after, b=before))

    if after != before:
        leaked = after - before
        print("[test 2] !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("[test 2] !! NOT ATOMIC: {n} rows from the FAILED batch landed !!".format(
            n=leaked))
        print("[test 2] !! Retrying a failed batch WOULD DUPLICATE DATA       !!")
        print("[test 2] !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

    assert after == before, (
        "ATOMICITY VIOLATED: failed INSERT left {n} of 5000 rows in the table "
        "(before={b}, after={a}). Retrying a Code 202 batch is UNSAFE.".format(
            n=after - before, b=before, a=after)
    )
    print("[test 2] ATOMIC: 0 of 5000 rows landed -> retrying a failed batch is safe")


# ─────────────────────────────────────────────────────────────────────────────
# test 3：batch_size 1000 → 5000 的性能与超时风险实测
# ─────────────────────────────────────────────────────────────────────────────


def test_03_large_batch_5000_rows_20mb_body(ch_client, temp_table):
    """
    项目要把导入 batch_size 从 1000 提到 5000。`call_record_imported` 导入类型
    的 `Call Record Text Detail Masked` 是长文本，5000 行单次 HTTP body 可能
    达到几十 MB，而客户端 timeout=60s。这里实测该场景的耗时与超时余量。
    """
    before = _count(temp_table)
    n_rows, cell_bytes = 5000, 4096
    rows = _good_rows(n_rows, id_start=100001, name_len=cell_bytes)

    # 估算 body 大小（与 insert_tsv 内部构造的 TSV 一致：name 为纯 ASCII 无需转义）
    body_bytes = sum(len(str(r[0])) + 1 + len(r[1]) + 1 + len(r[2]) + 1 for r in rows)
    body_mb = body_bytes / 1024.0 / 1024.0

    t0 = time.time()
    ch_client.insert_tsv("default", temp_table, rows)
    elapsed = time.time() - t0

    after = _count(temp_table)
    pct = elapsed / 60.0 * 100.0
    throughput = body_mb / elapsed if elapsed > 0 else float("inf")

    # 打印内容保持纯 ASCII（Windows 控制台约定）
    print("\n[test 3] batch_size=5000 large-body measurement")
    print("[test 3]   rows            = {n}".format(n=n_rows))
    print("[test 3]   per-row text    = ~{k} KB (name column)".format(k=cell_bytes // 1024))
    print("[test 3]   HTTP body size  = {m:.2f} MB".format(m=body_mb))
    print("[test 3]   elapsed         = {e:.2f} s".format(e=elapsed))
    print("[test 3]   client timeout  = 60 s")
    print("[test 3]   >>> CONCLUSION: elapsed is {p:.1f}% of the 60s timeout "
          "(headroom {r:.1f} s, ~{x:.0f}x safety margin)".format(
              p=pct, r=60.0 - elapsed,
              x=(60.0 / elapsed) if elapsed > 0 else 0))
    print("[test 3]   throughput      = {t:.1f} MB/s".format(t=throughput))
    print("[test 3]   count(): {b} -> {a} (+{d})".format(
        b=before, a=after, d=after - before))

    assert after - before == n_rows, (
        "large batch should insert exactly {n} rows, got +{d}".format(
            n=n_rows, d=after - before)
    )
    assert elapsed < 60, (
        "large batch exceeded the client's 60s timeout budget: {e:.2f}s".format(e=elapsed)
    )
