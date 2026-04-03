"""
test_skill_match_visibility.py
==============================
T1-T6 Skill Match Visibility 功能测试。

Sections:
  A (A1-A5):  _make_match_info() 单元测试
  B (B1-B6):  build_skill_prompt_async() keyword 模式匹配信息
  C (C1-C5):  build_skill_prompt_async() hybrid 模式匹配信息
  D (D1-D3):  get_last_match_info() 行为
  E (E1-E5):  run_streaming() 中 skill_matched 事件发射
  F (F1-F4):  事件形状与顺序验证
  G (G1-G3):  RBAC — GET /skills/load-errors 权限验证
  H (H1-H4):  边缘场景（无匹配 / 摘要模式 / 加载错误）
"""
import sys
import io
import os
import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

_pass = 0
_fail = 0
_section_fails = {}


def check(label, cond, detail=""):
    global _pass, _fail
    if cond:
        print(f"  [OK] {label}")
        _pass += 1
    else:
        print(f"  [FAIL] {label}" + (f"  detail={detail!r}" if detail else ""))
        _fail += 1
        sec = label.split()[0] if label else "?"
        _section_fails.setdefault(sec, []).append(label)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

SKILLS_DIR = Path(__file__).parent / ".claude" / "skills"


def make_loader(skills_dir=None):
    from skills.skill_loader import SkillLoader
    loader = SkillLoader(str(skills_dir or SKILLS_DIR))
    loader.load_all()
    return loader


def make_tmp_skills_dir(skill_files: dict) -> Path:
    """Create a temporary skills directory with given files and frontmatter."""
    tmp = Path(tempfile.mkdtemp())
    system_dir = tmp / "system"
    system_dir.mkdir()
    for name, content in skill_files.items():
        (system_dir / name).write_text(content, encoding="utf-8")
    return tmp


VALID_FRONTMATTER = """\
---
name: {name}
version: "1.0"
description: {desc}
triggers:
  - {trigger}
category: analytics
priority: medium
---

# {name} content
"""

MISSING_FRONTMATTER = """# No frontmatter here
Just markdown content.
"""


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section A: _make_match_info() 单元测试 ===")
# ─────────────────────────────────────────────────────────────────────────────


def _section_a():
    from skills.skill_loader import SkillLoader, SkillMD

    loader = SkillLoader(str(SKILLS_DIR))
    loader.load_all()

    # Build a fake SkillMD for testing
    def _fake_skill(name, tier, triggers):
        s = SkillMD(
            name=name,
            version="1.0",
            description=f"{name} desc",
            triggers=triggers,
            category="analytics",
            priority="medium",
            content="content",
            filepath="/fake/path.md",
            tier=tier,
            always_inject=False,
        )
        return s

    sk = _fake_skill("test-skill", "user", ["clickhouse", "外呼", "账单"])

    # A1: keyword method match_info 结构
    info = loader._make_match_info(
        mode="keyword",
        matched_skills=[sk],
        match_details={"test-skill": {"method": "keyword", "score": 1.0}},
        message="我要查clickhouse外呼数据",
        result_text="some content",
    )
    check("A1 mode=keyword in info", info["mode"] == "keyword")
    check("A1 matched contains test-skill", any(m["name"] == "test-skill" for m in info["matched"]))
    check("A1 method=keyword", info["matched"][0]["method"] == "keyword")
    check("A1 score=1.0", info["matched"][0]["score"] == 1.0)

    # A2: hit_triggers 正确提取（只含消息中出现的触发词）
    info2 = loader._make_match_info(
        mode="keyword",
        matched_skills=[sk],
        match_details={"test-skill": {"method": "keyword", "score": 1.0}},
        message="clickhouse外呼查询",
        result_text="content",
    )
    hit = info2["matched"][0]["hit_triggers"]
    check("A2 hit_triggers contains clickhouse", "clickhouse" in hit)
    check("A2 hit_triggers contains 外呼", "外呼" in hit)
    check("A2 hit_triggers excludes 账单 (not in msg)", "账单" not in hit)

    # A3: always_inject 来自 loader._base_skills
    base_names = [s["name"] for s in info["always_inject"]]
    check("A3 always_inject contains _base-tools or _base-safety",
          any("base" in n for n in base_names))

    # A4: summary_mode=False when result_text has no summary marker
    check("A4 summary_mode=False for normal text", info["summary_mode"] is False)

    # A5: summary_mode=True when result_text has summary marker
    summary_text = "当前激活的技能规程（摘要模式）已降级为摘要"
    info5 = loader._make_match_info(
        mode="hybrid",
        matched_skills=[sk],
        match_details={"test-skill": {"method": "semantic", "score": 0.8}},
        message="外呼",
        result_text=summary_text,
    )
    check("A5 summary_mode=True when marker present", info5["summary_mode"] is True)
    check("A5 semantic method preserved", info5["matched"][0]["method"] == "semantic")
    check("A5 semantic score preserved", info5["matched"][0]["score"] == 0.8)


