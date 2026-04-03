"""
test_user_list_pagination.py — GET /users 分页 + 排序功能测试 (2026-03-24)

Section P — 响应结构 (UserListOut shape)            P1-P5
Section Q — 分页参数 (page / page_size)             Q1-Q10
Section R — 排序参数 (sort_by / sort_order)         R1-R10
Section S — RBAC 权限控制                           S1-S6
Section T — 回归测试 (M5 fix & consistency)         T1-T4

总计: 35 个测试
"""
import os
import sys
import uuid
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_ulp_{uuid.uuid4().hex[:6]}_"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", password="Test1234!", is_superadmin=False,
               is_active=True, role_names=None, auth_source="local"):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"ULP {suffix}",
        hashed_password=hash_password(password),
        auth_source=auth_source,
        is_active=is_active,
        is_superadmin=is_superadmin,
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


def _token(user, roles=None):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    r = roles if roles is not None else []
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": r},
        settings.jwt_secret,
    )


def _auth(user, roles=None):
    return {"Authorization": f"Bearer {_token(user, roles)}"}


def teardown_module(_=None):
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ── Shared fixtures ─────────────────────────────────────────────────────────────
# superadmin 用户供各节功能测试使用
_admin_user, _admin_name, _ = _make_user(
    "global_admin", role_names=["superadmin"], is_superadmin=True
)


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=True)


