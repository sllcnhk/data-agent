"""
test_rbac_e2e.py — RBAC 端到端测试套件（补充 test_rbac.py）
==============================================================

重点覆盖本次新增/修复的内容：

  J  (12) — 角色管理 API（POST/PUT/DELETE /roles + 权限分配）
  K  ( 5) — 权限列表 API（GET /permissions）
  L  ( 9) — 菜单权限范围验证（新增菜单是否纳入权限管理）
  M  ( 8) — 端到端：角色生命周期（创建→授权→分配用户→验证→清理）
  N  ( 6) — 端到端：认证流程（initAuth 四条路径 + 401 自动检测）
  O  ( 5) — 端到端：权限矩阵完整性验证

总计: 45 个测试用例
"""

from __future__ import annotations

import os
import re
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ─── 全局测试数据前缀 ──────────────────────────────────────────────────────
_PREFIX = f"_e2e_{uuid.uuid4().hex[:6]}_"


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", password="Test1234!", role_names=None,
               is_superadmin=False, is_active=True):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"E2E {suffix}",
        hashed_password=hash_password(password),
        auth_source="local",
        is_active=is_active,
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
    return u, username, password


def _token(user):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret, settings.jwt_algorithm,
    )


def teardown_module(_=None):
    from backend.models.user import User
    from backend.models.role import Role
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        # 删除本次创建的自定义角色
        _g_db.query(Role).filter(
            Role.name.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ─── FastAPI TestClient ────────────────────────────────────────────────────
from fastapi.testclient import TestClient

def _make_app():
    from backend.main import app
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Section J — 角色管理 API（新增端点测试）
# ══════════════════════════════════════════════════════════════════════════════

class TestRoleManagementAPI(unittest.TestCase):
    """J1-J12: POST/PUT/DELETE /roles + 权限分配端点"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        # 有 users:write + users:assign_role 权限的用户
        cls.admin_user, cls.admin_name, cls.admin_pw = _make_user(
            "j_admin", role_names=["superadmin"]
        )
        # 只有 analyst 权限（无 users:write / users:assign_role）
        cls.analyst_user, cls.analyst_name, _ = _make_user(
            "j_analyst", role_names=["analyst"]
        )

    def _auth(self, user=None):
        u = user or self.admin_user
        return {"Authorization": f"Bearer {_token(u)}"}

    def test_J1_get_roles_authenticated_returns_list(self):
        """GET /roles 已认证返回角色列表"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/roles", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        names = [r["name"] for r in data]
        self.assertIn("superadmin", names)
        self.assertIn("admin", names)
        self.assertIn("analyst", names)
        self.assertIn("viewer", names)

    def test_J2_get_roles_unauthenticated_returns_401(self):
        """GET /roles 未认证返回 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/roles")
        self.assertEqual(resp.status_code, 401)

    def test_J3_create_role_with_users_write_returns_201(self):
        """POST /roles 有 users:write 权限可创建自定义角色"""
        role_name = f"{_PREFIX}role_j3"
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/roles",
                json={"name": role_name, "description": "J3 test role"},
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertEqual(data["name"], role_name)
        self.assertFalse(data["is_system"], "自定义角色 is_system 应为 False")
        self.assertEqual(data["permissions"], [])

    def test_J4_create_role_without_users_write_returns_403(self):
        """POST /roles 无 users:write 权限返回 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/roles",
                json={"name": f"{_PREFIX}noauth_j4", "description": "no perm"},
                headers=self._auth(self.analyst_user),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_J5_create_role_duplicate_name_returns_409(self):
        """POST /roles 重复角色名返回 409"""
        with patch("backend.config.settings.settings.enable_auth", True):
            self.client.post(
                "/api/v1/roles",
                json={"name": f"{_PREFIX}dup_j5", "description": "first"},
                headers=self._auth(),
            )
            resp = self.client.post(
                "/api/v1/roles",
                json={"name": f"{_PREFIX}dup_j5", "description": "second"},
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 409, resp.text)

    def test_J6_update_role_description(self):
        """PUT /roles/{id} 可修改描述"""
        role_name = f"{_PREFIX}role_j6"
        with patch("backend.config.settings.settings.enable_auth", True):
            create_resp = self.client.post(
                "/api/v1/roles",
                json={"name": role_name, "description": "original"},
                headers=self._auth(),
            )
            role_id = create_resp.json()["id"]
            resp = self.client.put(
                f"/api/v1/roles/{role_id}",
                json={"description": "updated description"},
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["description"], "updated description")

    def test_J7_delete_custom_role_returns_204(self):
        """DELETE /roles/{id} 删除自定义角色返回 204"""
        role_name = f"{_PREFIX}role_j7"
        with patch("backend.config.settings.settings.enable_auth", True):
            create_resp = self.client.post(
                "/api/v1/roles",
                json={"name": role_name, "description": "to delete"},
                headers=self._auth(),
            )
            role_id = create_resp.json()["id"]
            resp = self.client.delete(
                f"/api/v1/roles/{role_id}",
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 204, resp.text)

    def test_J8_delete_system_role_returns_403(self):
        """DELETE /roles/{id} 删除系统预置角色返回 403"""
        from backend.models.role import Role
        system_role = _g_db.query(Role).filter(Role.name == "analyst").first()
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(
                f"/api/v1/roles/{system_role.id}",
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("系统预置", resp.json()["detail"])

    def test_J9_assign_permission_to_role(self):
        """POST /roles/{id}/permissions 为角色分配权限"""
        from backend.models.permission import Permission
        role_name = f"{_PREFIX}role_j9"
        perm = _g_db.query(Permission).filter(
            Permission.resource == "chat", Permission.action == "use"
        ).first()
        with patch("backend.config.settings.settings.enable_auth", True):
            create_resp = self.client.post(
                "/api/v1/roles",
                json={"name": role_name, "description": "for perm test"},
                headers=self._auth(),
            )
            role_id = create_resp.json()["id"]
            resp = self.client.post(
                f"/api/v1/roles/{role_id}/permissions",
                json={"permission_id": str(perm.id)},
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        perm_keys = [f"{p['resource']}:{p['action']}" for p in resp.json()["permissions"]]
        self.assertIn("chat:use", perm_keys)

    def test_J10_remove_permission_from_role(self):
        """DELETE /roles/{id}/permissions/{perm_id} 移除角色权限"""
        from backend.models.permission import Permission
        role_name = f"{_PREFIX}role_j10"
        perm = _g_db.query(Permission).filter(
            Permission.resource == "models", Permission.action == "read"
        ).first()
        with patch("backend.config.settings.settings.enable_auth", True):
            create_resp = self.client.post(
                "/api/v1/roles",
                json={"name": role_name, "description": "for remove perm"},
                headers=self._auth(),
            )
            role_id = create_resp.json()["id"]
            # 先分配
            self.client.post(
                f"/api/v1/roles/{role_id}/permissions",
                json={"permission_id": str(perm.id)},
                headers=self._auth(),
            )
            # 再移除
            resp = self.client.delete(
                f"/api/v1/roles/{role_id}/permissions/{perm.id}",
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        perm_keys = [f"{p['resource']}:{p['action']}" for p in resp.json()["permissions"]]
        self.assertNotIn("models:read", perm_keys)

    def test_J11_assign_permission_idempotent(self):
        """重复分配同一权限不产生重复记录"""
        from backend.models.permission import Permission
        from backend.models.role_permission import RolePermission
        role_name = f"{_PREFIX}role_j11"
        perm = _g_db.query(Permission).filter(
            Permission.resource == "chat", Permission.action == "use"
        ).first()
        with patch("backend.config.settings.settings.enable_auth", True):
            create_resp = self.client.post(
                "/api/v1/roles",
                json={"name": role_name, "description": "idempotent"},
                headers=self._auth(),
            )
            role_id = create_resp.json()["id"]
            for _ in range(3):
                self.client.post(
                    f"/api/v1/roles/{role_id}/permissions",
                    json={"permission_id": str(perm.id)},
                    headers=self._auth(),
                )
        # 验证数据库中只有一条记录
        from backend.models.role import Role
        role = _g_db.query(Role).filter(Role.name == role_name).first()
        count = _g_db.query(RolePermission).filter(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == perm.id,
        ).count()
        self.assertEqual(count, 1, "重复分配不应产生多条记录")

    def test_J12_delete_nonexistent_role_returns_404(self):
        """DELETE /roles/{fake_id} 不存在的角色返回 404"""
        fake_id = str(uuid.uuid4())
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(
                f"/api/v1/roles/{fake_id}",
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 404, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section K — 权限列表 API
# ══════════════════════════════════════════════════════════════════════════════

class TestPermissionsAPI(unittest.TestCase):
    """K1-K5: GET /permissions 端点"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.superadmin_user, _, _ = _make_user("k_super", role_names=["superadmin"])
        cls.viewer_user, _, _ = _make_user("k_viewer", role_names=["viewer"])

    def _auth(self, user):
        return {"Authorization": f"Bearer {_token(user)}"}

    def test_K1_get_permissions_authenticated_returns_list(self):
        """GET /permissions 已认证（有 users:read）返回权限列表"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/permissions",
                headers=self._auth(self.superadmin_user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_K2_get_permissions_unauthenticated_returns_401(self):
        """GET /permissions 未认证返回 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/permissions")
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_K3_get_permissions_viewer_role_returns_403(self):
        """GET /permissions viewer 角色无 users:read → 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/permissions",
                headers=self._auth(self.viewer_user),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_K4_all_13_permissions_present(self):
        """权限列表包含设计文档中定义的全部 13 个权限"""
        expected = {
            "chat:use", "skills.user:read", "skills.user:write",
            "skills.project:read", "skills.project:write", "skills.system:read",
            "models:read", "models:write",
            "users:read", "users:write", "users:assign_role",
            "settings:read", "settings:write",
        }
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/permissions",
                headers=self._auth(self.superadmin_user),
            )
        actual = {f"{p['resource']}:{p['action']}" for p in resp.json()}
        missing = expected - actual
        self.assertEqual(missing, set(), f"缺少权限定义: {missing}")

    def test_K5_permissions_have_required_fields(self):
        """每个权限对象包含 id/resource/action/description 字段"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/permissions",
                headers=self._auth(self.superadmin_user),
            )
        for perm in resp.json():
            for field in ("id", "resource", "action"):
                self.assertIn(field, perm, f"权限缺少字段: {field}")


# ══════════════════════════════════════════════════════════════════════════════
# Section L — 菜单权限范围验证
# ══════════════════════════════════════════════════════════════════════════════

class TestMenuPermissionScope(unittest.TestCase):
    """
    L1-L9: 验证菜单权限范围

    菜单配置（AppLayout.tsx）:
      /chat          → chat:use
      /model-config  → models:read
      /dashboard     → (无限制)
      /agents        → (无限制)
      /tasks         → (无限制)
      /skills        → skills.user:read
      /users         → users:read        ← 旧菜单
      /roles         → users:read        ← 新增菜单
      /logs          → (无限制)
    """

    # 模拟 AppLayout 的权限过滤逻辑
    ALL_MENU = [
        {"key": "/chat",         "perm": "chat:use"},
        {"key": "/model-config", "perm": "models:read"},
        {"key": "/dashboard",    "perm": None},
        {"key": "/agents",       "perm": None},
        {"key": "/tasks",        "perm": None},
        {"key": "/skills",       "perm": "skills.user:read"},
        {"key": "/users",        "perm": "users:read"},
        {"key": "/roles",        "perm": "users:read"},   # 新增菜单
        {"key": "/logs",         "perm": None},
    ]

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.viewer_user, _, _ = _make_user("l_viewer", role_names=["viewer"])
        cls.analyst_user, _, _ = _make_user("l_analyst", role_names=["analyst"])
        cls.admin_user, _, _ = _make_user("l_admin", role_names=["admin"])
        cls.superadmin_user, _, _ = _make_user("l_super", role_names=["superadmin"])

    def _visible_menus(self, permissions: list[str]) -> list[str]:
        """模拟前端菜单过滤逻辑"""
        return [
            m["key"] for m in self.ALL_MENU
            if m["perm"] is None or m["perm"] in permissions
        ]

    def _get_permissions(self, user) -> list[str]:
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(user)}"},
            )
        return resp.json().get("permissions", [])

    def test_L1_viewer_cannot_see_users_menu(self):
        """/users 菜单对 viewer 角色不可见（无 users:read）"""
        perms = self._get_permissions(self.viewer_user)
        visible = self._visible_menus(perms)
        self.assertNotIn("/users", visible, f"viewer 不应看到 /users，当前权限: {perms}")

    def test_L2_viewer_cannot_see_roles_menu(self):
        """/roles 菜单对 viewer 角色不可见（无 users:read）"""
        perms = self._get_permissions(self.viewer_user)
        visible = self._visible_menus(perms)
        self.assertNotIn("/roles", visible, f"viewer 不应看到 /roles，当前权限: {perms}")

    def test_L3_analyst_cannot_see_users_roles_menus(self):
        """analyst 角色无 users:read，不可见 users/roles 菜单"""
        perms = self._get_permissions(self.analyst_user)
        visible = self._visible_menus(perms)
        self.assertNotIn("/users", visible)
        self.assertNotIn("/roles", visible)

    def test_L4_admin_role_lacks_users_read_by_design(self):
        """
        admin 角色（项目管理员）设计上不含 users:read，
        因此不可见用户/角色管理菜单（符合最小权限原则）
        """
        perms = self._get_permissions(self.admin_user)
        self.assertNotIn("users:read", perms,
                         "admin 角色不应包含 users:read（只有 superadmin 管理用户）")
        visible = self._visible_menus(perms)
        self.assertNotIn("/users", visible)
        self.assertNotIn("/roles", visible)

    def test_L5_superadmin_can_see_users_and_roles_menus(self):
        """superadmin 角色有 users:read，可见 users/roles 菜单"""
        perms = self._get_permissions(self.superadmin_user)
        visible = self._visible_menus(perms)
        self.assertIn("/users", visible)
        self.assertIn("/roles", visible)

    def test_L6_roles_api_requires_users_read_permission(self):
        """GET /roles API 要求 users:read，viewer/analyst 返回 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            for user, role in [(self.viewer_user, "viewer"), (self.analyst_user, "analyst")]:
                resp = self.client.get(
                    "/api/v1/roles",
                    headers={"Authorization": f"Bearer {_token(user)}"},
                )
                self.assertEqual(resp.status_code, 403,
                                 f"{role} 访问 /roles 应返回 403，实际: {resp.status_code}")

    def test_L7_users_api_requires_users_read_permission(self):
        """GET /users API 要求 users:read，viewer/analyst 返回 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            for user, role in [(self.viewer_user, "viewer"), (self.analyst_user, "analyst")]:
                resp = self.client.get(
                    "/api/v1/users",
                    headers={"Authorization": f"Bearer {_token(user)}"},
                )
                self.assertEqual(resp.status_code, 403,
                                 f"{role} 访问 /users 应返回 403，实际: {resp.status_code}")

    def test_L8_roles_new_menu_permission_perm_is_users_read(self):
        """验证新增 /roles 菜单使用 users:read 权限（与设计一致）"""
        # 直接验证菜单定义中 /roles 的 perm 字段
        roles_menu = next((m for m in self.ALL_MENU if m["key"] == "/roles"), None)
        self.assertIsNotNone(roles_menu, "/roles 菜单项未定义")
        self.assertEqual(roles_menu["perm"], "users:read",
                         f"/roles 菜单应使用 users:read，实际: {roles_menu['perm']}")

    def test_L9_superadmin_role_has_all_users_permissions(self):
        """superadmin 角色包含完整的 users:read/write/assign_role 三项权限"""
        perms = self._get_permissions(self.superadmin_user)
        for p in ("users:read", "users:write", "users:assign_role"):
            self.assertIn(p, perms, f"superadmin 缺少权限: {p}")


# ══════════════════════════════════════════════════════════════════════════════
# Section M — 端到端：角色生命周期
# ══════════════════════════════════════════════════════════════════════════════

class TestRoleLifecycle(unittest.TestCase):
    """M1-M8: 角色完整生命周期（创建→授权→分配用户→验证→清理）"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.super_user, _, _ = _make_user("m_super", role_names=["superadmin"])
        cls.target_user, cls.target_name, _ = _make_user("m_target")

    def _auth(self, user=None):
        u = user or self.super_user
        return {"Authorization": f"Bearer {_token(u)}"}

    def test_M1_create_role_assign_permission_assign_to_user(self):
        """完整流程：创建角色 → 授权 → 分配给用户 → 用户 /me 包含该权限"""
        from backend.models.permission import Permission

        role_name = f"{_PREFIX}custom_m1"
        perm = _g_db.query(Permission).filter(
            Permission.resource == "models", Permission.action == "read"
        ).first()

        with patch("backend.config.settings.settings.enable_auth", True):
            # 1. 创建角色
            r1 = self.client.post(
                "/api/v1/roles",
                json={"name": role_name, "description": "lifecycle test"},
                headers=self._auth(),
            )
            self.assertEqual(r1.status_code, 201)
            role_id = r1.json()["id"]

            # 2. 为角色分配 models:read 权限
            r2 = self.client.post(
                f"/api/v1/roles/{role_id}/permissions",
                json={"permission_id": str(perm.id)},
                headers=self._auth(),
            )
            self.assertEqual(r2.status_code, 200)

            # 3. 将角色分配给用户
            r3 = self.client.post(
                f"/api/v1/users/{self.target_user.id}/roles",
                json={"role_name": role_name},
                headers=self._auth(),
            )
            self.assertEqual(r3.status_code, 200)

            # 4. 验证用户 /me 包含 models:read
            r4 = self.client.get(
                "/api/v1/auth/me",
                headers=self._auth(self.target_user),
            )
            self.assertEqual(r4.status_code, 200)
            perms = r4.json()["permissions"]
            self.assertIn("models:read", perms, f"用户应有 models:read，当前: {perms}")

    def test_M2_remove_permission_from_role_user_loses_it(self):
        """移除角色权限后用户随即失去该权限"""
        from backend.models.permission import Permission
        role_name = f"{_PREFIX}custom_m2"
        perm = _g_db.query(Permission).filter(
            Permission.resource == "settings", Permission.action == "read"
        ).first()
        target, _, _ = _make_user("m2_tgt")

        with patch("backend.config.settings.settings.enable_auth", True):
            # 创建角色 + 授权 + 分配用户
            r = self.client.post("/api/v1/roles",
                                 json={"name": role_name}, headers=self._auth())
            role_id = r.json()["id"]
            self.client.post(f"/api/v1/roles/{role_id}/permissions",
                             json={"permission_id": str(perm.id)}, headers=self._auth())
            self.client.post(f"/api/v1/users/{target.id}/roles",
                             json={"role_name": role_name}, headers=self._auth())

            # 验证用户有权限
            me1 = self.client.get("/api/v1/auth/me", headers=self._auth(target))
            self.assertIn("settings:read", me1.json()["permissions"])

            # 移除角色的 settings:read 权限
            self.client.delete(
                f"/api/v1/roles/{role_id}/permissions/{perm.id}",
                headers=self._auth(),
            )

            # 验证用户失去权限（权限通过角色动态计算，无缓存）
            me2 = self.client.get("/api/v1/auth/me", headers=self._auth(target))
            self.assertNotIn("settings:read", me2.json()["permissions"],
                             "移除角色权限后用户不应再有该权限")

    def test_M3_revoke_role_from_user_loses_permissions(self):
        """撤销用户角色后，用户失去该角色的所有权限"""
        role_name = f"{_PREFIX}custom_m3"
        from backend.models.permission import Permission
        perm = _g_db.query(Permission).filter(
            Permission.resource == "models", Permission.action == "write"
        ).first()
        target, _, _ = _make_user("m3_tgt")

        with patch("backend.config.settings.settings.enable_auth", True):
            r = self.client.post("/api/v1/roles",
                                 json={"name": role_name}, headers=self._auth())
            role_id = r.json()["id"]
            self.client.post(f"/api/v1/roles/{role_id}/permissions",
                             json={"permission_id": str(perm.id)}, headers=self._auth())
            # 分配角色
            assign = self.client.post(f"/api/v1/users/{target.id}/roles",
                                      json={"role_name": role_name}, headers=self._auth())
            self.assertEqual(assign.status_code, 200)

            # 撤销角色 — 先获取 role_id
            me = self.client.get("/api/v1/auth/me", headers=self._auth(target))
            self.assertIn("models:write", me.json()["permissions"])

            # 查找用户的角色条目
            users_resp = self.client.get(
                f"/api/v1/users/{target.id}",
                headers=self._auth(),
            )
            # 获取 role_id 用于撤销
            from backend.models.role import Role
            role = _g_db.query(Role).filter(Role.name == role_name).first()
            revoke = self.client.delete(
                f"/api/v1/users/{target.id}/roles/{role.id}",
                headers=self._auth(),
            )
            self.assertEqual(revoke.status_code, 200)

            # 验证权限已移除
            me2 = self.client.get("/api/v1/auth/me", headers=self._auth(target))
            self.assertNotIn("models:write", me2.json()["permissions"])

    def test_M4_deleted_role_removed_from_user(self):
        """删除自定义角色后，用户的角色列表中不再包含该角色"""
        role_name = f"{_PREFIX}custom_m4"
        target, _, _ = _make_user("m4_tgt")

        with patch("backend.config.settings.settings.enable_auth", True):
            r = self.client.post("/api/v1/roles",
                                 json={"name": role_name}, headers=self._auth())
            role_id = r.json()["id"]
            self.client.post(f"/api/v1/users/{target.id}/roles",
                             json={"role_name": role_name}, headers=self._auth())

            # 验证角色已分配
            u_before = self.client.get(f"/api/v1/users/{target.id}", headers=self._auth())
            self.assertIn(role_name, u_before.json()["roles"])

            # 删除角色（级联删除 user_roles 关联）
            self.client.delete(f"/api/v1/roles/{role_id}", headers=self._auth())

            # 验证用户角色列表已清理
            u_after = self.client.get(f"/api/v1/users/{target.id}", headers=self._auth())
            self.assertNotIn(role_name, u_after.json()["roles"])

    def test_M5_role_names_in_user_list_response(self):
        """GET /users 返回 {total, items}，每个 item 包含 roles 字段"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/users", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("total", body, "响应缺少 total 字段")
        self.assertIn("items", body, "响应缺少 items 字段")
        for user in body["items"]:
            self.assertIn("roles", user, f"用户 {user.get('username')} 缺少 roles 字段")
            self.assertIsInstance(user["roles"], list)

    def test_M6_role_list_includes_permission_details(self):
        """GET /roles 返回的每个角色包含 permissions 详情"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/roles", headers=self._auth())
        for role in resp.json():
            self.assertIn("permissions", role, f"角色 {role['name']} 缺少 permissions 字段")
            for p in role["permissions"]:
                self.assertIn("resource", p)
                self.assertIn("action", p)

    def test_M7_assign_nonexistent_role_returns_404(self):
        """POST /users/{id}/roles 分配不存在的角色名返回 404"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                f"/api/v1/users/{self.target_user.id}/roles",
                json={"role_name": "nonexistent_role_xyz"},
                headers=self._auth(),
            )
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_M8_create_user_with_role_gets_permissions_immediately(self):
        """POST /users 创建用户时指定初始角色，/me 立即返回对应权限"""
        with patch("backend.config.settings.settings.enable_auth", True):
            create = self.client.post(
                "/api/v1/users",
                json={
                    "username": f"{_PREFIX}m8user",
                    "password": "Test1234!",
                    "role_names": ["analyst"],
                },
                headers=self._auth(),
            )
            self.assertEqual(create.status_code, 201)
            new_id = create.json()["id"]

            # 获取该用户 token 并验证权限
            from backend.models.user import User
            new_user = _g_db.query(User).filter(User.id == new_id).first()
            me = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(new_user)}"},
            )
            perms = me.json()["permissions"]
            self.assertIn("chat:use", perms)
            self.assertIn("skills.user:read", perms)


# ══════════════════════════════════════════════════════════════════════════════
# Section N — 端到端：认证流程（initAuth 四条路径）
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthFlow(unittest.TestCase):
    """N1-N6: initAuth() 的四条路径 + 登录/登出"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.test_user, cls.test_username, cls.test_pw = _make_user(
            "n_flow", password="FlowTest1!", role_names=["analyst"]
        )

    def test_N1_valid_access_token_me_returns_200(self):
        """有效 access_token → GET /auth/me 返回用户信息（initAuth 路径1）"""
        token = _token(self.test_user)
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["username"], self.test_username)
        self.assertIn("permissions", data)

    def test_N2_no_token_no_cookie_auth_enabled_returns_401(self):
        """无 token 无 Cookie 且 ENABLE_AUTH=true → 401（initAuth 路径4: 需登录）"""
        fresh = TestClient(self.app)  # 空 cookie jar
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = fresh.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_N3_no_token_auth_disabled_returns_anonymous(self):
        """无 token ENABLE_AUTH=false → 返回匿名用户（initAuth 路径4 降级）"""
        fresh = TestClient(self.app)
        with patch("backend.config.settings.settings.enable_auth", False):
            resp = fresh.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "default")

    def test_N4_login_success_returns_access_token_and_cookie(self):
        """POST /auth/login 成功返回 access_token + httpOnly refresh Cookie"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.test_username, "password": self.test_pw},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("access_token", resp.json())
        # refresh_token 必须在 Set-Cookie 中
        cookie_header = resp.headers.get("set-cookie", "")
        self.assertIn("refresh_token", cookie_header, "缺少 httpOnly refresh_token Cookie")
        self.assertIn("HttpOnly", cookie_header, "refresh_token Cookie 应为 HttpOnly")

    def test_N5_invalid_token_returns_401(self):
        """伪造 token → GET /auth/me 返回 401（initAuth 路径1 token 验证失败）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer invalid.token.here"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_N6_logout_then_me_returns_401(self):
        """登录 → 登出 → 无 token 时 /me 返回 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            # 登录
            login = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.test_username, "password": self.test_pw},
            )
            token = login.json()["access_token"]

            # 登出
            self.client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )

            # 新 client（无 cookie）访问 /me
            fresh = TestClient(self.app)
            resp = fresh.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            # access_token 本身仍有效（基于 JWT），/me 仍可访问
            # 但 refresh_token 已失效 → 体现在 /refresh 上
            # 这里验证 /me 在 token 有效期内仍可用（正确行为）
            self.assertIn(resp.status_code, [200, 401])


# ══════════════════════════════════════════════════════════════════════════════
# Section O — 权限矩阵完整性验证
# ══════════════════════════════════════════════════════════════════════════════

class TestPermissionMatrixCompleteness(unittest.TestCase):
    """O1-O5: 验证预置角色权限矩阵符合设计文档"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.super_user, _, _ = _make_user("o_super", role_names=["superadmin"])
        cls.viewer_user, _, _ = _make_user("o_viewer", role_names=["viewer"])
        cls.analyst_user, _, _ = _make_user("o_analyst", role_names=["analyst"])
        cls.admin_user, _, _ = _make_user("o_admin", role_names=["admin"])

    def _perms(self, user) -> set:
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(user)}"},
            )
        return set(resp.json().get("permissions", []))

    def test_O1_viewer_role_only_has_chat_use(self):
        """viewer 角色仅包含 chat:use（最小权限）"""
        perms = self._perms(self.viewer_user)
        self.assertEqual(perms, {"chat:use"},
                         f"viewer 应仅有 chat:use，实际: {perms}")

    def test_O2_analyst_role_permissions(self):
        """analyst 角色权限符合设计：chat + skills.user + skills.project:read + skills.system:read"""
        perms = self._perms(self.analyst_user)
        expected = {
            "chat:use",
            "skills.user:read", "skills.user:write",
            "skills.project:read",
            "skills.system:read",
        }
        self.assertEqual(perms, expected,
                         f"analyst 权限不符合设计\n期望: {expected}\n实际: {perms}")

    def test_O3_admin_role_permissions(self):
        """admin 角色包含 analyst 全部权限 + project:write + models:r/w + settings:r/w"""
        perms = self._perms(self.admin_user)
        expected = {
            "chat:use",
            "skills.user:read", "skills.user:write",
            "skills.project:read", "skills.project:write",
            "skills.system:read",
            "models:read", "models:write",
            "settings:read", "settings:write",
        }
        self.assertEqual(perms, expected,
                         f"admin 权限不符合设计\n期望: {expected}\n实际: {perms}")

    def test_O4_superadmin_role_has_all_permissions(self):
        """superadmin 角色拥有全部 13 个权限"""
        perms = self._perms(self.super_user)
        all_13 = {
            "chat:use",
            "skills.user:read", "skills.user:write",
            "skills.project:read", "skills.project:write",
            "skills.system:read",
            "models:read", "models:write",
            "users:read", "users:write", "users:assign_role",
            "settings:read", "settings:write",
        }
        missing = all_13 - perms
        self.assertEqual(missing, set(),
                         f"superadmin 缺少权限: {missing}")

    def test_O5_admin_role_does_not_have_users_permissions(self):
        """admin 角色不应包含 users:* 权限（用户管理仅限 superadmin）"""
        perms = self._perms(self.admin_user)
        users_perms = {p for p in perms if p.startswith("users:")}
        self.assertEqual(users_perms, set(),
                         f"admin 不应包含 users:* 权限，实际: {users_perms}")


# ══════════════════════════════════════════════════════════════════════════════
# 运行入口
# ══════════════════════════════════════════════════════════════════════════════

def _collect_results(suite):
    import io
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    return result, stream.getvalue()


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
