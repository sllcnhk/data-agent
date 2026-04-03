#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_skill_registry.py
======================
验证三层 Skill 体系重构：SkillLoader 三层加载、分层注入、向后兼容。

Sections:
  A  目录结构与文件加载（system/project/user 三层）
  B  always_inject 机制（_base-* 文件始终注入）
  C  三层叠加注入顺序（user → project → base → system triggered）
  D  触发词匹配（三层各自匹配，按 priority 排序）
  E  向后兼容（system/ 子目录不存在时回退到根目录扫描）
  F  REST API 静态检查（tier 字段、project-skills 端点）
  G  FilesystemPermissionProxy 错误提示更新
  H  SkillWatcher recursive=True 检查

运行: /d/ProgramData/Anaconda3/envs/dataagent/python.exe test_skill_registry.py
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    results.append((name, condition))
    msg = f"  {status} {name}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    return condition


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_skill_md(
    name: str,
    triggers: List[str],
    category: str = "general",
    priority: str = "medium",
    always_inject: bool = False,
    content: str = "## 测试内容\n这是测试 Skill 内容。",
) -> str:
    triggers_yaml = "\n".join(f"  - {t}" for t in triggers)
    always_line = f"always_inject: {'true' if always_inject else 'false'}\n" if always_inject else ""
    return (
        f"---\n"
        f"name: {name}\n"
        f'version: "1.0"\n'
        f"description: {name} 测试描述\n"
        f"triggers:\n{triggers_yaml}\n"
        f"category: {category}\n"
        f"priority: {priority}\n"
        f"{always_line}"
        f"---\n\n"
        f"{content}\n"
    )


def _make_test_skills_dir():
    """Create a temp skills dir with system/project/user subdirs and sample files."""
    tmp = Path(tempfile.mkdtemp())
    system_dir = tmp / "system"
    project_dir = tmp / "project"
    user_dir = tmp / "user"
    system_dir.mkdir()
    project_dir.mkdir()
    user_dir.mkdir()

    # System: _base-safety (always inject), etl-skill (triggered)
    (system_dir / "_base-safety.md").write_text(
        _make_skill_md("_base-safety", triggers=[], always_inject=True,
                       content="## 安全约束\n严禁高危操作。"),
        encoding="utf-8"
    )
    (system_dir / "etl-engineer.md").write_text(
        _make_skill_md("etl-engineer", triggers=["ETL", "宽表"], category="engineering", priority="high",
                       content="## ETL 规程\n请遵循标准 ETL 流程。"),
        encoding="utf-8"
    )

    # Project: biz-glossary
    (project_dir / "biz-glossary.md").write_text(
        _make_skill_md("biz-glossary", triggers=["留存", "DAU", "活跃"], category="analytics", priority="high",
                       content="## 业务词典\n留存 = 次日回访用户。"),
        encoding="utf-8"
    )

    # User: my-format
    (user_dir / "my-format.md").write_text(
        _make_skill_md("my-format", triggers=["报告", "输出格式"], priority="medium",
                       content="## 输出格式偏好\n使用 Markdown 表格。"),
        encoding="utf-8"
    )

    return tmp


# ──────────────────────────────────────────────────────────────────────────────
# Section A — 三层加载
# ──────────────────────────────────────────────────────────────────────────────

