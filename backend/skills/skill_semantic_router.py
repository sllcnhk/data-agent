"""
Skill Semantic Router
=====================
使用当前对话 LLM 对候选 skill 进行单次批量语义打分，
是混合命中机制（hybrid match）的 Phase 2 组件。

设计原则：
- 单次 LLM 调用批量处理所有候选 skill（非逐 skill 调用）
- 失败时静默返回 {}，调用方自动降级到纯关键词模式
- 与具体 LLM 提供商无关（使用统一 chat_plain 接口）
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from backend.skills.skill_loader import SkillMD

logger = logging.getLogger(__name__)

# 候选 skill 列表最大字符数（防止 prompt 过长）
_MAX_SKILL_LIST_CHARS = 2000
# 路由 LLM 响应最大 token 数（仅需 JSON，无需长篇）
_ROUTER_MAX_TOKENS = 300
# 消息最大截断长度（送给路由器的消息）
_MAX_MESSAGE_CHARS = 500

_ROUTER_SYSTEM = (
    "你是一个技能路由器。根据用户消息，判断候选技能的相关程度。"
    "只返回 JSON，不要其他解释。"
)

_ROUTER_PROMPT_TMPL = """\
用户消息：{message}

候选技能列表（格式: 名称: 描述 | 核心触发词）：
{skill_list}

请返回一个 JSON 对象，键为技能名，值为相关度分数（0.0~1.0）。
只包含分数 >= 0.3 的技能。无相关技能时返回空对象。

示例响应：{{"clickhouse-analyst": 0.9, "schema-explorer": 0.5}}"""

# 从 LLM 响应中提取 JSON 对象的正则（兼容 markdown 代码块包裹）
_JSON_RE = re.compile(r'\{[^{}]*\}', re.DOTALL)


def _skill_list_text(skills: List["SkillMD"]) -> str:
    """生成紧凑的技能描述文本，送给路由 LLM。"""
    lines = []
    total = 0
    for s in skills:
        triggers_preview = ", ".join(s.triggers[:5]) if s.triggers else "（无触发词）"
        desc = (s.description or "")[:80]
        line = f"- {s.name}: {desc} | {triggers_preview}"
        total += len(line)
        if total > _MAX_SKILL_LIST_CHARS:
            lines.append("- ...")
            break
        lines.append(line)
    return "\n".join(lines)


def _parse_routing_json(text: str) -> Dict[str, float]:
    """
    从 LLM 响应文本中提取并解析 JSON 评分对象。
    兼容 LLM 在代码块或文字中包裹 JSON 的各种情况。
    """
    # 优先尝试直接 JSON 解析
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return {k: float(v) for k, v in obj.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, ValueError):
        pass

    # 回退：正则提取第一个 {...} 块
    matches = _JSON_RE.findall(text)
    for m in matches:
        try:
            obj = json.loads(m)
            if isinstance(obj, dict):
                return {k: float(v) for k, v in obj.items() if isinstance(v, (int, float))}
        except (json.JSONDecodeError, ValueError):
            continue

    return {}


class SkillSemanticRouter:
    """
    单次 LLM 路由调用，批量对候选 skill 打分。

    Usage:
        router = SkillSemanticRouter()
        scores = await router.route(message, candidate_skills, llm_adapter)
        # scores = {"clickhouse-analyst": 0.9, "schema-explorer": 0.5}
    """

    async def route(
        self,
        message: str,
        candidate_skills: List["SkillMD"],
        llm_adapter,
    ) -> Dict[str, float]:
        """
        对候选 skill 列表进行语义相关度打分。

        Args:
            message: 用户消息原文
            candidate_skills: 未被关键词匹配命中的候选 skill 列表
            llm_adapter: 当前对话的 LLM adapter（需实现 chat_plain）

        Returns:
            dict: {skill_name: score (0.0~1.0)}，失败时返回 {}
        """
        if not candidate_skills:
            return {}

        if llm_adapter is None:
            logger.warning("[SkillSemanticRouter] llm_adapter is None, skipping semantic routing")
            return {}

        # 截断消息，避免路由 prompt 过长
        msg_truncated = message[:_MAX_MESSAGE_CHARS]
        if len(message) > _MAX_MESSAGE_CHARS:
            msg_truncated += "..."

        skill_list = _skill_list_text(candidate_skills)

        prompt = _ROUTER_PROMPT_TMPL.format(
            message=msg_truncated,
            skill_list=skill_list,
        )

        try:
            response = await llm_adapter.chat_plain(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_ROUTER_SYSTEM,
                max_tokens=_ROUTER_MAX_TOKENS,
            )
        except Exception as e:
            logger.warning(
                "[SkillSemanticRouter] LLM call failed, falling back to keyword-only: %s", e
            )
            return {}

        # 提取响应文本
        text = ""
        content = response.get("content", [])
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    break
                elif isinstance(block, str):
                    text = block
                    break

        if not text:
            logger.warning("[SkillSemanticRouter] Empty response from LLM router")
            return {}

        scores = _parse_routing_json(text)

        # 过滤掉不在候选列表中的 skill（防止 LLM 幻觉出不存在的 skill）
        valid_names = {s.name for s in candidate_skills}
        scores = {k: v for k, v in scores.items() if k in valid_names}

        logger.debug(
            "[SkillSemanticRouter] message=%r → scores=%s",
            message[:60],
            scores,
        )
        return scores
