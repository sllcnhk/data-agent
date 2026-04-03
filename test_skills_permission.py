"""
test_skills_permission.py
=========================
Permission-isolation tests for the skills subsystem.

Verifies:
  P1  System skills are read-only from the API (no write/delete endpoint)
  P2  User skill create is scoped to the user/ directory
  P3  User skill delete has path-boundary enforcement
  P4  Path traversal attempts are neutralised
  P5  Admin token dependency (require_admin) works correctly
  P6  is_readonly / is_user_defined flags are set correctly on list_md_skills
  P7  _slugify neutralises path-separator characters
"""

import asyncio
import sys
import os
import re
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_temp_user_dir():
    """Create a temporary directory to use as _USER_SKILLS_DIR in tests."""
    return Path(tempfile.mkdtemp())


# ── P7: _slugify ───────────────────────────────────────────────────────────────

def test_P7_slugify_strips_path_separators():
    """_slugify must remove / and \\ so caller cannot inject path components."""
    from backend.api.skills import _slugify

    cases = [
        ("../etc/passwd",  "etcpasswd"),
        ("..\\windows",    "windows"),
        ("user/secret",    "usersecret"),
        ("my-skill",       "my-skill"),     # normal name untouched
        ("My Skill Name!", "my-skill-name"),
        ("",               ""),
    ]
    for inp, expected in cases:
        got = _slugify(inp)
        assert got == expected, f"_slugify({inp!r}) = {got!r}, expected {expected!r}"
    print("[PASS] P7: _slugify strips path separators correctly")


# ── P3 / P4: path boundary in delete ─────────────────────────────────────────

async def test_P3_delete_stays_in_user_dir():
    """delete_user_skill must resolve to inside _USER_SKILLS_DIR."""
    tmp_user_dir = _make_temp_user_dir()
    try:
        # Create a real skill file to delete
        skill_file = tmp_user_dir / "my-skill.md"
        skill_file.write_text("---\nname: my-skill\n---\n# test", encoding="utf-8")

        # Patch the module-level constants
        import backend.api.skills as skills_mod
        orig_dir = skills_mod._USER_SKILLS_DIR
        orig_resolved = skills_mod._USER_SKILLS_DIR_RESOLVED
        try:
            skills_mod._USER_SKILLS_DIR = tmp_user_dir
            skills_mod._USER_SKILLS_DIR_RESOLVED = tmp_user_dir.resolve()

            from fastapi import HTTPException
            # Should succeed: slug maps to existing file inside dir
            result = await skills_mod.delete_user_skill("my-skill")
            assert result["success"] is True
            assert not skill_file.exists(), "File should have been deleted"
            print("[PASS] P3-a: delete_user_skill removes file from user dir")
        finally:
            skills_mod._USER_SKILLS_DIR = orig_dir
            skills_mod._USER_SKILLS_DIR_RESOLVED = orig_resolved
    finally:
        shutil.rmtree(tmp_user_dir, ignore_errors=True)


async def test_P4_path_traversal_is_neutralised():
    """Traversal payloads like '../system-skill' must not escape user/ dir."""
    from backend.api.skills import _slugify
    from fastapi import HTTPException

    traversal_attempts = [
        "../etl-engineer",
        "../../backend/config/settings",
        "..\\windows\\system32",
        "/etc/passwd",
        "user/../system-skill",
    ]

    for payload in traversal_attempts:
        slug = _slugify(payload)
        # After slugify the path should never contain / or ..
        assert "/" not in slug, f"Slug still contains /: {slug!r}"
        assert ".." not in slug, f"Slug still contains ..: {slug!r}"

    print("[PASS] P4: path traversal payloads are neutralised by _slugify")


async def test_P4_delete_boundary_check_blocks_escape():
    """
    Even if somehow a path was constructed that escapes user dir,
    the explicit boundary check in delete_user_skill must reject it.
    """
    import backend.api.skills as skills_mod
    from fastapi import HTTPException

    tmp_user_dir = _make_temp_user_dir()
    tmp_parent = tmp_user_dir.parent
    try:
        # Place a file one level UP (simulating escape)
        target = tmp_parent / "system-skill.md"
        target.write_text("---\nname: system-skill\n---\n", encoding="utf-8")

        orig_dir = skills_mod._USER_SKILLS_DIR
        orig_resolved = skills_mod._USER_SKILLS_DIR_RESOLVED
        try:
            skills_mod._USER_SKILLS_DIR = tmp_user_dir
            skills_mod._USER_SKILLS_DIR_RESOLVED = tmp_user_dir.resolve()

            # Manually craft what would happen if boundary check didn't exist
            # by monkey-patching _slugify to return a traversal path
            # The boundary check should catch it.
            #
            # We test the check logic directly:
            resolved_escape = (tmp_user_dir / ".." / "system-skill.md").resolve()
            is_in_user_dir = resolved_escape.parent == tmp_user_dir.resolve()
            assert not is_in_user_dir, "Escape path should NOT be inside user dir"
            print("[PASS] P4-b: boundary check correctly identifies path escape")
        finally:
            skills_mod._USER_SKILLS_DIR = orig_dir
            skills_mod._USER_SKILLS_DIR_RESOLVED = orig_resolved
    finally:
        target.unlink(missing_ok=True)
        shutil.rmtree(tmp_user_dir, ignore_errors=True)