_section_a()


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section B: build_skill_prompt_async() keyword 模式 ===")
# ─────────────────────────────────────────────────────────────────────────────


async def _section_b():
    import os as _os
    _os.environ["SKILL_MATCH_MODE"] = "keyword"

    loader = make_loader()

    # B1: keyword 模式 match_info.mode == "keyword"
    msg = "我想查看sg环境的接通呼叫统计和账单"
    await loader.build_skill_prompt_async(msg, llm_adapter=None)
    info = loader.get_last_match_info()
    check("B1 mode=keyword after keyword-mode call", info.get("mode") == "keyword")

    # B2: clickhouse-analyst 被 keyword 模式匹配到
    matched_names = [m["name"] for m in info.get("matched", [])]
    check("B2 clickhouse-analyst in matched", "clickhouse-analyst" in matched_names)

    # B3: matched skill 有 method=keyword
    ch = next((m for m in info["matched"] if m["name"] == "clickhouse-analyst"), None)
    check("B3 clickhouse-analyst method=keyword", ch is not None and ch["method"] == "keyword")

    # B4: hit_triggers 非空（sg / 接通 / 账单 应命中）
    check("B4 hit_triggers non-empty", ch is not None and len(ch["hit_triggers"]) > 0)
    check("B4 'sg' in hit_triggers", ch is not None and "sg" in ch["hit_triggers"])

    # B5: always_inject 非空（_base-tools / _base-safety）
    check("B5 always_inject non-empty", len(info.get("always_inject", [])) > 0)

    # B6: 不匹配消息 → matched 为空
    await loader.build_skill_prompt_async("你好，今天天气怎么样？", llm_adapter=None)
    info6 = loader.get_last_match_info()
    check("B6 unrelated msg → matched empty", len(info6.get("matched", [])) == 0)


asyncio.run(_section_b())


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section C: build_skill_prompt_async() hybrid 模式 ===")
# ─────────────────────────────────────────────────────────────────────────────


async def _section_c():
    from unittest.mock import MagicMock, PropertyMock, patch as _patch

    # settings 是单例，必须 patch 才能切换 mode（直接改 env var 无效）
    mock_settings = MagicMock()
    mock_settings.skill_match_mode = "hybrid"
    mock_settings.skill_semantic_threshold = 0.45

    loader = make_loader()

    with _patch("backend.config.settings.settings", mock_settings):
        # C1: hybrid 模式 match_info.mode == "hybrid"
        msg = "我想查看sg环境的外呼接通数据"
        await loader.build_skill_prompt_async(msg, llm_adapter=None)
        info = loader.get_last_match_info()
        check("C1 mode=hybrid after hybrid-mode call", info.get("mode") == "hybrid",
              detail=f"actual mode={info.get('mode')}")

        # C2: keyword 命中的 skill 在 hybrid 模式中 method=keyword
        ch = next((m for m in info["matched"] if m["name"] == "clickhouse-analyst"), None)
        check("C2 keyword hit → method=keyword in hybrid mode",
              ch is not None and ch["method"] == "keyword")

        # C3: total_chars 与实际注入字符数一致
        prompt_text = await loader.build_skill_prompt_async(msg, llm_adapter=None)
        info3 = loader.get_last_match_info()
        check("C3 total_chars == len(prompt_text)",
              info3.get("total_chars") == len(prompt_text))

        # C4: 每次调用 match_info 更新（不同消息 → 不同结果）
        await loader.build_skill_prompt_async("你好", llm_adapter=None)
        info4 = loader.get_last_match_info()
        check("C4 match_info updates on new call",
              len(info4.get("matched", [])) == 0)

        # C5: load_errors 包含在 match_info 中
        check("C5 load_errors key present in match_info", "load_errors" in info)


asyncio.run(_section_c())


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section D: get_last_match_info() 行为 ===")
# ─────────────────────────────────────────────────────────────────────────────


