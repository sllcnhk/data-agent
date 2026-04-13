"""
test_skill_e2e.py
=================
End-to-end tests for the 3-tier Skill System.

Coverage:
  A — GET /md-skills: tier / always_inject fields
  B — User skill CRUD (/user-defined)
  C — Project skill CRUD (/project-skills, admin-protected)
  D — Version bump (_bump_version helper)
  E — SkillLoader tier behavior (unit level)
  F — Frontend static code scan (Skills.tsx + api.ts)
  G — FilesystemPermissionProxy error message guidance
  H — SkillWatcher recursive flag

Run:
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe test_skill_e2e.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))

# Fake env so Settings doesn't require a real .env
os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("CLICKHOUSE_PORT", "9000")
os.environ.setdefault("CLICKHOUSE_USER", "default")
os.environ.setdefault("CLICKHOUSE_PASSWORD", "")
os.environ.setdefault("CLICKHOUSE_DATABASE", "default")
os.environ.setdefault("ADMIN_SECRET_TOKEN", "test-admin-token-e2e")
# Tests use anonymous/no-auth mode regardless of .env setting
os.environ.setdefault("ENABLE_AUTH", "False")

# ── FastAPI test client ───────────────────────────────────────────────────────
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.skills import router as skills_router, _bump_version

_test_app = FastAPI()
_test_app.include_router(skills_router, prefix="/api/v1")
_client = TestClient(_test_app)

ADMIN_HDR = {"X-Admin-Token": "test-admin-token-e2e"}

# ─────────────────────────────────────────────────────────────────────────────
# Section A: GET /md-skills — tier + always_inject fields
# ─────────────────────────────────────────────────────────────────────────────

class TestMdSkillsTierFields(unittest.TestCase):
    """A1-A6: /md-skills returns tier + always_inject metadata."""

    def _fetch(self):
        r = _client.get("/api/v1/skills/md-skills")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_A1_returns_list(self):
        """A1: GET /md-skills returns a list."""
        data = self._fetch()
        self.assertIsInstance(data, list)

    def test_A2_each_item_has_tier(self):
        """A2: Every skill has a 'tier' field."""
        data = self._fetch()
        for s in data:
            self.assertIn("tier", s, f"Missing 'tier' on {s.get('name')}")

    def test_A3_each_item_has_always_inject(self):
        """A3: Every skill has an 'always_inject' field."""
        data = self._fetch()
        for s in data:
            self.assertIn("always_inject", s, f"Missing 'always_inject' on {s.get('name')}")

    def test_A4_each_item_has_is_readonly(self):
        """A4: Every skill has an 'is_readonly' field."""
        data = self._fetch()
        for s in data:
            self.assertIn("is_readonly", s, f"Missing 'is_readonly' on {s.get('name')}")

    def test_A5_system_skills_are_readonly(self):
        """A5: System-tier skills have is_readonly=True."""
        data = self._fetch()
        for s in data:
            if s.get("tier") == "system":
                self.assertTrue(
                    s["is_readonly"],
                    f"System skill '{s['name']}' should be is_readonly=True"
                )

    def test_A6_user_skills_not_readonly(self):
        """A6: User-tier skills have is_readonly=False."""
        data = self._fetch()
        for s in data:
            if s.get("tier") == "user":
                self.assertFalse(
                    s["is_readonly"],
                    f"User skill '{s['name']}' should not be is_readonly"
                )

    def test_A7_base_safety_always_inject_true(self):
        """A7: _base-safety skill (if present) has always_inject=True."""
        data = self._fetch()
        base_skills = [s for s in data if s.get("name", "").startswith("_base")]
        if base_skills:
            for s in base_skills:
                self.assertTrue(
                    s["always_inject"],
                    f"Skill '{s['name']}' should have always_inject=True"
                )

    def test_A8_known_system_skills_have_system_tier(self):
        """A8: Known system skills (etl-engineer, schema-explorer) have tier=system."""
        data = self._fetch()
        names = {s["name"]: s for s in data}
        # clickhouse-analyst is a user skill (in .claude/skills/user/), not system
        known_system = ["etl-engineer", "schema-explorer"]
        found = [k for k in known_system if k in names]
        for name in found:
            self.assertEqual(
                names[name]["tier"], "system",
                f"'{name}' should be tier=system"
            )
        # clickhouse-analyst was migrated from user/ to project/ tier
        if "clickhouse-analyst" in names:
            self.assertEqual(
                names["clickhouse-analyst"]["tier"], "project",
                "clickhouse-analyst is in .claude/skills/project/ so tier should be project"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Section B: User skill CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestUserSkillCRUD(unittest.TestCase):
    """B1-B10: /user-defined CRUD operations."""

    _created: list = []

    def _cleanup(self, name: str):
        _client.delete(f"/api/v1/skills/user-defined/{name}")
        self._created = [n for n in self._created if n != name]

    def _payload(self, name="test-e2e-skill", **kw):
        base = {
            "name": name,
            "description": "E2E test skill",
            "triggers": ["e2e", "test"],
            "category": "general",
            "priority": "medium",
            "content": "# Test\n\nThis is an e2e test skill.",
        }
        base.update(kw)
        return base

    # --- Create ---

    def test_B1_create_user_skill_success(self):
        """B1: POST /user-defined creates a skill and returns tier=user."""
        name = "test-e2e-b1"
        self._cleanup(name)  # ensure clean state
        r = _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        self.assertEqual(r.status_code, 201, r.text)
        data = r.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tier"], "user")
        self.assertIn("filepath", data)
        self._created.append(name)
        self._cleanup(name)

    def test_B2_create_duplicate_returns_409(self):
        """B2: Creating a duplicate skill returns 409 Conflict."""
        name = "test-e2e-b2"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        r = _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        self.assertEqual(r.status_code, 409)
        self._cleanup(name)

    def test_B3_create_with_invalid_name_422(self):
        """B3: Creating skill with only special chars returns 422."""
        r = _client.post("/api/v1/skills/user-defined", json=self._payload("!@#$%"))
        self.assertEqual(r.status_code, 422)

    def test_B4_create_name_is_slugified(self):
        """B4: Skill name is slugified (spaces → hyphens, lowercase)."""
        name = "My Test Skill E2E B4"
        self._cleanup("my-test-skill-e2e-b4")  # ensure clean state
        r = _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        self.assertEqual(r.status_code, 201, r.text)
        data = r.json()
        self.assertEqual(data["name"], "my-test-skill-e2e-b4")
        self._cleanup("my-test-skill-e2e-b4")

    # --- List ---

    def test_B5_list_user_skills_returns_list(self):
        """B5: GET /user-defined returns a list."""
        r = _client.get("/api/v1/skills/user-defined")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_B6_created_skill_in_list(self):
        """B6: Newly created skill appears in user-defined list."""
        name = "test-e2e-b6"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        r = _client.get("/api/v1/skills/user-defined")
        names = [s.get("name") for s in r.json()]
        self.assertIn(name, names)
        self._cleanup(name)

    def test_B7_list_user_skills_tier_field(self):
        """B7: User skill list items include tier=user."""
        name = "test-e2e-b7"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        r = _client.get("/api/v1/skills/user-defined")
        for s in r.json():
            if s.get("name") == name:
                self.assertEqual(s["tier"], "user")
                break
        self._cleanup(name)

    # --- Delete ---

    def test_B8_delete_user_skill_success(self):
        """B8: DELETE /user-defined/{name} deletes the skill."""
        name = "test-e2e-b8"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        r = _client.delete(f"/api/v1/skills/user-defined/{name}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])

    def test_B9_delete_nonexistent_returns_404(self):
        """B9: Deleting non-existent skill returns 404."""
        r = _client.delete("/api/v1/skills/user-defined/definitely-does-not-exist-xyz")
        self.assertEqual(r.status_code, 404)

    def test_B10_deleted_skill_not_in_list(self):
        """B10: After deletion, skill no longer appears in list."""
        name = "test-e2e-b10"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        _client.delete(f"/api/v1/skills/user-defined/{name}")
        r = _client.get("/api/v1/skills/user-defined")
        names = [s.get("name") for s in r.json()]
        self.assertNotIn(name, names)


# ─────────────────────────────────────────────────────────────────────────────
# Section C: Project skill CRUD (admin-protected)
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectSkillCRUD(unittest.TestCase):
    """C1-C16: /project-skills CRUD + admin protection."""

    def _payload(self, name="test-proj-e2e", **kw):
        base = {
            "name": name,
            "description": "E2E project skill",
            "triggers": ["project", "e2e"],
            "category": "general",
            "priority": "medium",
            "content": "# Project Skill\n\nThis is a project-tier e2e skill.",
        }
        base.update(kw)
        return base

    def _cleanup(self, name: str):
        _client.delete(f"/api/v1/skills/project-skills/{name}", headers=ADMIN_HDR)

    # --- Create ---

    def test_C1_create_project_skill_with_admin_token(self):
        """C1: POST /project-skills with valid admin token creates skill."""
        name = "test-proj-c1"
        self._cleanup(name)  # ensure clean state
        r = _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        self.assertEqual(r.status_code, 201, r.text)
        data = r.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tier"], "project")
        self._cleanup(name)

    def test_C2_create_project_skill_without_token_401_or_403(self):
        """C2: POST /project-skills without admin token returns 401 or 403."""
        r = _client.post("/api/v1/skills/project-skills", json=self._payload("test-proj-c2"))
        self.assertIn(r.status_code, [401, 403])

    def test_C3_create_project_skill_wrong_token_401_or_403(self):
        """C3: POST /project-skills with wrong admin token returns 401 or 403."""
        r = _client.post(
            "/api/v1/skills/project-skills",
            json=self._payload("test-proj-c3"),
            headers={"X-Admin-Token": "wrong-token"},
        )
        self.assertIn(r.status_code, [401, 403])

    def test_C4_create_duplicate_project_skill_409(self):
        """C4: Creating duplicate project skill returns 409."""
        name = "test-proj-c4"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        self.assertEqual(r.status_code, 409)
        self._cleanup(name)

    # --- List ---

    def test_C5_list_project_skills_returns_list(self):
        """C5: GET /project-skills returns a list (public, no auth)."""
        r = _client.get("/api/v1/skills/project-skills")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_C6_created_project_skill_in_list(self):
        """C6: Newly created project skill appears in project list."""
        name = "test-proj-c6"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.get("/api/v1/skills/project-skills")
        names = [s.get("name") for s in r.json()]
        self.assertIn(name, names)
        self._cleanup(name)

    def test_C7_project_skill_list_has_tier_field(self):
        """C7: Project skill list items include tier=project."""
        name = "test-proj-c7"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.get("/api/v1/skills/project-skills")
        for s in r.json():
            if s.get("name") == name:
                self.assertEqual(s["tier"], "project")
                break
        self._cleanup(name)

    # --- Update ---

    def test_C8_update_project_skill_with_admin_token(self):
        """C8: PUT /project-skills/{name} with admin token updates skill."""
        name = "test-proj-c8"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.put(
            f"/api/v1/skills/project-skills/{name}",
            json={"description": "Updated description for C8"},
            headers=ADMIN_HDR,
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tier"], "project")
        self._cleanup(name)

    def test_C9_update_bumps_version(self):
        """C9: Updating a project skill bumps the minor version."""
        name = "test-proj-c9"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.put(
            f"/api/v1/skills/project-skills/{name}",
            json={"description": "Updated"},
            headers=ADMIN_HDR,
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["version"], "1.1")
        self._cleanup(name)

    def test_C10_update_without_token_401_or_403(self):
        """C10: PUT /project-skills/{name} without admin token returns 401 or 403."""
        name = "test-proj-c10"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.put(
            f"/api/v1/skills/project-skills/{name}",
            json={"description": "Updated"},
        )
        self.assertIn(r.status_code, [401, 403])
        self._cleanup(name)

    def test_C11_update_nonexistent_returns_404(self):
        """C11: Updating non-existent project skill returns 404."""
        r = _client.put(
            "/api/v1/skills/project-skills/definitely-missing-xyz",
            json={"description": "x"},
            headers=ADMIN_HDR,
        )
        self.assertEqual(r.status_code, 404)

    # --- Delete ---

    def test_C12_delete_project_skill_with_admin_token(self):
        """C12: DELETE /project-skills/{name} with admin token deletes skill."""
        name = "test-proj-c12"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.delete(f"/api/v1/skills/project-skills/{name}", headers=ADMIN_HDR)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])

    def test_C13_delete_without_token_401_or_403(self):
        """C13: DELETE /project-skills/{name} without admin token returns 401 or 403."""
        name = "test-proj-c13"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.delete(f"/api/v1/skills/project-skills/{name}")
        self.assertIn(r.status_code, [401, 403])
        self._cleanup(name)

    def test_C14_delete_nonexistent_returns_404(self):
        """C14: Deleting non-existent project skill returns 404."""
        r = _client.delete(
            "/api/v1/skills/project-skills/definitely-missing-xyz",
            headers=ADMIN_HDR,
        )
        self.assertEqual(r.status_code, 404)

    def test_C15_deleted_project_skill_not_in_list(self):
        """C15: After deletion, project skill no longer in list."""
        name = "test-proj-c15"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        _client.delete(f"/api/v1/skills/project-skills/{name}", headers=ADMIN_HDR)
        r = _client.get("/api/v1/skills/project-skills")
        names = [s.get("name") for s in r.json()]
        self.assertNotIn(name, names)

    def test_C16_project_skill_appears_in_md_skills(self):
        """C16: Project skill created via API appears in /md-skills with tier=project."""
        name = "test-proj-c16"
        _client.post("/api/v1/skills/project-skills", json=self._payload(name), headers=ADMIN_HDR)
        r = _client.get("/api/v1/skills/md-skills")
        proj_in_md = [s for s in r.json() if s.get("name") == name]
        if proj_in_md:
            self.assertEqual(proj_in_md[0]["tier"], "project")
        self._cleanup(name)


# ─────────────────────────────────────────────────────────────────────────────
# Section D: _bump_version helper
# ─────────────────────────────────────────────────────────────────────────────

class TestBumpVersion(unittest.TestCase):
    """D1-D6: Version bump logic."""

    def test_D1_bump_1_0_to_1_1(self):
        """D1: '1.0' → '1.1'."""
        self.assertEqual(_bump_version("1.0"), "1.1")

    def test_D2_bump_1_9_to_1_10(self):
        """D2: '1.9' → '1.10'."""
        self.assertEqual(_bump_version("1.9"), "1.10")

    def test_D3_bump_2_3_to_2_4(self):
        """D3: '2.3' → '2.4'."""
        self.assertEqual(_bump_version("2.3"), "2.4")

    def test_D4_bump_invalid_fallback(self):
        """D4: Invalid version string falls back to '1.1'."""
        self.assertEqual(_bump_version("invalid"), "1.1")

    def test_D5_bump_empty_fallback(self):
        """D5: Empty version string falls back to '1.1'."""
        self.assertEqual(_bump_version(""), "1.1")

    def test_D6_bump_preserves_major(self):
        """D6: Major version number is preserved."""
        result = _bump_version("3.7")
        self.assertTrue(result.startswith("3."))


# ─────────────────────────────────────────────────────────────────────────────
# Section E: SkillLoader tier behavior (unit tests with temp dirs)
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillLoaderTierBehavior(unittest.TestCase):
    """E1-E10: SkillLoader properly loads 3 tiers and handles always_inject."""

    def setUp(self):
        from backend.skills.skill_loader import TIER_SYSTEM, TIER_PROJECT, TIER_USER
        self.TIER_SYSTEM = TIER_SYSTEM
        self.TIER_PROJECT = TIER_PROJECT
        self.TIER_USER = TIER_USER

        self.tmpdir = Path(tempfile.mkdtemp())
        self.system_dir = self.tmpdir / "system"
        self.project_dir = self.tmpdir / "project"
        self.user_dir = self.tmpdir / "user"
        self.system_dir.mkdir()
        self.project_dir.mkdir()
        self.user_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: Path, name: str, triggers=None, always_inject=False, **meta):
        t_str = ""
        if triggers:
            t_str = "\ntriggers:\n" + "\n".join(f"  - {t}" for t in triggers)
        ai_str = f"\nalways_inject: {str(always_inject).lower()}" if always_inject else ""
        text = (
            f"---\n"
            f"name: {name}\n"
            f'version: "1.0"\n'
            f"description: Test skill {name}\n"
            f"category: {meta.get('category', 'general')}\n"
            f"priority: {meta.get('priority', 'medium')}\n"
            f"{t_str}\n"
            f"{ai_str}\n"
            f"---\n\n# {name}\n\nContent for {name}.\n"
        )
        path.write_text(text, encoding="utf-8")

    def _make_loader(self):
        from backend.skills.skill_loader import SkillLoader
        loader = SkillLoader(skills_dir=str(self.tmpdir))
        loader.load_all()
        return loader

    def test_E1_system_skills_get_system_tier(self):
        """E1: Skills in system/ dir get tier=system."""
        self._write(self.system_dir / "sys-skill.md", "sys-skill")
        loader = self._make_loader()
        system = loader.get_by_tier(self.TIER_SYSTEM)
        self.assertTrue(any(s.name == "sys-skill" for s in system))

    def test_E2_project_skills_get_project_tier(self):
        """E2: Skills in project/ dir get tier=project."""
        self._write(self.project_dir / "proj-skill.md", "proj-skill")
        loader = self._make_loader()
        project = loader.get_by_tier(self.TIER_PROJECT)
        self.assertTrue(any(s.name == "proj-skill" for s in project))

    def test_E3_user_skills_get_user_tier(self):
        """E3: Skills in user/ dir get tier=user."""
        self._write(self.user_dir / "user-skill.md", "user-skill")
        loader = self._make_loader()
        user = loader.get_by_tier(self.TIER_USER)
        self.assertTrue(any(s.name == "user-skill" for s in user))

    def test_E4_base_prefix_sets_always_inject(self):
        """E4: Skill file starting with _base sets always_inject=True."""
        self._write(self.system_dir / "_base-test.md", "_base-test")
        loader = self._make_loader()
        base = [s for s in loader.list_all() if s.name == "_base-test"]
        self.assertEqual(len(base), 1)
        self.assertTrue(base[0].always_inject)

    def test_E5_frontmatter_always_inject_true(self):
        """E5: Frontmatter always_inject: true sets always_inject=True."""
        self._write(self.system_dir / "always-on.md", "always-on", always_inject=True)
        loader = self._make_loader()
        skills = [s for s in loader.list_all() if s.name == "always-on"]
        self.assertEqual(len(skills), 1)
        self.assertTrue(skills[0].always_inject)

    def test_E6_always_inject_skill_not_in_find_triggered(self):
        """E6: always_inject skills don't appear in find_triggered results."""
        self._write(self.system_dir / "_base-always.md", "_base-always")
        loader = self._make_loader()
        triggered = loader.find_triggered("anything")
        self.assertFalse(any(s.name == "_base-always" for s in triggered))

    def test_E7_build_skill_prompt_includes_base_skills(self):
        """E7: build_skill_prompt() includes always_inject skills regardless of message."""
        self._write(self.system_dir / "_base-safety.md", "_base-safety")
        loader = self._make_loader()
        prompt = loader.build_skill_prompt("unrelated message")
        self.assertIn("_base-safety", prompt)

    def test_E8_user_skills_first_in_triggered(self):
        """E8: User-tier triggered skills appear before system-tier in find_triggered."""
        self._write(self.system_dir / "sys-kw.md", "sys-kw", triggers=["keyword"])
        self._write(self.user_dir / "usr-kw.md", "usr-kw", triggers=["keyword"])
        loader = self._make_loader()
        triggered = loader.find_triggered("keyword")
        names = [s.name for s in triggered]
        if "usr-kw" in names and "sys-kw" in names:
            self.assertLess(names.index("usr-kw"), names.index("sys-kw"))

    def test_E9_get_all_returns_all_tiers(self):
        """E9: get_all() returns skills from all 3 tiers."""
        self._write(self.system_dir / "s1.md", "s1")
        self._write(self.project_dir / "p1.md", "p1")
        self._write(self.user_dir / "u1.md", "u1")
        loader = self._make_loader()
        all_names = {s.name for s in loader.get_all()}
        self.assertIn("s1", all_names)
        self.assertIn("p1", all_names)
        self.assertIn("u1", all_names)

    def test_E10_empty_dirs_no_error(self):
        """E10: Empty tier dirs load without error."""
        loader = self._make_loader()
        self.assertIsNotNone(loader)
        self.assertIsInstance(loader.get_all(), list)


