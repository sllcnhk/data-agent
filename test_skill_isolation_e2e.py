"""
test_skill_isolation_e2e.py — 用户技能目录隔离 E2E 测试
=======================================================

覆盖 Bug 修复: user skill 文件写入路径错误
  旧行为: _get_user_skill_dir("default") → flat .claude/skills/user/
          Agent MCP 写入相对路径 → 落到 customer_data/.claude/skills/user/
  修复后: ENABLE_AUTH=true → 始终写入 user/{username}/; 注入 CURRENT_USER 到 system prompt

注意: backend/main.py 使用 `from api import skills`，
      因此运行时模块为 `api.skills`，REST API 测试需 patch `api.skills.*`；
      单元测试直接 import `backend.api.skills` 则 patch `backend.api.skills.*`。

测试分区:
  A (5)  — 单元: _get_user_skill_dir() 路径逻辑
  B (8)  — REST API: CRUD + 文件路径实际验证
  C (6)  — 跨用户隔离: Alice 不能访问 Bob 的技能
  D (6)  — 权限矩阵: viewer/analyst/admin/superadmin
  E (5)  — Context 注入链: username → context dict → system prompt CURRENT_USER
  F (4)  — ENABLE_AUTH=false 向后兼容
  G (5)  — 菜单权限范围: /skills 使用 skills.user:read
  H (4)  — init_rbac: 各角色技能权限覆盖

总计: 43 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 必须在 backend 包导入前设置 env ────────────────────────────────────────────
os.environ.setdefault("ENABLE_AUTH", "False")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ── 全局测试数据前缀 ───────────────────────────────────────────────────────────
_PREFIX = f"_sk_{uuid.uuid4().hex[:6]}_"

# ── DB helpers ─────────────────────────────────────────────────────────────────
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
        display_name=f"Test {suffix}",
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


def teardown_module(_=None):
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ── FastAPI TestClient ─────────────────────────────────────────────────────────
from fastapi.testclient import TestClient

def _make_app():
    from backend.main import app
    return app


# ── Skill 创建 payload ─────────────────────────────────────────────────────────
def _skill_payload(suffix=""):
    slug = f"test-skill-{suffix or uuid.uuid4().hex[:6]}"
    return {
        "name": slug,
        "description": f"Test skill {suffix}",
        "triggers": [f"trigger-{suffix}"],
        "category": "general",
        "priority": "medium",
        "content": f"# Test\nContent for {slug}",
    }


# ── REST API 测试用 patch 目标
# main.py 使用 `from api import skills`，运行时模块键为 `api.skills`
_SKILLS_MODULE = "api.skills"


# ══════════════════════════════════════════════════════════════════════════════
# A — 单元: _get_user_skill_dir() 核心路径逻辑
# (直接 import backend.api.skills, patch backend.api.skills.*)
# ══════════════════════════════════════════════════════════════════════════════

class TestGetUserSkillDir(unittest.TestCase):
    """A1-A5: _get_user_skill_dir() 在不同 ENABLE_AUTH 场景下的路径行为"""

    def _call(self, username, enable_auth, tmp_user_dir):
        mock_settings = MagicMock()
        mock_settings.enable_auth = enable_auth
        with patch("backend.api.skills.settings", mock_settings), \
             patch("backend.api.skills._USER_SKILLS_DIR", tmp_user_dir):
            from backend.api.skills import _get_user_skill_dir
            return _get_user_skill_dir(username)

    def test_A1_auth_enabled_regular_user_gets_subdir(self):
        """A1: ENABLE_AUTH=true 普通用户 → user/{username}/ 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            result = self._call("alice", enable_auth=True, tmp_user_dir=user_dir)
            self.assertEqual(result, user_dir / "alice")
            self.assertTrue(result.is_dir(), "子目录应被自动创建")

    def test_A2_auth_enabled_default_user_also_gets_subdir(self):
        """A2: ENABLE_AUTH=true username='default' 也进入子目录，不落 flat root (Bug 修复验证)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            result = self._call("default", enable_auth=True, tmp_user_dir=user_dir)
            self.assertNotEqual(result, user_dir, "BUG: 不应返回 flat root")
            self.assertEqual(result, user_dir / "default")

    def test_A3_auth_disabled_all_users_get_flat_dir(self):
        """A3: ENABLE_AUTH=false → 所有用户使用 flat user/ 目录（向后兼容）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            for uname in ["alice", "bob", "default"]:
                result = self._call(uname, enable_auth=False, tmp_user_dir=user_dir)
                self.assertEqual(result, user_dir,
                                 f"ENABLE_AUTH=false 时 {uname} 应使用 flat dir")

    def test_A4_different_users_get_different_dirs(self):
        """A4: ENABLE_AUTH=true 不同用户目录互相独立"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            alice_dir = self._call("alice", enable_auth=True, tmp_user_dir=user_dir)
            bob_dir   = self._call("bob",   enable_auth=True, tmp_user_dir=user_dir)
            self.assertNotEqual(alice_dir, bob_dir)
            self.assertEqual(alice_dir, user_dir / "alice")
            self.assertEqual(bob_dir,   user_dir / "bob")

    def test_A5_subdir_auto_created_if_not_exists(self):
        """A5: ENABLE_AUTH=true 子目录不存在时也会自动创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            self.assertFalse((user_dir / "newuser").exists())
            self._call("newuser", enable_auth=True, tmp_user_dir=user_dir)
            self.assertTrue((user_dir / "newuser").is_dir())


