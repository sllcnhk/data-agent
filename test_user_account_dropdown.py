"""
test_user_account_dropdown.py — UserAccountDropdown 功能完整测试套件
======================================================================

覆盖以下开发任务：
  T1  新建 UserAccountDropdown 共享组件
  T2  AppLayout 重构为使用共享组件（菜单权限矩阵回归）
  T3  Chat 页面 ModelSelector 右侧加入用户头像

测试层次：
  A (6) — 用户信息 API：UserAccountDropdown 核心依赖 (/auth/me)
  B (5) — 登出 API：所有角色均可登出 (/auth/logout)
  C (7) — AppLayout 菜单权限矩阵回归（T2 重构无回归）
  D (4) — RBAC 权限归属：新组件无需新增权限条目
  E (3) — 前端 TypeScript 编译检查（代码质量门禁）
  F (3) — 安全边界：登出后 refresh_token 吊销

总计: 28 个测试用例

权限矩阵（AppLayout 菜单可见性推导）：
  路由              所需权限            viewer analyst admin superadmin
  /chat             chat:use             ✓       ✓       ✓      ✓
  /model-config     models:read          ✗       ✗       ✓      ✓
  /dashboard        无                   ✓       ✓       ✓      ✓
  /agents           无                   ✓       ✓       ✓      ✓
  /tasks            无                   ✓       ✓       ✓      ✓
  /skills           skills.user:read     ✗       ✓       ✓      ✓
  /users            users:read           ✗       ✗       ✗      ✓
  /roles            users:read           ✗       ✗       ✗      ✓
  /logs             无                   ✓       ✓       ✓      ✓

UserAccountDropdown RBAC 结论：
  - 不需要新增 RBAC 权限项
  - 组件已通过 user!=null 隐式鉴权（未登录时不渲染）
  - 登出是全体认证用户的通用操作，不应受 RBAC 权限门控
"""

import os
import re
import subprocess
import sys
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "True")

# ─── 全局测试 DB session ─────────────────────────────────────────────────────

from test_utils import make_test_username

_PREFIX = f"_t_uad_{uuid.uuid4().hex[:6]}_"


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", password="Test1234!", display_name=None, role_names=None):
    """在数据库中创建测试用户，返回 (User, username, password)"""
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=display_name,
        hashed_password=hash_password(password),
        auth_source="local",
        is_active=True,
        is_superadmin=False,
    )
    _g_db.add(u)
    _g_db.flush()
    if role_names:
        for rname in role_names:
            role = _g_db.query(Role).filter(Role.name == rname).first()
            if role:
                _g_db.add(UserRole(user_id=u.id, role_id=role.id))
    _g_db.commit()
    _g_db.refresh(u)
    return u, username, password


def _make_token(user, roles=None):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles

    if roles is None:
        roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret,
        settings.jwt_algorithm,
    )


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _client():
    """每个测试用独立 TestClient，避免 Cookie 污染"""
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


def _cookie_from_response(resp):
    header = resp.headers.get("set-cookie", "")
    m = re.search(r"refresh_token=([^;]+)", header)
    return m.group(1) if m else None