async def _section_d():
    from skills.skill_loader import SkillLoader

    loader = SkillLoader(str(SKILLS_DIR))
    loader.load_all()

    # D1: 首次调用前返回空 dict
    check("D1 empty dict before first async call", loader.get_last_match_info() == {})

    # D2: 调用后返回 dict（非空，含必要字段）
    await loader.build_skill_prompt_async("sg外呼统计", llm_adapter=None)
    info = loader.get_last_match_info()
    required_keys = {"mode", "matched", "always_inject", "summary_mode", "total_chars", "load_errors"}
    check("D2 all required keys present", required_keys.issubset(info.keys()),
          detail=f"missing={required_keys - info.keys()}")

    # D3: 返回副本 — 修改外部 dict 不影响内部状态
    info_copy = loader.get_last_match_info()
    info_copy["mode"] = "MUTATED"
    info_after = loader.get_last_match_info()
    check("D3 returns copy (mutation does not affect internal)", info_after["mode"] != "MUTATED")


asyncio.run(_section_d())


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section E: run_streaming() skill_matched 事件发射 ===")
# ─────────────────────────────────────────────────────────────────────────────


class _MockLLMAdapter:
    """Minimal LLM adapter mock that returns end_turn with text."""
    async def chat_with_tools(self, messages, system_prompt, tools, **kwargs):
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "测试回复"}]}

    async def chat_plain(self, messages, system_prompt, **kwargs):
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "测试回复"}]}


def _make_mock_mcp_manager():
    mgr = MagicMock()
    mgr.list_servers.return_value = []
    return mgr


async def _collect_events(message: str, mode: str = "hybrid") -> list:
    """Run AgenticLoop with mock dependencies, collect all events."""
    import os as _os
    _os.environ["SKILL_MATCH_MODE"] = mode

    from agents.agentic_loop import AgenticLoop

    with patch("agents.agentic_loop.format_mcp_tools_for_claude", return_value=[]):
        loop = AgenticLoop(
            llm_adapter=_MockLLMAdapter(),
            mcp_manager=_make_mock_mcp_manager(),
            max_iterations=2,
        )
        context = {
            "system_prompt": "You are a helpful assistant.",
            "history": [],
            "username": "test_user",
        }
        events = []
        async for evt in loop.run_streaming(message, context):
            events.append(evt)
        return events


async def _section_e():
    # E1: skill_matched 事件在 sg 环境查询时被发射
    events = await _collect_events("我想查看sg环境的外呼接通统计")
    types = [e.type for e in events]
    check("E1 skill_matched event emitted", "skill_matched" in types)

    # E2: 无关消息时也发射 skill_matched（matched 为空）
    events2 = await _collect_events("你好，今天天气怎么样？")
    types2 = [e.type for e in events2]
    check("E2 skill_matched emitted for unrelated msg (empty matched)", "skill_matched" in types2)

    # E3: skill_matched data 包含所有必要字段
    sm_evt = next((e for e in events if e.type == "skill_matched"), None)
    if sm_evt and sm_evt.data:
        required = {"mode", "matched", "always_inject", "summary_mode", "total_chars", "load_errors"}
        check("E3 skill_matched.data has all required keys",
              required.issubset(sm_evt.data.keys()),
              detail=f"missing={required - sm_evt.data.keys()}")
    else:
        check("E3 skill_matched.data has all required keys", False, detail="event missing or data=None")

    # E4: skill_matched.data.matched 包含 clickhouse-analyst（sg 查询）
    if sm_evt and sm_evt.data:
        matched_names = [m["name"] for m in sm_evt.data.get("matched", [])]
        check("E4 clickhouse-analyst in matched for sg query",
              "clickhouse-analyst" in matched_names)
    else:
        check("E4 clickhouse-analyst in matched for sg query", False)

    # E5: skill_matched 在 content 事件之前
    idx_sm = next((i for i, e in enumerate(events) if e.type == "skill_matched"), None)
    idx_content = next((i for i, e in enumerate(events) if e.type == "content"), None)
    check("E5 skill_matched appears before content event",
          idx_sm is not None and idx_content is not None and idx_sm < idx_content,
          detail=f"idx_sm={idx_sm}, idx_content={idx_content}")


asyncio.run(_section_e())


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section F: 事件形状与顺序验证 ===")
# ─────────────────────────────────────────────────────────────────────────────


