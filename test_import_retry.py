"""
test_import_retry.py — 导入分批插入的 ClickHouse Code 202 退避重试测试套件
============================================================================

被测需求：
  Excel → ClickHouse 导入按批 POST。某批遇到
  `Code: 202 TOO_MANY_SIMULTANEOUS_QUERIES` 时，原地等 N 秒重发**同一批**，
  最多 M 次；成功则继续读下一批。已成功的批次不重发、失败的批次不跳过。
  只对 202 重试，其他任何错误（含超时、连接重置）仍立即 abort。

测试层次：
  R1 (3) — 重试成功路径（含最关键的「断点语义」验证）
  R2 (3) — 重试边界（非 202 不重试、超时不重试、三种 202 写法都识别）
  R3 (2) — 重试耗尽（状态、断点信息）
  R4 (1) — 重试等待期间取消
  R5 (2) — 参数可配（env var）
  R6 (1) — call_record_imported（JSONEachRow）路径同样重试
  R7 (1) — 前端契约（errors 条目的 level 字段）

总计: 13 个测试用例

设计原则：
  - 全部 Mock ClickHouse 客户端，不连真库（原子性前提由
    test_import_atomicity_thai.py 真连 THAI 实测）
  - 真实 PostgreSQL ImportJob 追踪状态，与 test_call_record_import.py 一致
  - asyncio.sleep 被替换为记录型 fake，避免真等 5×15=75 秒；
    退避时长通过「记录到的 sleep(1) 次数」间接断言
  - 所有 job 用 _PREFIX 前缀的 username，teardown 统一清理
"""
import asyncio
import os
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_t_retry_{uuid.uuid4().hex[:6]}_"

# 真实报错原文（取自 logs/backend.log 2026-09-08 16:06:21 那次失败），
# 其中主机地址与 DB 用户名已替换为占位符 —— 被测的分类逻辑只看
# "Code: 202" / "TOO_MANY_SIMULTANEOUS_QUERIES"，与这两项无关。
_MSG_202_REAL = (
    "ClickHouse INSERT 失败 500 (ch.example.internal:8123): Code: 202. "
    "DB::Exception: Too many simultaneous queries for user ch_user. "
    "Current: 8, maximum: 8. (TOO_MANY_SIMULTANEOUS_QUERIES) "
    "(version 25.7.4.11 (official build))"
)


def _err_202() -> RuntimeError:
    """每次返回新实例，避免异常对象被复用导致 __traceback__ 累积。"""
    return RuntimeError(_MSG_202_REAL)


# ─── auth patch ──────────────────────────────────────────────────────────────
_auth_patcher = None


def setup_module(_=None):
    global _auth_patcher
    from backend.config.settings import settings
    _auth_patcher = patch.object(settings, "enable_auth", False)
    _auth_patcher.start()