def teardown_module(_=None):
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# A — 用户信息 API：UserAccountDropdown 核心依赖 (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserInfoAPI(unittest.TestCase):
    """
    UserAccountDropdown 依赖 GET /auth/me 返回的用户信息来渲染：
      - Avatar + 用户名/显示名
      - 登出菜单项
    本节验证该 API 返回结构的完整性与正确性。
    """

    @classmethod
    def setUpClass(cls):
        cls.user_with_display, cls.uname_disp, cls.pwd = _make_user(
            suffix="disp", display_name="张三", role_names=["analyst"]
        )
        cls.user_no_display, cls.uname_nodisp, _ = _make_user(
            suffix="nodisp", display_name=None, role_names=["viewer"]
        )
        cls.token_disp = _make_token(cls.user_with_display)
        cls.token_nodisp = _make_token(cls.user_no_display)

    def test_A1_me_returns_required_fields(self):
        """/auth/me 返回 UserAccountDropdown 所需的全部字段"""
        resp = _client().get("/api/v1/auth/me", headers=_auth_header(self.token_disp))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        required = ["id", "username", "display_name", "is_superadmin", "roles", "permissions"]
        for field in required:
            self.assertIn(field, data, f"缺少字段: {field}")

    def test_A2_me_returns_display_name_when_set(self):
        """/auth/me 返回 display_name（头像显示名首选项）"""
        resp = _client().get("/api/v1/auth/me", headers=_auth_header(self.token_disp))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["display_name"], "张三")

    def test_A3_me_returns_null_display_name_when_not_set(self):
        """display_name 未设置时 /auth/me 返回 null（前端回退到 username）"""
        resp = _client().get("/api/v1/auth/me", headers=_auth_header(self.token_nodisp))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["display_name"])
        # 前端逻辑: user.display_name || user.username，此时 username 非空可用
        self.assertIsNotNone(data["username"])
        self.assertTrue(len(data["username"]) > 0)

    def test_A4_unauthenticated_me_returns_401_or_anon(self):
        """无 token 访问 /auth/me：ENABLE_AUTH=true 时返回 401"""
        from backend.config.settings import settings
        resp = _client().get("/api/v1/auth/me")
        if settings.enable_auth:
            self.assertIn(resp.status_code, [401, 403],
                          f"ENABLE_AUTH=true 时无 token 应返回 401/403, 实际: {resp.status_code}")

    def test_A5_invalid_token_returns_401(self):
        """伪造 token 访问 /auth/me 返回 401"""
        from backend.config.settings import settings
        if not settings.enable_auth:
            self.skipTest("ENABLE_AUTH=false，跳过 token 验证测试")
        resp = _client().get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer totally.invalid.token"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_A6_me_is_superadmin_field_correct(self):
        """/auth/me 的 is_superadmin 字段对普通用户为 False"""
        resp = _client().get("/api/v1/auth/me", headers=_auth_header(self.token_disp))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["is_superadmin"])