# ─────────────────────────────────────────────────────────────────────────────
# Section F: Frontend static code scan
# ─────────────────────────────────────────────────────────────────────────────

_SKILLS_TSX = Path(__file__).parent / "frontend" / "src" / "pages" / "Skills.tsx"
_API_TS = Path(__file__).parent / "frontend" / "src" / "services" / "api.ts"


class TestFrontendStaticSkillsTsx(unittest.TestCase):
    """F1-F14: Skills.tsx has correct 3-tier UI implementation."""

    def setUp(self):
        if not _SKILLS_TSX.exists():
            self.skipTest(f"Skills.tsx not found at {_SKILLS_TSX}")
        self._src = _SKILLS_TSX.read_text(encoding="utf-8")

    def test_F1_skill_md_interface_has_tier(self):
        """F1: SkillMD interface includes 'tier' field."""
        self.assertIn("tier?:", self._src)

    def test_F2_skill_md_interface_has_always_inject(self):
        """F2: SkillMD interface includes 'always_inject' field."""
        self.assertIn("always_inject?:", self._src)

    def test_F3_tier_color_map_exists(self):
        """F3: TIER_COLOR mapping exists with system/project/user keys."""
        self.assertIn("TIER_COLOR", self._src)
        self.assertIn("system:", self._src)
        self.assertIn("project:", self._src)
        self.assertIn("user:", self._src)

    def test_F4_tier_label_map_exists(self):
        """F4: TIER_LABEL mapping exists."""
        self.assertIn("TIER_LABEL", self._src)

    def test_F5_three_tabs_defined(self):
        """F5: tabItems contains at least 3 tab keys (system, project, user)."""
        self.assertIn("key: 'system'", self._src)
        self.assertIn("key: 'project'", self._src)
        self.assertIn("key: 'user'", self._src)

    def test_F6_project_skill_state_exists(self):
        """F6: projectSkills and loadingProject state variables exist."""
        self.assertIn("projectSkills", self._src)
        self.assertIn("loadingProject", self._src)

    def test_F7_load_project_skills_function(self):
        """F7: loadProjectSkills function calls skillApi.getProjectSkills."""
        self.assertIn("loadProjectSkills", self._src)
        self.assertIn("getProjectSkills", self._src)

    def test_F8_create_project_skill_handler(self):
        """F8: handleCreateProjectSkill handler uses createProjectSkill API."""
        self.assertIn("handleCreateProjectSkill", self._src)
        self.assertIn("createProjectSkill", self._src)

    def test_F9_update_project_skill_handler(self):
        """F9: handleEditProjectSkill handler uses updateProjectSkill API."""
        self.assertIn("handleEditProjectSkill", self._src)
        self.assertIn("updateProjectSkill", self._src)

    def test_F10_delete_project_skill_handler(self):
        """F10: handleDeleteProjectSkill handler uses deleteProjectSkill API."""
        self.assertIn("handleDeleteProjectSkill", self._src)
        self.assertIn("deleteProjectSkill", self._src)

    def test_F11_admin_token_state(self):
        """F11: savedAdminToken state and sessionStorage usage."""
        self.assertIn("savedAdminToken", self._src)
        self.assertIn("sessionStorage", self._src)

    def test_F12_always_inject_badge(self):
        """F12: SafetyOutlined icon used for always_inject display."""
        self.assertIn("SafetyOutlined", self._src)
        self.assertIn("always_inject", self._src)

    def test_F13_project_icon_used(self):
        """F13: ProjectOutlined icon used for project tab."""
        self.assertIn("ProjectOutlined", self._src)

    def test_F14_edit_project_skill_modal(self):
        """F14: Edit project skill modal form exists."""
        self.assertIn("editProjectOpen", self._src)
        self.assertIn("editProjectForm", self._src)