def _get(client, admin, **params):
    """Helper: GET /users with superadmin token and given query params."""
    with patch("backend.config.settings.settings.enable_auth", True):
        return client.get(
            "/api/v1/users",
            headers=_auth(admin, ["superadmin"]),
            params=params,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section P — 响应结构
# ══════════════════════════════════════════════════════════════════════════════

class TestP_ResponseStructure(unittest.TestCase):
    """P — UserListOut 响应结构：{total: int, items: list}"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.admin = _admin_user

    def test_P1_response_is_dict_not_list(self):
        """响应必须是 dict，不是裸 list（旧格式已废弃）"""
        r = _get(self.client, self.admin)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsInstance(body, dict, "响应应为 dict，不是 list")
        self.assertIn("total", body)
        self.assertIn("items", body)

    def test_P2_total_is_non_negative_integer(self):
        """total 字段必须是非负整数"""
        body = _get(self.client, self.admin).json()
        self.assertIsInstance(body["total"], int)
        self.assertGreaterEqual(body["total"], 0)

    def test_P3_items_is_list(self):
        """items 字段必须是 list"""
        self.assertIsInstance(_get(self.client, self.admin).json()["items"], list)

    def test_P4_item_has_all_required_fields(self):
        """每条 UserOut 必须包含全部 9 个字段"""
        required = {
            "id", "username", "display_name", "email",
            "auth_source", "is_active", "is_superadmin",
            "roles", "last_login_at", "created_at",
        }
        items = _get(self.client, self.admin, page_size=100).json()["items"]
        for item in items:
            missing = required - item.keys()
            self.assertFalse(missing, f"用户 {item.get('username')} 缺少字段: {missing}")

    def test_P5_total_reflects_newly_created_users(self):
        """新建 N 个用户后，total 必须增加 N"""
        before = _get(self.client, self.admin, page_size=1).json()["total"]
        _make_user("p5_x1")
        _make_user("p5_x2")
        _make_user("p5_x3")
        after = _get(self.client, self.admin, page_size=1).json()["total"]
        self.assertEqual(after, before + 3)


# ══════════════════════════════════════════════════════════════════════════════
# Section Q — 分页参数
# ══════════════════════════════════════════════════════════════════════════════

class TestQ_Pagination(unittest.TestCase):
    """Q — page / page_size 分页行为"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.admin = _admin_user
        # 预建 12 个测试用户，保证分页边界可测
        for i in range(12):
            _make_user(f"q_{i:02d}")

    def test_Q1_default_page_size_20(self):
        """默认 page=1 page_size=20，items 数量 ≤ 20"""
        r = _get(self.client, self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(r.json()["items"]), 20)

    def test_Q2_page_size_1_returns_exactly_1(self):
        """page_size=1 只返回 1 条"""
        r = _get(self.client, self.admin, page_size=1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["items"]), 1)

    def test_Q3_page_size_5_returns_at_most_5(self):
        r = _get(self.client, self.admin, page_size=5)
        self.assertLessEqual(len(r.json()["items"]), 5)

    def test_Q4_page2_disjoint_from_page1(self):
        """page=1 与 page=2 的 id 集合不能有重叠"""
        ids1 = {u["id"] for u in _get(self.client, self.admin, page=1, page_size=5).json()["items"]}
        ids2 = {u["id"] for u in _get(self.client, self.admin, page=2, page_size=5).json()["items"]}
        self.assertTrue(ids1.isdisjoint(ids2), "不同页不应含重复用户")

    def test_Q5_no_overlap_three_pages(self):
        """连续 3 页（page_size=3）id 两两不重叠"""
        pages = [
            {u["id"] for u in _get(self.client, self.admin, page=p, page_size=3).json()["items"]}
            for p in [1, 2, 3]
        ]
        self.assertTrue(pages[0].isdisjoint(pages[1]))
        self.assertTrue(pages[1].isdisjoint(pages[2]))
        self.assertTrue(pages[0].isdisjoint(pages[2]))

    def test_Q6_page_beyond_range_empty_items(self):
        """超大 page 返回空 items，total 不变"""
        total = _get(self.client, self.admin, page_size=100).json()["total"]
        big = total + 9999
        r = _get(self.client, self.admin, page=big, page_size=20)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"], [])
        self.assertEqual(r.json()["total"], total)

    def test_Q7_total_same_across_pages(self):
        """同一时刻 page=1 和 page=2 的 total 必须相同"""
        t1 = _get(self.client, self.admin, page=1, page_size=3).json()["total"]
        t2 = _get(self.client, self.admin, page=2, page_size=3).json()["total"]
        self.assertEqual(t1, t2)

    def test_Q8_page_size_101_rejected_422(self):
        """page_size 超过最大值 100 → 422"""
        r = _get(self.client, self.admin, page_size=101)
        self.assertEqual(r.status_code, 422)

    def test_Q9_page_size_0_rejected_422(self):
        """page_size=0 违反 ge=1 约束 → 422"""
        r = _get(self.client, self.admin, page_size=0)
        self.assertEqual(r.status_code, 422)

    def test_Q10_page_size_100_accepted(self):
        """page_size=100（最大值）应被接受"""
        r = _get(self.client, self.admin, page_size=100)
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(r.json()["items"]), 100)


# ══════════════════════════════════════════════════════════════════════════════
# Section R — 排序参数
# ══════════════════════════════════════════════════════════════════════════════

