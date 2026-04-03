"""
Agent Skills 模块

SKILL.md 加载器 + 文件监听热加载。
Python class-based skill 实现已移除（废弃），仅保留 base.py 供 agents 系统使用。
"""

from .base import (
    BaseSkill,
    SkillInput,
    SkillOutput,
    SkillType,
    create_skill_output,
    register_skill,
    SkillRegistry
)

__all__ = [
    "BaseSkill",
    "SkillInput",
    "SkillOutput",
    "SkillType",
    "create_skill_output",
    "register_skill",
    "SkillRegistry",
]