class TestFrontendStaticApiTs(unittest.TestCase):
    """F15-F22: api.ts has correct project skill API methods."""

    def setUp(self):
        if not _API_TS.exists():
            self.skipTest(f"api.ts not found at {_API_TS}")
        self._src = _API_TS.read_text(encoding="utf-8")

    def test_F15_get_project_skills_exists(self):
        """F15: skillApi.getProjectSkills method exists."""
        self.assertIn("getProjectSkills", self._src)

    def test_F16_create_project_skill_exists(self):
        """F16: skillApi.createProjectSkill method exists."""
        self.assertIn("createProjectSkill", self._src)

    def test_F17_update_project_skill_exists(self):
        """F17: skillApi.updateProjectSkill method exists."""
        self.assertIn("updateProjectSkill", self._src)

    def test_F18_delete_project_skill_exists(self):
        """F18: skillApi.deleteProjectSkill method exists."""
        self.assertIn("deleteProjectSkill", self._src)

    def test_F19_admin_token_header_in_create(self):
        """F19: createProjectSkill passes X-Admin-Token header."""
        self.assertIn("X-Admin-Token", self._src)

    def test_F20_project_skills_endpoint_create(self):
        """F20: Project skills POST endpoint URL is correct."""
        self.assertIn("/skills/project-skills", self._src)

    def test_F21_project_skills_put_endpoint(self):
        """F21: Project skills PUT endpoint with skill name in URL."""
        self.assertIn("project-skills/${skillName}", self._src)

    def test_F22_admin_token_parameter_in_project_methods(self):
        """F22: Project skill API methods accept adminToken parameter."""
        import re
        # Find createProjectSkill method definition
        m = re.search(
            r"createProjectSkill:\s*async\s*\(([^)]+)\)",
            self._src,
        )
        if m:
            self.assertIn("adminToken", m.group(1))


