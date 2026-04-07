"""
大语言模型配置表

存储各种大模型的API配置信息
"""
from sqlalchemy import Column, String, DateTime, Text, Boolean, JSON, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime
import uuid

from backend.config.database import Base


class LLMConfig(Base):
    """大语言模型配置表"""

    __tablename__ = "llm_configs"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 模型标识
    model_key = Column(
        String(50),
        nullable=False,
        unique=True,
        comment="模型唯一标识: claude, gemini, qianwen, doubao"
    )
    model_name = Column(String(100), nullable=False, comment="模型显示名称")
    model_type = Column(String(50), nullable=False, comment="模型类型,对应adapter")

    # API配置
    api_base_url = Column(String(500), nullable=True, comment="API基础URL")
    api_key = Column(Text, nullable=True, comment="API密钥")
    api_secret = Column(Text, nullable=True, comment="API密钥(部分模型需要)")

    # 模型参数
    default_model = Column(String(100), nullable=True, comment="默认使用的具体模型")
    temperature = Column(String(10), default="0.7", comment="温度参数")
    max_tokens = Column(String(10), default="8192", comment="最大token数")

    # 额外配置
    extra_config = Column(JSONB, nullable=True, comment="额外配置参数")

    # 状态
    is_enabled = Column(Boolean, default=True, comment="是否启用")
    is_default = Column(Boolean, default=False, comment="是否为默认模型")

    # 描述信息
    description = Column(Text, nullable=True, comment="模型描述")
    icon = Column(String(200), nullable=True, comment="图标URL或emoji")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间"
    )

    # 索引
    __table_args__ = (
        Index("idx_llm_configs_model_key", "model_key"),
        Index("idx_llm_configs_is_enabled", "is_enabled"),
    )

    def __repr__(self):
        return f"<LLMConfig(model_key={self.model_key}, model_name={self.model_name})>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": str(self.id),
            "model_key": self.model_key,
            "model_name": self.model_name,
            "model_type": self.model_type,
            "api_base_url": self.api_base_url,
            # 注意: api_key 敏感信息不应直接返回,这里用掩码
            "api_key": self._mask_api_key(self.api_key) if self.api_key else None,
            "api_secret": self._mask_api_key(self.api_secret) if self.api_secret else None,
            "default_model": self.default_model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "extra_config": self.extra_config,
            "is_enabled": self.is_enabled,
            "is_default": self.is_default,
            "description": self.description,
            "icon": self.icon,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_dict_with_secrets(self):
        """转换为字典(包含敏感信息)"""
        data = self.to_dict()
        data["api_key"] = self.api_key
        data["api_secret"] = self.api_secret
        return data

    @staticmethod
    def _mask_api_key(key: str) -> str:
        """掩码API密钥"""
        if not key or len(key) < 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"


# 默认配置数据
DEFAULT_LLM_CONFIGS = [
    {
        "model_key": "claude",
        "model_name": "Claude Code",
        "model_type": "claude",
        "api_base_url": "http://10.0.3.248:3000/api",
        "api_key": "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4",
        "default_model": "claude-sonnet-4-5",
        "temperature": "0.7",
        "max_tokens": "8192",
        "is_enabled": True,
        "is_default": True,
        "description": "Anthropic Claude - 强大的AI助手,擅长编码和深度思考",
        "icon": "🤖",
        "extra_config": {
            "supports_streaming": True,
            "supports_tools": True,
            "supports_vision": True,
        }
    },
    {
        "model_key": "gemini",
        "model_name": "Google Gemini",
        "model_type": "gemini",
        "api_base_url": "https://generativelanguage.googleapis.com",
        "api_key": "",
        "default_model": "gemini-pro",
        "temperature": "0.7",
        "max_tokens": "4096",
        "is_enabled": False,
        "is_default": False,
        "description": "Google Gemini - Google最新的多模态AI模型",
        "icon": "✨",
        "extra_config": {
            "supports_streaming": True,
            "supports_tools": True,
            "supports_vision": True,
        }
    },
    {
        "model_key": "qianwen",
        "model_name": "通义千问",
        "model_type": "qianwen",
        "api_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "default_model": "qwen3-max",
        "temperature": "0.7",
        "max_tokens": "8192",
        "is_enabled": False,
        "is_default": False,
        "description": "阿里巴巴通义千问 - 强大的中文语言模型",
        "icon": "🌟",
        "extra_config": {
            "supports_streaming": True,
            "supports_tools": True,
            "supports_vision": False,
            "fallback_models": ["deepseek-v3.2", "qwen3.5-plus", "qwen3.5"],
        }
    },
    {
        "model_key": "openai",
        "model_name": "OpenAI GPT",
        "model_type": "openai",
        "api_base_url": "http://10.0.3.112/v1",
        "api_key": "",
        "default_model": "openai-gpt-5.4",
        "temperature": "0.7",
        "max_tokens": "4096",
        "is_enabled": False,
        "is_default": False,
        "description": "OpenAI GPT - 通过内网代理访问，默认模型 gpt-5.4",
        "icon": "🧠",
        "extra_config": {
            "supports_streaming": True,
            "supports_tools": True,
            "supports_vision": True,
        }
    },
    {
        "model_key": "doubao",
        "model_name": "豆包",
        "model_type": "doubao",
        "api_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "",
        "api_secret": "",
        "default_model": "doubao-pro",
        "temperature": "0.7",
        "max_tokens": "4096",
        "is_enabled": False,
        "is_default": False,
        "description": "字节跳动豆包 - 高效的AI对话助手",
        "icon": "🫘",
        "extra_config": {
            "supports_streaming": True,
            "supports_tools": False,
            "supports_vision": False,
        }
    }
]