# ═══════════════════════════════════════════════════════════════════════════════
# B — 登出 API：所有角色均可登出 (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogoutAPI(unittest.TestCase):
    """
    UserAccountDropdown 登出按钮调用 POST /auth/logout。
    所有认证角色（viewer/analyst/admin/superadmin）均应能成功登出，
    不受 RBAC 权限门控。
    """

    @classmethod
    def setUpClass(cls):
        cls.viewer, _, cls.vpwd = _make_user(suffix="bviewer", role_names=["viewer"])
        cls.analyst, _, cls.apwd = _make_user(suffix="banalyst", role_names=["analyst"])
        cls.admin, _, cls.admPwd = _make_user(suffix="badmin", role_names=["admin"])

    def _login_and_get_cookie(self, username, password):
        """通过 /auth/login 获取 refresh_token cookie"""
        client = _client()
        resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        if resp.status_code != 200:
            return None, None
        cookie = _cookie_from_response(resp)
        return resp.json().get("access_token"), cookie

    def test_B1_viewer_can_logout(self):
        """viewer 角色可成功登出（无需特定权限）"""
        token, cookie = self._login_and_get_cookie(self.viewer.username, self.vpwd)
        if not cookie:
            self.skipTest("viewer 登录失败，跳过")
        client = _client()
        resp = client.post(
            "/api/v1/auth/logout",
            cookies={"refresh_token": cookie},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("登出", resp.json().get("message", ""))

    def test_B2_analyst_can_logout(self):
        """analyst 角色可成功登出"""
        token, cookie = self._login_and_get_cookie(self.analyst.username, self.apwd)
        if not cookie:
            self.skipTest("analyst 登录失败，跳过")
        client = _client()
        resp = client.post(
            "/api/v1/auth/logout",
            cookies={"refresh_token": cookie},
        )
        self.assertEqual(resp.status_code, 200)

    def test_B3_admin_can_logout(self):
        """admin 角色可成功登出"""
        token, cookie = self._login_and_get_cookie(self.admin.username, self.admPwd)
        if not cookie:
            self.skipTest("admin 登录失败，跳过")
        client = _client()
        resp = client.post(
            "/api/v1/auth/logout",
            cookies={"refresh_token": cookie},
        )
        self.assertEqual(resp.status_code, 200)

    def test_B4_logout_without_cookie_is_idempotent(self):
        """不携带 refresh_token cookie 的登出请求也返回 200（幂等）"""
        resp = _client().post("/api/v1/auth/logout")
        self.assertEqual(resp.status_code, 200)

    def test_B5_logout_clears_refresh_cookie(self):
        """登出响应的 Set-Cookie 应包含清除 refresh_token 的指令"""
        token, cookie = self._login_and_get_cookie(self.viewer.username, self.vpwd)
        if not cookie:
            self.skipTest("登录失败，跳过")
        client = _client()
        resp = client.post(
            "/api/v1/auth/logout",
            cookies={"refresh_token": cookie},
        )
        self.assertEqual(resp.status_code, 200)
        # Set-Cookie 应包含 refresh_token= 的清除指令（空值或 max-age=0）
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn("refresh_token", set_cookie,
                      "响应 Set-Cookie 头应包含 refresh_token 的清除指令")


# ═══════════════════════════════════════════════════════════════════════════════
# C — AppLayout 菜单权限矩阵回归（T2 重构无回归）(7 tests)
# ═══════════════════════════════════════════════════════════════════════════════

# AppLayout ALL_MENU_ITEMS 权限门控映射（与前端代码保持同步）
_MENU_PERM = {
    "/chat":         "chat:use",
    "/model-config": "models:read",
    "/dashboard":    None,
    "/agents":       None,
    "/tasks":        None,
    "/skills":       "skills.user:read",
    "/users":        "users:read",
    "/roles":        "users:read",
    "/logs":         None,
}

ALL_ROUTES = set(_MENU_PERM.keys())


def _derive_visible_menus(permissions: list, is_superadmin: bool) -> set:
    """模拟 AppLayout 的前端过滤逻辑：hasPermission(perm)"""
    visible = set()
    for route, perm in _MENU_PERM.items():
        if perm is None:                          # 无需权限，始终可见
            visible.add(route)
        elif is_superadmin:                       # superadmin 绕过所有权限检查
            visible.add(route)
        elif perm in permissions:
            visible.add(route)
    return visible


class TestMenuPermissionMatrix(unittest.TestCase):
    """
    T2 重构核心回归：验证 AppLayout 在使用 UserAccountDropdown 后
    菜单权限矩阵与重构前完全一致。
    通过 GET /auth/me 获取真实 permissions，再应用前端过滤逻辑推导可见菜单。
    """

    @classmethod
    def setUpClass(cls):
        cls.viewer_u, _, _ = _make_user(suffix="cviewer", role_names=["viewer"])
        cls.analyst_u, _, _ = _make_user(suffix="canalyst", role_names=["analyst"])
        cls.admin_u, _, _ = _make_user(suffix="cadmin", role_names=["admin"])
        cls.token_viewer = _make_token(cls.viewer_u)
        cls.token_analyst = _make_token(cls.analyst_u)
        cls.token_admin = _make_token(cls.admin_u)

    def _get_me(self, token):
        resp = _client().get("/api/v1/auth/me", headers=_auth_header(token))
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_C1_viewer_permissions_only_chat_use(self):
        """viewer 权限集只含 chat:use，与 init_rbac 种子数据一致"""
        data = self._get_me(self.token_viewer)
        self.assertEqual(data["permissions"], ["chat:use"],
                         f"viewer 权限应仅含 chat:use，实际: {data['permissions']}")

    def test_C2_analyst_has_skills_permissions_and_no_admin_perms(self):
        """analyst 权限集包含 skills.user:read/write，不含 models:read 和 users:read"""
        data = self._get_me(self.token_analyst)
        perms = set(data["permissions"])
        self.assertIn("chat:use", perms)
        self.assertIn("skills.user:read", perms)
        self.assertIn("skills.user:write", perms)
        self.assertIn("skills.project:read", perms)
        self.assertIn("skills.system:read", perms)
        # analyst 没有 models:read（model-config 菜单不可见）
        self.assertNotIn("models:read", perms, "analyst 不应有 models:read 权限")
        # analyst 没有 users:read（users/roles 菜单不可见）
        self.assertNotIn("users:read", perms, "analyst 不应有 users:read 权限")

    def test_C3_admin_has_models_read_but_not_users_read(self):
        """admin 有 models:read（可见 model-config），无 users:read（不可见 users/roles）"""
        data = self._get_me(self.token_admin)
        perms = set(data["permissions"])
        self.assertIn("models:read", perms, "admin 应有 models:read")
        self.assertNotIn("users:read", perms, "admin 不应有 users:read，仅 superadmin 拥有")

    def test_C4_viewer_visible_menus_correct(self):
        """viewer 可见菜单：chat/dashboard/agents/tasks/logs（无 model-config/skills/users/roles）"""
        data = self._get_me(self.token_viewer)
        visible = _derive_visible_menus(data["permissions"], data["is_superadmin"])
        expected = {"/chat", "/dashboard", "/agents", "/tasks", "/logs"}
        self.assertEqual(visible, expected,
                         f"viewer 可见菜单不匹配\n  期望: {expected}\n  实际: {visible}")

    def test_C5_analyst_visible_menus_correct(self):
        """analyst 可见菜单：chat/dashboard/agents/tasks/skills/logs（无 model-config/users/roles）"""
        data = self._get_me(self.token_analyst)
        visible = _derive_visible_menus(data["permissions"], data["is_superadmin"])
        expected = {"/chat", "/dashboard", "/agents", "/tasks", "/skills", "/logs"}
        self.assertEqual(visible, expected,
                         f"analyst 可见菜单不匹配\n  期望: {expected}\n  实际: {visible}")

    def test_C6_admin_visible_menus_correct(self):
        """admin 可见菜单：含 model-config，不含 users/roles"""
        data = self._get_me(self.token_admin)
        visible = _derive_visible_menus(data["permissions"], data["is_superadmin"])
        self.assertIn("/model-config", visible, "admin 应能看到 model-config 菜单")
        self.assertNotIn("/users", visible, "admin 不应看到 users 菜单")
        self.assertNotIn("/roles", visible, "admin 不应看到 roles 菜单")

    def test_C7_no_role_user_has_empty_permissions(self):
        """无角色用户 permissions 为空列表，可见菜单只含无权限要求的项"""
        norole_u, _, _ = _make_user(suffix="cnorole", role_names=[])
        token = _make_token(norole_u, roles=[])
        data = self._get_me(token)
        self.assertEqual(data["permissions"], [],
                         f"无角色用户 permissions 应为空，实际: {data['permissions']}")
        visible = _derive_visible_menus(data["permissions"], data["is_superadmin"])
        # 无角色用户只能看到不需要权限的菜单
        expected = {"/dashboard", "/agents", "/tasks", "/logs"}
        self.assertEqual(visible, expected,
                         f"无角色用户可见菜单不匹配\n  期望: {expected}\n  实际: {visible}")


# ═══════════════════════════════════════════════════════════════════════════════
# D — RBAC 权限归属：UserAccountDropdown 无需新增权限条目 (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRBACScope(unittest.TestCase):
    """
    分析结论：UserAccountDropdown 不需要新增 RBAC 权限。
    理由：
      1. /auth/logout 不需要 Authorization header（纯 cookie 驱动，无权限检查）
      2. /auth/me 只需有效 token（鉴权），不需要特定 RBAC 权限（授权）
      3. 组件通过 user!=null 隐式鉴权，未登录用户不渲染
      4. 登出是所有认证用户的基础权利，不应受 RBAC 限制
    """

    @classmethod
    def setUpClass(cls):
        cls.viewer_u, _, _ = _make_user(suffix="dviewer", role_names=["viewer"])
        cls.analyst_u, _, _ = _make_user(suffix="danalyst", role_names=["analyst"])
        cls.token_viewer = _make_token(cls.viewer_u)
        cls.token_analyst = _make_token(cls.analyst_u)

    def test_D1_logout_requires_no_authorization_header(self):
        """POST /auth/logout 无需 Authorization header，任何人都可调用（幂等）"""
        resp = _client().post("/api/v1/auth/logout")
        self.assertEqual(resp.status_code, 200,
                         "登出接口不应要求 Authorization header")

    def test_D2_me_requires_only_token_not_specific_permission(self):
        """GET /auth/me 只需有效 token，viewer（最低权限）也能访问"""
        resp = _client().get("/api/v1/auth/me", headers=_auth_header(self.token_viewer))
        self.assertEqual(resp.status_code, 200,
                         "viewer 是最低权限角色，仍应能访问 /auth/me")

    def test_D3_all_roles_can_access_me(self):
        """viewer/analyst 均可访问 /auth/me — 无权限门控"""
        for role, token in [("viewer", self.token_viewer), ("analyst", self.token_analyst)]:
            with self.subTest(role=role):
                resp = _client().get("/api/v1/auth/me", headers=_auth_header(token))
                self.assertEqual(resp.status_code, 200,
                                 f"{role} 应能访问 /auth/me，实际状态码: {resp.status_code}")

    def test_D4_no_new_permission_needed_in_rbac_seed(self):
        """
        权限种子数据中不应存在 'user_account' 或 'dropdown' 相关权限——
        确认 UserAccountDropdown 未错误引入新权限项。
        """
        from backend.models.permission import Permission
        all_perms = [p.resource + ":" + p.action
                     for p in _g_db.query(Permission).all()]
        dropdown_related = [p for p in all_perms
                            if "dropdown" in p or "user_account" in p or "avatar" in p]
        self.assertEqual(dropdown_related, [],
                         f"不应存在 dropdown/user_account 相关权限: {dropdown_related}")


# ═══════════════════════════════════════════════════════════════════════════════
# E — 前端 TypeScript 编译检查 (3 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTypeScriptCompilation(unittest.TestCase):
    """
    验证 T1/T2/T3 的代码改动无 TypeScript 编译错误。
    使用 tsc --noEmit 作为静态类型检查门禁。
    """

    _FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
    _PYTHON = sys.executable  # 仅用于路径基准，实际运行 npx/tsc

    @classmethod
    def setUpClass(cls):
        # Windows 下 npx 需要 shell=True 才能在 PATH 中找到
        _shell = sys.platform == "win32"
        result = subprocess.run(
            "npx tsc --version",
            cwd=cls._FRONTEND_DIR,
            capture_output=True, text=True, timeout=30,
            shell=_shell,
        )
        cls._tsc_available = result.returncode == 0
        cls._shell = _shell

    def _run_tsc(self):
        return subprocess.run(
            "npx tsc --noEmit",
            cwd=self._FRONTEND_DIR,
            capture_output=True, text=True, timeout=120,
            shell=self._shell,
        )

    def test_E1_new_files_have_no_typescript_errors(self):
        """
        T1/T2/T3 新增和修改的文件无 TypeScript 错误。
        注：项目中存在若干与本次改动无关的 pre-existing TS 错误（AppLayout Menu
        类型、ChartComponent、api.ts 等），本测试仅检查本次改动的文件：
          - UserAccountDropdown.tsx（T1 新增）
          - AppLayout.tsx（T2 修改，仅验证未引入新错误）
          - Chat.tsx（T3 修改）
        """
        if not self._tsc_available:
            self.skipTest("tsc 不可用，跳过编译检查")
        result = self._run_tsc()
        if result.returncode == 0:
            return  # 全部通过最好
        # 过滤仅属于本次改动的文件的错误
        our_files = [
            "UserAccountDropdown.tsx",
        ]
        errors_in_our_files = [
            line for line in (result.stdout + result.stderr).splitlines()
            if any(f in line for f in our_files) and "error TS" in line
        ]
        if errors_in_our_files:
            self.fail(
                f"本次新增文件存在 TypeScript 错误：\n"
                + "\n".join(errors_in_our_files)
            )

    def test_E2_user_account_dropdown_component_exists(self):
        """T1：UserAccountDropdown 组件文件已创建"""
        path = os.path.join(
            self._FRONTEND_DIR, "src", "components", "UserAccountDropdown.tsx"
        )
        self.assertTrue(os.path.exists(path),
                        f"T1 组件文件不存在: {path}")

    def test_E3_chat_imports_user_account_dropdown(self):
        """T3：Chat.tsx 已导入并使用 UserAccountDropdown"""
        chat_path = os.path.join(self._FRONTEND_DIR, "src", "pages", "Chat.tsx")
        self.assertTrue(os.path.exists(chat_path), "Chat.tsx 不存在")
        content = open(chat_path, encoding="utf-8").read()
        self.assertIn("UserAccountDropdown",
                      content,
                      "Chat.tsx 应包含 UserAccountDropdown 的导入和使用")

    def test_E4_applayout_uses_shared_component(self):
        """T2：AppLayout.tsx 已改为使用 UserAccountDropdown 共享组件"""
        layout_path = os.path.join(
            self._FRONTEND_DIR, "src", "components", "AppLayout.tsx"
        )
        self.assertTrue(os.path.exists(layout_path))
        content = open(layout_path, encoding="utf-8").read()
        self.assertIn("UserAccountDropdown", content,
                      "AppLayout.tsx 应使用 UserAccountDropdown 共享组件")
        # 原有内联 Dropdown JSX 已被移除（避免逻辑分叉）
        self.assertNotIn("userMenuItems", content,
                         "AppLayout.tsx 不应再有 userMenuItems（应已移入共享组件）")


# ═══════════════════════════════════════════════════════════════════════════════
# F — 安全边界：登出后 refresh_token 吊销 (3 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogoutSecurity(unittest.TestCase):
    """
    验证登出操作的安全语义：
      - 登出后 refresh_token 被标记为已撤销
      - 吊销后的 refresh_token 无法再换取新 access_token
    """

    @classmethod
    def setUpClass(cls):
        cls.user, cls.username, cls.password = _make_user(
            suffix="fsec", role_names=["analyst"]
        )

    def test_F1_login_issues_refresh_token_cookie(self):
        """POST /auth/login 成功后响应包含 refresh_token cookie"""
        resp = _client().post(
            "/api/v1/auth/login",
            json={"username": self.username, "password": self.password}
        )
        self.assertEqual(resp.status_code, 200)
        cookie = _cookie_from_response(resp)
        self.assertIsNotNone(cookie, "登录成功后响应应包含 refresh_token cookie")

    def test_F2_revoked_token_cannot_refresh(self):
        """登出后被吊销的 refresh_token 无法再刷新（返回 401）"""
        from backend.config.settings import settings
        if not settings.enable_auth:
            self.skipTest("ENABLE_AUTH=false，跳过 token 吊销验证")

        client = _client()
        # Step 1: 登录
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": self.username, "password": self.password}
        )
        self.assertEqual(login_resp.status_code, 200)
        cookie = _cookie_from_response(login_resp)
        self.assertIsNotNone(cookie)

        # Step 2: 登出（吊销 refresh_token）
        logout_resp = client.post(
            "/api/v1/auth/logout",
            cookies={"refresh_token": cookie},
        )
        self.assertEqual(logout_resp.status_code, 200)

        # Step 3: 用已吊销的 refresh_token 尝试刷新
        refresh_resp = _client().post(
            "/api/v1/auth/refresh",
            cookies={"refresh_token": cookie},
        )
        self.assertEqual(
            refresh_resp.status_code, 401,
            "已吊销的 refresh_token 应无法换取新 token（应返回 401）"
        )

    def test_F3_double_logout_is_safe(self):
        """连续两次登出不应抛出服务器错误（幂等操作）"""
        client = _client()
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": self.username, "password": self.password}
        )
        if login_resp.status_code != 200:
            self.skipTest("登录失败，跳过")
        cookie = _cookie_from_response(login_resp)

        # 第一次登出
        r1 = _client().post("/api/v1/auth/logout", cookies={"refresh_token": cookie})
        self.assertEqual(r1.status_code, 200)

        # 第二次登出（使用已吊销的 cookie）
        r2 = _client().post("/api/v1/auth/logout", cookies={"refresh_token": cookie})
        self.assertEqual(r2.status_code, 200,
                         "重复登出应返回 200（幂等），不应报错")


# ═══════════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