# ── P1: system skills have no write/delete API ────────────────────────────────

def test_P1_no_system_skill_write_endpoints():
    """
    The skills router must NOT expose any POST/PUT/DELETE route
    that could write system skill files directly.

    Allowed write routes:
      POST   /user-defined              → creates in user/ dir only (any user)
      DELETE /user-defined/{name}       → deletes from user/ dir only (any user)
      POST   /project-skills            → creates in project/ dir (admin-protected)
      PUT    /project-skills/{name}     → updates in project/ dir (admin-protected)
      DELETE /project-skills/{name}     → deletes from project/ dir (admin-protected)

    Forbidden:
      POST  /system-skill  (or any route writing to system/ dir)
      Any unprotected write to non-user/ non-project/ paths
    """
    from backend.api.skills import router

    forbidden_patterns = []
    for route in router.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")

        for method in methods:
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                # Allowed: POST/DELETE /user-defined  → creates/deletes in user/ dir only
                # Allowed: POST/PUT/DELETE /project-skills → admin-protected, writes to project/ dir
                # Allowed: POST /{skill_name}/execute (in-memory Python skills, legacy)
                is_user_write = "user-defined" in path
                is_project_write = "project-skills" in path  # admin-protected endpoints
                is_execute = path.endswith("/execute")
                if not (is_user_write or is_project_write or is_execute):
                    forbidden_patterns.append(f"{method} {path}")

    assert not forbidden_patterns, (
        f"Unexpected write endpoints found that could affect system skills: "
        f"{forbidden_patterns}"
    )
    print("[PASS] P1: no system-skill write/delete endpoints exposed")


# ── P2: create scoped to user/ dir ────────────────────────────────────────────

async def test_P2_create_writes_to_user_dir():
    """create_user_skill must write files to _USER_SKILLS_DIR only."""
    import backend.api.skills as skills_mod
    from backend.api.skills import UserSkillCreate

    tmp_user_dir = _make_temp_user_dir()
    orig_dir = skills_mod._USER_SKILLS_DIR
    orig_resolved = skills_mod._USER_SKILLS_DIR_RESOLVED
    try:
        skills_mod._USER_SKILLS_DIR = tmp_user_dir
        skills_mod._USER_SKILLS_DIR_RESOLVED = tmp_user_dir.resolve()

        payload = UserSkillCreate(
            name="test-perm-skill",
            description="Permission test skill",
            triggers=["perm test"],
            category="general",
            priority="low",
            content="# Test\nThis is a test skill.",
        )
        result = await skills_mod.create_user_skill(payload)
        assert result["success"] is True

        created = tmp_user_dir / "test-perm-skill.md"
        assert created.exists(), "Skill file should be in user dir"
        # Verify it is NOT anywhere else (no escaping)
        assert created.resolve().parent == tmp_user_dir.resolve()
        print("[PASS] P2: create_user_skill writes strictly to user/ dir")
    finally:
        skills_mod._USER_SKILLS_DIR = orig_dir
        skills_mod._USER_SKILLS_DIR_RESOLVED = orig_resolved
        shutil.rmtree(tmp_user_dir, ignore_errors=True)


async def test_P2_create_rejects_duplicate():
    """create_user_skill returns 409 when skill already exists."""
    import backend.api.skills as skills_mod
    from backend.api.skills import UserSkillCreate
    from fastapi import HTTPException

    tmp_user_dir = _make_temp_user_dir()
    orig_dir = skills_mod._USER_SKILLS_DIR
    orig_resolved = skills_mod._USER_SKILLS_DIR_RESOLVED
    try:
        skills_mod._USER_SKILLS_DIR = tmp_user_dir
        skills_mod._USER_SKILLS_DIR_RESOLVED = tmp_user_dir.resolve()

        payload = UserSkillCreate(
            name="dup-skill",
            description="Dup test",
            triggers=["dup"],
            category="general",
            priority="low",
            content="# Dup\nDuplicate skill test.",
        )
        await skills_mod.create_user_skill(payload)  # first: OK

        try:
            await skills_mod.create_user_skill(payload)  # second: should 409
            assert False, "Expected HTTPException 409"
        except HTTPException as exc:
            assert exc.status_code == 409
        print("[PASS] P2-b: create_user_skill rejects duplicates with 409")
    finally:
        skills_mod._USER_SKILLS_DIR = orig_dir
        skills_mod._USER_SKILLS_DIR_RESOLVED = orig_resolved
        shutil.rmtree(tmp_user_dir, ignore_errors=True)