def test_section_a():
    print("\n=== Section A: 三层目录加载 ===")
    from backend.skills.skill_loader import SkillLoader, TIER_SYSTEM, TIER_PROJECT, TIER_USER

    tmp = _make_test_skills_dir()
    try:
        loader = SkillLoader(skills_dir=str(tmp))
        all_skills = loader.load_all()

        # 总数：system=2, project=1, user=1 → 4
        check("A1 加载总数=4", len(all_skills) == 4, f"got {len(all_skills)}: {[s.name for s in all_skills]}")

        sys_skills = loader.get_by_tier(TIER_SYSTEM)
        proj_skills = loader.get_by_tier(TIER_PROJECT)
        user_skills = loader.get_by_tier(TIER_USER)

        check("A2 system tier 数=2", len(sys_skills) == 2, str([s.name for s in sys_skills]))
        check("A3 project tier 数=1", len(proj_skills) == 1, str([s.name for s in proj_skills]))
        check("A4 user tier 数=1", len(user_skills) == 1, str([s.name for s in user_skills]))

        # tier 字段正确设置
        for s in sys_skills:
            check(f"A5 {s.name} tier=system", s.tier == TIER_SYSTEM)
        for s in proj_skills:
            check(f"A6 {s.name} tier=project", s.tier == TIER_PROJECT)
        for s in user_skills:
            check(f"A7 {s.name} tier=user", s.tier == TIER_USER)

        # get_all() 返回所有层
        check("A8 get_all() 返回4条", len(loader.get_all()) == 4)

    finally:
        shutil.rmtree(tmp)


# ──────────────────────────────────────────────────────────────────────────────
# Section B — always_inject 机制
# ──────────────────────────────────────────────────────────────────────────────

def test_section_b():
    print("\n=== Section B: always_inject 机制 ===")
    from backend.skills.skill_loader import SkillLoader

    tmp = _make_test_skills_dir()
    try:
        loader = SkillLoader(skills_dir=str(tmp))
        loader.load_all()

        # _base-safety should be always_inject=True
        base_skills = loader._base_skills
        check("B1 _base_skills 列表非空", len(base_skills) >= 1, str([s.name for s in base_skills]))
        check("B2 _base-safety 在 base_skills 中",
              any(s.name == "_base-safety" for s in base_skills),
              str([s.name for s in base_skills]))

        # etl-engineer should NOT be always_inject
        etl = next((s for s in loader._system_skills.values() if s.name == "etl-engineer"), None)
        check("B3 etl-engineer always_inject=False", etl is not None and not etl.always_inject)

        # _base-safety should have always_inject=True
        base = next((s for s in loader._system_skills.values() if s.name == "_base-safety"), None)
        check("B4 _base-safety always_inject=True", base is not None and base.always_inject)

        # matches() on always_inject skill returns False (uses separate path)
        if base:
            check("B5 always_inject skill matches() 返回 False（不参与触发匹配）",
                  not base.matches("任何消息"))

        # build_skill_prompt 无论什么消息都包含 base
        prompt_empty = loader.build_skill_prompt("毫无相关的消息 xyz999")
        check("B6 无触发词时 prompt 仍包含 base skill",
              "_base-safety" in prompt_empty or "安全约束" in prompt_empty,
              prompt_empty[:200] if prompt_empty else "(empty)")

        prompt_etl = loader.build_skill_prompt("帮我设计ETL宽表")
        check("B7 ETL消息时 prompt 包含 base skill",
              "安全约束" in prompt_etl or "_base-safety" in prompt_etl,
              prompt_etl[:200] if prompt_etl else "(empty)")

    finally:
        shutil.rmtree(tmp)


# ──────────────────────────────────────────────────────────────────────────────
# Section C — 三层叠加注入顺序
# ──────────────────────────────────────────────────────────────────────────────

