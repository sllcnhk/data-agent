"""
pytest session-level conftest

在每次测试会话结束后（yield 之后）自动清理以测试前缀开头的用户和角色。
无论测试是否因异常中断，pytest 的 session-scoped fixture 均会在进程退出前执行 finalizer。

注意：此 conftest 依赖 backend 已在 sys.path 中。
若从项目根目录运行 pytest，需确保 backend/ 已加入 sys.path（见 pytest.ini 或 pyproject.toml）。
"""
import sys
import os
from pathlib import Path

# 确保 backend/ 在 sys.path（兼容从项目根或子目录运行）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

# 测试前缀：匹配这些前缀的用户/角色将在 session 结束后删除
_TEST_PREFIXES = ("_rbact_", "_e2e_", "_flow_", "_ulp_")

# 受保护的系统角色（不可删除）
_PROTECTED_ROLES = {"viewer", "analyst", "admin", "superadmin"}

# 受保护的用户（不可删除）
_PROTECTED_USERS = {"superadmin"}


def _is_test_name(name: str) -> bool:
    return any(name.startswith(p) for p in _TEST_PREFIXES)


def _cleanup_test_data():
    """删除所有测试前缀的用户和角色。幂等、安全。"""
    try:
        from backend.config.database import SessionLocal
        from backend.models.user import User
        from backend.models.role import Role
        from backend.models.user_role import UserRole
        from backend.models.role_permission import RolePermission
    except ImportError:
        # 若 backend 模块不可导入（如纯前端测试环境），静默跳过
        return

    db = SessionLocal()
    try:
        # 删除测试用户
        test_users = [
            u for u in db.query(User).all()
            if u.username not in _PROTECTED_USERS and _is_test_name(u.username)
        ]
        for u in test_users:
            db.query(UserRole).filter(UserRole.user_id == u.id).delete()
            db.delete(u)

        # 删除测试角色
        test_roles = [
            r for r in db.query(Role).all()
            if r.name not in _PROTECTED_ROLES and _is_test_name(r.name)
        ]
        for r in test_roles:
            db.query(RolePermission).filter(RolePermission.role_id == r.id).delete()
            db.query(UserRole).filter(UserRole.role_id == r.id).delete()
            db.delete(r)

        db.commit()

        total = len(test_users) + len(test_roles)
        if total:
            print(
                f"\n[conftest] Session cleanup: deleted {len(test_users)} user(s) "
                f"and {len(test_roles)} role(s) with test prefixes."
            )
    except Exception as exc:
        db.rollback()
        print(f"\n[conftest] Session cleanup failed (non-fatal): {exc}")
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_data_session():
    """Session-scoped fixture: 测试会话结束后自动清理测试数据。"""
    yield
    _cleanup_test_data()