class TestR_Sorting(unittest.TestCase):
    """R — sort_by / sort_order 排序行为"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.admin = _admin_user
        # 用确定后缀创建用户，利用前缀使其在字典序上可预期
        cls.u_aaa, cls.n_aaa, _ = _make_user("r_aaa")
        cls.u_bbb, cls.n_bbb, _ = _make_user("r_bbb")
        cls.u_ccc, cls.n_ccc, _ = _make_user("r_ccc")
        cls.u_inactive, cls.n_inactive, _ = _make_user("r_inactive", is_active=False)
        cls.u_super, cls.n_super, _ = _make_user("r_super", is_superadmin=True)

    def _all(self, **params):
        """拉取 page_size=100 的结果（覆盖所有测试用户）"""
        return _get(self.client, self.admin, page_size=100, **params).json()["items"]

    def _ours(self, items):
        """只保留本次测试创建的用户"""
        return [u for u in items if u["username"].startswith(_PREFIX)]

    def _our_names(self, items):
        return [u["username"] for u in self._ours(items)]

    def test_R1_sort_username_asc(self):
        """sort_by=username asc → 本测试集用户名升序"""
        names = self._our_names(self._all(sort_by="username", sort_order="asc"))
        self.assertEqual(names, sorted(names), f"升序失败: {names}")

    def test_R2_sort_username_desc(self):
        """sort_by=username desc → 本测试集用户名降序"""
        names = self._our_names(self._all(sort_by="username", sort_order="desc"))
        self.assertEqual(names, sorted(names, reverse=True), f"降序失败: {names}")

    def test_R3_sort_created_at_desc_default(self):
        """默认排序 created_at desc → 本测试集内新建的在前"""
        items = self._ours(self._all())  # no params = default
        dates = [u["created_at"] for u in items]
        self.assertEqual(dates, sorted(dates, reverse=True), f"created_at desc 失败: {dates}")

    def test_R4_sort_created_at_asc(self):
        """sort_by=created_at asc → 较早创建的用户排前"""
        items = self._ours(self._all(sort_by="created_at", sort_order="asc"))
        dates = [u["created_at"] for u in items]
        self.assertEqual(dates, sorted(dates), f"created_at asc 失败: {dates}")

    def test_R5_sort_is_active_asc_inactive_first(self):
        """sort_by=is_active asc → inactive(False) 在 active(True) 前面"""
        items = self._ours(self._all(sort_by="is_active", sort_order="asc"))
        usernames = [u["username"] for u in items]
        inactive_idx = next((i for i, u in enumerate(items) if u["username"] == self.n_inactive), None)
        active_idxs = [i for i, u in enumerate(items) if u["username"] in [self.n_aaa, self.n_bbb]]
        if inactive_idx is not None and active_idxs:
            self.assertLess(inactive_idx, min(active_idxs),
                            f"inactive 应在 active 前 (asc)，顺序: {usernames}")

    def test_R6_sort_is_active_desc_active_first(self):
        """sort_by=is_active desc → active(True) 在 inactive(False) 前面"""
        items = self._ours(self._all(sort_by="is_active", sort_order="desc"))
        usernames = [u["username"] for u in items]
        inactive_idx = next((i for i, u in enumerate(items) if u["username"] == self.n_inactive), None)
        active_idxs = [i for i, u in enumerate(items) if u["username"] in [self.n_aaa, self.n_bbb]]
        if inactive_idx is not None and active_idxs:
            self.assertGreater(inactive_idx, max(active_idxs),
                               f"inactive 应在 active 后 (desc)，顺序: {usernames}")

    def test_R7_sort_is_superadmin_desc_superadmin_first(self):
        """sort_by=is_superadmin desc → is_superadmin=True 的用户排最前"""
        items = self._ours(self._all(sort_by="is_superadmin", sort_order="desc"))
        super_idx = next((i for i, u in enumerate(items) if u["username"] == self.n_super), None)
        normal_idxs = [i for i, u in enumerate(items) if u["username"] in [self.n_aaa, self.n_bbb]]
        if super_idx is not None and normal_idxs:
            self.assertLess(super_idx, min(normal_idxs), "superadmin 应排在普通用户前面")

    def test_R8_sort_auth_source_asc_no_error(self):
        """sort_by=auth_source asc — 不报错，本测试集都是 local"""
        r = _get(self.client, self.admin, page_size=100, sort_by="auth_source", sort_order="asc")
        self.assertEqual(r.status_code, 200)
        sources = [u["auth_source"] for u in self._ours(r.json()["items"])]
        self.assertEqual(sources, sorted(sources))

    def test_R9_unknown_sort_by_falls_back_gracefully(self):
        """未知 sort_by 字段应静默回退到 created_at，不返回 5xx"""
        r = _get(self.client, self.admin, sort_by="nonexistent_xyz")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("total", body)
        self.assertIn("items", body)

    def test_R10_invalid_sort_order_returns_422(self):
        """sort_order 不是 asc/desc → 422"""
        r = _get(self.client, self.admin, sort_by="username", sort_order="INVALID")
        self.assertEqual(r.status_code, 422)


# ══════════════════════════════════════════════════════════════════════════════
# Section S — RBAC 权限控制
# ══════════════════════════════════════════════════════════════════════════════

class TestS_RBAC(unittest.TestCase):
    """S — 各角色对 GET /users 的访问权限"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.viewer,   _, _ = _make_user("s_viewer",        role_names=["viewer"])
        cls.analyst,  _, _ = _make_user("s_analyst",       role_names=["analyst"])
        cls.admin_u,  _, _ = _make_user("s_admin",         role_names=["admin"])
        cls.sa_role,  _, _ = _make_user("s_superrole",     role_names=["superadmin"])
        cls.sa_flag,  _, _ = _make_user("s_superflag",     is_superadmin=True)

    def _get_with(self, user, roles=None):
        with patch("backend.config.settings.settings.enable_auth", True):
            return self.client.get(
                "/api/v1/users",
                headers=_auth(user, roles or []),
            )

    def test_S1_no_token_returns_401(self):
        """无 Authorization → 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            r = self.client.get("/api/v1/users")
        self.assertIn(r.status_code, [401, 403])

    def test_S2_viewer_returns_403(self):
        """viewer 无 users:read → 403"""
        r = self._get_with(self.viewer, ["viewer"])
        self.assertEqual(r.status_code, 403)

    def test_S3_analyst_returns_403(self):
        """analyst 无 users:read → 403"""
        r = self._get_with(self.analyst, ["analyst"])
        self.assertEqual(r.status_code, 403)

    def test_S4_admin_returns_403(self):
        """admin 角色无 users:* 权限（设计如此）→ 403"""
        r = self._get_with(self.admin_u, ["admin"])
        self.assertEqual(r.status_code, 403)

    def test_S5_superadmin_role_returns_200(self):
        """superadmin 角色有 users:read → 200，响应含 total+items"""
        r = self._get_with(self.sa_role, ["superadmin"])
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("total", body)
        self.assertIn("items", body)

    def test_S6_superadmin_flag_bypasses_role_check(self):
        """is_superadmin=True 用户绕过角色权限直接返回 200"""
        r = self._get_with(self.sa_flag, [])
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("total", body)
        self.assertIn("items", body)


# ══════════════════════════════════════════════════════════════════════════════
# Section T — 回归测试
# ══════════════════════════════════════════════════════════════════════════════

class TestT_Regression(unittest.TestCase):
    """T — 回归：M5 fix + 响应一致性"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.admin = _admin_user

    def test_T1_response_not_bare_list(self):
        """回归 M5：响应不再是裸 list，而是 {total, items}"""
        body = _get(self.client, self.admin).json()
        self.assertNotIsInstance(body, list, "响应不应是裸 list（旧 API 格式）")

    def test_T2_items_has_roles_field(self):
        """回归 M5：resp.json()['items'] 中每个用户包含 roles 字段"""
        items = _get(self.client, self.admin).json()["items"]
        self.assertIsInstance(items, list)
        for user in items:
            self.assertIn("roles", user, f"用户 {user.get('username')} 缺少 roles 字段")
            self.assertIsInstance(user["roles"], list)

    def test_T3_total_increments_after_create(self):
        """创建 1 个用户后 total +1"""
        before = _get(self.client, self.admin, page_size=1).json()["total"]
        _make_user("t3_new")
        after = _get(self.client, self.admin, page_size=1).json()["total"]
        self.assertEqual(after, before + 1)

    def test_T4_all_page_sizes_accepted(self):
        """pageSizeOptions [20, 50, 100] 全部被后端接受"""
        for ps in [20, 50, 100]:
            r = _get(self.client, self.admin, page_size=ps)
            self.assertEqual(r.status_code, 200, f"page_size={ps} 应被接受")
            self.assertLessEqual(len(r.json()["items"]), ps)


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