def teardown_module(_=None):
    global _auth_patcher
    if _auth_patcher:
        _auth_patcher.stop()
        _auth_patcher = None
    from backend.models.import_job import ImportJob
    from backend.config.database import SessionLocal
    db = SessionLocal()
    try:
        db.query(ImportJob).filter(
            ImportJob.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_xlsx(n_rows: int, sheet_name: str = "Sheet1", headers=None) -> str:
    """生成 n_rows 行数据的临时 xlsx。每行形如 (i, f"name{i}", "2026-01-01")。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers or ["id", "name", "dt"])
    for i in range(1, n_rows + 1):
        ws.append([i, f"name{i}", "2026-01-01"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        wb.save(f.name)
        return f.name


def _safe_unlink(fp):
    try:
        if fp and os.path.exists(fp):
            os.unlink(fp)
    except OSError:
        pass


class FakeCH:
    """
    假 ClickHouse 客户端：记录每一次插入尝试，可按「第几次调用」注入异常。

    fail_plan: {调用序号(1-based): 无参可调用对象返回异常实例}
    attempted: 全部尝试收到的批（含失败的），用于验证重发的是同一份数据
    succeeded: 仅成功落库的批，用于验证无重复、无跳过
    """

    def __init__(self, fail_plan=None):
        self.fail_plan = fail_plan or {}
        self.attempted = []
        self.succeeded = []
        self.n = 0

    def _record(self, payload):
        self.n += 1
        self.attempted.append(payload)
        factory = self.fail_plan.get(self.n)
        if factory is not None:
            raise factory()
        self.succeeded.append(payload)

    def insert_tsv(self, database, table, rows):
        self._record([tuple(r) for r in rows])

    def insert_json_rows(self, database, table, col_names, rows):
        self._record([dict(r) for r in rows])


_REAL_SLEEP = asyncio.sleep


class SleepSpy:
    """
    替换 asyncio.sleep：记录时长后立即让出（不真等），可在第 k 次回调。

    服务里有两种 sleep：退避用 sleep(1)、让出事件循环用 sleep(0)。
    retry_sleeps 只统计非 0 的，避免把让出计入退避。
    """

    def __init__(self, on_retry_sleep=None):
        self.all = []
        self.on_retry_sleep = on_retry_sleep

    @property
    def retry_sleeps(self):
        return [d for d in self.all if d]

    async def __call__(self, delay, *args, **kwargs):
        self.all.append(delay)
        if delay and self.on_retry_sleep:
            self.on_retry_sleep(len(self.retry_sleeps))
        await _REAL_SLEEP(0)


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


def _create_import_job(env="my", status="pending") -> str:
    from backend.models.import_job import ImportJob
    db = _db()
    try:
        job = ImportJob(
            id=uuid.uuid4(),
            user_id=str(uuid.uuid4()),
            username=f"{_PREFIX}u",
            upload_id=str(uuid.uuid4()),
            filename="retry_test.xlsx",
            connection_env=env,
            status=status,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return str(job.id)
    finally:
        db.close()


def _set_job_status(job_id: str, status: str):
    from backend.models.import_job import ImportJob
    db = _db()
    try:
        j = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        if j:
            j.status = status
            db.commit()
    finally:
        db.close()


def _run_job(
    n_rows=25,
    batch_size=10,
    fail_plan=None,
    import_type="standard",
    sheet_name="Sheet1",
    headers=None,
    env_overrides=None,
    on_retry_sleep=None,
):
    """
    跑一次导入协程，返回 (job_dict, fake_client, sleep_spy, job_id)。

    on_retry_sleep: 回调 (第几次退避) -> None，用于在等待期间制造外部变化
                    （比如把 job 改成 cancelling）
    """
    from backend.models.import_job import ImportJob
    from backend.services.data_import_service import run_import_job

    fp = _make_xlsx(n_rows, sheet_name, headers)
    job_id = _create_import_job()
    fake = FakeCH(fail_plan)
    spy = SleepSpy(
        on_retry_sleep=(lambda k: on_retry_sleep(k, job_id)) if on_retry_sleep else None
    )
    config = {
        "file_path": fp,
        "connection_env": "my",
        "batch_size": batch_size,
        "sheets": [{
            "sheet_name": sheet_name,
            "database": "db",
            "table": "t",
            "has_header": True,
            "enabled": True,
            "import_type": import_type,
        }],
    }

    patches = [
        patch("backend.services.data_import_service._build_ch_client",
              return_value=fake),
        patch("backend.services.data_import_service.asyncio.sleep", spy),
    ]
    if env_overrides is not None:
        patches.append(patch.dict(os.environ, env_overrides))

    try:
        for p in patches:
            p.start()
        try:
            asyncio.run(run_import_job(job_id, config))
        finally:
            for p in reversed(patches):
                p.stop()
    finally:
        # run_import_job 成功路径会自己删临时文件；失败路径也会（finally 之外的
        # 清理块），这里兜底
        _safe_unlink(fp)

    db = _db()
    try:
        j = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        return (j.to_dict() if j else {}), fake, spy, job_id
    finally:
        db.close()


def _warnings(job):
    return [e for e in (job.get("errors") or []) if e.get("level") == "warning"]


def _hard_errors(job):
    return [e for e in (job.get("errors") or []) if e.get("level") != "warning"]


# ═════════════════════════════════════════════════════════════════════════════
# R1 — 重试成功路径
# ═════════════════════════════════════════════════════════════════════════════

class TestRetrySucceeds(unittest.TestCase):

    def test_R1_1_single_202_then_success(self):
        """第 2 批遇一次 202，重试一次成功，整个任务仍然 completed 且行数完整"""
        # 25 行 / 每批 10 → 批次 10+10+5，共 3 次插入
        # 第 2 次调用注入 202 → 第 3 次调用是它的重发
        job, fake, spy, _ = _run_job(n_rows=25, batch_size=10,
                                     fail_plan={2: _err_202})

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["imported_rows"], 25)
        self.assertEqual(job["done_batches"], 3)
        self.assertEqual(len(fake.attempted), 4, "3 批 + 1 次重发 = 4 次尝试")
        self.assertEqual(len(fake.succeeded), 3)
        # 退避 15 秒 → 15 次 sleep(1)
        self.assertEqual(len(spy.retry_sleeps), 15)
        self.assertEqual(set(spy.retry_sleeps), {1})

    def test_R1_2_breakpoint_semantics_no_dup_no_skip(self):
        """
        断点语义（本套件最关键的一条）：重发的是同一批同一份数据，
        成功落库的批既不重复也不跳过，顺序与文件一致。
        """
        job, fake, spy, _ = _run_job(n_rows=25, batch_size=10,
                                     fail_plan={2: _err_202})

        # 失败的那次尝试与紧随其后的重发，必须是逐字节相同的一批
        self.assertEqual(fake.attempted[1], fake.attempted[2],
                         "重发的批与失败的批必须完全相同")

        # 成功落库的行拼起来，必须恰好是文件里的 25 行、无重复、原序
        flat = [r for batch in fake.succeeded for r in batch]
        self.assertEqual(len(flat), 25, "总行数必须正好 25，不多不少")
        ids = [r[0] for r in flat]
        self.assertEqual(ids, list(range(1, 26)),
                         "id 必须是 1..25 严格递增：无重复、无跳过、无重排")
        self.assertEqual(len(set(ids)), 25, "不允许任何重复行")

    def test_R1_3_multiple_batches_each_retried(self):
        """多个批次分别遇 202，各自独立重试，互不影响"""
        # 批次序列（含重发）：1成 2失 3(2的重发)成 4失 5(4的重发)成
        job, fake, spy, _ = _run_job(n_rows=25, batch_size=10,
                                     fail_plan={2: _err_202, 4: _err_202})

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["imported_rows"], 25)
        self.assertEqual(len(fake.attempted), 5)
        self.assertEqual(len(fake.succeeded), 3)
        self.assertEqual(len(spy.retry_sleeps), 30, "两次退避 × 15 秒")
        self.assertEqual(len(_warnings(job)), 2)
        self.assertEqual(_hard_errors(job), [], "重试成功后不应留下 error 级条目")


# ═════════════════════════════════════════════════════════════════════════════
# R2 — 重试边界
# ═════════════════════════════════════════════════════════════════════════════

class TestRetryBoundary(unittest.TestCase):

    def test_R2_1_non_202_error_not_retried(self):
        """非 202 错误（内存超限 Code 241）立即 abort，零次退避"""
        err = lambda: RuntimeError(
            "ClickHouse INSERT 失败 500 (1.2.3.4:8123): Code: 241. "
            "DB::Exception: Memory limit (total) exceeded. (MEMORY_LIMIT_EXCEEDED)"
        )
        job, fake, spy, _ = _run_job(n_rows=25, batch_size=10, fail_plan={2: err})

        self.assertEqual(job["status"], "failed")
        self.assertEqual(spy.retry_sleeps, [], "非 202 必须零次退避")
        self.assertEqual(len(fake.attempted), 2, "不得重发")
        self.assertEqual(job["imported_rows"], 10, "第 1 批已入库")
        self.assertEqual(_warnings(job), [])
        self.assertEqual(len(_hard_errors(job)), 1)

    def test_R2_2_timeout_and_connection_error_not_retried(self):
        """
        超时与连接错误刻意不重试：客户端超时只是单方面放弃等待，
        服务端可能正在成功写入，重发会产生无法事后察觉的重复行。
        """
        cases = [
            ("timeout", lambda: TimeoutError("ClickHouse HTTP 超时 1.2.3.4:8123")),
            ("conn", lambda: ConnectionError(
                "ClickHouse HTTP 连接失败 1.2.3.4:8123: Connection reset by peer")),
        ]
        for name, factory in cases:
            with self.subTest(err=name):
                job, fake, spy, _ = _run_job(n_rows=25, batch_size=10,
                                             fail_plan={2: factory})
                self.assertEqual(job["status"], "failed")
                self.assertEqual(spy.retry_sleeps, [])
                self.assertEqual(len(fake.attempted), 2)

    def test_R2_3_all_202_spellings_recognized(self):
        """三种 202 报错写法都要触发重试（复用导出侧的 is_ch_too_many_queries_error）"""
        spellings = [
            _MSG_202_REAL,                                   # 完整原文
            "Code: 202. DB::Exception: something",            # 只有错误码
            "Too many simultaneous queries for user ch_user",  # 只有文字
            "TOO_MANY_SIMULTANEOUS_QUERIES",                  # 只有枚举名
        ]
        for msg in spellings:
            with self.subTest(msg=msg[:40]):
                job, fake, spy, _ = _run_job(
                    n_rows=25, batch_size=10,
                    fail_plan={2: (lambda m=msg: RuntimeError(m))})
                self.assertEqual(job["status"], "completed",
                                 f"这种写法没被识别成 202: {msg[:60]}")
                self.assertEqual(job["imported_rows"], 25)
                self.assertEqual(len(spy.retry_sleeps), 15)


# ═════════════════════════════════════════════════════════════════════════════
# R3 — 重试耗尽
# ═════════════════════════════════════════════════════════════════════════════

class TestRetryExhausted(unittest.TestCase):

    def test_R3_1_exhausted_marks_failed_with_full_warning_trail(self):
        """5 次重试全失败 → failed，5 条 warning + 1 条 error，已入库进度保留"""
        # 第 2 次调用起全部失败（2..7 = 首次 + 5 次重发）
        plan = {i: _err_202 for i in range(2, 20)}
        job, fake, spy, _ = _run_job(n_rows=25, batch_size=10, fail_plan=plan)

        self.assertEqual(job["status"], "failed")
        # 第 1 批成功 1 次 + 第 2 批（首次 + 5 次重发）6 次 = 7 次尝试
        self.assertEqual(len(fake.attempted), 7)
        self.assertEqual(len(spy.retry_sleeps), 75, "5 × 15 秒")
        self.assertEqual(len(_warnings(job)), 5)
        self.assertEqual(len(_hard_errors(job)), 1)
        # 第 1 批已入库，进度必须如实保留
        self.assertEqual(job["imported_rows"], 10)
        self.assertEqual(job["done_batches"], 1)
        self.assertEqual(len(fake.succeeded), 1)

    def test_R3_2_error_message_carries_breakpoint_info(self):
        """
        error_message 必须写清断点：已重试次数、已入库行数、失败批次及其行数。
        这是用户手工清理数据的唯一依据。
        """
        plan = {i: _err_202 for i in range(2, 20)}
        job, _, _, _ = _run_job(n_rows=25, batch_size=10, fail_plan=plan)

        msg = job["error_message"] or ""
        self.assertIn("第 2 批插入失败", msg)
        self.assertIn("已重试 5 次仍失败", msg)
        self.assertIn("已成功导入 10 行", msg)
        self.assertIn("第 1~1 批已入库", msg)
        self.assertIn("第 2 批的 10 行未入库", msg)
        # 原始 CH 报错必须保留，不能被断点信息吞掉
        self.assertIn("TOO_MANY_SIMULTANEOUS_QUERIES", msg)

    def test_R3_3_first_batch_failure_wording(self):
        """第 1 批就失败时，断点描述不能出现「第 1~0 批」这种荒谬区间"""
        plan = {i: _err_202 for i in range(1, 20)}
        job, _, _, _ = _run_job(n_rows=25, batch_size=10, fail_plan=plan)

        msg = job["error_message"] or ""
        self.assertEqual(job["imported_rows"], 0)
        self.assertIn("尚无批次入库", msg)
        self.assertNotIn("第 1~0 批", msg)


# ═════════════════════════════════════════════════════════════════════════════
# R4 — 重试等待期间取消
# ═════════════════════════════════════════════════════════════════════════════

class TestCancelDuringRetry(unittest.TestCase):

    def test_R4_1_cancel_during_retry_wait_is_cancelled_not_failed(self):
        """
        退避等待期间点取消 → 状态是 cancelled 而非 failed，
        且最多 1 秒（1 次 sleep(1)）就生效，不用等满 75 秒。
        """
        plan = {i: _err_202 for i in range(2, 20)}

        def flip(nth_sleep, job_id):
            # 第 3 秒把任务改成 cancelling，模拟用户在退避途中点了取消
            if nth_sleep == 3:
                _set_job_status(job_id, "cancelling")

        job, fake, spy, _ = _run_job(n_rows=25, batch_size=10,
                                     fail_plan=plan, on_retry_sleep=flip)

        self.assertEqual(job["status"], "cancelled",
                         "退避期间取消必须收敛到 cancelled，不能误报 failed")
        self.assertIsNone(job["error_message"])
        # 第 3 次 sleep 后就该退出，远小于 75
        self.assertEqual(len(spy.retry_sleeps), 3,
                         "取消应在下一秒即生效，不得等满整轮退避")
        # 已入库的第 1 批进度保留
        self.assertEqual(job["imported_rows"], 10)
        self.assertEqual(job["done_batches"], 1)


# ═════════════════════════════════════════════════════════════════════════════
# R5 — 参数可配
# ═════════════════════════════════════════════════════════════════════════════

class TestConfigurable(unittest.TestCase):

    def test_R5_1_env_vars_override_max_and_backoff(self):
        """IMPORT_TOO_MANY_RETRY_MAX / _BACKOFF 生效"""
        plan = {i: _err_202 for i in range(2, 20)}
        job, fake, spy, _ = _run_job(
            n_rows=25, batch_size=10, fail_plan=plan,
            env_overrides={"IMPORT_TOO_MANY_RETRY_MAX": "2",
                           "IMPORT_TOO_MANY_RETRY_BACKOFF": "3"})

        self.assertEqual(job["status"], "failed")
        # 第 1 批成功 1 次 + 第 2 批（首次 + 2 次重发）3 次 = 4 次尝试
        self.assertEqual(len(fake.attempted), 4)
        self.assertEqual(len(spy.retry_sleeps), 6, "2 × 3 秒")
        self.assertEqual(len(_warnings(job)), 2)
        self.assertIn("已重试 2 次仍失败", job["error_message"] or "")

    def test_R5_2_zero_max_disables_retry(self):
        """MAX=0 等价于关闭重试（保留旧行为的逃生阀）"""
        job, fake, spy, _ = _run_job(
            n_rows=25, batch_size=10, fail_plan={2: _err_202},
            env_overrides={"IMPORT_TOO_MANY_RETRY_MAX": "0"})

        self.assertEqual(job["status"], "failed")
        self.assertEqual(spy.retry_sleeps, [])
        self.assertEqual(len(fake.attempted), 2)

    def test_R5_3_garbage_env_falls_back_to_default(self):
        """env var 是非法值时回退默认 5/15，不能因此崩掉整个导入"""
        job, fake, spy, _ = _run_job(
            n_rows=25, batch_size=10, fail_plan={2: _err_202},
            env_overrides={"IMPORT_TOO_MANY_RETRY_MAX": "abc",
                           "IMPORT_TOO_MANY_RETRY_BACKOFF": ""})

        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(spy.retry_sleeps), 15, "回退到默认 15 秒")


# ═════════════════════════════════════════════════════════════════════════════
# R6 — call_record_imported（JSONEachRow）路径
# ═════════════════════════════════════════════════════════════════════════════

class TestCallRecordPathRetries(unittest.TestCase):

    def test_R6_1_json_rows_path_also_retries(self):
        """import_type=call_record_imported 走 insert_json_rows，同样要重试"""
        from backend.services.data_import_service import _CR_FIXED_COLS

        job, fake, spy, _ = _run_job(
            n_rows=25, batch_size=10, fail_plan={2: _err_202},
            import_type="call_record_imported",
            headers=_CR_FIXED_COLS)

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["imported_rows"], 25)
        self.assertEqual(len(fake.attempted), 4)
        self.assertEqual(len(spy.retry_sleeps), 15)
        # JSONEachRow 路径收到的是 dict
        self.assertIsInstance(fake.succeeded[0][0], dict)
        # 重发的批与失败的批一致
        self.assertEqual(fake.attempted[1], fake.attempted[2])


# ═════════════════════════════════════════════════════════════════════════════
# R7 — 前端契约
# ═════════════════════════════════════════════════════════════════════════════

class TestFrontendContract(unittest.TestCase):

    def test_R7_1_error_entry_shape(self):
        """
        errors 条目形状必须与前端约定一致：
        warning 条目带 level='warning'，真失败条目带 level='error'，
        两者都有 sheet / batch / message。
        """
        plan = {i: _err_202 for i in range(2, 20)}
        job, _, _, _ = _run_job(n_rows=25, batch_size=10, fail_plan=plan)

        for e in job["errors"]:
            self.assertIn(e.get("level"), ("warning", "error"))
            self.assertEqual(e["sheet"], "Sheet1")
            self.assertEqual(e["batch"], 2)
            self.assertTrue(e["message"])

        w = _warnings(job)
        self.assertEqual(len(w), 5)
        for i, e in enumerate(w, start=1):
            self.assertIn(f"({i}/5)", e["message"], "warning 要写明第几次/共几次")
            self.assertIn("15s 后重试", e["message"])

        err = _hard_errors(job)[0]
        self.assertIn("TOO_MANY_SIMULTANEOUS_QUERIES", err["message"])
        self.assertNotIn("断点信息", err["message"],
                         "断点信息只放 error_message，errors 条目保留原始报错")


if __name__ == "__main__":
    unittest.main(verbosity=2)