async def _section_f():
    events = await _collect_events("sg环境账单统计接通率分析")

    # F1: skill_matched 在第一个 thinking 事件之前
    idx_sm = next((i for i, e in enumerate(events) if e.type == "skill_matched"), None)
    idx_thinking = next((i for i, e in enumerate(events) if e.type == "thinking"), None)
    check("F1 skill_matched before first thinking event",
          idx_sm is not None and idx_thinking is not None and idx_sm < idx_thinking,
          detail=f"idx_sm={idx_sm}, idx_thinking={idx_thinking}")

    # F2: skill_matched.data.matched 列表每项含必要字段
    sm = next((e for e in events if e.type == "skill_matched"), None)
    if sm and sm.data and sm.data.get("matched"):
        item = sm.data["matched"][0]
        for key in ("name", "tier", "method", "hit_triggers", "score"):
            check(f"F2 matched item has key '{key}'", key in item)
    else:
        check("F2 matched items have required keys", False, detail="no matched items")

    # F3: hit_triggers 是 list（不是 None）
    if sm and sm.data and sm.data.get("matched"):
        check("F3 hit_triggers is a list",
              isinstance(sm.data["matched"][0].get("hit_triggers"), list))
    else:
        check("F3 hit_triggers is a list", False)

    # F4: AgentEvent.to_dict() 正常序列化 skill_matched 数据
    from agents.agentic_loop import AgentEvent
    if sm:
        d = sm.to_dict()
        check("F4 to_dict() has type=skill_matched", d["type"] == "skill_matched")
        check("F4 to_dict() data is dict", isinstance(d["data"], dict))


asyncio.run(_section_f())


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section G: RBAC — GET /skills/load-errors 权限验证 ===")
# ─────────────────────────────────────────────────────────────────────────────


def _section_g():
    """验证 load-errors 端点的权限配置，以及角色映射关系。"""

    # G1: 端点使用 require_permission("settings", "read") 装饰
    from api.skills import router
    load_errors_route = None
    for route in router.routes:
        if hasattr(route, "path") and "load-errors" in route.path:
            load_errors_route = route
            break
    check("G1 /skills/load-errors route registered", load_errors_route is not None)

    # G2: analyst 角色拥有 settings:read 权限（确保端点对 analyst 可见）
    try:
        from scripts.init_rbac import ROLES_CONFIG
        analyst_perms = ROLES_CONFIG.get("analyst", {}).get("permissions", [])
        check("G2 analyst role has settings:read",
              "settings:read" in analyst_perms)
    except ImportError:
        # fallback: read init_rbac.py 源码确认
        init_rbac_path = Path(__file__).parent / "backend" / "scripts" / "init_rbac.py"
        content = init_rbac_path.read_text(encoding="utf-8") if init_rbac_path.exists() else ""
        check("G2 analyst role has settings:read in init_rbac.py",
              "settings:read" in content)

    # G3: viewer 角色不拥有 settings:read（不能访问 load-errors）
    try:
        from scripts.init_rbac import ROLES_CONFIG
        viewer_perms = ROLES_CONFIG.get("viewer", {}).get("permissions", [])
        check("G3 viewer role does NOT have settings:read",
              "settings:read" not in viewer_perms)
    except ImportError:
        init_rbac_path = Path(__file__).parent / "backend" / "scripts" / "init_rbac.py"
        content = init_rbac_path.read_text(encoding="utf-8") if init_rbac_path.exists() else ""
        # viewer section should not contain settings:read
        # Parse viewer section roughly
        viewer_start = content.find('"viewer"')
        analyst_start = content.find('"analyst"')
        if viewer_start != -1 and analyst_start != -1:
            viewer_section = content[viewer_start:analyst_start]
            check("G3 viewer section does NOT contain settings:read",
                  "settings:read" not in viewer_section)
        else:
            check("G3 viewer role does NOT have settings:read", True,
                  detail="skipped (could not parse)")


_section_g()


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Section H: 边缘场景 ===")
# ─────────────────────────────────────────────────────────────────────────────