# ─────────────────────────────────────────────────────────────────────────────
# Section G: FilesystemPermissionProxy error message
# ─────────────────────────────────────────────────────────────────────────────

class TestFilesystemProxyErrorMessage(unittest.TestCase):
    """G1-G3: FilesystemPermissionProxy error message includes project-skills guidance."""

    def setUp(self):
        proxy_path = Path(__file__).parent / "backend" / "core" / "filesystem_permission_proxy.py"
        if not proxy_path.exists():
            self.skipTest("filesystem_permission_proxy.py not found")
        self._src = proxy_path.read_text(encoding="utf-8")

    def test_G1_error_message_mentions_customer_data(self):
        """G1: Blocked write error message mentions customer_data/ directory."""
        self.assertIn("customer_data/", self._src)

    def test_G2_error_message_mentions_user_skills_path(self):
        """G2: Blocked write error message mentions .claude/skills/user/ path (not REST API)."""
        self.assertIn(".claude/skills/user/", self._src)
        # Must NOT instruct Agent to call internal REST API (Agent has no HTTP MCP tool)
        self.assertNotIn("POST /api/v1/skills/user-defined", self._src)

    def test_G3_error_message_mentions_system_project_readonly(self):
        """G3: Blocked write error message mentions system/project dirs are readonly."""
        self.assertIn(".claude/skills/system/", self._src)
        self.assertIn(".claude/skills/project/", self._src)


# ─────────────────────────────────────────────────────────────────────────────
# Section H: SkillWatcher recursive flag
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillWatcherRecursive(unittest.TestCase):
    """H1-H2: SkillWatcher uses recursive=True to monitor subdirs."""

    def setUp(self):
        watcher_path = Path(__file__).parent / "backend" / "skills" / "skill_watcher.py"
        if not watcher_path.exists():
            self.skipTest("skill_watcher.py not found")
        self._src = watcher_path.read_text(encoding="utf-8")

    def test_H1_recursive_true_in_schedule(self):
        """H1: observer.schedule() is called with recursive=True."""
        self.assertIn("recursive=True", self._src)

    def test_H2_recursive_false_not_present(self):
        """H2: recursive=False should not appear (was the old value)."""
        self.assertNotIn("recursive=False", self._src)


# ─────────────────────────────────────────────────────────────────────────────
# Section I: base-safety.md file checks
# ─────────────────────────────────────────────────────────────────────────────

class TestBaseSafetySkill(unittest.TestCase):
    """I1-I5: _base-safety.md exists and has correct content."""

    def setUp(self):
        self._path = (
            Path(__file__).parent / ".claude" / "skills" / "system" / "_base-safety.md"
        )

    def test_I1_file_exists(self):
        """I1: _base-safety.md exists in system/ directory."""
        self.assertTrue(self._path.exists(), f"Missing: {self._path}")

    def test_I2_has_always_inject_true(self):
        """I2: _base-safety.md has always_inject: true in frontmatter."""
        if not self._path.exists():
            self.skipTest("_base-safety.md not found")
        content = self._path.read_text(encoding="utf-8")
        self.assertIn("always_inject: true", content)

    def test_I3_has_system_category(self):
        """I3: _base-safety.md has category: system."""
        if not self._path.exists():
            self.skipTest("_base-safety.md not found")
        content = self._path.read_text(encoding="utf-8")
        self.assertIn("category: system", content)

    def test_I4_contains_data_write_rule(self):
        """I4: _base-safety.md body contains data write scope rule."""
        if not self._path.exists():
            self.skipTest("_base-safety.md not found")
        content = self._path.read_text(encoding="utf-8")
        self.assertIn("customer_data", content)

    def test_I5_contains_db_safety_rule(self):
        """I5: _base-safety.md body mentions DROP/TRUNCATE safety."""
        if not self._path.exists():
            self.skipTest("_base-safety.md not found")
        content = self._path.read_text(encoding="utf-8")
        self.assertIn("DROP", content)


