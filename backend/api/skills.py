"""
Skills API路由

提供三层 SKILL.md 技能管理的 REST API：

  GET  /md-skills                        列出所有三层 skill（含 tier 字段）
  POST /user-defined                     创建用户 skill（Tier 3）
  GET  /user-defined                     列出用户 skill
  DELETE /user-defined/{name}            删除用户 skill
  POST /project-skills                   创建项目 skill（Tier 2，管理员）
  PUT  /project-skills/{name}            更新项目 skill（管理员）
  DELETE /project-skills/{name}          删除项目 skill（管理员）
  GET  /project-skills                   列出项目 skill

权限说明：
  系统 Skill (system/)    只读，通过代码部署更新
  项目 Skill (project/)   Admin REST API 可写，Agent MCP 不可写
  用户 Skill (user/)      用户 REST API 可写，Agent MCP 限写 .claude/skills/user/
"""
from typing import List, Dict, Any, Optional
from pathlib import Path
import re
import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import require_admin, get_current_user, require_permission
from backend.config.settings import settings

router = APIRouter(prefix="/skills", tags=["skills"])
logger = logging.getLogger(__name__)

# ── Skill storage directories ───────────────────────────────────────────────
_SKILLS_ROOT = (
    Path(__file__).resolve().parent.parent.parent / ".claude" / "skills"
)
_SYSTEM_SKILLS_DIR = _SKILLS_ROOT / "system"
_PROJECT_SKILLS_DIR = _SKILLS_ROOT / "project"
_USER_SKILLS_DIR = _SKILLS_ROOT / "user"

# Ensure directories exist
_PROJECT_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
_USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

# Resolved paths for O(1) boundary checks
_PROJECT_SKILLS_DIR_RESOLVED = _PROJECT_SKILLS_DIR.resolve()
_USER_SKILLS_DIR_RESOLVED = _USER_SKILLS_DIR.resolve()


def _get_user_skill_dir(username: str = "default") -> Path:
    """
    返回用户的 skill 目录：
      - ENABLE_AUTH=false: .claude/skills/user/         (兼容旧行为，flat 目录)
      - ENABLE_AUTH=true:  .claude/skills/user/{username}/ (每个用户独立子目录)
    """
    if settings.enable_auth:
        d = _USER_SKILLS_DIR / username
        d.mkdir(parents=True, exist_ok=True)
        return d
    return _USER_SKILLS_DIR


def _current_username(current_user) -> str:
    """从 User 或 AnonymousUser 中提取 username"""
    return getattr(current_user, "username", "default") or "default"


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class UserSkillCreate(BaseModel):
    """用户/项目自定义技能创建请求"""
    name: str = Field(..., description="技能唯一标识（kebab-case）", min_length=2, max_length=64)
    description: str = Field(..., description="一行简要描述（≤120字符）", max_length=120)
    triggers: List[str] = Field(..., description="触发关键词列表", min_items=1)
    category: str = Field(default="general", description="engineering | analytics | general")
    priority: str = Field(default="medium", description="high | medium | low")
    content: str = Field(..., description="技能 Markdown 内容体（不含 frontmatter）", min_length=10)


class ProjectSkillUpdate(BaseModel):
    """项目 skill 更新请求（允许部分字段更新）"""
    description: Optional[str] = Field(default=None, max_length=120)
    triggers: Optional[List[str]] = Field(default=None)
    category: Optional[str] = Field(default=None)
    priority: Optional[str] = Field(default=None)
    content: Optional[str] = Field(default=None, min_length=10)


