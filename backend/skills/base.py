"""
Skill基础框架

定义Agent Skills的通用接口和基类
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import asyncio
import json
from datetime import datetime


class SkillType(str, Enum):
    """技能类型"""
    DATABASE = "database"
    ANALYSIS = "analysis"
    SQL = "sql"
    VISUALIZATION = "visualization"
    ETL = "etl"
    DATA_PROCESSING = "data_processing"
    UTILITY = "utility"


class SkillStatus(str, Enum):
    """技能执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SkillInput:
    """技能输入"""
    parameters: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SkillOutput:
    """技能输出"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    execution_time: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "execution_time": self.execution_time,
            "metadata": self.metadata
        }


class BaseSkill(ABC):
    """技能基类"""

    def __init__(self, name: str, description: str, skill_type: SkillType):
        """
        初始化技能

        Args:
            name: 技能名称
            description: 技能描述
            skill_type: 技能类型
        """
        self.name = name
        self.description = description
        self.skill_type = skill_type
        self.created_at = datetime.utcnow()
        self.execution_count = 0
        self.success_count = 0
        self.error_count = 0

    @abstractmethod
    async def execute(self, input_data: SkillInput) -> SkillOutput:
        """
        执行技能

        Args:
            input_data: 技能输入

        Returns:
            技能输出
        """
        pass

    async def __call__(self, input_data: SkillInput) -> SkillOutput:
        """使技能可调用"""
        self.execution_count += 1
        start_time = datetime.utcnow()

        try:
            output = await self.execute(input_data)

            # 记录执行时间
            end_time = datetime.utcnow()
            execution_time = (end_time - start_time).total_seconds()
            output.execution_time = execution_time

            # 更新统计
            if output.success:
                self.success_count += 1
            else:
                self.error_count += 1

            return output
        except Exception as e:
            self.error_count += 1
            return SkillOutput(
                success=False,
                error=str(e)
            )

    def get_stats(self) -> Dict[str, Any]:
        """获取技能统计信息"""
        success_rate = 0
        if self.execution_count > 0:
            success_rate = (self.success_count / self.execution_count) * 100

        return {
            "name": self.name,
            "description": self.description,
            "type": self.skill_type.value,
            "created_at": self.created_at.isoformat(),
            "execution_count": self.execution_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": round(success_rate, 2)
        }

    def validate_input(self, input_data: SkillInput) -> bool:
        """
        验证输入

        Args:
            input_data: 技能输入

        Returns:
            是否有效
        """
        # 子类可以重写此方法进行自定义验证
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取技能参数Schema"""
        return {
            "type": "object",
            "properties": {},
            "required": []
        }


class CompositeSkill(BaseSkill):
    """组合技能 - 包含多个子技能"""

    def __init__(
        self,
        name: str,
        description: str,
        skill_type: SkillType,
        skills: List[BaseSkill]
    ):
        super().__init__(name, description, skill_type)
        self.skills = skills

    async def execute(self, input_data: SkillInput) -> SkillOutput:
        """执行组合技能"""
        results = []
        current_input = input_data

        for skill in self.skills:
            result = await skill(current_input)
            results.append({
                "skill": skill.name,
                "result": result.to_dict()
            })

            if not result.success:
                return SkillOutput(
                    success=False,
                    error=f"技能 {skill.name} 执行失败: {result.error}",
                    data={"step_results": results}
                )

            # 将输出作为下一个技能的输入
            if result.data:
                current_input = SkillInput(
                    parameters=result.data,
                    context=current_input.context,
                    metadata={"previous_skill": skill.name}
                )

        return SkillOutput(
            success=True,
            data={
                "final_result": results[-1]["result"]["data"],
                "step_results": results
            }
        )

    def add_skill(self, skill: BaseSkill):
        """添加子技能"""
        self.skills.append(skill)

    def remove_skill(self, skill_name: str):
        """移除子技能"""
        self.skills = [s for s in self.skills if s.name != skill_name]


class SkillRegistry:
    """技能注册表"""

    def __init__(self):
        self.skills: Dict[str, BaseSkill] = {}
        self.skill_types: Dict[SkillType, List[str]] = {}

    def register(self, skill: BaseSkill):
        """注册技能"""
        self.skills[skill.name] = skill

        # 按类型分组
        if skill.skill_type not in self.skill_types:
            self.skill_types[skill.skill_type] = []
        self.skill_types[skill.skill_type].append(skill.name)

    def unregister(self, skill_name: str):
        """注销技能"""
        if skill_name in self.skills:
            skill = self.skills[skill_name]
            del self.skills[skill_name]

            # 从类型分组中移除
            if skill.skill_type in self.skill_types:
                self.skill_types[skill.skill_type].remove(skill_name)

    def get(self, skill_name: str) -> Optional[BaseSkill]:
        """获取技能"""
        return self.skills.get(skill_name)

    def get_skill(self, skill_name: str) -> Optional[BaseSkill]:
        """获取技能（get方法的别名，保持API兼容性）"""
        return self.get(skill_name)

    def list_all(self) -> List[BaseSkill]:
        """列出所有技能"""
        return list(self.skills.values())

    def list_skills(self) -> List[BaseSkill]:
        """列出所有技能（list_all的别名，保持API兼容性）"""
        return self.list_all()

    def list_by_type(self, skill_type: SkillType) -> List[BaseSkill]:
        """按类型列出技能"""
        skill_names = self.skill_types.get(skill_type, [])
        return [self.skills[name] for name in skill_names]

    def get_all_stats(self) -> List[Dict[str, Any]]:
        """获取所有技能统计"""
        return [skill.get_stats() for skill in self.skills.values()]


# 全局技能注册表
_global_registry = SkillRegistry()


def get_registry() -> SkillRegistry:
    """获取全局技能注册表"""
    return _global_registry


def register_skill(skill: BaseSkill):
    """注册技能装饰器"""
    get_registry().register(skill)
    return skill


# 辅助函数

def create_skill_input(
    parameters: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> SkillInput:
    """创建技能输入"""
    return SkillInput(
        parameters=parameters,
        context=context or {},
        metadata=metadata or {}
    )


def create_skill_output(
    success: bool,
    data: Any = None,
    error: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> SkillOutput:
    """创建技能输出"""
    return SkillOutput(
        success=success,
        data=data,
        error=error,
        metadata=metadata or {}
    )


# 示例用法

if __name__ == "__main__":
    # 示例技能
    class HelloSkill(BaseSkill):
        def __init__(self):
            super().__init__(
                name="hello",
                description="说Hello",
                skill_type=SkillType.UTILITY
            )

        async def execute(self, input_data: SkillInput) -> SkillOutput:
            name = input_data.parameters.get("name", "World")
            message = f"Hello, {name}!"

            return create_skill_output(
                success=True,
                data={"message": message}
            )

    # 注册技能
    hello_skill = HelloSkill()
    register_skill(hello_skill)

    # 使用技能
    import asyncio

    async def main():
        input_data = create_skill_input(
            parameters={"name": "Alice"}
        )

        output = await hello_skill(input_data)
        print(json.dumps(output.to_dict(), indent=2, ensure_ascii=False))

        # 获取统计
        print("\n技能统计:")
        print(json.dumps(hello_skill.get_stats(), indent=2, ensure_ascii=False))

    asyncio.run(main())
