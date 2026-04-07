"""
pytest 根目录 conftest — session 级测试数据清理

覆盖项目根目录下所有测试文件（test_*.py）。
- Session 开始前：清理上次崩溃/中断残留的测试数据（幂等）
- Session 结束后：清理本次测试产生的测试数据

识别规则：用户名或角色名匹配 ^_[a-z][a-z0-9]*_ 正则（如 _t_、_rbact_、_e2e_ 等）
customer_data/ 下同名前缀的测试用户目录也一并清理。
"""
import re
import shutil
import sys
from pathlib import Path

# 确保 backend/ 在 sys.path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

# 匹配所有 _xxx_ 格式的测试前缀（统一规范）
_TEST_PATTERN = re.compile(r'^_[a-z][a-z0-9]*_')

# 永远保护的系统角色和用户（不得删除）
_PROTECTED_ROLES = {"viewer", "analyst", "admin", "superadmin"}
_PROTECTED_USERS = {"superadmin", "default", "anonymous"}


def _is_test_entity(name: str) -> bool:
    """判断名称是否为测试数据（匹配 _xxx_ 前缀格式）。"""
    return bool(_TEST_PATTERN.match(name))


def _cleanup_customer_data_dirs(label: str = "") -> int:
    """
    清理 customer_data/ 下匹配测试前缀的用户目录。
    返回删除的目录数量。
    """
    customer_data = _ROOT / "customer_data"
    if not customer_data.is_dir():
        return 0

    deleted = 0
    for entry in customer_data.iterdir():
        if entry.is_dir() and _is_test_entity(entry.name):
            try:
                shutil.rmtree(entry)
                deleted += 1
            except Exception as exc:
                print(f"\n[conftest] Failed to remove {entry}: {exc}")

    if deleted:
        tag = f"[{label}] " if label else ""
        print(f"\n[conftest] {tag}Cleanup: removed {deleted} customer_data dir(s).")
    return deleted


def _cleanup_test_data(label: str = ""):
    """
    清理数据库中的测试用户、角色和导入任务，以及 customer_data/ 下的测试用户目录。
    返回 (deleted_users, deleted_roles)。
    label 仅用于日志标注（如 "pre" / "post"）。
    """
    # 清理 customer_data/ 测试目录（不依赖 DB，先做）
    _cleanup_customer_data_dirs(label)

    try:
        from backend.config.database import SessionLocal
        from backend.models.user import User
        from backend.models.role import Role
        from backend.models.user_role import UserRole
        from backend.models.role_permission import RolePermission
    except ImportError:
        return 0, 0

    db = SessionLocal()
    try:
        # 清理测试导入/导出任务（username 匹配测试前缀）
        try:
            from backend.models.import_job import ImportJob
            test_import_jobs = [
                j for j in db.query(ImportJob).all()
                if _is_test_entity(j.username)
            ]
            for j in test_import_jobs:
                db.delete(j)
            if test_import_jobs:
                tag = f"[{label}] " if label else ""
                print(f"\n[conftest] {tag}Cleanup: deleted {len(test_import_jobs)} import job(s).")
        except Exception as exc:
            print(f"\n[conftest] ImportJob cleanup skipped (non-fatal): {exc}")

        try:
            import os as _os
            from backend.models.export_job import ExportJob
            test_export_jobs = [
                j for j in db.query(ExportJob).all()
                if _is_test_entity(j.username)
            ]
            for j in test_export_jobs:
                # 同时删除磁盘上的导出文件
                if j.file_path:
                    try:
                        _os.unlink(j.file_path)
                    except OSError:
                        pass
                db.delete(j)
            if test_export_jobs:
                tag = f"[{label}] " if label else ""
                print(f"\n[conftest] {tag}Cleanup: deleted {len(test_export_jobs)} export job(s).")
        except Exception as exc:
            print(f"\n[conftest] ExportJob cleanup skipped (non-fatal): {exc}")

        test_users = [
            u for u in db.query(User).all()
            if u.username not in _PROTECTED_USERS and _is_test_entity(u.username)
        ]
        for u in test_users:
            db.query(UserRole).filter(UserRole.user_id == u.id).delete()
            db.delete(u)

        test_roles = [
            r for r in db.query(Role).all()
            if r.name not in _PROTECTED_ROLES and _is_test_entity(r.name)
        ]
        for r in test_roles:
            db.query(RolePermission).filter(RolePermission.role_id == r.id).delete()
            db.query(UserRole).filter(UserRole.role_id == r.id).delete()
            db.delete(r)

        db.commit()
        u_count, r_count = len(test_users), len(test_roles)
        if u_count or r_count:
            tag = f"[{label}] " if label else ""
            print(f"\n[conftest] {tag}Cleanup: deleted {u_count} user(s), {r_count} role(s).")
        return u_count, r_count
    except Exception as exc:
        db.rollback()
        print(f"\n[conftest] Cleanup failed (non-fatal): {exc}")
        return 0, 0
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_data_session():
    """Session 级测试数据清理：开始前清理残留，结束后清理本次产生。"""
    _cleanup_test_data(label="pre-session")   # 清理上次崩溃/中断的残留
    yield
    _cleanup_test_data(label="post-session")  # 清理本次 session 产生的数据
