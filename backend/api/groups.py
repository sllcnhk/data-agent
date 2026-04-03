"""
对话分组管理API

提供分组的CRUD操作和对话分组管理
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict

from backend.config.database import get_db
from backend.models import ConversationGroup, Conversation
from backend.api.deps import get_current_user

router = APIRouter(prefix="/groups", tags=["分组管理"])


# ========== 用户隔离辅助函数 ==========

def _get_user_id(current_user):
    from backend.api.deps import AnonymousUser
    if isinstance(current_user, AnonymousUser):
        return None
    return current_user.id


def _is_superadmin(current_user) -> bool:
    return getattr(current_user, "is_superadmin", False)


def _check_group_ownership(group: ConversationGroup, current_user):
    if _is_superadmin(current_user):
        return
    user_id = _get_user_id(current_user)
    if user_id is None:
        return
    if group.user_id is None:
        return
    if group.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该分组")


# ========== Pydantic 模型 ==========

class CreateGroupRequest(BaseModel):
    """创建分组请求"""
    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(..., min_length=1, max_length=100, description="分组名称")
    description: Optional[str] = Field(None, description="分组描述")
    icon: Optional[str] = Field(None, max_length=50, description="图标")
    color: Optional[str] = Field(None, max_length=20, description="颜色")


class UpdateGroupRequest(BaseModel):
    """更新分组请求"""
    model_config = ConfigDict(protected_namespaces=())

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
    sort_order: Optional[int] = None
    is_expanded: Optional[bool] = None


class ReorderGroupsRequest(BaseModel):
    """批量调整分组顺序请求"""
    model_config = ConfigDict(protected_namespaces=())

    group_ids: List[str] = Field(..., min_length=1, description="分组ID列表（按新顺序）")


# ========== API 端点 ==========

@router.post("", response_model=dict, summary="创建分组")
async def create_group(
    request: CreateGroupRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    创建新的对话分组

    - **name**: 分组名称（必填）
    - **description**: 分组描述
    - **icon**: 图标（emoji或图标名）
    - **color**: 颜色标识
    """
    try:
        user_id = _get_user_id(current_user)

        # 检查名称是否已存在（同一用户范围内）
        q = db.query(ConversationGroup).filter(ConversationGroup.name == request.name)
        if user_id is not None:
            q = q.filter(ConversationGroup.user_id == user_id)
        existing = q.first()

        if existing:
            raise HTTPException(status_code=400, detail=f"分组名称已存在: {request.name}")

        # 获取当前用户的最大排序值
        q_sort = db.query(func.max(ConversationGroup.sort_order))
        if user_id is not None:
            q_sort = q_sort.filter(ConversationGroup.user_id == user_id)
        max_sort = q_sort.scalar() or 0

        # 创建分组
        group = ConversationGroup(
            name=request.name,
            description=request.description,
            icon=request.icon,
            color=request.color,
            sort_order=max_sort + 1,
            user_id=user_id,
        )

        db.add(group)
        db.commit()
        db.refresh(group)

        return {
            "success": True,
            "data": group.to_dict(),
            "message": f"分组创建成功: {group.name}"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建分组失败: {str(e)}")


@router.get("", response_model=dict, summary="获取分组列表")
async def list_groups(
    include_conversations: bool = Query(False, description="是否包含对话列表"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取当前用户的分组列表

    - 按 sort_order 排序
    - 可选择是否包含每个分组内的对话列表
    - 自动更新 conversation_count
    """
    try:
        user_id = _get_user_id(current_user)
        q = db.query(ConversationGroup).order_by(ConversationGroup.sort_order)
        if user_id is not None:
            q = q.filter(ConversationGroup.user_id == user_id)
        groups = q.all()

        # 更新对话计数
        for group in groups:
            count_q = db.query(func.count(Conversation.id)).filter(
                Conversation.group_id == group.id,
                Conversation.status == "active"
            )
            # 按 user_id 过滤：只统计当前用户在该分组下的对话数
            if user_id is not None:
                count_q = count_q.filter(Conversation.user_id == user_id)
            count = count_q.scalar()

            if group.conversation_count != count:
                group.conversation_count = count
                db.commit()

        return {
            "groups": [g.to_dict(include_conversations=include_conversations) for g in groups],
            "total": len(groups)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分组列表失败: {str(e)}")


@router.get("/{group_id}", response_model=dict, summary="获取分组详情")
async def get_group(
    group_id: UUID,
    include_conversations: bool = Query(False, description="是否包含对话列表"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取指定分组的详细信息

    - 返回分组基本信息
    - 可选择包含该分组内的所有对话
    """
    try:
        group = db.query(ConversationGroup).filter(
            ConversationGroup.id == group_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail=f"分组不存在: {group_id}")

        _check_group_ownership(group, current_user)

        # 更新对话计数
        count = db.query(func.count(Conversation.id)).filter(
            Conversation.group_id == group.id,
            Conversation.status == "active"
        ).scalar()

        group.conversation_count = count
        db.commit()

        return {
            "success": True,
            "data": group.to_dict(include_conversations=include_conversations)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分组失败: {str(e)}")


@router.put("/{group_id}", response_model=dict, summary="更新分组")
async def update_group(
    group_id: UUID,
    request: UpdateGroupRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    更新分组信息

    - 可以更新名称、描述、图标、颜色、排序顺序、展开状态
    - 只更新提供的字段
    """
    try:
        group = db.query(ConversationGroup).filter(
            ConversationGroup.id == group_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail=f"分组不存在: {group_id}")

        _check_group_ownership(group, current_user)

        # 更新字段
        update_data = request.model_dump(exclude_unset=True)

        # 如果要更新名称，检查是否重复（同用户范围内）
        if "name" in update_data and update_data["name"] != group.name:
            user_id = _get_user_id(current_user)
            q = db.query(ConversationGroup).filter(
                ConversationGroup.name == update_data["name"],
                ConversationGroup.id != group_id
            )
            if user_id is not None:
                q = q.filter(ConversationGroup.user_id == user_id)
            existing = q.first()

            if existing:
                raise HTTPException(status_code=400, detail=f"分组名称已存在: {update_data['name']}")

        for key, value in update_data.items():
            setattr(group, key, value)

        db.commit()
        db.refresh(group)

        return {
            "success": True,
            "data": group.to_dict(),
            "message": f"分组更新成功: {group.name}"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新分组失败: {str(e)}")


@router.delete("/{group_id}", response_model=dict, summary="删除分组")
async def delete_group(
    group_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    删除分组

    - 删除分组后，该分组内的对话会变为未分组状态
    - 使用了 ON DELETE SET NULL 外键约束
    """
    try:
        group = db.query(ConversationGroup).filter(
            ConversationGroup.id == group_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail=f"分组不存在: {group_id}")

        _check_group_ownership(group, current_user)

        group_name = group.name
        conversation_count = group.conversation_count

        # 删除分组（外键会自动将对话的 group_id 设为 NULL）
        db.delete(group)
        db.commit()

        return {
            "success": True,
            "message": f"分组已删除: {group_name}",
            "conversations_affected": conversation_count
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除分组失败: {str(e)}")


@router.post("/reorder", response_model=dict, summary="批量调整分组顺序")
async def reorder_groups(
    request: ReorderGroupsRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    批量调整分组的排序顺序

    - 提供分组ID列表，按新的顺序排列
    - 自动更新每个分组的 sort_order
    """
    try:
        # 验证所有分组是否存在
        group_ids = [UUID(gid) for gid in request.group_ids]
        groups = db.query(ConversationGroup).filter(
            ConversationGroup.id.in_(group_ids)
        ).all()

        if len(groups) != len(group_ids):
            raise HTTPException(status_code=400, detail="部分分组ID不存在")

        # 按新顺序更新 sort_order
        group_dict = {str(g.id): g for g in groups}

        for index, group_id_str in enumerate(request.group_ids):
            group = group_dict[group_id_str]
            group.sort_order = index

        db.commit()

        return {
            "success": True,
            "message": f"已更新 {len(groups)} 个分组的顺序"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"调整顺序失败: {str(e)}")


@router.get("/{group_id}/conversations", response_model=dict, summary="获取分组内的对话列表")
async def get_group_conversations(
    group_id: UUID,
    limit: int = Query(50, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取指定分组内的对话列表

    - 只返回 active 状态的对话
    - 按最后消息时间倒序排列
    - 支持分页
    """
    try:
        # 验证分组是否存在
        group = db.query(ConversationGroup).filter(
            ConversationGroup.id == group_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail=f"分组不存在: {group_id}")

        _check_group_ownership(group, current_user)

        # 查询对话
        query = db.query(Conversation).filter(
            Conversation.group_id == group_id,
            Conversation.status == "active"
        ).order_by(
            Conversation.is_pinned.desc(),
            Conversation.last_message_at.desc()
        )

        total = query.count()
        conversations = query.offset(offset).limit(limit).all()

        return {
            "group": group.to_dict(),
            "conversations": [c.to_dict() for c in conversations],
            "total": total,
            "limit": limit,
            "offset": offset
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取对话列表失败: {str(e)}")