# ══════════════════════════════════════════════════════════════════════════════
# B — REST API: CRUD 操作 + 实际文件路径验证
# patch `api.skills.*` (运行时模块键)
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillCRUDWithFilePath(unittest.TestCase):
    """B1-B8: 通过 REST API 创建/列出/更新/删除技能，验证文件落在正确目录"""

    @classmethod
    def setUpClass(cls):
        cls.app    = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.analyst = _make_user("b_analyst", role_names=["analyst"])

    def _auth(self, user):
        return {"Authorization": f"Bearer {_token(user)}"}

    def _create_skill_patched(self, payload, user, user_dir):
        """Helper: POST /skills/user-defined with patched _USER_SKILLS_DIR"""
        with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
             patch("backend.config.settings.settings.enable_auth", True):
            return self.client.post(
                "/api/v1/skills/user-defined",
                json=payload,
                headers=self._auth(user),
            )

    def test_B1_create_skill_file_lands_in_user_subdir(self):
        """B1: ENABLE_AUTH=true 创建技能 → 文件落在 user/{username}/ 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("b1")
            resp = self._create_skill_patched(payload, self.analyst, user_dir)
            self.assertEqual(resp.status_code, 201, f"创建失败: {resp.text}")
            slug = resp.json()["name"]
            expected = user_dir / self.analyst.username / f"{slug}.md"
            self.assertTrue(expected.exists(),
                            f"文件应在 user/{self.analyst.username}/{slug}.md")

    def test_B2_create_skill_file_does_not_land_in_flat_root(self):
        """B2: ENABLE_AUTH=true 创建的文件不应出现在 flat user/ 根"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("b2")
            resp = self._create_skill_patched(payload, self.analyst, user_dir)
            self.assertEqual(resp.status_code, 201)
            slug = resp.json()["name"]
            self.assertFalse((user_dir / f"{slug}.md").exists(),
                             "文件不应在 flat user/ 根")

    def test_B3_list_user_skills_only_sees_own_files(self):
        """B3: ENABLE_AUTH=true 列出技能只看到自己目录的文件"""
        other = _make_user("b3_other", role_names=["analyst"])
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            alice_dir = user_dir / self.analyst.username
            other_dir = user_dir / other.username
            alice_dir.mkdir(); other_dir.mkdir()

            for name, d in [("alice-skill", alice_dir), ("other-skill", other_dir)]:
                (d / f"{name}.md").write_text(
                    f"---\nname: {name}\nversion: \"1.0\"\ndescription: {name}\n"
                    f"triggers:\n  - {name}\ncategory: general\npriority: medium\n---\ncontent",
                    encoding="utf-8",
                )
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.get("/api/v1/skills/user-defined",
                                       headers=self._auth(self.analyst))
            self.assertEqual(resp.status_code, 200)
            names = {s["name"] for s in resp.json()}
            self.assertIn("alice-skill",  names, "应看到自己的 skill")
            self.assertNotIn("other-skill", names, "不应看到他人的 skill")

    def test_B4_delete_own_skill_succeeds(self):
        """B4: 删除自己的技能 → 200 且文件消失"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("b4")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                r = self.client.post("/api/v1/skills/user-defined",
                                     json=payload, headers=self._auth(self.analyst))
                self.assertEqual(r.status_code, 201)
                slug = r.json()["name"]
                resp = self.client.delete(
                    f"/api/v1/skills/user-defined/{slug}",
                    headers=self._auth(self.analyst),
                )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse((user_dir / self.analyst.username / f"{slug}.md").exists())

    def test_B5_update_own_skill_version_bumped(self):
        """B5: 更新自己的技能 → 版本递增，文件内容正确更新"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("b5")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                r = self.client.post("/api/v1/skills/user-defined",
                                     json=payload, headers=self._auth(self.analyst))
                self.assertEqual(r.status_code, 201)
                slug = r.json()["name"]
                resp = self.client.put(
                    f"/api/v1/skills/user-defined/{slug}",
                    json={"description": "updated desc"},
                    headers=self._auth(self.analyst),
                )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["version"], "1.1")
            content = (user_dir / self.analyst.username / f"{slug}.md").read_text(encoding="utf-8")
            self.assertIn("updated desc", content)

    def test_B6_conflict_409_if_skill_already_exists(self):
        """B6: 重复创建同名技能 → 409 Conflict"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("b6dup")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                self.client.post("/api/v1/skills/user-defined",
                                 json=payload, headers=self._auth(self.analyst))
                resp = self.client.post("/api/v1/skills/user-defined",
                                        json=payload, headers=self._auth(self.analyst))
            self.assertEqual(resp.status_code, 409)

    def test_B7_delete_nonexistent_skill_returns_404(self):
        """B7: 删除不存在的技能 → 404"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.delete(
                    "/api/v1/skills/user-defined/no-such-skill-xyz",
                    headers=self._auth(self.analyst),
                )
            self.assertEqual(resp.status_code, 404)

    def test_B8_filepath_in_create_response_contains_username(self):
        """B8: 创建响应中 filepath 字段包含用户名（路径可追溯）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("b8")
            resp = self._create_skill_patched(payload, self.analyst, user_dir)
            self.assertEqual(resp.status_code, 201)
            filepath = resp.json().get("filepath", "")
            self.assertIn(self.analyst.username, filepath,
                          f"filepath 应含用户名，实际: {filepath}")


# ══════════════════════════════════════════════════════════════════════════════
# C — 跨用户隔离: Alice 不能访问 Bob 的技能
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossUserIsolation(unittest.TestCase):
    """C1-C6: 严格验证不同用户间的技能目录互相隔离"""

    @classmethod
    def setUpClass(cls):
        cls.app    = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.alice  = _make_user("c_alice", role_names=["analyst"])
        cls.bob    = _make_user("c_bob",   role_names=["analyst"])

    def _auth(self, user):
        return {"Authorization": f"Bearer {_token(user)}"}

    def test_C1_bob_cannot_delete_alices_skill(self):
        """C1: Bob 无法删除 Alice 的技能（Bob 目录下不存在该文件 → 404）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("c1")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                r = self.client.post("/api/v1/skills/user-defined",
                                     json=payload, headers=self._auth(self.alice))
                self.assertEqual(r.status_code, 201,
                                 f"Alice 创建失败: {r.text}")
                slug = r.json()["name"]
                # Alice 的文件存在，但 Bob 目录下没有
                self.assertTrue((user_dir / self.alice.username / f"{slug}.md").exists())
                # Bob 尝试删除
                resp = self.client.delete(
                    f"/api/v1/skills/user-defined/{slug}",
                    headers=self._auth(self.bob),
                )
            self.assertEqual(resp.status_code, 404,
                             "Bob 删除 Alice 的 skill 应返回 404")

    def test_C2_bob_cannot_update_alices_skill(self):
        """C2: Bob 无法更新 Alice 的技能（Bob 目录下不存在 → 404）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("c2")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                r = self.client.post("/api/v1/skills/user-defined",
                                     json=payload, headers=self._auth(self.alice))
                self.assertEqual(r.status_code, 201)
                slug = r.json()["name"]
                resp = self.client.put(
                    f"/api/v1/skills/user-defined/{slug}",
                    json={"description": "bob hacked"},
                    headers=self._auth(self.bob),
                )
            self.assertEqual(resp.status_code, 404,
                             "Bob 修改 Alice 的 skill 应返回 404")

    def test_C3_alice_list_does_not_show_bobs_skills(self):
        """C3: Alice 的列表中看不到 Bob 的技能，Bob 的列表中看不到 Alice 的"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            alice_dir = user_dir / self.alice.username
            bob_dir   = user_dir / self.bob.username
            alice_dir.mkdir(); bob_dir.mkdir()

            (alice_dir / "alice-only.md").write_text(
                "---\nname: alice-only\nversion: \"1.0\"\ndescription: alice\n"
                "triggers:\n  - alice\ncategory: general\npriority: medium\n---\ncontent",
                encoding="utf-8",
            )
            (bob_dir / "bob-only.md").write_text(
                "---\nname: bob-only\nversion: \"1.0\"\ndescription: bob\n"
                "triggers:\n  - bob\ncategory: general\npriority: medium\n---\ncontent",
                encoding="utf-8",
            )
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                alice_resp = self.client.get("/api/v1/skills/user-defined",
                                            headers=self._auth(self.alice))
                bob_resp   = self.client.get("/api/v1/skills/user-defined",
                                            headers=self._auth(self.bob))

            alice_names = {s["name"] for s in alice_resp.json()}
            bob_names   = {s["name"] for s in bob_resp.json()}
            self.assertIn("alice-only",    alice_names)
            self.assertNotIn("bob-only",   alice_names)
            self.assertIn("bob-only",      bob_names)
            self.assertNotIn("alice-only", bob_names)

    def test_C4_alice_skill_file_not_in_flat_root(self):
        """C4: ENABLE_AUTH=true Alice 的技能文件不应出现在 flat user/ 根"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("c4")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.post("/api/v1/skills/user-defined",
                                        json=payload, headers=self._auth(self.alice))
            self.assertEqual(resp.status_code, 201)
            slug = resp.json()["name"]
            self.assertFalse((user_dir / f"{slug}.md").exists(),
                             "文件不应出现在 flat user/ 根")

    def test_C5_alice_file_in_alice_subdir_not_in_bob_subdir(self):
        """C5: Alice 的文件只在 alice/ 子目录，不出现在 bob/ 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("c5")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.post("/api/v1/skills/user-defined",
                                        json=payload, headers=self._auth(self.alice))
            self.assertEqual(resp.status_code, 201)
            slug = resp.json()["name"]
            self.assertTrue( (user_dir / self.alice.username / f"{slug}.md").exists())
            self.assertFalse((user_dir / self.bob.username   / f"{slug}.md").exists())

    def test_C6_path_traversal_in_skill_name_blocked(self):
        """C6: skill name 含路径穿越字符 → 422（slug 化后为空）或 slug 不含 /"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload()
            payload["name"] = "../../../etc/passwd"
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.post("/api/v1/skills/user-defined",
                                        json=payload, headers=self._auth(self.alice))
            if resp.status_code == 422:
                return  # 正确：slug 化后为空，被拒绝
            slug = resp.json().get("name", "")
            self.assertNotIn("/",  slug)
            self.assertNotIn("..", slug)


# ══════════════════════════════════════════════════════════════════════════════
# D — 权限矩阵: viewer/analyst/admin/superadmin 对技能 API 的访问控制
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillPermissionMatrix(unittest.TestCase):
    """D1-D6: 各角色对 /skills/user-defined 端点的权限验证"""

    @classmethod
    def setUpClass(cls):
        cls.app    = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.viewer      = _make_user("d_viewer",   role_names=["viewer"])
        cls.analyst     = _make_user("d_analyst",  role_names=["analyst"])
        cls.admin       = _make_user("d_admin",    role_names=["admin"])
        cls.superadmin  = _make_user("d_super",    role_names=["superadmin"])

    def _auth(self, user):
        return {"Authorization": f"Bearer {_token(user)}"}

    def test_D1_viewer_cannot_create_user_skill(self):
        """D1: viewer 角色无 skills.user:write → 创建 skill 返回 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/skills/user-defined",
                json=_skill_payload("d1v"),
                headers=self._auth(self.viewer),
            )
        self.assertEqual(resp.status_code, 403)

    def test_D2_viewer_cannot_list_user_skills(self):
        """D2: viewer 角色无 skills.user:read → 列出 skill 返回 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/skills/user-defined",
                headers=self._auth(self.viewer),
            )
        self.assertEqual(resp.status_code, 403)

    def test_D3_analyst_can_create_and_list_user_skills(self):
        """D3: analyst 角色有 skills.user:read/write → 可以创建和列出"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                r_create = self.client.post(
                    "/api/v1/skills/user-defined",
                    json=_skill_payload("d3a"),
                    headers=self._auth(self.analyst),
                )
                r_list = self.client.get(
                    "/api/v1/skills/user-defined",
                    headers=self._auth(self.analyst),
                )
        self.assertEqual(r_create.status_code, 201,
                         f"analyst 应可创建: {r_create.text}")
        self.assertEqual(r_list.status_code, 200)

    def test_D4_admin_can_create_user_skills(self):
        """D4: admin 角色有 skills.user:write → 可以创建用户技能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.post(
                    "/api/v1/skills/user-defined",
                    json=_skill_payload("d4a"),
                    headers=self._auth(self.admin),
                )
        self.assertEqual(resp.status_code, 201)

    def test_D5_viewer_cannot_delete_skill(self):
        """D5: viewer 角色无 skills.user:write → 删除返回 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(
                "/api/v1/skills/user-defined/some-skill",
                headers=self._auth(self.viewer),
            )
        self.assertEqual(resp.status_code, 403)

    def test_D6_superadmin_can_do_all_skill_operations(self):
        """D6: superadmin 拥有全部权限，可执行 CRUD 所有 skill 操作"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("d6s")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", True):
                r1 = self.client.post("/api/v1/skills/user-defined",
                                      json=payload, headers=self._auth(self.superadmin))
                self.assertEqual(r1.status_code, 201)
                slug = r1.json()["name"]
                r2 = self.client.get("/api/v1/skills/user-defined",
                                     headers=self._auth(self.superadmin))
                self.assertEqual(r2.status_code, 200)
                r3 = self.client.delete(f"/api/v1/skills/user-defined/{slug}",
                                        headers=self._auth(self.superadmin))
                self.assertEqual(r3.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# E — Context 注入链: username → context dict → CURRENT_USER system prompt
# ══════════════════════════════════════════════════════════════════════════════

class TestContextInjectionChain(unittest.TestCase):
    """E1-E5: username 在 context 链路中正确传递并注入 system prompt"""

    def test_E1_build_context_includes_username_field(self):
        """E1: _build_context(username='alice') 返回 dict 包含 username='alice'"""
        mock_db = MagicMock()
        # conversation not found → early return
        mock_db.query.return_value.filter.return_value.first.return_value = None

        from backend.services.conversation_service import ConversationService
        svc = ConversationService(mock_db)
        ctx = svc._build_context("test-id", username="alice")
        self.assertEqual(ctx.get("username"), "alice")

    def test_E2_build_context_default_username_is_anonymous(self):
        """E2: _build_context() 不传 username 时默认为 'anonymous'"""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        from backend.services.conversation_service import ConversationService
        svc = ConversationService(mock_db)
        ctx = svc._build_context("test-id")
        self.assertEqual(ctx.get("username"), "anonymous")

    def test_E3_send_message_stream_has_username_param(self):
        """E3: send_message_stream() 函数签名包含 username 参数"""
        import inspect
        from backend.services.conversation_service import ConversationService
        sig = inspect.signature(ConversationService.send_message_stream)
        self.assertIn("username", sig.parameters)

    def test_E4_system_prompt_injects_current_user(self):
        """E4: _build_system_prompt 将 context['username'] 注入为 CURRENT_USER:"""
        import asyncio
        from backend.agents.agentic_loop import AgenticLoop

        mock_llm = MagicMock()
        mock_mcp = MagicMock()
        mock_mcp.list_servers.return_value = [
            {"name": "filesystem", "type": "filesystem", "tool_count": 5}
        ]
        # Mock the servers dict for dir_hint
        mock_fs_obj = MagicMock()
        mock_fs_obj.allowed_directories = ["/data/.claude/skills", "/data/customer_data"]
        mock_mcp.servers = {"filesystem": mock_fs_obj}

        loop_obj = AgenticLoop(mock_llm, mock_mcp)
        ctx = {"system_prompt": "base", "username": "alice"}
        prompt = asyncio.get_event_loop().run_until_complete(
            loop_obj._build_system_prompt(ctx, message="")
        )
        self.assertIn("CURRENT_USER: alice", prompt,
                      f"应含 CURRENT_USER: alice, 实际末尾:\n{prompt[-300:]}")

    def test_E5_system_prompt_uses_anonymous_when_no_username(self):
        """E5: context 中无 username 时, CURRENT_USER 为 'anonymous'"""
        import asyncio
        from backend.agents.agentic_loop import AgenticLoop

        mock_llm = MagicMock()
        mock_mcp = MagicMock()
        mock_mcp.list_servers.return_value = [
            {"name": "filesystem", "type": "filesystem", "tool_count": 5}
        ]
        mock_fs_obj = MagicMock()
        mock_fs_obj.allowed_directories = []
        mock_mcp.servers = {"filesystem": mock_fs_obj}

        loop_obj = AgenticLoop(mock_llm, mock_mcp)
        ctx = {"system_prompt": "base"}   # 无 username
        prompt = asyncio.get_event_loop().run_until_complete(
            loop_obj._build_system_prompt(ctx, message="")
        )
        self.assertIn("CURRENT_USER: anonymous", prompt)


# ══════════════════════════════════════════════════════════════════════════════
# F — ENABLE_AUTH=false 向后兼容
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthDisabledBackwardCompatibility(unittest.TestCase):
    """F1-F4: ENABLE_AUTH=false 时使用 flat user/ 目录（匿名用户行为）"""

    @classmethod
    def setUpClass(cls):
        cls.app    = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)

    def test_F1_auth_disabled_skill_lands_in_flat_user_dir(self):
        """F1: ENABLE_AUTH=false 创建技能 → 文件在 flat user/ 目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("f1")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", False):
                resp = self.client.post("/api/v1/skills/user-defined", json=payload)
            self.assertEqual(resp.status_code, 201)
            slug = resp.json()["name"]
            self.assertTrue((user_dir / f"{slug}.md").exists(),
                            "ENABLE_AUTH=false 文件应在 flat user/ 目录")

    def test_F2_auth_disabled_no_username_subdir_created(self):
        """F2: ENABLE_AUTH=false 不应创建 username/ 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            payload = _skill_payload("f2")
            with patch(f"{_SKILLS_MODULE}._USER_SKILLS_DIR", user_dir), \
                 patch("backend.config.settings.settings.enable_auth", False):
                self.client.post("/api/v1/skills/user-defined", json=payload)
            subdirs = [d for d in user_dir.iterdir() if d.is_dir()]
            self.assertEqual(len(subdirs), 0,
                             f"ENABLE_AUTH=false 不应创建子目录: {subdirs}")

    def test_F3_auth_disabled_list_skills_returns_200(self):
        """F3: ENABLE_AUTH=false 无 token 可以列出技能（AnonymousUser 超管权限）"""
        with patch("backend.config.settings.settings.enable_auth", False):
            resp = self.client.get("/api/v1/skills/user-defined")
        self.assertEqual(resp.status_code, 200)

    def test_F4_get_user_skill_dir_auth_false_all_users_flat(self):
        """F4: 代码层: ENABLE_AUTH=false 时所有 username 均返回 flat dir"""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()
            mock_s = MagicMock()
            mock_s.enable_auth = False
            with patch("backend.api.skills.settings", mock_s), \
                 patch("backend.api.skills._USER_SKILLS_DIR", user_dir):
                from backend.api.skills import _get_user_skill_dir
                for uname in ["alice", "bob", "default", "anonymous"]:
                    result = _get_user_skill_dir(uname)
                    self.assertEqual(result, user_dir,
                                     f"ENABLE_AUTH=false {uname} 应返回 flat dir")


# ══════════════════════════════════════════════════════════════════════════════
# G — 菜单权限范围: /skills 菜单纳入 RBAC
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillsMenuPermissionScope(unittest.TestCase):
    """G1-G5: /skills 菜单已纳入 RBAC 权限范围，验证各角色可见性"""

    # 与 AppLayout.tsx 保持一致的菜单定义
    ALL_MENU = [
        {"key": "/chat",         "perm": "chat:use"},
        {"key": "/model-config", "perm": "models:read"},
        {"key": "/dashboard",    "perm": None},
        {"key": "/skills",       "perm": "skills.user:read"},
        {"key": "/users",        "perm": "users:read"},
        {"key": "/roles",        "perm": "users:read"},
    ]

    @classmethod
    def setUpClass(cls):
        cls.app    = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.viewer     = _make_user("g_viewer",  role_names=["viewer"])
        cls.analyst    = _make_user("g_analyst", role_names=["analyst"])
        cls.superadmin = _make_user("g_super",   role_names=["superadmin"])

    def _visible_menus(self, user) -> list:
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(user)}"},
            )
        perms = resp.json().get("permissions", [])
        return [m["key"] for m in self.ALL_MENU
                if m["perm"] is None or m["perm"] in perms]

    def test_G1_skills_menu_uses_skills_user_read_perm(self):
        """G1: /skills 菜单的权限键为 skills.user:read（与设计文档一致）"""
        skills_menu = next((m for m in self.ALL_MENU if m["key"] == "/skills"), None)
        self.assertIsNotNone(skills_menu, "/skills 菜单项未定义")
        self.assertEqual(skills_menu["perm"], "skills.user:read")

    def test_G2_viewer_cannot_see_skills_menu(self):
        """G2: viewer 无 skills.user:read → /skills 菜单不可见"""
        visible = self._visible_menus(self.viewer)
        self.assertNotIn("/skills", visible)

    def test_G3_analyst_can_see_skills_menu(self):
        """G3: analyst 有 skills.user:read → /skills 菜单可见"""
        visible = self._visible_menus(self.analyst)
        self.assertIn("/skills", visible)

    def test_G4_superadmin_sees_all_perm_gated_menus(self):
        """G4: superadmin 有全部权限 → 所有有权限约束的菜单均可见"""
        visible = self._visible_menus(self.superadmin)
        for item in self.ALL_MENU:
            if item["perm"]:
                self.assertIn(item["key"], visible,
                              f"superadmin 应看到 {item['key']}")

    def test_G5_skills_api_returns_403_for_viewer(self):
        """G5: GET /skills/user-defined 对 viewer 返回 403（权限在 API 层强制执行）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/skills/user-defined",
                headers={"Authorization": f"Bearer {_token(self.viewer)}"},
            )
        self.assertEqual(resp.status_code, 403)


