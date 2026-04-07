"""
test_data_export_download.py — 下载端点单元测试

验证 GET /api/v1/data-export/jobs/{job_id}/download 端点的行为：

  A (4)  — 后端下载端点核心逻辑
           A1: 带有效 token + 完成任务 + 文件存在 → 200 + 正确 MIME + Content-Disposition
           A2: 无 token（ENABLE_AUTH=True）→ 401
           A3: 任务未完成 → 400
           A4: 文件已不存在磁盘 → 404

  B (2)  — ENABLE_AUTH=False AnonymousUser 兼容性
           B1: ENABLE_AUTH=False、无 token → 200（AnonymousUser 直接通过）
           B2: AnonymousUser.is_superadmin=True，require_permission 不拦截

共计: 6 个测试用例

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_data_export_download.py -v -s
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

from fastapi.testclient import TestClient

_PREFIX = f"_t_dl_{uuid.uuid4().hex[:6]}_"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", role_names=None, is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"DL {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    for rname in (role_names or []):
        role = _g_db.query(Role).filter(Role.name == rname).first()
        if role:
            _g_db.add(UserRole(user_id=u.id, role_id=role.id))
    _g_db.commit()
    _g_db.refresh(u)
    return u


def _token(user):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret, settings.jwt_algorithm,
    )


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


def _make_export_job(db, username, status="completed", file_path=None):
    """直接在 DB 中插入一条导出任务记录"""
    from backend.models.export_job import ExportJob
    job = ExportJob(
        user_id="uid-test",
        username=username,
        query_sql="SELECT 1",
        connection_env="test",
        connection_type="clickhouse",
        status=status,
        output_filename=f"{_PREFIX}out.xlsx",
        file_path=file_path,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _make_real_xlsx(tmp_dir: str) -> str:
    """生成一个最小的合法 xlsx 文件，用于下载端点测试"""
    import openpyxl
    path = os.path.join(tmp_dir, f"{_PREFIX}test.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["col_a", "col_b"])
    ws.append([1, 2])
    wb.save(path)
    return path


def teardown_module(_=None):
    """清理测试数据"""
    from backend.models.user import User
    from backend.models.export_job import ExportJob
    try:
        _g_db.query(ExportJob).filter(
            ExportJob.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


def _make_app():
    from backend.main import app
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Section A — 后端下载端点核心逻辑（ENABLE_AUTH=True）
# ══════════════════════════════════════════════════════════════════════════════

class TestDownloadEndpoint(unittest.TestCase):
    """A1-A4: 验证 GET /jobs/{id}/download 端点行为"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.superadmin = _make_user("super", is_superadmin=True)
        cls.tmp_dir = tempfile.mkdtemp()

    def _req(self, method, path, headers=None, **kwargs):
        """以 ENABLE_AUTH=True 发请求"""
        with patch("backend.config.settings.settings.enable_auth", True):
            return getattr(self.client, method)(path, headers=headers or {}, **kwargs)

    def test_A1_completed_job_with_file_returns_200_xlsx(self):
        """A1: 带有效 token + 完成任务 + 文件存在 → 200 + xlsx MIME + Content-Disposition"""
        xlsx_path = _make_real_xlsx(self.tmp_dir)
        job = _make_export_job(_g_db, self.superadmin.username,
                               status="completed", file_path=xlsx_path)
        resp = self._req("get",
                         f"/api/v1/data-export/jobs/{job.id}/download",
                         headers=_auth(self.superadmin))

        self.assertEqual(resp.status_code, 200,
                         f"A1 预期 200，实际 {resp.status_code}: {resp.text}")
        self.assertIn(XLSX_MIME, resp.headers.get("content-type", ""),
                      f"A1 MIME 不正确: {resp.headers.get('content-type')}")
        disposition = resp.headers.get("content-disposition", "")
        self.assertIn("attachment", disposition,
                      f"A1 Content-Disposition 应含 attachment: {disposition}")
        # 响应体应是非空的二进制内容
        self.assertGreater(len(resp.content), 0, "A1 响应体为空")

        # 清理
        _g_db.delete(job)
        _g_db.commit()
        os.unlink(xlsx_path)

    def test_A2_no_token_returns_401(self):
        """A2: ENABLE_AUTH=True 下不带 token → 401"""
        job = _make_export_job(_g_db, self.superadmin.username, status="completed")
        resp = self._req("get", f"/api/v1/data-export/jobs/{job.id}/download")

        self.assertEqual(resp.status_code, 401,
                         f"A2 预期 401，实际 {resp.status_code}: {resp.text}")

        _g_db.delete(job)
        _g_db.commit()

    def test_A3_non_completed_job_returns_400(self):
        """A3: 任务状态非 completed → 400（包含错误信息）"""
        for bad_status in ("pending", "running", "failed", "cancelled"):
            with self.subTest(status=bad_status):
                job = _make_export_job(_g_db, self.superadmin.username, status=bad_status)
                resp = self._req("get",
                                 f"/api/v1/data-export/jobs/{job.id}/download",
                                 headers=_auth(self.superadmin))
                self.assertEqual(resp.status_code, 400,
                                 f"A3 [{bad_status}] 预期 400，实际 {resp.status_code}")
                self.assertIn(bad_status, resp.text,
                              f"A3 [{bad_status}] 错误信息未含状态名")
                _g_db.delete(job)
                _g_db.commit()

    def test_A4_file_missing_on_disk_returns_404(self):
        """A4: 任务已完成但磁盘文件已删除 → 404"""
        missing_path = os.path.join(self.tmp_dir, f"{_PREFIX}gone.xlsx")
        # 确保文件不存在
        if os.path.exists(missing_path):
            os.unlink(missing_path)

        job = _make_export_job(_g_db, self.superadmin.username,
                               status="completed", file_path=missing_path)
        resp = self._req("get",
                         f"/api/v1/data-export/jobs/{job.id}/download",
                         headers=_auth(self.superadmin))
        self.assertEqual(resp.status_code, 404,
                         f"A4 预期 404，实际 {resp.status_code}: {resp.text}")

        _g_db.delete(job)
        _g_db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Section B — ENABLE_AUTH=False AnonymousUser 兼容性