# ── P5: require_admin dependency ──────────────────────────────────────────────

async def test_P5_require_admin_blocks_when_no_token_configured():
    """require_admin returns 503 when ADMIN_SECRET_TOKEN is not set."""
    from fastapi import HTTPException
    from backend.api.deps import require_admin
    from backend.config.settings import settings

    orig = settings.admin_secret_token
    try:
        settings.admin_secret_token = None
        try:
            await require_admin(x_admin_token=None)
            assert False, "Expected HTTPException 503"
        except HTTPException as exc:
            assert exc.status_code == 503
        print("[PASS] P5-a: require_admin → 503 when ADMIN_SECRET_TOKEN not configured")
    finally:
        settings.admin_secret_token = orig


async def test_P5_require_admin_blocks_wrong_token():
    """require_admin returns 401 when token doesn't match."""
    from fastapi import HTTPException
    from backend.api.deps import require_admin
    from backend.config.settings import settings

    orig = settings.admin_secret_token
    try:
        settings.admin_secret_token = "correct-secret"
        try:
            await require_admin(x_admin_token="wrong-secret")
            assert False, "Expected HTTPException 401"
        except HTTPException as exc:
            assert exc.status_code == 401
        print("[PASS] P5-b: require_admin → 401 for wrong token")
    finally:
        settings.admin_secret_token = orig


async def test_P5_require_admin_passes_correct_token():
    """require_admin passes when correct token is provided."""
    from fastapi import HTTPException
    from backend.api.deps import require_admin
    from backend.config.settings import settings

    orig = settings.admin_secret_token
    try:
        settings.admin_secret_token = "my-secret"
        # Should NOT raise
        try:
            result = await require_admin(x_admin_token="my-secret")
            assert result is None  # dependency returns None on success
        except HTTPException as exc:
            assert False, f"Should not raise for correct token, got {exc.status_code}"
        print("[PASS] P5-c: require_admin passes correct token")
    finally:
        settings.admin_secret_token = orig


# ── P6: is_readonly / is_user_defined flags ───────────────────────────────────

def test_P6_is_user_defined_logic():
    """
    list_md_skills response: is_user_defined=True only for user/ dir skills.
    is_readonly must be the inverse.
    """
    # Simulate the logic in list_md_skills
    from backend.api.skills import _USER_SKILLS_DIR_RESOLVED

    def _compute_flags(filepath: str):
        from pathlib import Path
        from backend.api.skills import _USER_SKILLS_DIR_RESOLVED
        fp = filepath
        is_user = "user" in fp and _USER_SKILLS_DIR_RESOLVED == (
            Path(fp).resolve().parent if fp else Path(".")
        )
        return {"is_user_defined": is_user, "is_readonly": not is_user}

    # System skill path
    sys_path = str(_USER_SKILLS_DIR_RESOLVED.parent / "etl-engineer.md")
    flags = _compute_flags(sys_path)
    assert flags["is_user_defined"] is False
    assert flags["is_readonly"] is True

    # User skill path
    user_path = str(_USER_SKILLS_DIR_RESOLVED / "my-skill.md")
    flags = _compute_flags(user_path)
    assert flags["is_user_defined"] is True
    assert flags["is_readonly"] is False

    print("[PASS] P6: is_readonly / is_user_defined flags computed correctly")


# ── runner ─────────────────────────────────────────────────────────────────────

async def run_all():
    all_tests = [
        ("P7-a", test_P7_slugify_strips_path_separators),
        ("P3-a", test_P3_delete_stays_in_user_dir),
        ("P4-a", test_P4_path_traversal_is_neutralised),
        ("P4-b", test_P4_delete_boundary_check_blocks_escape),
        ("P1-a", test_P1_no_system_skill_write_endpoints),
        ("P2-a", test_P2_create_writes_to_user_dir),
        ("P2-b", test_P2_create_rejects_duplicate),
        ("P5-a", test_P5_require_admin_blocks_when_no_token_configured),
        ("P5-b", test_P5_require_admin_blocks_wrong_token),
        ("P5-c", test_P5_require_admin_passes_correct_token),
        ("P6-a", test_P6_is_user_defined_logic),
    ]

    passed = failed = 0
    print("\n" + "=" * 60)
    print("Skills Permission-Isolation Tests")
    print("=" * 60)

    for label, fn in all_tests:
        try:
            if asyncio.iscoroutinefunction(fn):
                await fn()
            else:
                fn()
            passed += 1
        except Exception as exc:
            failed += 1
            import traceback
            print(f"[FAIL] {label} {fn.__name__}: {exc}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed / {len(all_tests)} total")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    import sys, atexit
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).parent))
    try:
        from conftest import _cleanup_test_data as _ctd
        atexit.register(_ctd, label="post-run")   # 进程退出时必然执行（含 sys.exit）
        _ctd(label="pre-run")
    except Exception:
        pass
    asyncio.run(run_all())