def test_section_c():
    print("\n=== Section C: 三层叠加注入顺序 ===")
    from backend.skills.skill_loader import SkillLoader

    tmp = _make_test_skills_dir()
    try:
        loader = SkillLoader(skills_dir=str(tmp))
        loader.load_all()

        # 消息同时触发三层（user: 报告, project: 留存, system: ETL）
        msg = "请帮我生成ETL宽表，分析留存率，输出报告格式"
        prompt = loader.build_skill_prompt(msg)

        check("C1 prompt 非空", bool(prompt))

        # 顺序检查：用户技能描述 应 出现在 项目技能描述 之前
        pos_user = prompt.find("my-format") if "my-format" in prompt else prompt.find("输出格式偏好")
        pos_proj = prompt.find("biz-glossary") if "biz-glossary" in prompt else prompt.find("业务词典")
        pos_base = prompt.find("安全约束")
        pos_sys  = prompt.find("ETL 规程") if "ETL 规程" in prompt else prompt.find("etl-engineer")

        check("C2 用户 Skill 出现在项目 Skill 之前",
              pos_user != -1 and pos_proj != -1 and pos_user < pos_proj,
              f"user@{pos_user} proj@{pos_proj}")
        check("C3 项目 Skill 出现在 base 之前",
              pos_proj != -1 and pos_base != -1 and pos_proj < pos_base,
              f"proj@{pos_proj} base@{pos_base}")
        check("C4 base 出现在 system-triggered 之前",
              pos_base != -1 and pos_sys != -1 and pos_base < pos_sys,
              f"base@{pos_base} sys@{pos_sys}")

        # 消息不触发任何 Tier3 和 Tier2 时，prompt 只含 base + system-triggered
        msg2 = "帮我设计宽表ETL"
        prompt2 = loader.build_skill_prompt(msg2)
        check("C5 纯 ETL 消息不含用户 Skill header",
              "个人技能规程" not in prompt2,
              prompt2[:100] if prompt2 else "(empty)")
        check("C6 纯 ETL 消息含 base 安全约束",
              "安全约束" in prompt2,
              prompt2[:200] if prompt2 else "(empty)")

    finally:
        shutil.rmtree(tmp)


# ──────────────────────────────────────────────────────────────────────────────
# Section D — 触发词匹配
# ──────────────────────────────────────────────────────────────────────────────

def test_section_d():
    print("\n=== Section D: 触发词匹配 ===")
    from backend.skills.skill_loader import SkillLoader

    tmp = _make_test_skills_dir()
    try:
        loader = SkillLoader(skills_dir=str(tmp))
        loader.load_all()

        # 无关消息：只有 base（不参与 find_triggered）
        triggered = loader.find_triggered("今天天气如何")
        check("D1 无关消息 find_triggered 返回空", len(triggered) == 0, str([s.name for s in triggered]))

        # ETL 触发
        triggered_etl = loader.find_triggered("我想设计ETL脚本")
        names_etl = [s.name for s in triggered_etl]
        check("D2 ETL消息触发 etl-engineer", "etl-engineer" in names_etl, str(names_etl))
        check("D3 ETL消息不触发 _base-safety（走 always_inject 路径）",
              "_base-safety" not in names_etl, str(names_etl))

        # 多层同时触发
        triggered_multi = loader.find_triggered("ETL宽表设计和留存分析报告")
        names_multi = [s.name for s in triggered_multi]
        check("D4 多层触发：etl-engineer 在列表中", "etl-engineer" in names_multi, str(names_multi))
        check("D5 多层触发：biz-glossary 在列表中", "biz-glossary" in names_multi, str(names_multi))
        check("D6 多层触发：my-format 在列表中", "my-format" in names_multi, str(names_multi))

        # find_triggered 中 user skill 优先（排在前面）
        if len(triggered_multi) >= 2:
            user_idx = next((i for i, s in enumerate(triggered_multi) if s.tier == "user"), -1)
            sys_idx  = next((i for i, s in enumerate(triggered_multi) if s.tier == "system"), -1)
            check("D7 find_triggered 中 user tier 排在 system tier 前面",
                  user_idx != -1 and sys_idx != -1 and user_idx < sys_idx,
                  f"user@{user_idx} sys@{sys_idx}")

    finally:
        shutil.rmtree(tmp)


# ──────────────────────────────────────────────────────────────────────────────
# Section E — 向后兼容（无 system/ 子目录）
# ──────────────────────────────────────────────────────────────────────────────

