"""
大语言模型配置管理API

提供模型配置的CRUD操作
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from backend.config.database import get_db
from backend.models.llm_config import LLMConfig, DEFAULT_LLM_CONFIGS
from pydantic import BaseModel, Field, ConfigDict

router = APIRouter(prefix="/llm-configs", tags=["模型配置"])


# ========== Pydantic模型 ==========

class CreateLLMConfigRequest(BaseModel):
    """创建模型配置请求"""
    model_config = ConfigDict(protected_namespaces=())

    model_key: str = Field(..., description="模型唯一标识")
    model_name: str = Field(..., description="模型显示名称")
    model_type: str = Field(..., description="模型类型")
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    default_model: Optional[str] = None
    temperature: str = "0.7"
    max_tokens: str = "8192"
    extra_config: Optional[dict] = None
    is_enabled: bool = True
    is_default: bool = False
    description: Optional[str] = None
    icon: Optional[str] = None


class UpdateLLMConfigRequest(BaseModel):
    """更新模型配置请求"""
    model_config = ConfigDict(protected_namespaces=())

    model_name: Optional[str] = None
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    default_model: Optional[str] = None
    temperature: Optional[str] = None
    max_tokens: Optional[str] = None
    extra_config: Optional[dict] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    description: Optional[str] = None
    icon: Optional[str] = None


# ========== API端点 ==========

@router.post("", response_model=dict, summary="创建模型配置")
async def create_llm_config(
    request: CreateLLMConfigRequest,
    db: Session = Depends(get_db)
):
    """
    创建新的模型配置

    - **model_key**: 模型唯一标识(claude/gemini/qianwen/doubao)
    - **model_name**: 模型显示名称
    - **model_type**: 模型类型,对应adapter
    - **api_base_url**: API基础URL
    - **api_key**: API密钥
    """
    # 检查是否已存在
    existing = db.query(LLMConfig).filter(
        LLMConfig.model_key == request.model_key
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail=f"模型配置已存在: {request.model_key}")

    # 创建配置
    config = LLMConfig(
        model_key=request.model_key,
        model_name=request.model_name,
        model_type=request.model_type,
        api_base_url=request.api_base_url,
        api_key=request.api_key,
        api_secret=request.api_secret,
        default_model=request.default_model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        extra_config=request.extra_config,
        is_enabled=request.is_enabled,
        is_default=request.is_default,
        description=request.description,
        icon=request.icon
    )

    # 如果设为默认,取消其他默认配置
    if request.is_default:
        db.query(LLMConfig).update({"is_default": False})

    db.add(config)
    db.commit()
    db.refresh(config)

    return {
        "success": True,
        "data": config.to_dict()
    }


@router.get("", response_model=dict, summary="获取模型配置列表")
async def list_llm_configs(
    enabled_only: bool = Query(default=False, description="仅返回启用的配置"),
    db: Session = Depends(get_db)
):
    """
    获取所有模型配置

    - **enabled_only**: 仅返回启用的配置
    """
    query = db.query(LLMConfig)

    if enabled_only:
        query = query.filter(LLMConfig.is_enabled == True)

    configs = query.all()

    return {
        "success": True,
        "data": [config.to_dict() for config in configs]
    }


@router.get("/{model_key}", response_model=dict, summary="获取模型配置详情")
async def get_llm_config(
    model_key: str,
    include_secrets: bool = Query(default=False, description="是否包含敏感信息"),
    db: Session = Depends(get_db)
):
    """
    获取模型配置详情

    - **model_key**: 模型标识
    - **include_secrets**: 是否返回完整的API密钥
    """
    config = db.query(LLMConfig).filter(
        LLMConfig.model_key == model_key
    ).first()

    if not config:
        raise HTTPException(status_code=404, detail=f"模型配置不存在: {model_key}")

    if include_secrets:
        data = config.to_dict_with_secrets()
    else:
        data = config.to_dict()

    return {
        "success": True,
        "data": data
    }


@router.put("/{model_key}", response_model=dict, summary="更新模型配置")
async def update_llm_config(
    model_key: str,
    request: UpdateLLMConfigRequest,
    db: Session = Depends(get_db)
):
    """
    更新模型配置

    - **model_key**: 模型标识
    - 其他参数: 要更新的字段
    """
    config = db.query(LLMConfig).filter(
        LLMConfig.model_key == model_key
    ).first()

    if not config:
        raise HTTPException(status_code=404, detail=f"模型配置不存在: {model_key}")

    # 更新字段
    update_data = request.dict(exclude_unset=True)

    for key, value in update_data.items():
        if hasattr(config, key):
            setattr(config, key, value)

    # 如果设为默认,取消其他默认配置
    if request.is_default:
        db.query(LLMConfig).filter(
            LLMConfig.model_key != model_key
        ).update({"is_default": False})

    db.commit()
    db.refresh(config)

    return {
        "success": True,
        "data": config.to_dict()
    }


@router.delete("/{model_key}", response_model=dict, summary="删除模型配置")
async def delete_llm_config(
    model_key: str,
    db: Session = Depends(get_db)
):
    """
    删除模型配置

    - **model_key**: 模型标识
    """
    config = db.query(LLMConfig).filter(
        LLMConfig.model_key == model_key
    ).first()

    if not config:
        raise HTTPException(status_code=404, detail=f"模型配置不存在: {model_key}")

    db.delete(config)
    db.commit()

    return {
        "success": True,
        "message": f"模型配置已删除: {model_key}"
    }


@router.post("/init-defaults", response_model=dict, summary="初始化默认配置")
async def init_default_configs(
    force: bool = Query(default=False, description="强制覆盖已有配置"),
    db: Session = Depends(get_db)
):
    """
    初始化默认的模型配置

    - **force**: 是否强制覆盖已有配置
    """
    created_count = 0
    updated_count = 0

    for default_config in DEFAULT_LLM_CONFIGS:
        existing = db.query(LLMConfig).filter(
            LLMConfig.model_key == default_config["model_key"]
        ).first()

        if existing:
            if force:
                # 更新
                for key, value in default_config.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                updated_count += 1
            # 否则跳过
        else:
            # 创建新配置
            config = LLMConfig(**default_config)
            db.add(config)
            created_count += 1

    db.commit()

    return {
        "success": True,
        "message": f"初始化完成: 创建{created_count}个, 更新{updated_count}个",
        "created": created_count,
        "updated": updated_count
    }


@router.get("/default/current", response_model=dict, summary="获取默认模型配置")
async def get_default_config(
    db: Session = Depends(get_db)
):
    """
    获取当前默认的模型配置
    """
    config = db.query(LLMConfig).filter(
        LLMConfig.is_default == True
    ).first()

    if not config:
        # 如果没有默认配置,返回第一个启用的配置
        config = db.query(LLMConfig).filter(
            LLMConfig.is_enabled == True
        ).first()

    if not config:
        raise HTTPException(status_code=404, detail="没有可用的模型配置")

    return {
        "success": True,
        "data": config.to_dict()
    }


@router.post("/{model_key}/test", response_model=dict, summary="测试模型配置")
async def test_llm_config(
    model_key: str,
    db: Session = Depends(get_db)
):
    """
    测试模型配置是否可用

    - **model_key**: 模型标识
    """
    config = db.query(LLMConfig).filter(
        LLMConfig.model_key == model_key
    ).first()

    if not config:
        raise HTTPException(status_code=404, detail=f"模型配置不存在: {model_key}")

    try:
        # 创建适配器测试连接
        from backend.core.model_adapters.factory import ModelAdapterFactory
        from backend.core.conversation_format import UnifiedConversation

        adapter = ModelAdapterFactory.create_adapter(
            provider=config.model_type,
            api_key=config.api_key,
            model=config.default_model,
            base_url=config.api_base_url,
            temperature=float(config.temperature or 0.7),
            max_tokens=int(config.max_tokens or 8192)
        )

        # 发送测试消息
        test_conv = UnifiedConversation(system_prompt="You are a helpful assistant.")
        test_conv.add_user_message("Hi")

        response = await adapter.chat(test_conv)

        return {
            "success": True,
            "message": "模型配置测试成功",
            "test_response": response.content[:100] + "..." if len(response.content) > 100 else response.content
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"模型配置测试失败: {str(e)}"
        }
