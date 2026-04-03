"""
对话管理API

提供对话的CRUD操作和流式聊天接口
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
import asyncio
import json
import logging
import traceback

from backend.config.database import get_db
from backend.models.conversation import Conversation, Message
from backend.services.conversation_service import ConversationService
from backend.api.deps import get_current_user
from pydantic import BaseModel, Field

router = APIRouter(prefix="/conversations", tags=["对话管理"])
logger = logging.getLogger(__name__)


def _deep_safe_encode(obj):
    """递归将 dict/list 中无法 JSON 序列化的值转为字符串，作为兜底。"""
    if isinstance(obj, dict):
        return {k: _deep_safe_encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_deep_safe_encode(v) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


# ========== 用户隔离辅助函数 ==========

def _get_user_id(current_user):
    """返回用户 UUID，匿名用户（ENABLE_AUTH=false）返回 None。"""
    from backend.api.deps import AnonymousUser
    if isinstance(current_user, AnonymousUser):
        return None
    return current_user.id


def _is_superadmin(current_user) -> bool:
    return getattr(current_user, "is_superadmin", False)


def _check_conversation_ownership(conversation: Conversation, current_user):
    """非 owner 且非 superadmin 时抛 403。"""
    if _is_superadmin(current_user):
        return
    user_id = _get_user_id(current_user)
    if user_id is None:
        return  # 匿名模式，跳过检查
    if conversation.user_id is None:
        return  # 迁移前数据，允许访问
    if conversation.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该对话")


def _check_conversation_write_permission(conversation: Conversation, current_user):
    """写操作权限检查：superadmin 不能向非自己且未共享的对话写入消息。

    逻辑：
    - 非 superadmin → 走普通 ownership check
    - superadmin + is_shared=True → 允许写入（群组聊天预留）
    - superadmin + is_shared=False + 非 owner → 拒绝（只读模式）
    - superadmin + owner → 允许写入自己的对话
    """
    user_id = _get_user_id(current_user)

    if not _is_superadmin(current_user):
        # 普通用户走 ownership check
        _check_conversation_ownership(conversation, current_user)
        return

    # superadmin 逻辑
    if user_id is not None and conversation.user_id == user_id:
        return  # 自己的对话，允许写入

    if getattr(conversation, "is_shared", False):
        return  # 已共享对话，允许写入（群组聊天扩展点）

    # 其他用户的非共享对话，superadmin 只读
    if conversation.user_id is not None and conversation.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="当前对话属于其他用户且未开放共享，superadmin 仅可查看"
        )


# ========== Pydantic模型 ==========

class CreateConversationRequest(BaseModel):
    """创建对话请求"""
    title: Optional[str] = Field(None, description="对话标题,为空则自动生成")
    model_key: str = Field(default="claude", description="使用的模型")
    system_prompt: Optional[str] = Field(None, description="系统提示词")


class UpdateConversationRequest(BaseModel):
    """更新对话请求"""
    title: Optional[str] = None
    is_pinned: Optional[bool] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None
    model_key: Optional[str] = None  # 切换对话使用的模型


class AttachmentData(BaseModel):
    """附件数据"""
    name: str = Field(..., description="文件名")
    mime_type: str = Field(..., description="MIME类型")
    size: int = Field(..., description="文件大小(bytes)")
    data: str = Field(..., description="Base64编码的文件内容")


class SendMessageRequest(BaseModel):
    """发送消息请求"""
    content: str = Field(..., description="消息内容")
    model_key: Optional[str] = Field(None, description="使用的模型,为空则使用对话默认模型")
    stream: bool = Field(default=True, description="是否流式响应")
    attachments: List[AttachmentData] = Field(default=[], description="附件列表")


class ConversationListResponse(BaseModel):
    """对话列表响应"""
    conversations: List[dict]
    total: int


class ConversationDetailResponse(BaseModel):
    """对话详情响应"""
    conversation: dict
    messages: List[dict]


# ========== API端点 ==========

@router.post("", response_model=dict, summary="创建新对话")
async def create_conversation(
    request: CreateConversationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    创建新对话

    - **title**: 对话标题(可选)
    - **model_key**: 使用的模型(claude/gemini/qianwen/doubao)
    - **system_prompt**: 系统提示词(可选)
    """
    try:
        logger.info(f"Creating conversation: title='{request.title}', model_key='{request.model_key}'")
        service = ConversationService(db)

        title = request.title or "新对话"
        conversation = service.create_conversation(
            title=title,
            model_key=request.model_key,
            system_prompt=request.system_prompt,
            user_id=_get_user_id(current_user),
        )

        logger.info(f"Conversation created successfully: id={conversation.id}")
        return {
            "success": True,
            "data": conversation.to_dict()
        }
    except Exception as e:
        logger.error(f"Failed to create conversation: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"创建对话失败: {str(e)}"
        )