def _slugify(name: str) -> str:
    """Normalise a skill name to kebab-case, allow only [a-z0-9-]."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def _build_skill_md(skill: UserSkillCreate, version: str = "1.0") -> str:
    """Render a complete SKILL.md file string from a skill payload."""
    slug = _slugify(skill.name)
    triggers_yaml = "\n".join(f"  - {t}" for t in skill.triggers)
    return (
        f"---\n"
        f"name: {slug}\n"
        f'version: "{version}"\n'
        f"description: {skill.description}\n"
        f"triggers:\n{triggers_yaml}\n"
        f"category: {skill.category}\n"
        f"priority: {skill.priority}\n"
        f"---\n\n"
        f"{skill.content.strip()}\n"
    )


def _bump_version(current: str) -> str:
    """Increment minor version number: '1.0' → '1.1', '2.3' → '2.4'."""
    try:
        major, minor = current.split(".")
        return f"{major}.{int(minor) + 1}"
    except (ValueError, AttributeError):
        return "1.1"


# ── MD Skills API (所有层汇总) ────────────────────────────────────────────────

@router.get("/md-skills", response_model=List[Dict[str, Any]])
async def list_md_skills(
    current_user=Depends(get_current_user),
):
    """
    获取所有三层 SKILL.md 技能（用户层按当前用户过滤）。

    每条记录包含：
      name / version / description / triggers / category / priority /
      content / filepath / tier / is_readonly / always_inject
    """
    try:
        from backend.skills.skill_loader import get_skill_loader
        loader = get_skill_loader()
        loader.load_all()
        skills = loader.get_all()
        username = _current_username(current_user)
        user_skill_dir = _get_user_skill_dir(username).resolve()
        result = []
        for s in skills:
            fp = str(s.filepath) if s.filepath else ""
            # 当 ENABLE_AUTH=true 时，只显示属于当前用户的 user 层技能
            if settings.enable_auth and s.tier == "user" and s.filepath:
                skill_file_dir = Path(s.filepath).resolve().parent
                if skill_file_dir != user_skill_dir:
                    continue  # 属于其他用户，不显示
            result.append({
                "name": s.name,
                "version": s.version,
                "description": s.description,
                "triggers": s.triggers,
                "category": s.category,
                "priority": s.priority,
                "content": s.content,
                "filepath": fp,
                "tier": s.tier,
                "is_user_defined": s.tier == "user",
                "is_readonly": s.tier == "system",
                "always_inject": s.always_inject,
            })
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Skill Load Error Report ──────────────────────────────────────────────────

@router.get("/load-errors", response_model=List[Dict[str, Any]])
async def list_skill_load_errors(
    current_user=Depends(require_permission("settings", "read")),
):
    """
    返回上次 skill 加载中解析失败的文件列表。

    每条记录包含：filepath / reason
    用于诊断 skill 文件格式错误（如缺少 YAML frontmatter）。
    """
    try:
        from backend.skills.skill_loader import get_skill_loader
        loader = get_skill_loader()
        loader.load_all()
        return loader.get_load_errors()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── User-defined SKILL.md API (Tier 3) ────────────────────────────────────────

@router.post("/user-defined", response_model=Dict[str, Any], status_code=201)
async def create_user_skill(
    skill: UserSkillCreate,
    current_user=Depends(require_permission("skills.user", "write")),
):
    """
    创建用户自定义技能（Tier 3）。

    - ENABLE_AUTH=false: 写入 `.claude/skills/user/`
    - ENABLE_AUTH=true:  写入 `.claude/skills/user/{username}/`
    SkillWatcher 热加载后立即生效，无需重启。
    """
    username = _current_username(current_user)
    user_skill_dir = _get_user_skill_dir(username)
    slug = _slugify(skill.name)
    if not slug:
        raise HTTPException(status_code=422, detail="技能名称不合法")

    skill_path = user_skill_dir / f"{slug}.md"
    if skill_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"技能 '{slug}' 已存在，请先删除或使用不同名称。",
        )

    content = _build_skill_md(skill)
    try:
        skill_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.error("[UserSkill] Failed to write %s: %s", skill_path, exc)
        raise HTTPException(status_code=500, detail=f"写入技能文件失败: {exc}")

    logger.info("[UserSkill] Created %s (user=%s)", skill_path, username)
    return {
        "success": True,
        "name": slug,
        "tier": "user",
        "filepath": str(skill_path),
        "message": f"技能 '{slug}' 已创建，即将热加载生效。",
    }


@router.get("/user-defined", response_model=List[Dict[str, Any]])
async def list_user_skills(
    current_user=Depends(require_permission("skills.user", "read")),
):
    """列出当前用户的自定义技能（Tier 3），返回完整字段包括 content / triggers / version"""
    from backend.skills.skill_loader import _parse_yaml_subset, _FRONTMATTER_RE
    username = _current_username(current_user)
    user_skill_dir = _get_user_skill_dir(username)
    skills = []
    for md_file in sorted(user_skill_dir.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
            m = _FRONTMATTER_RE.match(text)
            if m:
                meta = _parse_yaml_subset(m.group(1))
                body = text[m.end():].strip()
            else:
                meta = {}
                body = text.strip()
            skills.append({
                "name": meta.get("name") or md_file.stem,
                "version": str(meta.get("version", "1.0")),
                "description": meta.get("description", ""),
                "triggers": meta.get("triggers", []),
                "category": meta.get("category", "general"),
                "priority": meta.get("priority", "medium"),
                "content": body,
                "filepath": str(md_file),
                "filename": md_file.name,
                "tier": "user",
                "always_inject": bool(meta.get("always_inject", False)),
            })
        except Exception:
            pass
    return skills


@router.put("/user-defined/{skill_name}", response_model=Dict[str, Any])
async def update_user_skill(
    skill_name: str,
    update: ProjectSkillUpdate,
    current_user=Depends(require_permission("skills.user", "write")),
):
    """
    更新用户自定义技能（Tier 3）。

    允许部分字段更新（None = 保留原值）。版本号自动递增。
    """
    username = _current_username(current_user)
    user_skill_dir = _get_user_skill_dir(username)
    slug = _slugify(skill_name)
    if not slug:
        raise HTTPException(status_code=422, detail="技能名称不合法")

    skill_path = user_skill_dir / f"{slug}.md"

    try:
        resolved = skill_path.resolve()
        if resolved.parent != user_skill_dir.resolve():
            raise HTTPException(status_code=403, detail="禁止操作：路径越界")
    except OSError:
        raise HTTPException(status_code=422, detail="无法解析技能路径")

    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"用户技能 '{slug}' 不存在")

    try:
        from backend.skills.skill_loader import _parse_yaml_subset, _FRONTMATTER_RE
        text = skill_path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise HTTPException(status_code=500, detail="无法解析现有技能文件格式")

        meta = _parse_yaml_subset(m.group(1))
        old_body = text[m.end():].strip()

        new_desc = update.description or meta.get("description", "")
        new_triggers = update.triggers or meta.get("triggers", [])
        new_cat = update.category or meta.get("category", "general")
        new_pri = update.priority or meta.get("priority", "medium")
        new_body = (update.content or old_body).strip()
        new_version = _bump_version(str(meta.get("version", "1.0")))

        triggers_yaml = "\n".join(f"  - {t}" for t in new_triggers)
        new_content = (
            f"---\n"
            f"name: {slug}\n"
            f'version: "{new_version}"\n'
            f"description: {new_desc}\n"
            f"triggers:\n{triggers_yaml}\n"
            f"category: {new_cat}\n"
            f"priority: {new_pri}\n"
            f"---\n\n"
            f"{new_body}\n"
        )
        skill_path.write_text(new_content, encoding="utf-8")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新用户技能失败: {exc}")

    logger.info("[UserSkill] Updated %s -> v%s", skill_path, new_version)
    return {
        "success": True,
        "name": slug,
        "version": new_version,
        "tier": "user",
        "message": f"用户技能 '{slug}' 已更新至 v{new_version}。",
    }


@router.delete("/user-defined/{skill_name}", response_model=Dict[str, Any])
async def delete_user_skill(
    skill_name: str,
    current_user=Depends(require_permission("skills.user", "write")),
):
    """删除用户自定义技能（仅限当前用户的 skill 目录内）"""
    username = _current_username(current_user)
    user_skill_dir = _get_user_skill_dir(username)
    slug = _slugify(skill_name)
    if not slug:
        raise HTTPException(status_code=422, detail="技能名称不合法")

    skill_path = user_skill_dir / f"{slug}.md"

    # Defense-in-depth: explicit path boundary check
    try:
        resolved = skill_path.resolve()
    except OSError:
        raise HTTPException(status_code=422, detail="无法解析技能路径")

    if resolved.parent != user_skill_dir.resolve():
        logger.warning("[UserSkill] Path boundary violation attempt: %s", skill_name)
        raise HTTPException(status_code=403, detail="禁止操作：路径越界")

    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"技能 '{slug}' 不存在")

    try:
        skill_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除技能文件失败: {exc}")

    logger.info("[UserSkill] Deleted %s", skill_path)
    return {"success": True, "name": slug, "message": f"技能 '{slug}' 已删除。"}


# ── Project SKILL.md API (Tier 2，管理员) ────────────────────────────────────

@router.post("/project-skills", response_model=Dict[str, Any], status_code=201)
async def create_project_skill(skill: UserSkillCreate, _=Depends(require_admin)):
    """
    创建项目级技能（Tier 2，管理员专用）。

    文件写入 `.claude/skills/project/` 目录，热加载后所有用户立即生效。
    此接口通过 Python 直接写文件，不受 FilesystemPermissionProxy 约束。
    """
    slug = _slugify(skill.name)
    if not slug:
        raise HTTPException(status_code=422, detail="技能名称不合法")

    skill_path = _PROJECT_SKILLS_DIR / f"{slug}.md"
    if skill_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"项目技能 '{slug}' 已存在，请使用 PUT 接口更新。",
        )

    content = _build_skill_md(skill)
    try:
        skill_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.error("[ProjectSkill] Failed to write %s: %s", skill_path, exc)
        raise HTTPException(status_code=500, detail=f"写入项目技能文件失败: {exc}")

    logger.info("[ProjectSkill] Created %s", skill_path)
    return {
        "success": True,
        "name": slug,
        "tier": "project",
        "filepath": str(skill_path),
        "message": f"项目技能 '{slug}' 已创建，即将热加载生效。",
    }


@router.put("/project-skills/{skill_name}", response_model=Dict[str, Any])
async def update_project_skill(
    skill_name: str,
    update: ProjectSkillUpdate,
    _=Depends(require_admin),
):
    """更新项目级技能（Tier 2，管理员专用）"""
    slug = _slugify(skill_name)
    if not slug:
        raise HTTPException(status_code=422, detail="技能名称不合法")

    skill_path = _PROJECT_SKILLS_DIR / f"{slug}.md"

    try:
        resolved = skill_path.resolve()
        resolved.relative_to(_PROJECT_SKILLS_DIR_RESOLVED)
    except (OSError, ValueError):
        raise HTTPException(status_code=403, detail="禁止操作：路径越界")

    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"项目技能 '{slug}' 不存在")

    # 读取现有内容，解析 frontmatter，合并更新字段
    try:
        from backend.skills.skill_loader import _parse_yaml_subset, _FRONTMATTER_RE
        text = skill_path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise HTTPException(status_code=500, detail="无法解析现有技能文件格式")

        meta = _parse_yaml_subset(m.group(1))
        old_body = text[m.end():].strip()

        # 合并更新（None = 保留原值）
        new_desc = update.description or meta.get("description", "")
        new_triggers = update.triggers or meta.get("triggers", [])
        new_cat = update.category or meta.get("category", "general")
        new_pri = update.priority or meta.get("priority", "medium")
        new_body = (update.content or old_body).strip()
        new_version = _bump_version(str(meta.get("version", "1.0")))

        triggers_yaml = "\n".join(f"  - {t}" for t in new_triggers)
        new_content = (
            f"---\n"
            f"name: {slug}\n"
            f'version: "{new_version}"\n'
            f"description: {new_desc}\n"
            f"triggers:\n{triggers_yaml}\n"
            f"category: {new_cat}\n"
            f"priority: {new_pri}\n"
            f"---\n\n"
            f"{new_body}\n"
        )
        skill_path.write_text(new_content, encoding="utf-8")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新项目技能失败: {exc}")

    logger.info("[ProjectSkill] Updated %s → v%s", skill_path, new_version)
    return {
        "success": True,
        "name": slug,
        "version": new_version,
        "tier": "project",
        "message": f"项目技能 '{slug}' 已更新至 v{new_version}。",
    }


@router.delete("/project-skills/{skill_name}", response_model=Dict[str, Any])
async def delete_project_skill(skill_name: str, _=Depends(require_admin)):
    """删除项目级技能（Tier 2，管理员专用）"""
    slug = _slugify(skill_name)
    if not slug:
        raise HTTPException(status_code=422, detail="技能名称不合法")

    skill_path = _PROJECT_SKILLS_DIR / f"{slug}.md"

    try:
        resolved = skill_path.resolve()
        resolved.relative_to(_PROJECT_SKILLS_DIR_RESOLVED)
    except (OSError, ValueError):
        raise HTTPException(status_code=403, detail="禁止操作：路径越界")

    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"项目技能 '{slug}' 不存在")

    try:
        skill_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除项目技能失败: {exc}")

    logger.info("[ProjectSkill] Deleted %s", skill_path)
    return {"success": True, "name": slug, "message": f"项目技能 '{slug}' 已删除。"}


# ── Skill Trigger Preview API ─────────────────────────────────────────────────

@router.get("/preview", response_model=Dict[str, Any])
async def preview_skill_trigger(
    message: str,
    mode: str = "",
    view_as: str = "",
    current_user=Depends(get_current_user),
):
    """
    预览给定消息会激活哪些 Skill（测试触发器）。

    参数：
      - message:  要测试的用户消息
      - mode:     强制覆盖匹配模式（keyword/hybrid/llm），空字符串使用系统配置
      - view_as:  仅 superadmin 可用；指定要模拟的用户名，预览该用户看到的 skill 视图

    返回：
      - triggered: 将被触发注入的 skill 列表（按层次排序）
      - always_inject: 始终注入的 base skill 列表
      - total_chars: 预估注入的总字符数
      - preview_prompt: 注入的完整 prompt 文本（截断到 2000 字符）
      - match_details: 每个命中 skill 的匹配方式 {name: {method, score, tier}}
      - preview_user: 实际预览所用的用户名（superadmin override 时显示）
    """
    from backend.api.deps import AnonymousUser

    # 确定预览所用用户名
    # superadmin 可通过 view_as 模拟其他用户视角；普通用户强制使用自身身份
    is_anon = isinstance(current_user, AnonymousUser)
    if view_as and not is_anon:
        is_superadmin = getattr(current_user, "is_superadmin", False)
        if not is_superadmin:
            raise HTTPException(status_code=403, detail="view_as 参数仅 superadmin 可用")
        effective_user_id = view_as
    elif is_anon:
        effective_user_id = "default"
    else:
        effective_user_id = current_user.username

    try:
        from backend.skills.skill_loader import get_skill_loader
        loader = get_skill_loader()
        loader.load_all()

        # 收集 always_inject skills
        base_skills = list(loader._base_skills)

        # Build preview prompt via async path (supports hybrid/semantic routing)
        from backend.config.settings import settings as _settings

        if mode and mode in ("keyword", "hybrid", "llm"):
            # Temporarily override mode for this preview call
            import contextlib

            @contextlib.asynccontextmanager
            async def _mode_override():
                orig = _settings.skill_match_mode
                _settings.skill_match_mode = mode
                try:
                    yield
                finally:
                    _settings.skill_match_mode = orig

            async with _mode_override():
                preview_prompt = await loader.build_skill_prompt_async(
                    message, llm_adapter=None, user_id=effective_user_id
                )
        else:
            preview_prompt = await loader.build_skill_prompt_async(
                message, llm_adapter=None, user_id=effective_user_id
            )

        total_chars = len(preview_prompt)

        # Collect triggered skills (keyword-based, for display, filtered by user)
        triggered_all = loader.find_triggered(message)
        visible_user_names = set(loader._get_visible_user_skills(effective_user_id).keys())
        user_triggered = [s for s in triggered_all if s.tier == "user" and s.name in visible_user_names]
        proj_triggered = [s for s in triggered_all if s.tier == "project"]
        sys_triggered = [s for s in triggered_all if s.tier == "system"]

        def _skill_info(s) -> Dict[str, Any]:
            return {
                "name": s.name,
                "tier": s.tier,
                "description": s.description,
                "triggers": s.triggers,
                "priority": s.priority,
                "always_inject": s.always_inject,
            }

        # Build match_details from keyword hits + always_inject
        # 传入 effective_user_id 确保 user-tier skill 按用户隔离
        match_details = loader.get_match_details(message, username=effective_user_id)

        return {
            "message": message,
            "preview_user": effective_user_id,
            "triggered": {
                "user": [_skill_info(s) for s in user_triggered],
                "project": [_skill_info(s) for s in proj_triggered],
                "system": [_skill_info(s) for s in sys_triggered],
            },
            "always_inject": [_skill_info(s) for s in base_skills],
            "total_chars": total_chars,
            "preview_prompt": preview_prompt[:2000] + ("..." if total_chars > 2000 else ""),
            "match_details": match_details,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/project-skills", response_model=List[Dict[str, Any]])
async def list_project_skills():
    """列出所有项目级技能（Tier 2）"""
    skills = []
    for md_file in sorted(_PROJECT_SKILLS_DIR.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
            import re as _re
            m = _re.search(r"^name:\s*(.+)$", text, _re.MULTILINE)
            desc = _re.search(r"^description:\s*(.+)$", text, _re.MULTILINE)
            ver = _re.search(r'^version:\s*"?(.+?)"?$', text, _re.MULTILINE)
            skills.append({
                "name": m.group(1).strip() if m else md_file.stem,
                "description": desc.group(1).strip() if desc else "",
                "version": ver.group(1).strip() if ver else "1.0",
                "filepath": str(md_file),
                "filename": md_file.name,
                "tier": "project",
            })
        except Exception:
            pass
    return skills
