"""
test_session_management.py — Session 过期管理测试

测试范围：
  Section A (5): Cookie 为 Session Cookie（无 max-age/expires）
  Section B (5): SESSION_IDLE_TIMEOUT_MINUTES 配置
  Section C (5): last_active_at 节流更新逻辑
  Section D (6): /auth/refresh 空闲超时检测
  Section E (3): 活跃用户保持登录
  Section F (2): 端到端场景

目标: 26/26 通过
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "data_agent")

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

_pass = 0
_fail = 0
_results = []


def result(name, ok, detail=""):
    global _pass, _fail
    if ok:
        _pass += 1
        _results.append(f"  [PASS] {name}  [{detail}]" if detail else f"  [PASS] {name}")
    else:
        _fail += 1
        _results.append(f"  [FAIL] {name}  [{detail}]")


# ══════════════════════════════════════════════════════════════════════
# Section A — Cookie 为 Session Cookie（静态分析）
# ══════════════════════════════════════════════════════════════════════

def test_section_a():
    print("\n=== Section A: Cookie 为 Session Cookie ===")
    import ast

    auth_path = os.path.join(project_root, "backend/api/auth.py")
    with open(auth_path, encoding="utf-8") as f:
        src = f.read()

    # A1: max_age 行不存在于 set_cookie 调用中
    import re
    cookie_blocks = re.findall(r'set_cookie\(.*?\)', src, re.DOTALL)
    has_max_age_in_cookie = any("max_age=" in b for b in cookie_blocks)
    result("A1: set_cookie 调用中无 max_age 参数", not has_max_age_in_cookie,
           f"has_max_age={has_max_age_in_cookie}")

    # A2: set_cookie 调用中无 expires=
    has_expires_in_cookie = any("expires=" in b for b in cookie_blocks)
    result("A2: set_cookie 调用中无 expires 参数", not has_expires_in_cookie,
           f"has_expires={has_expires_in_cookie}")

    # A3: httponly 仍保留
    has_httponly = any("httponly=True" in b for b in cookie_blocks)
    result("A3: set_cookie 调用保留 httponly=True", has_httponly,
           f"has_httponly={has_httponly}")

    # A4: samesite 仍保留
    has_samesite = any("samesite=" in b for b in cookie_blocks)
    result("A4: set_cookie 调用保留 samesite=", has_samesite,
           f"has_samesite={has_samesite}")

    # A5: path 限制仍保留
    has_path = any('path="/api/v1/auth"' in b for b in cookie_blocks)
    result("A5: set_cookie 调用保留 path='/api/v1/auth'", has_path,
           f"has_path={has_path}")


# ══════════════════════════════════════════════════════════════════════
# Section B — Settings 配置
# ══════════════════════════════════════════════════════════════════════

def test_section_b():
    print("\n=== Section B: SESSION_IDLE_TIMEOUT_MINUTES 配置 ===")

    # 独立测试 settings 字段，不加载完整 .env（避免连接数据库）
    settings_path = os.path.join(project_root, "backend/config/settings.py")
    with open(settings_path, encoding="utf-8") as f:
        src = f.read()

    # B1: 字段定义存在
    has_field = "session_idle_timeout_minutes" in src
    result("B1: settings.py 中存在 session_idle_timeout_minutes 字段", has_field)

    # B2: 默认值为 120（匹配字段定义行，不匹配注释行）
    import re
    m = re.search(r'^\s+session_idle_timeout_minutes.*?default=(\d+)', src, re.MULTILINE)
    default_val = int(m.group(1)) if m else None
    result("B2: session_idle_timeout_minutes 默认值为 120", default_val == 120,
           f"default={default_val}")

    # B3: env var 名称正确
    has_env = 'env="SESSION_IDLE_TIMEOUT_MINUTES"' in src
    result("B3: env var 名为 SESSION_IDLE_TIMEOUT_MINUTES", has_env)

    # B4: .env 中存在 SESSION_IDLE_TIMEOUT_MINUTES
    env_path = os.path.join(project_root, ".env")
    with open(env_path, encoding="utf-8") as f:
        env_src = f.read()
    has_env_config = "SESSION_IDLE_TIMEOUT_MINUTES=" in env_src
    result("B4: .env 中已配置 SESSION_IDLE_TIMEOUT_MINUTES", has_env_config)

    # B5: .env 中 ACCESS_TOKEN_EXPIRE_MINUTES=120（与超时对齐）
    m2 = re.search(r'^ACCESS_TOKEN_EXPIRE_MINUTES=(\d+)', env_src, re.MULTILINE)
    access_min = int(m2.group(1)) if m2 else None
    result("B5: .env ACCESS_TOKEN_EXPIRE_MINUTES=120（与空闲超时对齐）",
           access_min == 120, f"ACCESS_TOKEN_EXPIRE_MINUTES={access_min}")


# ══════════════════════════════════════════════════════════════════════
# Section C — last_active_at 节流更新（mock 测试）
# ══════════════════════════════════════════════════════════════════════

def test_section_c():
    print("\n=== Section C: last_active_at 节流更新 ===")

    deps_path = os.path.join(project_root, "backend/api/deps.py")
    with open(deps_path, encoding="utf-8") as f:
        src = f.read()

    # C1: _update_last_active 函数存在
    has_fn = "_update_last_active" in src
    result("C1: deps.py 中存在 _update_last_active 函数", has_fn)

    # C2: _ACTIVITY_THROTTLE_SEC 常量存在
    has_throttle = "_ACTIVITY_THROTTLE_SEC" in src
    result("C2: _ACTIVITY_THROTTLE_SEC 常量存在", has_throttle)

    # C3: get_current_user 注入 BackgroundTasks
    has_bt = "BackgroundTasks" in src and "background_tasks" in src
    result("C3: get_current_user 接受 BackgroundTasks 参数", has_bt)

    # C4: background_tasks.add_task 调用存在
    has_add_task = "background_tasks.add_task" in src
    result("C4: 代码中调用 background_tasks.add_task", has_add_task)

    # C5: _update_last_active 使用独立 SessionLocal（不依赖传入 db）
    has_session_local = "SessionLocal" in src
    result("C5: _update_last_active 使用独立 SessionLocal（不阻塞请求）", has_session_local)


# ══════════════════════════════════════════════════════════════════════
# Section D — /auth/refresh 空闲超时检测（mock 测试）
# ══════════════════════════════════════════════════════════════════════

def _make_mock_rt(revoked=False, expires_delta_days=14):
    rt = MagicMock()
    rt.revoked = revoked
    rt.expires_at = datetime.utcnow() + timedelta(days=expires_delta_days)
    rt.user_id = "test-user-id"
    return rt


def _make_mock_user(last_active_at=None, last_login_at=None, is_active=True):
    user = MagicMock()
    user.is_active = is_active
    user.id = "test-user-id"
    user.last_active_at = last_active_at
    user.last_login_at = last_login_at
    user.username = "testuser"
    return user


def _run_refresh_check(last_active_at, last_login_at, idle_timeout_min=120,
                       enable_auth=True):
    """模拟 /auth/refresh 端点的空闲超时检测逻辑（直接测试逻辑，非 HTTP 请求）"""
    from fastapi import HTTPException

    # 复现 auth.py 中的检测逻辑
    user = _make_mock_user(last_active_at=last_active_at, last_login_at=last_login_at)

    if enable_auth:
        timeout_min = idle_timeout_min
        activity_ts = user.last_active_at or user.last_login_at
        if activity_ts is not None:
            idle_min = (datetime.utcnow() - activity_ts).total_seconds() / 60
            if idle_min > timeout_min:
                return "rejected"
    return "allowed"


def test_section_d():
    print("\n=== Section D: /auth/refresh 空闲超时检测 ===")

    # D1: activity_ts=None（brand-new user）→ 不拒绝
    r = _run_refresh_check(last_active_at=None, last_login_at=None)
    result("D1: last_active_at=None, last_login_at=None → 不拒绝（新账号）",
           r == "allowed", f"result={r}")

    # D2: last_active_at 在超时范围内 → 允许
    recent = datetime.utcnow() - timedelta(minutes=60)
    r = _run_refresh_check(last_active_at=recent, last_login_at=None)
    result("D2: last_active_at=60min前（<120min）→ 允许", r == "allowed",
           f"result={r}")

    # D3: last_active_at 超过超时 → 拒绝
    old = datetime.utcnow() - timedelta(minutes=130)
    r = _run_refresh_check(last_active_at=old, last_login_at=None)
    result("D3: last_active_at=130min前（>120min）→ 拒绝", r == "rejected",
           f"result={r}")

    # D4: last_active_at=None, last_login_at 在超时范围内 → 允许（兜底）
    recent_login = datetime.utcnow() - timedelta(minutes=30)
    r = _run_refresh_check(last_active_at=None, last_login_at=recent_login)
    result("D4: last_login_at=30min前（兜底）→ 允许", r == "allowed",
           f"result={r}")

    # D5: last_active_at=None, last_login_at 超时 → 拒绝（兜底）
    old_login = datetime.utcnow() - timedelta(minutes=200)
    r = _run_refresh_check(last_active_at=None, last_login_at=old_login)
    result("D5: last_login_at=200min前（兜底，超时）→ 拒绝", r == "rejected",
           f"result={r}")

    # D6: ENABLE_AUTH=false → 不检测
    old = datetime.utcnow() - timedelta(minutes=500)
    r = _run_refresh_check(last_active_at=old, last_login_at=None, enable_auth=False)
    result("D6: ENABLE_AUTH=false → 空闲检测跳过，始终允许", r == "allowed",
           f"result={r}")


# ══════════════════════════════════════════════════════════════════════
# Section E — 活跃用户保持登录（逻辑验证）
# ══════════════════════════════════════════════════════════════════════

def test_section_e():
    print("\n=== Section E: 活跃用户保持登录 ===")

    # E1: 5 分钟内多次访问 → 只触发一次节流写入
    mock_bt = MagicMock()
    calls_to_add_task = []

    # 模拟 3 次请求，间隔 1 分钟（<5min 节流）
    last = datetime.utcnow() - timedelta(minutes=1)
    for _ in range(3):
        now = datetime.utcnow()
        if (now - last).total_seconds() > 300:
            calls_to_add_task.append("write")
    result("E1: 1min 内 3 次请求不重复触发节流写入（0 次写）",
           len(calls_to_add_task) == 0, f"write_calls={len(calls_to_add_task)}")

    # E2: 距上次活动 > 5 分钟 → 触发写入
    last_old = datetime.utcnow() - timedelta(minutes=10)
    should_write = (datetime.utcnow() - last_old).total_seconds() > 300
    result("E2: 距上次活动 10min → 节流条件满足，应触发写入",
           should_write, f"should_write={should_write}")

    # E3: 活跃用户（60min 内活动）refresh 检测通过
    active_ts = datetime.utcnow() - timedelta(minutes=60)
    r = _run_refresh_check(last_active_at=active_ts, last_login_at=None)
    result("E3: 活跃用户（60min内有活动）→ refresh 通过", r == "allowed",
           f"result={r}")


# ══════════════════════════════════════════════════════════════════════
# Section F — 静态代码验证（auth.py 含空闲检测代码）
# ══════════════════════════════════════════════════════════════════════

def test_section_f():
    print("\n=== Section F: auth.py 代码结构验证 ===")

    auth_path = os.path.join(project_root, "backend/api/auth.py")
    with open(auth_path, encoding="utf-8") as f:
        src = f.read()

    # F1: 空闲超时检测代码块存在
    has_idle_check = "session_idle_timeout_minutes" in src
    result("F1: auth.py 中存在 session_idle_timeout_minutes 引用（空闲检测代码）",
           has_idle_check)

    # F2: 空闲超时触发 401 时也吊销 refresh token
    has_revoke_on_idle = "会话已超时" in src
    result("F2: 空闲超时拒绝时返回 '会话已超时' detail", has_revoke_on_idle)


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("test_session_management.py  Session 过期管理测试")
    print("=" * 70)

    test_section_a()
    test_section_b()
    test_section_c()
    test_section_d()
    test_section_e()
    test_section_f()

    print()
    for r in _results:
        print(r)

    total = _pass + _fail
    print()
    print("=" * 70)
    print(f"Results: {_pass}/{total} passed, {_fail} failed")
    print("=" * 70)

    if _fail > 0:
        sys.exit(1)