@router.get("/all-users-view", response_model=dict, summary="超管：所有用户的对话（按用户分组）")
async def list_all_users_conversations(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    超管专用：返回除自身外所有用户的活跃对话，供侧边栏"其他用户"区块渲染。

    - 仅 superadmin 可调用，其他用户返回 403
    """
    if not _is_superadmin(current_user):
        raise HTTPException(status_code=403, detail="仅 superadmin 可访问")
    service = ConversationService(db)
    exclude_id = _get_user_id(current_user)
    users_data = service.list_all_conversations_by_user(exclude_user_id=exclude_id)
    return {"users": users_data}


@router.get("", response_model=ConversationListResponse, summary="获取对话列表")
async def list_conversations(
    status: Optional[str] = Query(default="active", description="状态筛选"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取对话列表（仅返回当前用户的对话）

    - **status**: 状态筛选(active/archived/deleted)
    - **limit**: 返回数量
    - **offset**: 偏移量
    """
    service = ConversationService(db)

    conversations, total = service.list_conversations(
        status=status,
        limit=limit,
        offset=offset,
        user_id=_get_user_id(current_user),
    )

    return {
        "conversations": [conv.to_dict() for conv in conversations],
        "total": total
    }


@router.get("/{conversation_id}", response_model=ConversationDetailResponse, summary="获取对话详情")
async def get_conversation(
    conversation_id: UUID,
    include_messages: bool = Query(default=True, description="是否包含消息"),
    message_limit: int = Query(default=100, description="消息数量限制"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取对话详情

    - **conversation_id**: 对话ID
    - **include_messages**: 是否包含消息列表
    - **message_limit**: 消息数量限制
    """
    try:
        logger.info(f"Getting conversation: id={conversation_id}")
        service = ConversationService(db)

        conversation = service.get_conversation(str(conversation_id))
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")

        _check_conversation_ownership(conversation, current_user)

        messages = []
        if include_messages:
            messages = service.get_messages(str(conversation_id), limit=message_limit)

        logger.info(f"Conversation loaded: {len(messages)} messages")
        return {
            "conversation": conversation.to_dict(),
            "messages": [msg.to_dict() for msg in messages]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get conversation {conversation_id}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"获取对话失败: {str(e)}"
        )


@router.put("/{conversation_id}", summary="更新对话")
async def update_conversation(
    conversation_id: UUID,
    request: UpdateConversationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    更新对话信息

    - **title**: 新标题
    - **is_pinned**: 是否置顶
    - **status**: 状态
    - **tags**: 标签列表
    """
    try:
        logger.info(f"Updating conversation {conversation_id}")
        service = ConversationService(db)

        conversation_id_str = str(conversation_id)
        conversation = service.get_conversation(conversation_id_str)
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")

        _check_conversation_ownership(conversation, current_user)

        updates = {}
        if request.title is not None:
            updates["title"] = request.title
        if request.is_pinned is not None:
            updates["is_pinned"] = request.is_pinned
        if request.status is not None:
            updates["status"] = request.status
        if request.tags is not None:
            updates["tags"] = request.tags
        if request.model_key is not None:
            updates["current_model"] = request.model_key

        conversation = service.update_conversation(conversation_id_str, **updates)

        logger.info(f"Conversation updated successfully")
        return {
            "success": True,
            "data": conversation.to_dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update conversation: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"更新对话失败: {str(e)}"
        )


@router.delete("/{conversation_id}", summary="删除对话")
async def delete_conversation(
    conversation_id: UUID,
    hard_delete: bool = Query(default=False, description="是否硬删除"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    删除对话

    - **conversation_id**: 对话ID
    - **hard_delete**: 是否硬删除(否则标记为deleted状态)
    """
    try:
        logger.info(f"Deleting conversation {conversation_id}: hard_delete={hard_delete}")
        service = ConversationService(db)

        conversation_id_str = str(conversation_id)
        conversation = service.get_conversation(conversation_id_str)
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")

        _check_conversation_ownership(conversation, current_user)

        if hard_delete:
            service.hard_delete_conversation(conversation_id_str)
        else:
            service.soft_delete_conversation(conversation_id_str)

        logger.info(f"Conversation deleted successfully")
        return {
            "success": True,
            "message": "对话已删除"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete conversation: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"删除对话失败: {str(e)}"
        )


@router.post("/{conversation_id}/messages", summary="发送消息")
async def send_message(
    conversation_id: UUID,
    request: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    发送消息到对话

    - **content**: 消息内容
    - **model_key**: 使用的模型(可选)
    - **stream**: 是否流式响应

    流式响应返回SSE格式,非流式返回JSON
    """
    try:
        logger.info(f"Sending message to conversation {conversation_id}: stream={request.stream}")
        service = ConversationService(db)

        conversation_id_str = str(conversation_id)
        conversation = service.get_conversation(conversation_id_str)
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")

        _check_conversation_write_permission(conversation, current_user)

        # 确定使用的模型；如果前端显式传入了 model_key 且与当前对话记录不同，则持久化
        model_key = request.model_key or conversation.current_model
        if request.model_key and request.model_key != conversation.current_model:
            conversation.current_model = model_key
            db.commit()
            db.refresh(conversation)
        logger.info(f"Using model: {model_key}")

        # Extract username for skill directory isolation and system prompt injection
        _username = getattr(current_user, "username", None) or "anonymous"

        if request.stream:
            # 流式响应
            # 根本原因：FastAPI 的 GZipMiddleware 会将 StreamingResponse 的所有
            # 小数据块压缩后缓存在 zlib 内部缓冲区，直到流结束时才一次性 flush。
            # 这导致 approval_required 等 SSE 事件需要等待 3 分钟 Agent 审批超时
            # 后才一次性抵达浏览器——弹窗因此严重延迟。
            #
            # 解决方案：在 StreamingResponse headers 中设置 Content-Encoding: identity，
            # Starlette 的 GZipMiddleware 检测到已有 Content-Encoding 时会跳过压缩，
            # SSE 数据块因此能逐条实时到达浏览器。
            #
            # 心跳机制（辅助）：producer 将 Agent 事件投入队列，generate() 每 10 秒若无
            # 新事件则发送一条 SSE 注释行（": heartbeat\n\n"），进一步确保在极端网络
            # 环境下保持连接活跃，注释行不影响前端 SSE 解析逻辑。

            async def generate():
                # _SENTINEL 表示 producer 已完成
                _SENTINEL = object()
                event_queue: asyncio.Queue = asyncio.Queue()

                async def _producer():
                    try:
                        async for chunk in service.send_message_stream(
                            conversation_id=conversation_id_str,
                            content=request.content,
                            model_key=model_key,
                            username=_username,
                            attachments=[a.model_dump() for a in request.attachments],
                        ):
                            await event_queue.put(("data", chunk))
                        await event_queue.put(("done", None))
                    except Exception as exc:
                        logger.error(f"Stream producer error: {exc}\n{traceback.format_exc()}")
                        await event_queue.put(("error", str(exc)))

                producer_task = asyncio.create_task(_producer())

                try:
                    while True:
                        try:
                            msg_type, payload = await asyncio.wait_for(
                                event_queue.get(), timeout=10.0
                            )
                        except asyncio.TimeoutError:
                            # 10 秒内无新事件 → 发送心跳注释，强制刷新 TCP 写缓冲区
                            # 这确保已写入缓冲区的 approval_required 等事件被立即推送到
                            # Vite 代理 / 浏览器，而不需要等到流结束才一次性传输。
                            yield ": heartbeat\n\n"
                            continue

                        if msg_type == "done":
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            break
                        elif msg_type == "error":
                            error_data = {"type": "error", "error": f"对话流异常: {payload}"}
                            yield f"data: {json.dumps(error_data)}\n\n"
                            break
                        else:  # "data"
                            chunk = payload
                            try:
                                data_str = json.dumps(chunk, ensure_ascii=False)
                            except (TypeError, ValueError) as serialize_err:
                                logger.warning(
                                    f"Chunk serialization warning (type={chunk.get('type')}): "
                                    f"{serialize_err} — falling back to safe encode"
                                )
                                data_str = json.dumps(
                                    _deep_safe_encode(chunk), ensure_ascii=False
                                )
                            yield f"data: {data_str}\n\n"
                finally:
                    if not producer_task.done():
                        producer_task.cancel()

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",       # 禁止 nginx/代理层缓冲 SSE 响应
                    "Content-Encoding": "identity",  # 绕过 GZipMiddleware：已有 Content-Encoding 时跳过 gzip 压缩
                }
            )
        else:
            # 非流式响应
            user_message, assistant_message = await service.send_message(
                conversation_id=conversation_id_str,
                content=request.content,
                model_key=model_key,
                username=_username,
            )

            logger.info(f"Message sent successfully")
            return {
                "success": True,
                "data": {
                    "user_message": user_message.to_dict(),
                    "assistant_message": assistant_message.to_dict()
                }
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"发送消息失败: {str(e)}"
        )


@router.get("/{conversation_id}/messages", summary="获取对话消息")
async def get_messages(
    conversation_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取对话的消息列表

    - **conversation_id**: 对话ID
    - **limit**: 返回数量
    - **offset**: 偏移量
    """
    service = ConversationService(db)

    conversation_id_str = str(conversation_id)
    conversation = service.get_conversation(conversation_id_str)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")

    _check_conversation_ownership(conversation, current_user)

    messages = service.get_messages(
        conversation_id=conversation_id_str,
        limit=limit,
        offset=offset
    )

    return {
        "success": True,
        "data": [msg.to_dict() for msg in messages],
        "total": len(messages)
    }


@router.post("/{conversation_id}/regenerate", summary="重新生成最后一条消息")
async def regenerate_last_message(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    重新生成最后一条助手消息
    """
    service = ConversationService(db)

    conversation_id_str = str(conversation_id)
    conversation = service.get_conversation(conversation_id_str)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")

    _check_conversation_write_permission(conversation, current_user)

    # 获取最后一条用户消息
    messages = service.get_messages(conversation_id_str, limit=50)
    user_messages = [m for m in messages if m.role == "user"]

    if not user_messages:
        raise HTTPException(status_code=400, detail="没有可重新生成的消息")

    last_user_message = user_messages[-1]

    # 删除最后的助手消息(如果有)
    assistant_messages = [m for m in messages if m.role == "assistant"]
    if assistant_messages:
        last_assistant = assistant_messages[-1]
        service.delete_message(last_assistant.id)

    # 重新生成
    user_message, assistant_message = await service.send_message(
        conversation_id=conversation_id_str,
        content=last_user_message.content,
        model_key=conversation.current_model,
        regenerate=True
    )

    return {
        "success": True,
        "data": {
            "assistant_message": assistant_message.to_dict()
        }
    }


@router.post("/{conversation_id}/clear", summary="清空对话消息")
async def clear_conversation(
    conversation_id: UUID,
    keep_system: bool = Query(default=True, description="是否保留系统消息"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    清空对话的所有消息
    """
    service = ConversationService(db)

    conversation_id_str = str(conversation_id)
    conversation = service.get_conversation(conversation_id_str)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")

    _check_conversation_write_permission(conversation, current_user)

    service.clear_messages(conversation_id_str, keep_system=keep_system)

    return {
        "success": True,
        "message": "对话已清空"
    }


# ========== 分组管理相关 ==========

class MoveToGroupRequest(BaseModel):
    """移动对话到分组请求"""
    group_id: Optional[UUID] = Field(None, description="分组ID，null表示移出分组")


class RenameTitleRequest(BaseModel):
    """重命名对话请求"""
    title: str = Field(..., min_length=1, max_length=500, description="新标题")


@router.put("/{conversation_id}/group", response_model=dict, summary="移动对话到分组")
async def move_conversation_to_group(
    conversation_id: UUID,
    request: MoveToGroupRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    将对话移动到指定分组，或移出分组

    - **group_id**: 目标分组ID，为null时表示移出分组（变为未分组状态）
    """
    try:
        from backend.models import ConversationGroup

        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()

        if not conversation:
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")

        _check_conversation_ownership(conversation, current_user)

        # 如果提供了 group_id，验证分组是否存在
        if request.group_id is not None:
            group = db.query(ConversationGroup).filter(
                ConversationGroup.id == request.group_id
            ).first()

            if not group:
                raise HTTPException(status_code=404, detail=f"分组不存在: {request.group_id}")

        # 更新分组
        old_group_id = conversation.group_id
        conversation.group_id = request.group_id
        db.commit()

        # 更新原分组和新分组的对话计数
        from sqlalchemy import func
        if old_group_id:
            old_group = db.query(ConversationGroup).filter(
                ConversationGroup.id == old_group_id
            ).first()
            if old_group:
                old_group.conversation_count = db.query(func.count(Conversation.id)).filter(
                    Conversation.group_id == old_group_id,
                    Conversation.status == "active"
                ).scalar()

        if request.group_id:
            new_group = db.query(ConversationGroup).filter(
                ConversationGroup.id == request.group_id
            ).first()
            if new_group:
                new_group.conversation_count = db.query(func.count(Conversation.id)).filter(
                    Conversation.group_id == request.group_id,
                    Conversation.status == "active"
                ).scalar()

        db.commit()
        db.refresh(conversation)

        return {
            "success": True,
            "data": conversation.to_dict(),
            "message": "对话已移动" if request.group_id else "对话已移出分组"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"移动对话失败: {str(e)}")


@router.put("/{conversation_id}/title", response_model=dict, summary="重命名对话")
async def rename_conversation(
    conversation_id: UUID,
    request: RenameTitleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    重命名对话标题

    - **title**: 新的对话标题
    """
    try:
        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()

        if not conversation:
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")

        _check_conversation_ownership(conversation, current_user)

        old_title = conversation.title
        conversation.title = request.title
        db.commit()
        db.refresh(conversation)

        return {
            "success": True,
            "data": conversation.to_dict(),
            "message": f"对话已重命名: {old_title} → {request.title}"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"重命名对话失败: {str(e)}")


@router.post("/{conversation_id}/cancel", summary="取消正在进行的对话生成")
async def cancel_conversation_stream(conversation_id: UUID):
    """
    通知后端停止当前对话的生成流。

    操作幂等：即使没有活跃流也不会报错。
    后端通过 asyncio.Event 通知 AgenticLoop 在下一个取消点退出。
    """
    from backend.core.cancel_manager import cancel_manager
    cancel_manager.request_cancel(str(conversation_id))
    return {"status": "cancellation_requested", "conversation_id": str(conversation_id)}