# ══════════════════════════════════════════════════════════════════════════════
# H — init_rbac: 各角色技能权限种子数据覆盖
# ══════════════════════════════════════════════════════════════════════════════

class TestInitRbacSkillsPermissions(unittest.TestCase):
    """H1-H4: 验证 init_rbac.py 为各角色正确分配技能权限"""

    @classmethod
    def setUpClass(cls):
        cls.viewer      = _make_user("h_viewer",  role_names=["viewer"])
        cls.analyst     = _make_user("h_analyst", role_names=["analyst"])
        cls.admin       = _make_user("h_admin",   role_names=["admin"])
        cls.superadmin  = _make_user("h_super",   role_names=["superadmin"])

    def _perms(self, user) -> set:
        from backend.core.rbac import get_user_permissions
        return set(get_user_permissions(user, _g_db))

    def test_H1_viewer_has_no_skills_permissions(self):
        """H1: viewer 角色不含任何 skills.* 权限"""
        skills_perms = {p for p in self._perms(self.viewer) if p.startswith("skills.")}
        self.assertEqual(skills_perms, set(),
                         f"viewer 不应有技能权限, 实际: {skills_perms}")

    def test_H2_analyst_has_skills_user_read_write(self):
        """H2: analyst 含 skills.user:read + skills.user:write"""
        perms = self._perms(self.analyst)
        self.assertIn("skills.user:read",  perms)
        self.assertIn("skills.user:write", perms)

    def test_H3_admin_has_full_skills_permissions(self):
        """H3: admin 含 skills 全系权限（含 project:write）"""
        perms = self._perms(self.admin)
        for p in ["skills.user:read", "skills.user:write",
                  "skills.project:read", "skills.project:write",
                  "skills.system:read"]:
            self.assertIn(p, perms, f"admin 应有 {p}")

    def test_H4_superadmin_has_all_skills_permissions(self):
        """H4: superadmin 通过 /auth/me 获得全部 skills.* 权限"""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(self.superadmin)}"},
            )
        perms = set(resp.json().get("permissions", []))
        for p in ["skills.user:read", "skills.user:write",
                  "skills.project:read", "skills.project:write",
                  "skills.system:read"]:
            self.assertIn(p, perms, f"superadmin 应有 {p}")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