# ══════════════════════════════════════════════════════════════════════════════

class TestAnonymousUserCompatibility(unittest.TestCase):
    """B1-B2: ENABLE_AUTH=False 场景下下载端点不应要求 token"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.tmp_dir = tempfile.mkdtemp()

    def test_B1_no_token_with_auth_disabled_returns_200(self):
        """B1: ENABLE_AUTH=False，无 token → AnonymousUser → 200（开发模式兼容）"""
        xlsx_path = _make_real_xlsx(self.tmp_dir)
        # 使用匿名用户名（ENABLE_AUTH=false 下 username="default"）
        job = _make_export_job(_g_db, "default", status="completed", file_path=xlsx_path)

        # ENABLE_AUTH=False（默认），不带 Authorization header
        resp = self.client.get(f"/api/v1/data-export/jobs/{job.id}/download")

        self.assertEqual(resp.status_code, 200,
                         f"B1 预期 200，实际 {resp.status_code}: {resp.text}")
        self.assertIn(XLSX_MIME, resp.headers.get("content-type", ""))

        _g_db.delete(job)
        _g_db.commit()
        os.unlink(xlsx_path)

    def test_B2_anonymous_user_has_superadmin_flag(self):
        """B2: AnonymousUser.is_superadmin=True → require_permission 不抛 403"""
        from backend.api.deps import AnonymousUser
        anon = AnonymousUser()
        self.assertTrue(anon.is_superadmin,
                        "AnonymousUser.is_superadmin 应为 True，否则 ENABLE_AUTH=False 下权限检查会失败")
        self.assertEqual(anon.username, "default")
        self.assertEqual(anon.id, "default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