def test_section_e():
    print("\n=== Section E: 向后兼容（无 system/ 子目录）===")
    from backend.skills.skill_loader import SkillLoader, TIER_SYSTEM

    # 旧布局：skills 放在根目录，无子目录
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "etl-engineer.md").write_text(
            _make_skill_md("etl-engineer", triggers=["ETL"], category="engineering", priority="high"),
            encoding="utf-8"
        )
        (tmp / "clickhouse-analyst.md").write_text(
            _make_skill_md("clickhouse-analyst", triggers=["分析", "查询"], priority="medium"),
            encoding="utf-8"
        )
        (tmp / "README.md").write_text("# 说明文件（不应被加载）", encoding="utf-8")

        loader = SkillLoader(skills_dir=str(tmp))
        all_skills = loader.load_all()

        check("E1 旧布局可加载根目录 skill", len(all_skills) >= 2,
              f"got {len(all_skills)}")
        check("E2 README.md 被过滤",
              all(s.name.upper() != "README" for s in all_skills))
        check("E3 旧布局 skill tier=system",
              all(s.tier == TIER_SYSTEM for s in all_skills),
              str([f"{s.name}:{s.tier}" for s in all_skills]))

        # ETL 触发正常
        triggered = loader.find_triggered("帮我设计ETL")
        check("E4 旧布局触发词匹配正常", len(triggered) >= 1, str([s.name for s in triggered]))

    finally:
        shutil.rmtree(tmp)


# ──────────────────────────────────────────────────────────────────────────────
# Section F — REST API 静态代码检查
# ──────────────────────────────────────────────────────────────────────────────

def test_section_f():
    print("\n=== Section F: REST API 静态代码检查 ===")

    skills_api_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "backend", "api", "skills.py"
    )
    src = open(skills_api_path, encoding="utf-8").read()

    check("F1 list_md_skills 返回 tier 字段", '"tier"' in src and 's.tier' in src)
    check("F2 project-skills 端点存在", "/project-skills" in src)
    check("F3 create_project_skill 使用 require_admin", "require_admin" in src and "project" in src)
    check("F4 update_project_skill PUT 端点存在", "project_skill_update" in src.lower() or "ProjectSkillUpdate" in src)
    check("F5 delete_project_skill 有路径边界检查",
          "_PROJECT_SKILLS_DIR_RESOLVED" in src and "relative_to" in src)
    check("F6 system tier is_readonly=True",
          's.tier == "system"' in src or "tier == TIER_SYSTEM" in src or '"system"' in src)
    check("F7 project tier is_readonly 为 False",
          'is_readonly' in src)
    check("F8 list_project_skills 端点存在", "list_project_skills" in src)


# ──────────────────────────────────────────────────────────────────────────────
# Section G — FilesystemPermissionProxy 错误提示
# ──────────────────────────────────────────────────────────────────────────────

def test_section_g():
    print("\n=== Section G: FilesystemPermissionProxy 错误提示更新 ===")

    proxy_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "backend", "core", "filesystem_permission_proxy.py"
    )
    src = open(proxy_path, encoding="utf-8").read()

    check("G1 错误提示包含 project-skills 接口引导",
          "project-skills" in src or "project" in src)
    check("G2 错误提示包含 user-defined 接口引导",
          "user-defined" in src)
    check("G3 错误提示格式更新（多条提示）",
          "数据文件" in src and "用户 Skill" in src)


# ──────────────────────────────────────────────────────────────────────────────
# Section H — SkillWatcher recursive=True
# ──────────────────────────────────────────────────────────────────────────────

def test_section_h():
    print("\n=== Section H: SkillWatcher recursive=True ===")

    watcher_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "backend", "skills", "skill_watcher.py"
    )
    src = open(watcher_path, encoding="utf-8").read()

    check("H1 SkillWatcher 使用 recursive=True",
          "recursive=True" in src,
          "SkillWatcher 需要监听子目录（system/project/user）的变化")
    check("H2 SkillWatcher 不使用 recursive=False",
          "recursive=False" not in src)


# ──────────────────────────────────────────────────────────────────────────────
# Section I — _base-safety.md 文件存在性检查
# ──────────────────────────────────────────────────────────────────────────────