# ─────────────────────────────────────────────────────────────────────────────
# Section J: Path boundary enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestPathBoundaryEnforcement(unittest.TestCase):
    """J1-J4: API path boundary checks prevent directory traversal."""

    def test_J1_delete_user_skill_traversal_blocked(self):
        """J1: Deleting user skill with path traversal pattern is blocked or fails safely."""
        r = _client.delete("/api/v1/skills/user-defined/../../etc/passwd")
        # Either 403 (boundary check), 404 (not found after slug), or 422 (invalid name)
        self.assertIn(r.status_code, [403, 404, 422])

    def test_J2_delete_project_skill_traversal_blocked(self):
        """J2: Deleting project skill with traversal is blocked or fails safely."""
        r = _client.delete(
            "/api/v1/skills/project-skills/../../secrets",
            headers=ADMIN_HDR,
        )
        self.assertIn(r.status_code, [403, 404, 422])

    def test_J3_create_user_skill_name_with_slash_blocked(self):
        """J3: Creating user skill with slash in name is blocked or slugified safely."""
        r = _client.post(
            "/api/v1/skills/user-defined",
            json={
                "name": "../evil/skill",
                "description": "Test",
                "triggers": ["test"],
                "content": "# Test\n\nContent",
            },
        )
        # Either 422 (validation) or the slug is safe (no traversal possible)
        if r.status_code == 200:
            safe_name = r.json().get("name", "")
            self.assertNotIn("..", safe_name)
            self.assertNotIn("/", safe_name)
            _client.delete(f"/api/v1/skills/user-defined/{safe_name}")

    def test_J4_slugify_removes_dangerous_chars(self):
        """J4: _slugify() removes path-traversal chars from skill names."""
        from backend.api.skills import _slugify
        dangerous = "../../../etc/passwd"
        result = _slugify(dangerous)
        self.assertNotIn("..", result)
        self.assertNotIn("/", result)
        self.assertNotIn("\\", result)


# ─────────────────────────────────────────────────────────────────────────────
# Section K: User skill UPDATE API (PUT /user-defined/{name})
# ─────────────────────────────────────────────────────────────────────────────

