"""
SKILL.md Loader (3-Tier Registry)
==================================
加载并管理三层 SKILL.md 技能文件：

  Tier 1 — 系统 Skill (.claude/skills/system/)
    • 开发人员维护，部署时固定
    • 文件名以 `_base` 开头的 skill（如 _base-safety.md）始终注入，不依赖触发词
    • 其他系统 skill 按触发词匹配后注入

  Tier 2 — 项目 Skill (.claude/skills/project/)
    • 管理员通过 REST API 维护
    • 按触发词匹配后注入

  Tier 3 — 用户 Skill (.claude/skills/user/)
    • 用户通过 REST API 或 Agent 对话创建
    • 按触发词匹配后注入

注入顺序（用户优先，系统兜底）：
  [Tier 3 user-triggered] → [Tier 2 project-triggered] →
  [Tier 1 base (always)] → [Tier 1 system-triggered]

向后兼容：
  若 .claude/skills/system/ 不存在，则回退到扫描 .claude/skills/ 根目录（旧布局）。

File format:
  ---
  name: skill-name
  version: "1.0"
  description: Short description (≤120 chars)
  triggers:
    - keyword1
    - keyword2
  category: engineering|analytics|general|system
  priority: high|medium|low
  always_inject: false        # true → 不依赖触发词，始终追加进 system prompt
  ---

  # Skill Title

  [Markdown content — injected verbatim into system prompt]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Tier 常量
TIER_SYSTEM = "system"
TIER_PROJECT = "project"
TIER_USER = "user"

# Priority ordering for sort
_PRIORITY_ORDER: Dict[str, int] = {"high": 0, "medium": 1, "low": 2}

# 每层最多注入的触发 skill 数量（防止 context 爆炸）
_MAX_TRIGGERED_PER_TIER = 3

# 注入内容的总字符上限（防止 skill 注入导致 context 过长）
# 超过此限制时，降级为元数据摘要模式（只注入 name + description + triggers 摘要）
# 行业参考：16000 chars ≈ 4000 tokens，适合多技能场景（Dify/Coze 等生产系统同级设置）
_MAX_INJECT_CHARS = 16000


# ──────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────


@dataclass
class SkillMD:
    """
    A skill loaded from a SKILL.md file.

    Attributes:
        name:          Unique skill identifier (kebab-case)
        version:       Version string for change detection
        description:   One-line description (used in skill index)
        triggers:      Keywords that activate this skill
        category:      engineering | analytics | general | system
        priority:      high | medium | low
        content:       Full Markdown body (injected into system prompt)
        filepath:      Source file path
        tier:          system | project | user
        always_inject: If True, injected regardless of triggers (e.g. _base-*.md)
    """
    name: str
    version: str
    description: str
    triggers: List[str]
    category: str
    priority: str
    content: str
    filepath: str
    tier: str = TIER_SYSTEM
    always_inject: bool = False
    # Extended taxonomy fields (T5)
    scope: str = ""          # global-ch / aggregator / env-sg / env-idn / ...
    layer: str = ""          # workflow / scenario / knowledge / maintenance
    sub_skills: List[str] = field(default_factory=list)   # child skill names to load when parent matched
    env_tags: List[str] = field(default_factory=list)     # env filter: [sg, idn, br, my, thai, mx]

    def matches(self, message: str) -> bool:
        """Return True if any trigger keyword appears in the message."""
        if self.always_inject:
            return False  # always_inject skills use separate path
        msg_lower = message.lower()
        return any(t.lower() in msg_lower for t in self.triggers)

    def get_metadata_summary(self) -> str:
        """~100-token metadata summary for skill index display."""
        first_section = "\n".join(self.content.splitlines()[:6])
        return (
            f"**{self.name}** [{self.category}/{self.priority}]\n"
            f"{self.description}\n"
            f"触发词: {', '.join(self.triggers[:5])}\n"
            f"---\n{first_section}"
        )

    def get_injection(self) -> str:
        """Full content formatted for system-prompt injection."""
        tier_label = {"system": "系统", "project": "项目", "user": "用户"}.get(self.tier, self.tier)
        return (
            f"## 专业技能：{self.name}（{tier_label}层）\n"
            f"*{self.description}*\n\n"
            f"{self.content}"
        )


# ──────────────────────────────────────────────────────────
# SkillLoader (3-Tier Registry)
# ──────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


class SkillLoader:
    """
    三层 SKILL.md 注册表，取代旧的单层扫描。

    Usage::

        loader = SkillLoader()
        loader.load_all()
        prompt_extra = loader.build_skill_prompt("帮我设计ETL脚本")
    """

    def __init__(self, skills_dir: Optional[str] = None):
        if skills_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            skills_dir = str(project_root / ".claude" / "skills")

        self.skills_dir = Path(skills_dir)
        # 三层 skill 字典：name → SkillMD
        self._system_skills: Dict[str, SkillMD] = {}
        self._project_skills: Dict[str, SkillMD] = {}
        self._user_skills: Dict[str, SkillMD] = {}
        # 始终注入的 base skill 列表（系统层中 always_inject=True 的 skill）
        self._base_skills: List[SkillMD] = []
        self._loaded = False
        # 加载失败记录（文件路径 → 错误原因），每次 load_all() 重置
        self._load_errors: List[Dict[str, str]] = []
        # 最近一次 build_skill_prompt_async() 的匹配结果（供 AgenticLoop 取用后发 SSE 事件）
        self._last_match_info: Dict[str, Any] = {}
        # skill_set_version：每次热重载后递增，用于缓存版本校验
        self._skill_set_version: int = 0

        # 延迟初始化语义路由组件（避免 import 时产生副作用）
        self._routing_cache: Optional[object] = None  # SkillRoutingCache
        self._semantic_router: Optional[object] = None  # SkillSemanticRouter
        self._routing_components_inited = False

    # ── Loading ──────────────────────────────────────────

    def load_all(self) -> List[SkillMD]:
        """
        扫描三层目录，加载所有 SkillMD 文件。
        返回所有成功加载的 SkillMD 列表（含三层）。
        """
        self._system_skills.clear()
        self._project_skills.clear()
        self._user_skills.clear()
        self._base_skills.clear()
        self._load_errors.clear()

        if not self.skills_dir.exists():
            logger.warning(f"[SkillLoader] Skills dir not found: {self.skills_dir}")
            self._loaded = True
            self._skill_set_version += 1
            if self._routing_cache is not None:
                self._routing_cache.update_version(f"v{self._skill_set_version}")
            return []

        system_dir = self.skills_dir / "system"
        project_dir = self.skills_dir / "project"
        user_dir = self.skills_dir / "user"

        # 系统层：若 system/ 子目录存在则扫描之，否则回退到根目录（向后兼容）
        if system_dir.exists():
            self._load_tier_dir(system_dir, TIER_SYSTEM, self._system_skills)
        else:
            # 旧布局：系统 skill 直接放在 .claude/skills/ 根
            logger.info("[SkillLoader] system/ not found, falling back to root dir scan")
            self._load_tier_dir(self.skills_dir, TIER_SYSTEM, self._system_skills, exclude_subdirs=True)

        # 项目层
        if project_dir.exists():
            self._load_tier_dir(project_dir, TIER_PROJECT, self._project_skills)

        # 用户层（scan_subdirs=True：同时扫描 user/{username}/ 子目录，支持 ENABLE_AUTH=true）
        if user_dir.exists():
            self._load_tier_dir(user_dir, TIER_USER, self._user_skills, scan_subdirs=True)

        # 提取 always_inject 的 base skill
        self._base_skills = [
            s for s in self._system_skills.values() if s.always_inject
        ]

        all_loaded = (
            list(self._system_skills.values())
            + list(self._project_skills.values())
            + list(self._user_skills.values())
        )
        self._loaded = True
        logger.info(
            "[SkillLoader] Loaded: system=%d (base=%d), project=%d, user=%d",
            len(self._system_skills), len(self._base_skills),
            len(self._project_skills), len(self._user_skills),
        )
        # 每次重载后递增版本号，旧缓存条目在查询时自动失效
        self._skill_set_version += 1
        if self._routing_cache is not None:
            self._routing_cache.update_version(f"v{self._skill_set_version}")
        return all_loaded

    def _load_tier_dir(
        self,
        dirpath: Path,
        tier: str,
        target_dict: Dict[str, SkillMD],
        exclude_subdirs: bool = False,
        scan_subdirs: bool = False,
    ) -> None:
        """Scan dirpath for *.md files and load into target_dict.

        scan_subdirs=True additionally scans one level of subdirectories
        (used for user tier to support per-user subdirs like user/{username}/).
        """
        patterns = ["*.md"]
        if scan_subdirs:
            patterns.append("*/*.md")
        filepaths = set()
        for pattern in patterns:
            filepaths.update(dirpath.glob(pattern))
        for filepath in sorted(filepaths):
            name = filepath.name
            if name.upper() == "README.MD":
                continue
            skill = self._parse_file(filepath, tier=tier)
            if skill:
                target_dict[skill.name] = skill
                logger.debug(
                    "[SkillLoader][%s] Loaded '%s' (always_inject=%s, triggers=%d)",
                    tier, skill.name, skill.always_inject, len(skill.triggers),
                )

    def _parse_file(self, filepath: Path, tier: str = TIER_SYSTEM) -> Optional[SkillMD]:
        """Parse a single SKILL.md file. Returns None on error."""
        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"Cannot read file: {exc}"
            logger.warning(f"[SkillLoader] {msg}: {filepath}")
            self._load_errors.append({"filepath": str(filepath), "reason": msg})
            return None

        match = _FRONTMATTER_RE.match(text)
        if not match:
            msg = "Missing YAML frontmatter (file must start with ---)"
            logger.warning(f"[SkillLoader] {msg}: {filepath}")
            self._load_errors.append({"filepath": str(filepath), "reason": msg})
            return None

        body = text[match.end():].strip()
        try:
            meta = _parse_yaml_subset(match.group(1))
        except Exception as exc:
            msg = f"Bad frontmatter: {exc}"
            logger.warning(f"[SkillLoader] {msg}: {filepath}")
            self._load_errors.append({"filepath": str(filepath), "reason": msg})
            return None

        # always_inject: 由 frontmatter 字段控制，也可由文件名 _base 前缀隐式设置
        always_inject_meta = str(meta.get("always_inject", "false")).lower() == "true"
        always_inject_by_name = filepath.stem.startswith("_base")
        always_inject = always_inject_meta or always_inject_by_name

        return SkillMD(
            name=str(meta.get("name") or filepath.stem),
            version=str(meta.get("version") or "1.0"),
            description=str(meta.get("description") or ""),
            triggers=_as_list(meta.get("triggers")),
            category=str(meta.get("category") or "general"),
            priority=str(meta.get("priority") or "medium"),
            content=body,
            filepath=str(filepath),
            tier=tier,
            always_inject=always_inject,
            scope=str(meta.get("scope") or ""),
            layer=str(meta.get("layer") or ""),
            sub_skills=_as_list(meta.get("sub_skills")),
            env_tags=_as_list(meta.get("env_tags")),
        )

    # ── Querying ─────────────────────────────────────────

    def get_load_errors(self) -> List[Dict[str, str]]:
        """Return list of skill files that failed to load, with reason.

        Each entry: {"filepath": "...", "reason": "..."}
        """
        return list(self._load_errors)

    def get_last_match_info(self) -> Dict[str, Any]:
        """Return match metadata from the most recent build_skill_prompt_async() call.

        Shape:
          {
            "mode": "keyword|hybrid|llm",
            "matched": [{"name", "tier", "method", "hit_triggers", "score"}, ...],
            "always_inject": [{"name", "tier"}, ...],
            "summary_mode": bool,
            "total_chars": int,
            "load_errors": [{"filepath", "reason"}, ...]
          }
        Returns empty dict if build_skill_prompt_async() has not been called yet.
        """
        return dict(self._last_match_info)

    def _make_match_info(
        self,
        mode: str,
        matched_skills: List["SkillMD"],
        match_details: Dict[str, dict],
        message: str,
        result_text: str,
    ) -> Dict[str, Any]:
        """Build the match_info dict from matched skill objects and match details."""
        msg_lower = message.lower()
        summary_mode = "摘要模式" in result_text
        return {
            "mode": mode,
            "matched": [
                {
                    "name": s.name,
                    "tier": s.tier,
                    "method": match_details.get(s.name, {}).get("method", "keyword"),
                    "hit_triggers": [t for t in s.triggers if t.lower() in msg_lower],
                    "score": match_details.get(s.name, {}).get("score", 1.0),
                }
                for s in matched_skills
            ],
            "always_inject": [
                {"name": s.name, "tier": s.tier}
                for s in self._base_skills
            ],
            "summary_mode": summary_mode,
            "total_chars": len(result_text),
            "load_errors": list(self._load_errors),
        }

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_all()

    def get_all(self) -> List[SkillMD]:
        """Return all loaded skills across all tiers (auto-loads on first call)."""
        self._ensure_loaded()
        return (
            list(self._system_skills.values())
            + list(self._project_skills.values())
            + list(self._user_skills.values())
        )

    def get_by_tier(self, tier: str) -> List[SkillMD]:
        """Return skills for a specific tier."""
        self._ensure_loaded()
        if tier == TIER_SYSTEM:
            return list(self._system_skills.values())
        elif tier == TIER_PROJECT:
            return list(self._project_skills.values())
        elif tier == TIER_USER:
            return list(self._user_skills.values())
        return []

    def find_triggered(self, message: str) -> List[SkillMD]:
        """
        Return skills from all tiers whose triggers appear in *message*.
        Sorted by priority within each tier.
        Backward-compatible: returns flat list.
        """
        self._ensure_loaded()
        result = []
        for tier_dict in (self._user_skills, self._project_skills, self._system_skills):
            triggered = [s for s in tier_dict.values() if s.matches(message)]
            triggered.sort(key=lambda s: _PRIORITY_ORDER.get(s.priority, 1))
            result.extend(triggered)
        return result

    def build_skill_prompt(self, message: str, user_id: str = "default") -> str:
        """
        构建三层叠加的 skill system-prompt 注入文本。

        注入顺序（用户优先，系统兜底）：
          1. Tier 3 用户层：与 message 匹配的用户 skill（最多 3 条）
          2. Tier 2 项目层：与 message 匹配的项目 skill（最多 3 条）
          3. Tier 1 系统层 base：始终注入的安全/规范 skill（不依赖触发词）
          4. Tier 1 系统层 triggered：与 message 匹配的专业规程 skill（最多 3 条）

        Returns empty string when nothing is activated.
        """
        self._ensure_loaded()

        # ── 1. 关键词匹配（各层独立，按 priority 取前 N）────────────────
        user_triggered = [s for s in self._user_skills.values() if s.matches(message)]
        user_triggered.sort(key=lambda s: _PRIORITY_ORDER.get(s.priority, 1))
        user_triggered = user_triggered[:_MAX_TRIGGERED_PER_TIER]

        proj_triggered = [s for s in self._project_skills.values() if s.matches(message)]
        proj_triggered.sort(key=lambda s: _PRIORITY_ORDER.get(s.priority, 1))
        proj_triggered = proj_triggered[:_MAX_TRIGGERED_PER_TIER]

        sys_triggered = [
            s for s in self._system_skills.values()
            if not s.always_inject and s.matches(message)
        ]
        sys_triggered.sort(key=lambda s: _PRIORITY_ORDER.get(s.priority, 1))
        sys_triggered = sys_triggered[:_MAX_TRIGGERED_PER_TIER]

        # ── 2. Sub-skill 展开（父 skill 声明的子 skill）──────────────────
        user_triggered, proj_triggered, sys_triggered = self._expand_sub_skills(
            user_triggered, proj_triggered, sys_triggered, message
        )

        # ── 3. 组装 prompt 文本（含 base skills）────────────────────────
        parts: List[str] = []
        if user_triggered:
            parts.append("# 你的个人技能规程（用户自定义）\n")
            for s in user_triggered:
                parts.append(s.get_injection())

        if proj_triggered:
            parts.append("# 项目知识规程\n")
            for s in proj_triggered:
                parts.append(s.get_injection())

        if self._base_skills:
            parts.append("# 基础安全约束（始终生效）\n")
            for s in self._base_skills:
                parts.append(s.get_injection())

        if sys_triggered:
            parts.append("# 专业技能规程\n")
            for s in sys_triggered:
                parts.append(s.get_injection())

        if not parts:
            return ""

        header = (
            "\n\n---\n\n# 当前激活的技能规程\n"
            "> 以下规程已根据请求自动激活，请严格遵循其中的规范与约束。\n"
        )

        full_text = header + "\n\n---\n\n".join(parts)

        # B5: context length cap — 超限时降级为元数据摘要模式
        if len(full_text) > _MAX_INJECT_CHARS:
            logger.warning(
                "[SkillLoader] Skill injection too long (%d chars > %d limit), "
                "falling back to summary mode",
                len(full_text), _MAX_INJECT_CHARS,
            )
            all_active = (
                user_triggered + proj_triggered + list(self._base_skills) + sys_triggered
            )
            summary_lines = []
            for s in all_active:
                tier_tag = f"[{s.tier}]"
                triggers_str = ", ".join(s.triggers[:5]) if s.triggers else "（始终激活）"
                summary_lines.append(
                    f"- **{s.name}** {tier_tag}: {s.description}  触发词: {triggers_str}"
                )
            logger.info(
                "[SkillLoader] Injecting (summary mode): %d skills", len(all_active)
            )
            return (
                "\n\n---\n\n# 当前激活的技能规程（摘要模式）\n"
                "> 注入内容超过字符上限，已降级为摘要。各规程详情请参考技能管理页面。\n\n"
                + "\n".join(summary_lines)
            )

        logger.info(
            "[SkillLoader] Injecting: user=%d proj=%d base=%d sys=%d",
            len(user_triggered), len(proj_triggered),
            len(self._base_skills), len(sys_triggered),
        )
        return full_text

    # ── Semantic routing helpers ──────────────────────────────────────────────

    def _ensure_routing_components(self) -> None:
        """延迟初始化语义路由组件（首次调用时）。"""
        if self._routing_components_inited:
            return
        self._routing_components_inited = True
        try:
            from backend.skills.skill_routing_cache import SkillRoutingCache
            from backend.skills.skill_semantic_router import SkillSemanticRouter
            from backend.config.settings import settings
            self._routing_cache = SkillRoutingCache(
                db_path=settings.skill_routing_cache_path,
                skill_set_version=f"v{self._skill_set_version}",
                ttl=settings.skill_semantic_cache_ttl,
            )
            self._semantic_router = SkillSemanticRouter()
            logger.info("[SkillLoader] Semantic routing components initialized")
        except Exception as e:
            logger.warning(
                "[SkillLoader] Failed to init semantic routing components: %s. "
                "Falling back to keyword-only mode.", e
            )
            self._routing_cache = None
            self._semantic_router = None

    def _build_from_matched_skills(
        self,
        user_skills: List[SkillMD],
        proj_skills: List[SkillMD],
        sys_skills: List[SkillMD],
    ) -> str:
        """
        将已匹配的三层 skill 组装成 skill_prompt 文本（含 _MAX_INJECT_CHARS 保护）。
        被 build_skill_prompt 和 build_skill_prompt_async 共用。
        """
        parts: List[str] = []

        if user_skills:
            parts.append("# 你的个人技能规程（用户自定义）\n")
            for s in user_skills:
                parts.append(s.get_injection())

        if proj_skills:
            parts.append("# 项目知识规程\n")
            for s in proj_skills:
                parts.append(s.get_injection())

        if self._base_skills:
            parts.append("# 基础安全约束（始终生效）\n")
            for s in self._base_skills:
                parts.append(s.get_injection())

        if sys_skills:
            parts.append("# 专业技能规程\n")
            for s in sys_skills:
                parts.append(s.get_injection())

        if not parts:
            return ""

        header = (
            "\n\n---\n\n# 当前激活的技能规程\n"
            "> 以下规程已根据请求自动激活，请严格遵循其中的规范与约束。\n"
        )
        full_text = header + "\n\n---\n\n".join(parts)

        if len(full_text) > _MAX_INJECT_CHARS:
            logger.warning(
                "[SkillLoader] Skill injection too long (%d chars > %d limit), "
                "falling back to summary mode",
                len(full_text), _MAX_INJECT_CHARS,
            )
            all_active = user_skills + proj_skills + list(self._base_skills) + sys_skills
            summary_lines = []
            for s in all_active:
                tier_tag = f"[{s.tier}]"
                triggers_str = ", ".join(s.triggers[:5]) if s.triggers else "（始终激活）"
                summary_lines.append(
                    f"- **{s.name}** {tier_tag}: {s.description}  触发词: {triggers_str}"
                )
            logger.info("[SkillLoader] Injecting (summary mode): %d skills", len(all_active))
            return (
                "\n\n---\n\n# 当前激活的技能规程（摘要模式）\n"
                "> 注入内容超过字符上限，已降级为摘要。各规程详情请参考技能管理页面。\n\n"
                + "\n".join(summary_lines)
            )

        logger.info(
            "[SkillLoader] Injecting: user=%d proj=%d base=%d sys=%d",
            len(user_skills), len(proj_skills),
            len(self._base_skills), len(sys_skills),
        )
        return full_text

    async def build_skill_prompt_async(
        self,
        message: str,
        llm_adapter=None,
        user_id: str = "default",
    ) -> str:
        """
        混合模式 skill 注入构建（async 版本）。

        模式由 settings.skill_match_mode 控制：
          keyword → 直接调用同步 build_skill_prompt（零延迟，完全向后兼容）
          llm     → 全部用 LLM 路由器打分
          hybrid  → 关键词先行，LLM 补充未命中部分（推荐）

        Args:
            message:     用户消息原文
            llm_adapter: 当前对话 LLM adapter（hybrid/llm 模式需要）
            user_id:     保留参数，与同步版本兼容

        Returns:
            str: skill_prompt 注入文本（空字符串表示无激活技能）
        """
        self._ensure_loaded()

        # 读取配置
        try:
            from backend.config.settings import settings
            mode = settings.skill_match_mode
            threshold = settings.skill_semantic_threshold
        except Exception:
            mode = "keyword"
            threshold = 0.45

        # keyword 模式：内联关键词匹配，同步路径，零额外延迟
        if mode == "keyword":
            kw_user_k = [s for s in self._user_skills.values() if s.matches(message)]
            kw_proj_k = [s for s in self._project_skills.values() if s.matches(message)]
            kw_sys_k = [
                s for s in self._system_skills.values()
                if not s.always_inject and s.matches(message)
            ]
            result_k = self._build_from_matched_skills(kw_user_k, kw_proj_k, kw_sys_k)
            all_matched_k = kw_user_k + kw_proj_k + kw_sys_k
            self._last_match_info = self._make_match_info(
                mode=mode,
                matched_skills=all_matched_k,
                match_details={s.name: {"method": "keyword", "score": 1.0} for s in all_matched_k},
                message=message,
                result_text=result_k,
            )
            return result_k

        # hybrid / llm 模式：初始化路由组件
        self._ensure_routing_components()

        # ── Phase 1: 关键词匹配 ──────────────────────────────────
        kw_user = [s for s in self._user_skills.values() if s.matches(message)]
        kw_proj = [s for s in self._project_skills.values() if s.matches(message)]
        kw_sys = [
            s for s in self._system_skills.values()
            if not s.always_inject and s.matches(message)
        ]
        kw_names = {s.name for s in kw_user + kw_proj + kw_sys}
        kw_names |= {s.name for s in self._base_skills}  # always_inject 不参与语义路由

        # llm 模式下关键词结果清空，全部交给语义路由
        if mode == "llm":
            kw_user, kw_proj, kw_sys = [], [], []
            kw_names = {s.name for s in self._base_skills}

        # ── Phase 2: 语义路由 ────────────────────────────────────
        # 候选：排除已命中和 always_inject 的 skill
        all_skills = (
            list(self._user_skills.values())
            + list(self._project_skills.values())
            + [s for s in self._system_skills.values() if not s.always_inject]
        )
        candidates = [s for s in all_skills if s.name not in kw_names]

        semantic_scores: Dict[str, float] = {}
        match_details: Dict[str, dict] = {}

        # 关键词命中的 skill 记入 match_details
        for s in kw_user + kw_proj + kw_sys:
            match_details[s.name] = {"method": "keyword", "score": 1.0}

        if candidates and (self._routing_cache is not None or llm_adapter is not None):
            # 查缓存
            cached = self._routing_cache.get(message) if self._routing_cache else None
            if cached is not None:
                semantic_scores = cached
                logger.debug("[SkillLoader] semantic routing cache hit for message=%r", message[:60])
            elif self._semantic_router is not None and llm_adapter is not None:
                # 缓存未命中，调用 LLM 路由
                try:
                    semantic_scores = await self._semantic_router.route(
                        message, candidates, llm_adapter
                    )
                    if self._routing_cache is not None:
                        self._routing_cache.put(message, semantic_scores)
                except Exception as e:
                    logger.warning("[SkillLoader] semantic routing failed, fallback: %s", e)

        # 按阈值过滤，分配到对应层
        sem_user: List[SkillMD] = []
        sem_proj: List[SkillMD] = []
        sem_sys: List[SkillMD] = []
        for s in candidates:
            score = semantic_scores.get(s.name, 0.0)
            if score >= threshold:
                match_details[s.name] = {"method": "semantic", "score": score}
                if s.tier == TIER_USER:
                    sem_user.append(s)
                elif s.tier == TIER_PROJECT:
                    sem_proj.append(s)
                else:
                    sem_sys.append(s)

        # ── 合并结果，按 priority 排序，各层最多 _MAX_TRIGGERED_PER_TIER 条 ──
        def _sort_take(lst: List[SkillMD]) -> List[SkillMD]:
            lst.sort(key=lambda s: _PRIORITY_ORDER.get(s.priority, 1))
            return lst[:_MAX_TRIGGERED_PER_TIER]

        final_user = _sort_take(kw_user + sem_user)
        final_proj = _sort_take(kw_proj + sem_proj)
        final_sys = _sort_take(kw_sys + sem_sys)

        # ── Sub-skill expansion ───────────────────────────────
        final_user, final_proj, final_sys = self._expand_sub_skills(
            final_user, final_proj, final_sys, message
        )

        result = self._build_from_matched_skills(final_user, final_proj, final_sys)
        all_matched = final_user + final_proj + final_sys
        self._last_match_info = self._make_match_info(
            mode=mode,
            matched_skills=all_matched,
            match_details=match_details,
            message=message,
            result_text=result,
        )
        return result

    def get_match_details(
        self,
        message: str,
        semantic_scores: Optional[Dict[str, float]] = None,
        threshold: float = 0.45,
    ) -> Dict[str, dict]:
        """
        返回每个触发 skill 的命中方式（keyword/semantic/always_inject）和分数。
        供 /skills/preview API 使用。
        """
        self._ensure_loaded()
        details: Dict[str, dict] = {}

        # always_inject
        for s in self._base_skills:
            details[s.name] = {"method": "always_inject", "score": 1.0, "tier": s.tier}

        # keyword
        for tier_dict in (self._user_skills, self._project_skills, self._system_skills):
            for s in tier_dict.values():
                if s.always_inject:
                    continue
                if s.matches(message):
                    details[s.name] = {"method": "keyword", "score": 1.0, "tier": s.tier}

        # semantic
        if semantic_scores:
            all_skills = (
                list(self._user_skills.values())
                + list(self._project_skills.values())
                + [s for s in self._system_skills.values() if not s.always_inject]
            )
            for s in all_skills:
                if s.name in details:
                    continue
                score = semantic_scores.get(s.name, 0.0)
                if score >= threshold:
                    details[s.name] = {"method": "semantic", "score": score, "tier": s.tier}

        return details

    # ── Sub-skill expansion (T6) ──────────────────────────

    def _expand_sub_skills(
        self,
        user_skills: List["SkillMD"],
        proj_skills: List["SkillMD"],
        sys_skills: List["SkillMD"],
        message: str,
    ) -> tuple:
        """Expand parent skills' sub_skills declarations into additional matched skills.

        For each matched skill that declares sub_skills:
          1. Look up the sub skill by name across all tier dicts.
          2. If sub skill has env_tags, check _detect_env(message) matches.
          3. Append passing sub skills to the appropriate tier list.

        Sub-skills bypass the per-tier 3-item cap but are still subject to
        _MAX_INJECT_CHARS in _build_from_matched_skills.
        """
        matched_names = {s.name for s in user_skills + proj_skills + sys_skills}
        matched_names |= {s.name for s in self._base_skills}

        # Build a flat name→skill lookup across all tiers
        all_skill_map: Dict[str, "SkillMD"] = {}
        for d in (self._user_skills, self._project_skills, self._system_skills):
            all_skill_map.update(d)

        detected_env = _detect_env(message)
        extra_user: List["SkillMD"] = []
        extra_proj: List["SkillMD"] = []
        extra_sys: List["SkillMD"] = []

        for parent in user_skills + proj_skills + sys_skills:
            if not parent.sub_skills:
                continue
            for sub_name in parent.sub_skills:
                if sub_name in matched_names:
                    continue
                sub = all_skill_map.get(sub_name)
                if sub is None:
                    logger.debug(
                        "[SkillLoader] sub_skill '%s' declared by '%s' not found",
                        sub_name, parent.name,
                    )
                    continue
                # env_tags filter: if sub has env_tags, only load when env matches
                if sub.env_tags:
                    if detected_env not in sub.env_tags:
                        logger.debug(
                            "[SkillLoader] sub_skill '%s' skipped (env_tags=%s, detected=%s)",
                            sub_name, sub.env_tags, detected_env,
                        )
                        continue
                matched_names.add(sub_name)
                if sub.tier == TIER_USER:
                    extra_user.append(sub)
                elif sub.tier == TIER_PROJECT:
                    extra_proj.append(sub)
                else:
                    extra_sys.append(sub)

        if extra_user or extra_proj or extra_sys:
            logger.info(
                "[SkillLoader] Sub-skill expansion: +user=%d +proj=%d +sys=%d",
                len(extra_user), len(extra_proj), len(extra_sys),
            )

        return (
            user_skills + extra_user,
            proj_skills + extra_proj,
            sys_skills + extra_sys,
        )

    # ── Backward-compat alias ──────────────────────────
    def list_all(self) -> List[SkillMD]:
        """Alias for get_all() — backward compatibility."""
        return self.get_all()

    def list_skills(self) -> List[SkillMD]:
        """Alias for get_all() — backward compatibility."""
        return self.get_all()


# ──────────────────────────────────────────────────────────
# Environment detection helper (T6)
# ──────────────────────────────────────────────────────────

_ENV_KEYWORDS: Dict[str, List[str]] = {
    "sg":   ["sg", "singapore", "\u65b0\u52a0\u5761", "sg_azure", "azure"],
    "idn":  ["idn", "indonesia", "\u5370\u5c3c"],
    "br":   ["br", "brazil", "\u5df4\u897f"],
    "my":   ["my", "malaysia", "\u9a6c\u6765"],
    "thai": ["thai", "thailand", "\u6cf0\u56fd"],
    "mx":   ["mx", "mexico", "\u58a8\u897f\u54e5"],
}


def _detect_env(message: str) -> Optional[str]:
    """Detect target ClickHouse environment from user message keywords.

    Returns env name (sg/idn/br/my/thai/mx) or None if not detected.
    """
    msg_lower = message.lower()
    for env, keywords in _ENV_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            return env
    return None


# ──────────────────────────────────────────────────────────
# Simple YAML subset parser (no external deps)
# ──────────────────────────────────────────────────────────


def _parse_yaml_subset(text: str) -> Dict[str, Any]:
    """
    Parse the YAML frontmatter subset used by SKILL.md files.

    Supports:
      - key: scalar value
      - key:
          - list item 1
          - list item 2
    Does NOT support nested objects or multi-line scalars.
    """
    result: Dict[str, Any] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" in line:
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()

            if not rest or rest.startswith("#"):
                # Collect indented list items
                items: List[str] = []
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if re.match(r"^\s+-\s+", next_line):
                        items.append(next_line.strip()[2:].strip())
                        i += 1
                    else:
                        break
                result[key] = items
                continue
            else:
                # Scalar: strip inline comment and quotes
                value = rest.split("#")[0].strip().strip('"').strip("'")
                result[key] = value
        i += 1

    return result


def _as_list(value: Any) -> List[str]:
    """Coerce a parsed YAML value to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if value is None:
        return []
    return [str(value)]


# ──────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────

_singleton: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    """Return the process-wide SkillLoader instance (lazily initialised)."""
    global _singleton
    if _singleton is None:
        _singleton = SkillLoader()
    return _singleton


def reload_skills() -> List[SkillMD]:
    """Force-reload all skills and return the new list."""
    return get_skill_loader().load_all()