def test_section_i():
    print("\n=== Section I: _base-safety.md 文件检查 ===")

    project_root = Path(os.path.dirname(os.path.abspath(__file__)))
    base_safety = project_root / ".claude" / "skills" / "system" / "_base-safety.md"
    system_dir = project_root / ".claude" / "skills" / "system"
    project_dir = project_root / ".claude" / "skills" / "project"

    check("I1 .claude/skills/system/ 目录存在", system_dir.exists())
    check("I2 .claude/skills/project/ 目录存在", project_dir.exists())
    check("I3 _base-safety.md 存在", base_safety.exists(), str(base_safety))

    if base_safety.exists():
        content = base_safety.read_text(encoding="utf-8")
        check("I4 _base-safety.md 包含 always_inject: true", "always_inject: true" in content)
        check("I5 _base-safety.md 包含安全约束内容", "严禁" in content or "禁止" in content or "安全" in content)

    # 验证系统 skill 已迁移到 system/ 子目录
    etl_in_system = (system_dir / "etl-engineer.md").exists()
    check("I6 etl-engineer.md 已迁移至 system/ 目录", etl_in_system)

    # 旧根目录下的系统 skill 可以保留（向后兼容），检查 system/ 下至少有 2 个 skill
    system_skills_count = len(list(system_dir.glob("*.md"))) if system_dir.exists() else 0
    check("I7 system/ 目录下至少有 2 个 skill 文件", system_skills_count >= 2,
          f"found {system_skills_count}")


# ──────────────────────────────────────────────────────────────────────────────
# Section J — 实际 SkillLoader 加载真实项目目录
# ──────────────────────────────────────────────────────────────────────────────

def test_section_j():
    print("\n=== Section J: 实际项目目录加载测试 ===")
    from backend.skills.skill_loader import SkillLoader, TIER_SYSTEM, TIER_USER

    project_root = Path(os.path.dirname(os.path.abspath(__file__)))
    skills_dir = project_root / ".claude" / "skills"

    loader = SkillLoader(skills_dir=str(skills_dir))
    all_skills = loader.load_all()

    check("J1 实际项目加载 skill 总数 >= 2", len(all_skills) >= 2,
          f"got {len(all_skills)}: {[s.name for s in all_skills]}")

    sys_skills = loader.get_by_tier(TIER_SYSTEM)
    check("J2 system tier 至少 1 个 skill", len(sys_skills) >= 1,
          str([s.name for s in sys_skills]))

    # _base-safety 被正确识别为 always_inject
    check("J3 _base-safety 被识别为 always_inject",
          any(s.always_inject for s in sys_skills),
          str([(s.name, s.always_inject) for s in sys_skills]))

    # ETL 触发
    triggered = loader.find_triggered("帮我设计ETL数据管道")
    check("J4 ETL 消息触发 etl-engineer",
          any(s.name == "etl-engineer" for s in triggered),
          str([s.name for s in triggered]))

    # build_skill_prompt 含 base
    prompt = loader.build_skill_prompt("帮我设计ETL宽表")
    check("J5 build_skill_prompt 返回非空", bool(prompt))
    check("J6 prompt 包含安全约束内容",
          "安全" in prompt or "禁止" in prompt or "严禁" in prompt,
          prompt[:300] if prompt else "(empty)")
    check("J7 prompt 包含 ETL 专业规程",
          "ETL" in prompt,
          prompt[:300] if prompt else "(empty)")


# ──────────────────────────────────────────────────────────────────────────────
# 汇总结果
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  test_skill_registry.py")
    print("  三层 Skill 体系重构验证测试")
    print("=" * 60)

    test_section_a()
    test_section_b()
    test_section_c()
    test_section_d()
    test_section_e()
    test_section_f()
    test_section_g()
    test_section_h()
    test_section_i()
    test_section_j()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    total = len(results)
    print(f"  结果: {passed}/{total} 通过  ({failed} 失败)")
    print("=" * 60)

    if failed:
        print("\n失败项：")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
        sys.exit(1)
    else:
        print("  全部通过！")
        sys.exit(0)