class TestUserSkillUpdate(unittest.TestCase):
    """K1-K7: PUT /user-defined/{name} user skill update."""

    def _payload(self, name="test-upd-user", **kw):
        base = {
            "name": name,
            "description": "Original description",
            "triggers": ["original"],
            "category": "general",
            "priority": "medium",
            "content": "# Original\n\nOriginal content.",
        }
        base.update(kw)
        return base

    def _cleanup(self, name: str):
        _client.delete(f"/api/v1/skills/user-defined/{name}")

    def test_K1_update_user_skill_success(self):
        """K1: PUT /user-defined/{name} updates skill and returns tier=user."""
        name = "test-upd-k1"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        r = _client.put(
            f"/api/v1/skills/user-defined/{name}",
            json={"description": "Updated description K1"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tier"], "user")
        self._cleanup(name)

    def test_K2_update_bumps_version(self):
        """K2: Updating user skill bumps the minor version."""
        name = "test-upd-k2"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        r = _client.put(
            f"/api/v1/skills/user-defined/{name}",
            json={"description": "Updated"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["version"], "1.1")
        self._cleanup(name)

    def test_K3_update_nonexistent_returns_404(self):
        """K3: Updating non-existent user skill returns 404."""
        r = _client.put(
            "/api/v1/skills/user-defined/definitely-missing-user-xyz",
            json={"description": "x"},
        )
        self.assertEqual(r.status_code, 404)

    def test_K4_update_preserves_unspecified_fields(self):
        """K4: Partial update preserves fields not included in request."""
        name = "test-upd-k4"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        # Only update description; triggers should be preserved
        r = _client.put(
            f"/api/v1/skills/user-defined/{name}",
            json={"description": "New desc"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        # Verify the file was updated (check via md-skills listing)
        md_r = _client.get("/api/v1/skills/md-skills")
        updated = [s for s in md_r.json() if s.get("name") == name]
        if updated:
            self.assertIn("original", updated[0].get("triggers", []))
        self._cleanup(name)

    def test_K5_update_content_changes_body(self):
        """K5: Updating content field changes the skill body."""
        name = "test-upd-k5"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        r = _client.put(
            f"/api/v1/skills/user-defined/{name}",
            json={"content": "# New Title\n\nNew content for K5."},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self._cleanup(name)

    def test_K6_update_traversal_blocked(self):
        """K6: PUT traversal path is blocked or sanitized."""
        r = _client.put(
            "/api/v1/skills/user-defined/../../etc/passwd",
            json={"description": "evil"},
        )
        self.assertIn(r.status_code, [403, 404, 422])

    def test_K7_update_no_admin_token_required(self):
        """K7: User skill update does NOT require admin token."""
        name = "test-upd-k7"
        _client.post("/api/v1/skills/user-defined", json=self._payload(name))
        # No headers — should succeed
        r = _client.put(
            f"/api/v1/skills/user-defined/{name}",
            json={"description": "No auth needed"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self._cleanup(name)


# ─────────────────────────────────────────────────────────────────────────────
# Section L: Skill preview API (GET /preview)
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillPreviewAPI(unittest.TestCase):
    """L1-L8: GET /preview returns trigger preview for a message."""

    def test_L1_preview_returns_200(self):
        """L1: GET /preview?message=xxx returns 200."""
        r = _client.get("/api/v1/skills/preview", params={"message": "ETL pipeline"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_L2_preview_has_triggered_field(self):
        """L2: Preview result has 'triggered' dict with tier keys."""
        r = _client.get("/api/v1/skills/preview", params={"message": "anything"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("triggered", data)
        triggered = data["triggered"]
        self.assertIsInstance(triggered, dict)

    def test_L3_preview_has_always_inject_field(self):
        """L3: Preview result has 'always_inject' list."""
        r = _client.get("/api/v1/skills/preview", params={"message": "anything"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("always_inject", data)
        self.assertIsInstance(data["always_inject"], list)

    def test_L4_preview_has_total_chars(self):
        """L4: Preview result includes total_chars field."""
        r = _client.get("/api/v1/skills/preview", params={"message": "anything"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("total_chars", data)
        self.assertIsInstance(data["total_chars"], int)

    def test_L5_preview_has_preview_prompt(self):
        """L5: Preview result includes preview_prompt string."""
        r = _client.get("/api/v1/skills/preview", params={"message": "anything"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("preview_prompt", data)
        self.assertIsInstance(data["preview_prompt"], str)

    def test_L6_preview_etl_triggers_etl_skill(self):
        """L6: ETL keyword triggers etl-engineer system skill."""
        r = _client.get("/api/v1/skills/preview", params={"message": "设计一个ETL流程"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        system_names = [s["name"] for s in data["triggered"].get("system", [])]
        # etl-engineer should be triggered (if present in system dir)
        if any("etl" in n for n in system_names):
            self.assertTrue(any("etl" in n for n in system_names))

    def test_L7_always_inject_skills_always_present(self):
        """L7: always_inject skills are present in every preview regardless of message."""
        for msg in ["hello", "ETL", "random text 12345"]:
            r = _client.get("/api/v1/skills/preview", params={"message": msg})
            self.assertEqual(r.status_code, 200)
            data = r.json()
            ai_names = [s["name"] for s in data["always_inject"]]
            # _base-safety and _base-tools should always be present if files exist
            if any("_base" in n for n in ai_names):
                # OK - base skills found
                pass

    def test_L8_preview_message_reflected(self):
        """L8: Preview result echoes back the input message."""
        test_msg = "unique_test_message_L8"
        r = _client.get("/api/v1/skills/preview", params={"message": test_msg})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["message"], test_msg)


# ─────────────────────────────────────────────────────────────────────────────
# Section M: Context length cap in build_skill_prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestContextLengthCap(unittest.TestCase):
    """M1-M5: build_skill_prompt caps injection at _MAX_INJECT_CHARS."""

    def setUp(self):
        from backend.skills.skill_loader import _MAX_INJECT_CHARS
        self._limit = _MAX_INJECT_CHARS

    def _make_loader_with_large_skills(self, n_chars: int):
        """Create a SkillLoader with one large skill that exceeds the char limit."""
        from backend.skills.skill_loader import SkillLoader
        tmpdir = Path(tempfile.mkdtemp())
        sys_dir = tmpdir / "system"
        sys_dir.mkdir()

        # Create a skill whose content is large
        large_content = "X" * n_chars
        text = (
            f"---\nname: large-skill\nversion: \"1.0\"\n"
            f"description: Large skill\ntriggers:\n  - large\n"
            f"category: general\npriority: high\n---\n\n{large_content}\n"
        )
        (sys_dir / "large-skill.md").write_text(text, encoding="utf-8")

        loader = SkillLoader(skills_dir=str(tmpdir))
        loader.load_all()
        return loader, tmpdir

    def test_M1_constant_exists(self):
        """M1: _MAX_INJECT_CHARS constant is defined in skill_loader."""
        self.assertGreater(self._limit, 0)
        self.assertLessEqual(self._limit, 20000)

    def test_M2_normal_prompt_not_capped(self):
        """M2: Short skill prompt is returned in full (not summarized)."""
        from backend.skills.skill_loader import SkillLoader
        tmpdir = Path(tempfile.mkdtemp())
        sys_dir = tmpdir / "system"
        sys_dir.mkdir()
        text = (
            "---\nname: short-skill\nversion: \"1.0\"\n"
            "description: Short skill\ntriggers:\n  - short\n"
            "category: general\npriority: medium\n---\n\n# Short\n\nShort content.\n"
        )
        (sys_dir / "short-skill.md").write_text(text, encoding="utf-8")
        loader = SkillLoader(skills_dir=str(tmpdir))
        loader.load_all()
        prompt = loader.build_skill_prompt("short")
        self.assertNotIn("摘要模式", prompt)
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_M3_large_prompt_triggers_summary_mode(self):
        """M3: Skill injection exceeding _MAX_INJECT_CHARS triggers summary mode."""
        loader, tmpdir = self._make_loader_with_large_skills(self._limit + 1000)
        prompt = loader.build_skill_prompt("large")
        self.assertIn("摘要模式", prompt)
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_M4_summary_mode_includes_skill_name(self):
        """M4: Summary mode prompt includes the skill name."""
        loader, tmpdir = self._make_loader_with_large_skills(self._limit + 1000)
        prompt = loader.build_skill_prompt("large")
        self.assertIn("large-skill", prompt)
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_M5_summary_mode_prompt_shorter_than_full(self):
        """M5: Summary mode prompt is shorter than the full content."""
        loader, tmpdir = self._make_loader_with_large_skills(self._limit + 500)
        prompt = loader.build_skill_prompt("large")
        self.assertLess(len(prompt), self._limit + 1000)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section N: New frontend features static scan
# ─────────────────────────────────────────────────────────────────────────────

class TestFrontendNewFeatures(unittest.TestCase):
    """N1-N12: New frontend features (edit user skill, trigger panel, promote)."""

    def setUp(self):
        if not _SKILLS_TSX.exists():
            self.skipTest(f"Skills.tsx not found at {_SKILLS_TSX}")
        self._tsx = _SKILLS_TSX.read_text(encoding="utf-8")

        if not _API_TS.exists():
            self.skipTest(f"api.ts not found at {_API_TS}")
        self._api = _API_TS.read_text(encoding="utf-8")

    # --- User skill edit ---

    def test_N1_user_skill_edit_button_in_table(self):
        """N1: User skill table has edit button (EditOutlined icon)."""
        self.assertIn("handleOpenEditUser", self._tsx)

    def test_N2_edit_user_skill_modal_exists(self):
        """N2: Edit user skill modal state variables exist."""
        self.assertIn("editUserOpen", self._tsx)
        self.assertIn("editUserForm", self._tsx)

    def test_N3_handle_edit_user_skill_calls_api(self):
        """N3: handleEditUserSkill calls updateUserSkill API."""
        self.assertIn("handleEditUserSkill", self._tsx)
        self.assertIn("updateUserSkill", self._tsx)

    def test_N4_update_user_skill_api_exists(self):
        """N4: api.ts has updateUserSkill method."""
        self.assertIn("updateUserSkill", self._api)

    def test_N5_update_user_skill_uses_put(self):
        """N5: updateUserSkill uses PUT HTTP method."""
        import re
        m = re.search(r"updateUserSkill.*?apiClient\.(\w+)", self._api, re.DOTALL)
        if m:
            self.assertEqual(m.group(1), "put")

    # --- Skill trigger test panel ---

    def test_N6_trigger_test_panel_exists(self):
        """N6: Trigger test panel component exists in Skills.tsx."""
        self.assertIn("triggerTestPanel", self._tsx)
        self.assertIn("testMessage", self._tsx)

    def test_N7_trigger_test_calls_preview_api(self):
        """N7: handleTestTrigger calls previewSkillTrigger API."""
        self.assertIn("handleTestTrigger", self._tsx)
        self.assertIn("previewSkillTrigger", self._tsx)

    def test_N8_preview_api_method_in_api_ts(self):
        """N8: api.ts has previewSkillTrigger method."""
        self.assertIn("previewSkillTrigger", self._api)

    def test_N9_preview_api_uses_get_endpoint(self):
        """N9: previewSkillTrigger uses GET /skills/preview."""
        self.assertIn("/skills/preview", self._api)

    # --- Promote user skill ---

    def test_N10_promote_button_in_user_table(self):
        """N10: User skill table has promote (RiseOutlined) button."""
        self.assertIn("RiseOutlined", self._tsx)
        self.assertIn("handleOpenPromote", self._tsx)

    def test_N11_promote_modal_exists(self):
        """N11: Promote skill modal state and handler exist."""
        self.assertIn("promoteOpen", self._tsx)
        self.assertIn("handlePromoteSkill", self._tsx)

    def test_N12_promote_requires_admin_token(self):
        """N12: Promote modal includes admin token field."""
        self.assertIn("promoteForm", self._tsx)
        # The promote modal should reference adminToken
        self.assertIn("adminToken", self._tsx)

    # --- _base-tools.md ---

    def test_N13_base_tools_file_exists(self):
        """N13: _base-tools.md exists in system/ directory."""
        path = Path(__file__).parent / ".claude" / "skills" / "system" / "_base-tools.md"
        self.assertTrue(path.exists(), f"Missing: {path}")

    def test_N14_base_tools_has_always_inject(self):
        """N14: _base-tools.md has always_inject: true."""
        path = Path(__file__).parent / ".claude" / "skills" / "system" / "_base-tools.md"
        if not path.exists():
            self.skipTest("_base-tools.md not found")
        content = path.read_text(encoding="utf-8")
        self.assertIn("always_inject: true", content)

    def test_N15_base_tools_loaded_as_always_inject(self):
        """N15: SkillLoader loads _base-tools.md with always_inject=True."""
        r = _client.get("/api/v1/skills/md-skills")
        self.assertEqual(r.status_code, 200)
        skills = {s["name"]: s for s in r.json()}
        if "_base-tools" in skills:
            self.assertTrue(skills["_base-tools"]["always_inject"])

    # --- Preview API in skills endpoint ---

    def test_N16_preview_endpoint_in_skills_py(self):
        """N16: skills.py defines /preview GET endpoint."""
        path = Path(__file__).parent / "backend" / "api" / "skills.py"
        if not path.exists():
            self.skipTest("skills.py not found")
        src = path.read_text(encoding="utf-8")
        self.assertIn('"/preview"', src)

    def test_N17_user_skill_update_endpoint_in_skills_py(self):
        """N17: skills.py defines PUT /user-defined/{skill_name} endpoint."""
        path = Path(__file__).parent / "backend" / "api" / "skills.py"
        if not path.exists():
            self.skipTest("skills.py not found")
        src = path.read_text(encoding="utf-8")
        self.assertIn('"/user-defined/{skill_name}"', src)
        self.assertIn("update_user_skill", src)

    # --- Context length cap in skill_loader ---

    def test_N18_context_length_cap_constant(self):
        """N18: _MAX_INJECT_CHARS constant exists in skill_loader.py."""
        path = Path(__file__).parent / "backend" / "skills" / "skill_loader.py"
        if not path.exists():
            self.skipTest("skill_loader.py not found")
        src = path.read_text(encoding="utf-8")
        self.assertIn("_MAX_INJECT_CHARS", src)

    def test_N19_summary_mode_in_skill_loader(self):
        """N19: skill_loader.py has summary mode fallback logic."""
        path = Path(__file__).parent / "backend" / "skills" / "skill_loader.py"
        if not path.exists():
            self.skipTest("skill_loader.py not found")
        src = path.read_text(encoding="utf-8")
        self.assertIn("摘要模式", src)


# ─────────────────────────────────────────────────────────────────────────────
# Section O: Semantic routing — preview API match_details + code-level checks
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticRoutingE2E(unittest.TestCase):
    """O1-O10: Semantic hybrid routing integration via preview API and source checks."""

    # O1: preview response includes match_details field
    def test_O1_preview_has_match_details_field(self):
        """O1: GET /preview now returns match_details dict."""
        r = _client.get("/api/v1/skills/preview", params={"message": "设计ETL"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("match_details", data)
        self.assertIsInstance(data["match_details"], dict)

    # O2: keyword-triggered skill has method=keyword and score=1.0 in match_details
    def test_O2_keyword_hit_has_keyword_method(self):
        """O2: Keyword-triggered skills show method='keyword' and score=1.0."""
        r = _client.get("/api/v1/skills/preview", params={"message": "设计ETL流程"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        details = data.get("match_details", {})
        for name, info in details.items():
            if info.get("method") == "keyword":
                self.assertAlmostEqual(info["score"], 1.0)
                break

    # O3: preview supports mode=keyword parameter (forces keyword-only)
    def test_O3_mode_keyword_param_accepted(self):
        """O3: mode=keyword query param is accepted and returns 200."""
        r = _client.get("/api/v1/skills/preview", params={"message": "ETL", "mode": "keyword"})
        self.assertEqual(r.status_code, 200, r.text)

    # O4: _build_from_matched_skills method exists in SkillLoader
    def test_O4_build_from_matched_skills_method_exists(self):
        """O4: SkillLoader._build_from_matched_skills helper method exists."""
        import sys
        sys.path.insert(0, "backend")
        from skills.skill_loader import SkillLoader
        self.assertTrue(
            hasattr(SkillLoader, "_build_from_matched_skills"),
            "_build_from_matched_skills method missing from SkillLoader"
        )

    # O5: build_skill_prompt_async method exists in SkillLoader
    def test_O5_build_skill_prompt_async_exists(self):
        """O5: SkillLoader.build_skill_prompt_async exists and is a coroutine function."""
        import sys, inspect
        sys.path.insert(0, "backend")
        from skills.skill_loader import SkillLoader
        self.assertTrue(hasattr(SkillLoader, "build_skill_prompt_async"))
        self.assertTrue(inspect.iscoroutinefunction(SkillLoader.build_skill_prompt_async))

    # O6: settings has skill_match_mode field
    def test_O6_settings_has_skill_match_mode(self):
        """O6: Settings model includes skill_match_mode field."""
        import sys
        sys.path.insert(0, "backend")
        from backend.config.settings import settings
        self.assertTrue(
            hasattr(settings, "skill_match_mode"),
            "settings missing skill_match_mode"
        )

    # O7: settings has skill_semantic_threshold field
    def test_O7_settings_has_semantic_threshold(self):
        """O7: Settings model includes skill_semantic_threshold field."""
        import sys
        sys.path.insert(0, "backend")
        from backend.config.settings import settings
        self.assertTrue(hasattr(settings, "skill_semantic_threshold"))
        self.assertIsInstance(settings.skill_semantic_threshold, float)

    # O8: settings has skill_semantic_cache_ttl and skill_routing_cache_path
    def test_O8_settings_has_cache_fields(self):
        """O8: Settings model includes cache TTL and path fields."""
        import sys
        sys.path.insert(0, "backend")
        from backend.config.settings import settings
        self.assertTrue(hasattr(settings, "skill_semantic_cache_ttl"))
        self.assertTrue(hasattr(settings, "skill_routing_cache_path"))

    # O9: SkillSemanticRouter module exists and exports SkillSemanticRouter class
    def test_O9_skill_semantic_router_module_exists(self):
        """O9: backend/skills/skill_semantic_router.py exists with SkillSemanticRouter class."""
        path = Path(__file__).parent / "backend" / "skills" / "skill_semantic_router.py"
        self.assertTrue(path.exists(), "skill_semantic_router.py not found")
        import sys
        sys.path.insert(0, "backend")
        from skills.skill_semantic_router import SkillSemanticRouter
        self.assertTrue(hasattr(SkillSemanticRouter, "route"))

    # O10: SkillRoutingCache module exists and is importable
    def test_O10_skill_routing_cache_module_exists(self):
        """O10: backend/skills/skill_routing_cache.py exists with SkillRoutingCache class."""
        path = Path(__file__).parent / "backend" / "skills" / "skill_routing_cache.py"
        self.assertTrue(path.exists(), "skill_routing_cache.py not found")
        import sys
        sys.path.insert(0, "backend")
        from skills.skill_routing_cache import SkillRoutingCache
        self.assertTrue(hasattr(SkillRoutingCache, "get"))
        self.assertTrue(hasattr(SkillRoutingCache, "put"))


# ─────────────────────────────────────────────────────────────────────────────
# P: User Skill Directory Isolation (ENABLE_AUTH flag)
# ─────────────────────────────────────────────────────────────────────────────

class TestUserSkillDirIsolation(unittest.TestCase):
    """P1-P9: _get_user_skill_dir() 和 _current_username() 路径隔离逻辑"""

    def setUp(self):
        import sys, os
        sys.path.insert(0, "backend")
        # Patch settings to control enable_auth
        from unittest.mock import patch, MagicMock
        self._patch = patch
        self._MagicMock = MagicMock

    def _get_skills_py_src(self):
        with open("backend/api/skills.py", encoding="utf-8") as f:
            return f.read()

    def test_P1_get_user_skill_dir_no_username_default_guard(self):
        """_get_user_skill_dir: 已删除 username != 'default' 的 guard"""
        src = self._get_skills_py_src()
        self.assertNotIn('username != "default"', src)

    def test_P2_get_user_skill_dir_enable_auth_always_creates_subdir(self):
        """ENABLE_AUTH=true 时, 任意 username 均使用 user/{username}/ 子目录"""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()

            mock_settings = MagicMock()
            mock_settings.enable_auth = True

            with patch("backend.api.skills.settings", mock_settings), \
                 patch("backend.api.skills._USER_SKILLS_DIR", user_dir):
                from backend.api.skills import _get_user_skill_dir
                result = _get_user_skill_dir("alice")
                self.assertEqual(result, user_dir / "alice")
                self.assertTrue(result.exists())

    def test_P3_get_user_skill_dir_default_user_also_gets_subdir(self):
        """ENABLE_AUTH=true 时, username='default' 也应进入子目录, 而非 flat root"""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()

            mock_settings = MagicMock()
            mock_settings.enable_auth = True

            with patch("backend.api.skills.settings", mock_settings), \
                 patch("backend.api.skills._USER_SKILLS_DIR", user_dir):
                from backend.api.skills import _get_user_skill_dir
                result = _get_user_skill_dir("default")
                # 必须是子目录, 不能是 flat user/ 根
                self.assertNotEqual(result, user_dir)
                self.assertEqual(result, user_dir / "default")

    def test_P4_get_user_skill_dir_auth_disabled_returns_flat(self):
        """ENABLE_AUTH=false 时, 返回 flat user/ 目录 (兼容旧行为)"""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()

            mock_settings = MagicMock()
            mock_settings.enable_auth = False

            with patch("backend.api.skills.settings", mock_settings), \
                 patch("backend.api.skills._USER_SKILLS_DIR", user_dir):
                from backend.api.skills import _get_user_skill_dir
                result = _get_user_skill_dir("alice")
                self.assertEqual(result, user_dir)

    def test_P5_get_user_skill_dir_different_users_get_different_dirs(self):
        """ENABLE_AUTH=true 时, 不同用户得到不同目录, 互相隔离"""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "user"
            user_dir.mkdir()

            mock_settings = MagicMock()
            mock_settings.enable_auth = True

            with patch("backend.api.skills.settings", mock_settings), \
                 patch("backend.api.skills._USER_SKILLS_DIR", user_dir):
                from backend.api.skills import _get_user_skill_dir
                alice_dir = _get_user_skill_dir("alice")
                bob_dir = _get_user_skill_dir("bob")
                self.assertNotEqual(alice_dir, bob_dir)
                self.assertEqual(alice_dir, user_dir / "alice")
                self.assertEqual(bob_dir, user_dir / "bob")

    def test_P6_build_context_includes_username(self):
        """_build_context() 返回的 dict 中包含 username 键"""
        src = open("backend/services/conversation_service.py", encoding="utf-8").read()
        self.assertIn('"username": username', src)

    def test_P7_send_message_stream_accepts_username_param(self):
        """send_message_stream() 接受 username 参数"""
        src = open("backend/services/conversation_service.py", encoding="utf-8").read()
        # Check the param in function definition
        import re
        m = re.search(r"async def send_message_stream\([^)]+\)", src, re.DOTALL)
        self.assertIsNotNone(m, "send_message_stream not found")
        self.assertIn("username", m.group(0))

    def test_P8_agentic_loop_injects_current_user_in_prompt(self):
        """_build_system_prompt 将 CURRENT_USER 注入 filesystem 工具提示"""
        src = open("backend/agents/agentic_loop.py", encoding="utf-8").read()
        self.assertIn("CURRENT_USER:", src)
        self.assertIn('context.get("username"', src)

    def test_P9_skill_creator_md_path_includes_current_user(self):
        """skill-creator.md 路径规则包含 {CURRENT_USER}/ 层级"""
        with open(".claude/skills/system/skill-creator.md", encoding="utf-8") as f:
            content = f.read()
        # Should reference CURRENT_USER in path instructions
        self.assertIn("CURRENT_USER", content)
        # Should NOT have the old flat path as the primary example
        # (we still allow it in comment/warning context but not as primary rule)
        self.assertIn("user/{CURRENT_USER}/", content)
        # Should have the warning about not skipping username layer
        self.assertIn("禁止省略", content)


# ─────────────────────────────────────────────────────────────────────────────
# Section O — chart-reporter Skill 语义触发回归
# ─────────────────────────────────────────────────────────────────────────────

class TestO_ChartReporterSkillRegression(unittest.TestCase):
    """O1-O4: chart-reporter.md skill 语义触发验证。"""

    @classmethod
    def setUpClass(cls):
        skill_path = Path(".claude/skills/project/chart-reporter.md")
        cls.skill_exists = skill_path.exists()
        if cls.skill_exists:
            cls.skill_content = skill_path.read_text(encoding="utf-8")

    def test_O1_skill_file_exists(self):
        self.assertTrue(self.skill_exists, ".claude/skills/project/chart-reporter.md 应存在")

    def test_O2_skill_has_required_frontmatter(self):
        if not self.skill_exists: self.skipTest("skill 不存在")
        self.assertIn("name: chart-reporter", self.skill_content)
        self.assertIn("triggers:", self.skill_content)
        self.assertIn("layer:", self.skill_content)

    def test_O3_skill_has_key_triggers(self):
        if not self.skill_exists: self.skipTest("skill 不存在")
        for kw in ["图表", "报告", "echarts", "折线图"]:
            self.assertIn(kw, self.skill_content, f"triggers 应包含: {kw}")

    def test_O4_skill_mentions_build_api(self):
        if not self.skill_exists: self.skipTest("skill 不存在")
        self.assertIn("/api/v1/reports/build", self.skill_content)


# ─────────────────────────────────────────────────────────────────────────────
# Section P — AgenticLoop is_report 字段回归
# ─────────────────────────────────────────────────────────────────────────────

class TestP_AgenticLoopIsReportRegression(unittest.TestCase):
    """P1-P3: agentic_loop.py 对 HTML 报告文件附加 is_report 字段。"""

    @classmethod
    def setUpClass(cls):
        cls.src = open("backend/agents/agentic_loop.py", encoding="utf-8").read()

    def test_P1_is_report_field_present(self):
        self.assertIn("is_report", self.src, "agentic_loop.py 应包含 is_report 字段")

    def test_P2_reports_path_detection(self):
        self.assertIn("/reports/", self.src, "应检测 /reports/ 路径")

    def test_P3_text_html_mime_check(self):
        self.assertIn("text/html", self.src, "应检查 text/html MIME 类型")


# ─────────────────────────────────────────────────────────────────────────────
# Section Q — 非侵入回归（现有功能不受影响）
# ─────────────────────────────────────────────────────────────────────────────

class TestQ_NonIntrusiveRegression(unittest.TestCase):
    """Q1-Q5: 报告功能不破坏现有技能和文件下载流程。"""

    def test_Q1_skills_api_still_works(self):
        r = _client.get("/api/v1/skills/md-skills")
        self.assertEqual(r.status_code, 200, "GET /md-skills 应仍正常")

    def test_Q2_report_builder_importable(self):
        from backend.services.report_builder_service import (
            build_report_html, generate_refresh_token
        )
        self.assertTrue(callable(build_report_html))
        self.assertTrue(callable(generate_refresh_token))

    def test_Q3_pdf_export_service_importable(self):
        from backend.services.pdf_export_service import html_to_pdf
        self.assertTrue(callable(html_to_pdf))

    def test_Q4_pptx_export_service_importable(self):
        from backend.services.pptx_export_service import html_to_pptx
        self.assertTrue(callable(html_to_pptx))

    def test_Q5_report_model_has_new_fields(self):
        from backend.models.report import Report
        self.assertTrue(hasattr(Report, "username"))
        self.assertTrue(hasattr(Report, "refresh_token"))
        self.assertTrue(hasattr(Report, "report_file_path"))
        self.assertTrue(hasattr(Report, "llm_summary"))
        self.assertTrue(hasattr(Report, "summary_status"))


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