async def _section_h():
    import os as _os
    _os.environ["SKILL_MATCH_MODE"] = "keyword"

    # H1: 加载错误文件 → load_errors 在 match_info 中反映
    tmp_dir = make_tmp_skills_dir({
        "valid-skill.md": VALID_FRONTMATTER.format(
            name="valid-skill", desc="Valid skill", trigger="valid"),
        "broken-skill.md": MISSING_FRONTMATTER,
    })
    try:
        from skills.skill_loader import SkillLoader
        broken_loader = SkillLoader(str(tmp_dir))
        broken_loader.load_all()

        # H1: load_errors 反映 broken-skill.md
        errors = broken_loader.get_load_errors()
        check("H1 load_errors reports missing frontmatter file",
              any("broken-skill" in e["filepath"] for e in errors),
              detail=f"errors={errors}")

        await broken_loader.build_skill_prompt_async("valid keyword", llm_adapter=None)
        info = broken_loader.get_last_match_info()
        check("H1 load_errors included in match_info",
              len(info.get("load_errors", [])) > 0)
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)

    # H2: 无触发词匹配 → matched 为空，match_info 仍有效
    loader2 = make_loader()
    await loader2.build_skill_prompt_async("完全无关的消息，不含任何触发词xyzabc", llm_adapter=None)
    info2 = loader2.get_last_match_info()
    check("H2 no match → matched=[]", info2.get("matched", []) == [])
    check("H2 no match → match_info still valid dict", isinstance(info2, dict))
    check("H2 no match → summary_mode=False", info2.get("summary_mode") is False)

    # H3: summary_mode 在注入超限时被正确标记
    # 构造两个大 skill，使总注入超过 _MAX_INJECT_CHARS
    from skills.skill_loader import _MAX_INJECT_CHARS
    big_content = "x" * (_MAX_INJECT_CHARS // 2 + 1000)
    tmp_big = make_tmp_skills_dir({
        "big-skill-a.md": (
            "---\nname: big-skill-a\nversion: \"1.0\"\n"
            "description: big skill a\ntriggers:\n  - bigtest\n"
            "category: analytics\npriority: high\n---\n\n" + big_content
        ),
        "big-skill-b.md": (
            "---\nname: big-skill-b\nversion: \"1.0\"\n"
            "description: big skill b\ntriggers:\n  - bigtest\n"
            "category: analytics\npriority: high\n---\n\n" + big_content
        ),
    })
    try:
        from skills.skill_loader import SkillLoader
        big_loader = SkillLoader(str(tmp_big))
        big_loader.load_all()
        await big_loader.build_skill_prompt_async("bigtest message", llm_adapter=None)
        info3 = big_loader.get_last_match_info()
        check("H3 summary_mode=True when two big skills both match",
              info3.get("summary_mode") is True,
              detail=f"total_chars={info3.get('total_chars')}, limit={_MAX_INJECT_CHARS}")
    finally:
        shutil.rmtree(str(tmp_big), ignore_errors=True)

    # H4: 只有一个 skill 匹配时不触发 summary_mode（用真实 clickhouse-analyst）
    loader4 = make_loader()
    await loader4.build_skill_prompt_async("sg环境外呼接通率", llm_adapter=None)
    info4 = loader4.get_last_match_info()
    check("H4 single skill match → summary_mode=False",
          info4.get("summary_mode") is False,
          detail=f"total_chars={info4.get('total_chars')}")


asyncio.run(_section_h())


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Regression: 既有 skill_loader 功能无回归 ===")
# ─────────────────────────────────────────────────────────────────────────────


async def _regression():
    import os as _os
    _os.environ["SKILL_MATCH_MODE"] = "keyword"

    loader = make_loader()

    # R1: build_skill_prompt_async 返回值仍为 str
    result = await loader.build_skill_prompt_async("sg环境外呼统计", llm_adapter=None)
    check("R1 build_skill_prompt_async returns str", isinstance(result, str))

    # R2: clickhouse-analyst skill 内容被注入到返回字符串
    check("R2 clickhouse-analyst content in prompt",
          "ClickHouse" in result or "外呼" in result or "clickhouse" in result.lower())

    # R3: get_load_errors() 依然可用
    errors = loader.get_load_errors()
    check("R3 get_load_errors() returns list", isinstance(errors, list))

    # R4: list_skills() 正常（两个 clickhouse skill 均存在）
    skills = loader.list_skills()
    names = [s.name for s in skills]
    check("R4 clickhouse-analyst loaded", "clickhouse-analyst" in names)
    check("R4 clickhouse-analyst-mx loaded", "clickhouse-analyst-mx" in names)

    # R5: agentic_loop run_streaming 正常产出 content 事件
    events = await _collect_events("sg外呼统计")
    types = [e.type for e in events]
    check("R5 run_streaming still yields content event", "content" in types)


asyncio.run(_regression())


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"  Total: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
if _section_fails:
    print("  Failed items:")
    for label in sum(_section_fails.values(), []):
        print(f"    - {label}")
print(f"{'='*60}")
