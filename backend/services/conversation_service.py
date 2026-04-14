"""
对话服务

提供对话的CRUD操作和业务逻辑
"""
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from sqlalchemy.exc import SQLAlchemyError
from uuid import UUID
import logging

from backend.models.conversation import Conversation, Message, ContextSnapshot
from backend.models.llm_config import LLMConfig
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    ConversationSummary,
    MessageRole
)
from backend.core.token_counter import get_token_counter
from backend.core.context_manager import HybridContextManager
from backend.core.conversation_summarizer import ConversationSummarizer
from backend.mcp.manager import get_mcp_manager
from backend.agents.orchestrator import MasterAgent

logger = logging.getLogger(__name__)


class ConversationService:
    """对话服务"""

    def __init__(self, db: Session):
        """
        初始化对话服务

        Args:
            db: 数据库会话
        """
        self.db = db

    def create_conversation(
        self,
        title: str,
        system_prompt: Optional[str] = None,
        model: str = "claude",
        model_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user_id=None,
    ) -> Conversation:
        """
        创建新对话

        Args:
            title: 对话标题
            system_prompt: 系统提示词
            model: 使用的模型（已弃用，使用model_key）
            model_key: 使用的模型key（优先）
            metadata: 元数据

        Returns:
            创建的对话对象

        Raises:
            SQLAlchemyError: 数据库错误
        """
        # 优先使用 model_key，否则使用 model
        current_model = model_key or model

        # 准备元数据，将 system_prompt 存储在 extra_metadata 中
        extra_metadata = metadata or {}
        if system_prompt:
            extra_metadata['system_prompt'] = system_prompt

        conversation = Conversation(
            title=title,
            current_model=current_model,
            extra_metadata=extra_metadata,
            user_id=user_id,
        )

        try:
            self.db.add(conversation)
            self.db.commit()
            self.db.refresh(conversation)
            return conversation
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """
        获取对话

        Args:
            conversation_id: 对话ID

        Returns:
            对话对象或None
        """
        try:
            uuid_obj = UUID(conversation_id)
            return self.db.query(Conversation).filter(
                Conversation.id == uuid_obj
            ).first()
        except (ValueError, SQLAlchemyError):
            return None

    def find_pilot_conversation(
        self,
        context_type: str,
        context_id: str,
        user_id=None,
    ) -> Optional[Conversation]:
        """
        查找指定上下文（报表/文档）的已有 Pilot 对话。

        认证模式下同时按 user_id 过滤，确保用户隔离；
        匿名模式（user_id=None）仅按 context_id 查找。

        Args:
            context_type: 上下文类型，如 "report"
            context_id:   上下文 ID（报表 UUID 字符串）
            user_id:      当前用户 ID（认证关闭时为 None）

        Returns:
            最新一条匹配对话，或 None
        """
        try:
            query = self.db.query(Conversation).filter(
                Conversation.extra_metadata["context_type"].astext == context_type,
                Conversation.extra_metadata["context_id"].astext == context_id,
            )
            if user_id is not None:
                query = query.filter(Conversation.user_id == user_id)
            return query.order_by(desc(Conversation.created_at)).first()
        except Exception:
            return None

    def list_conversations(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        order_by: str = "created_at",
        user_id=None,
    ) -> Tuple[List[Conversation], int]:
        """
        获取对话列表

        Args:
            limit: 限制数量
            offset: 偏移量
            status: 过滤状态
            order_by: 排序字段
            user_id: 用户ID，非 None 时只返回该用户的对话

        Returns:
            (对话列表, 总数) 元组
        """
        query = self.db.query(Conversation)

        # 用户隔离：user_id 非 None 时过滤（None = ENABLE_AUTH=false 匿名模式，返回全部）
        if user_id is not None:
            query = query.filter(Conversation.user_id == user_id)

        # 状态过滤
        if status:
            query = query.filter(Conversation.status == status)

        # 获取总数
        total = query.count()

        # 排序
        if order_by == "created_at":
            query = query.order_by(desc(Conversation.created_at))
        elif order_by == "updated_at":
            query = query.order_by(desc(Conversation.updated_at))
        elif order_by == "title":
            query = query.order_by(asc(Conversation.title))

        conversations = query.offset(offset).limit(limit).all()
        return conversations, total

    def list_all_conversations_by_user(self, exclude_user_id=None) -> list:
        """
        超管专用：获取所有用户的活跃对话，按用户分组。

        Args:
            exclude_user_id: 排除指定用户（通常是超管自身，自己的对话已在主列表显示）

        Returns:
            [{username, display_name, user_id, conversations: [to_dict()]}]
        """
        from backend.models.user import User
        result = []
        q = self.db.query(User).filter(User.is_active == True)  # noqa: E712
        if exclude_user_id is not None:
            q = q.filter(User.id != exclude_user_id)
        users = q.order_by(User.username).all()
        for user in users:
            convs = (
                self.db.query(Conversation)
                .filter(Conversation.user_id == user.id, Conversation.status == "active")
                .order_by(desc(Conversation.last_message_at), desc(Conversation.updated_at))
                .all()
            )
            if convs:
                result.append({
                    "username": user.username,
                    "display_name": user.display_name or user.username,
                    "user_id": str(user.id),
                    "conversations": [c.to_dict() for c in convs],
                })
        return result

    def update_conversation(
        self,
        conversation_id: str,
        **kwargs
    ) -> Optional[Conversation]:
        """
        更新对话

        Args:
            conversation_id: 对话ID
            **kwargs: 要更新的字段

        Returns:
            更新后的对话对象
        """
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None

        try:
            for key, value in kwargs.items():
                if hasattr(conversation, key):
                    setattr(conversation, key, value)

            self.db.commit()
            self.db.refresh(conversation)
            return conversation
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        删除对话(级联删除消息和任务)

        Args:
            conversation_id: 对话ID

        Returns:
            是否删除成功
        """
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return False

        try:
            self.db.delete(conversation)
            self.db.commit()
            return True
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        **kwargs
    ) -> Optional[Message]:
        """
        添加消息

        Args:
            conversation_id: 对话ID
            role: 消息角色
            content: 消息内容
            **kwargs: 其他字段(artifacts, tool_calls等)

        Returns:
            创建的消息对象
        """
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None

        try:
            # 获取模型名称,用于 token 计数
            model = conversation.current_model or "claude"

            # 计算消息的 token 数量
            token_counter = get_token_counter()
            message_tokens = token_counter.count_tokens(content, model)

            # 添加格式开销 (role 标记等)
            format_overhead = 4
            total_message_tokens = message_tokens + format_overhead

            # 创建消息对象
            message = Message(
                conversation_id=conversation.id,
                role=role,
                content=content,
                model=model,
                total_tokens=total_message_tokens,
                **kwargs
            )

            # 如果 kwargs 中已经提供了 token 信息,使用提供的值
            if "prompt_tokens" in kwargs or "completion_tokens" in kwargs:
                message.prompt_tokens = kwargs.get("prompt_tokens", 0)
                message.completion_tokens = kwargs.get("completion_tokens", 0)
                message.total_tokens = kwargs.get("total_tokens", total_message_tokens)
            else:
                # 根据角色分配 token
                if role in ["user", "system"]:
                    message.prompt_tokens = total_message_tokens
                    message.completion_tokens = 0
                elif role == "assistant":
                    message.prompt_tokens = 0
                    message.completion_tokens = total_message_tokens
                else:
                    message.prompt_tokens = total_message_tokens
                    message.completion_tokens = 0

            self.db.add(message)

            # 更新对话统计
            conversation.message_count += 1
            conversation.total_tokens = (conversation.total_tokens or 0) + message.total_tokens

            logger.debug(
                f"Message added: role={role}, tokens={message.total_tokens}, "
                f"conversation_total={conversation.total_tokens}"
            )

            self.db.commit()
            self.db.refresh(message)
            return message
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_messages(
        self,
        conversation_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Message]:
        """
        获取消息列表

        Args:
            conversation_id: 对话ID
            limit: 限制数量
            offset: 偏移量

        Returns:
            消息列表
        """
        try:
            uuid_obj = UUID(conversation_id)
            return self.db.query(Message).filter(
                Message.conversation_id == uuid_obj
            ).order_by(Message.created_at).offset(offset).limit(limit).all()
        except (ValueError, SQLAlchemyError):
            return []

    def update_message(
        self,
        message_id: str,
        **kwargs
    ) -> Optional[Message]:
        """
        更新消息

        Args:
            message_id: 消息ID
            **kwargs: 要更新的字段

        Returns:
            更新后的消息对象
        """
        try:
            uuid_obj = UUID(message_id)
            message = self.db.query(Message).filter(
                Message.id == uuid_obj
            ).first()

            if not message:
                return None

            for key, value in kwargs.items():
                if hasattr(message, key):
                    setattr(message, key, value)

            self.db.commit()
            self.db.refresh(message)
            return message
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def delete_message(self, message_id: str) -> bool:
        """
        删除消息

        Args:
            message_id: 消息ID

        Returns:
            是否删除成功
        """
        try:
            uuid_obj = UUID(message_id)
            message = self.db.query(Message).filter(
                Message.id == uuid_obj
            ).first()

            if not message:
                return False

            self.db.delete(message)
            self.db.commit()
            return True
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def clear_messages(self, conversation_id: str, keep_system: bool = True) -> int:
        """
        清空对话的所有消息

        Args:
            conversation_id: 对话ID
            keep_system: 是否保留 role='system' 的消息

        Returns:
            删除的消息数量
        """
        try:
            uuid_obj = UUID(conversation_id)
            query = self.db.query(Message).filter(Message.conversation_id == uuid_obj)
            if keep_system:
                query = query.filter(Message.role != "system")
            deleted = query.delete(synchronize_session=False)
            # 重置消息计数
            conversation = self.db.query(Conversation).filter(
                Conversation.id == uuid_obj
            ).first()
            if conversation:
                remaining = self.db.query(Message).filter(
                    Message.conversation_id == uuid_obj
                ).count()
                conversation.message_count = remaining
                self.db.add(conversation)
            self.db.commit()
            return deleted
        except SQLAlchemyError:
            self.db.rollback()
            raise

    def create_snapshot(
        self,
        conversation_id: str,
        snapshot_type: str = "compressed",
        content: Optional[Dict[str, Any]] = None,
        summary: Optional[str] = None
    ) -> Optional[ContextSnapshot]:
        """
        创建上下文快照

        Args:
            conversation_id: 对话ID
            snapshot_type: 快照类型
            content: 快照内容
            summary: 摘要

        Returns:
            创建的快照对象
        """
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None

        try:
            snapshot = ContextSnapshot(
                conversation_id=conversation.id,
                snapshot_type=snapshot_type,
                content=content or {},
                summary=summary
            )

            self.db.add(snapshot)
            self.db.commit()
            self.db.refresh(snapshot)
            return snapshot
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_snapshots(
        self,
        conversation_id: str,
        snapshot_type: Optional[str] = None
    ) -> List[ContextSnapshot]:
        """
        获取快照列表

        Args:
            conversation_id: 对话ID
            snapshot_type: 快照类型过滤

        Returns:
            快照列表
        """
        try:
            uuid_obj = UUID(conversation_id)
            query = self.db.query(ContextSnapshot).filter(
                ContextSnapshot.conversation_id == uuid_obj
            )

            if snapshot_type:
                query = query.filter(ContextSnapshot.snapshot_type == snapshot_type)

            return query.order_by(desc(ContextSnapshot.created_at)).all()
        except (ValueError, SQLAlchemyError):
            return []

    def to_unified_conversation(
        self,
        conversation_id: str
    ) -> Optional[UnifiedConversation]:
        """
        转换为统一对话格式

        Args:
            conversation_id: 对话ID

        Returns:
            统一对话格式对象
        """
        # 获取对话
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None

        # 获取消息
        messages = self.get_messages(conversation_id, limit=10000)

        # 转换为统一格式
        unified = UnifiedConversation(
            conversation_id=str(conversation.id),
            title=conversation.title,
            model=conversation.current_model,
            system_prompt=conversation.system_prompt,
            total_tokens=conversation.total_tokens,
            message_count=conversation.message_count
        )

        # 添加消息
        for msg in messages:
            unified_msg = UnifiedMessage(
                role=msg.role,
                content=msg.content,
                artifacts=msg.artifacts,
                tool_calls=msg.tool_calls,
                tool_results=msg.tool_results,
                metadata=msg.metadata
            )
            unified.add_message(unified_msg)

        return unified

    def from_unified_conversation(
        self,
        unified: UnifiedConversation
    ) -> Optional[Conversation]:
        """
        从统一格式创建对话

        Args:
            unified: 统一对话格式

        Returns:
            数据库对话对象
        """
        try:
            # 准备 extra_metadata，包含 system_prompt
            extra_metadata = unified.metadata or {}
            if unified.system_prompt:
                extra_metadata['system_prompt'] = unified.system_prompt

            # 创建对话
            conversation = Conversation(
                id=unified.conversation_id,
                title=unified.title,
                current_model=unified.model,
                total_tokens=unified.total_tokens,
                message_count=unified.message_count,
                extra_metadata=extra_metadata
            )

            self.db.add(conversation)
            self.db.flush()  # 获取ID

            # 添加消息
            for msg in unified.messages:
                message = Message(
                    conversation_id=conversation.id,
                    role=msg.role.value,
                    content=msg.content,
                    artifacts=msg.artifacts,
                    tool_calls=msg.tool_calls,
                    tool_results=msg.tool_results,
                    metadata=msg.metadata
                )
                self.db.add(message)

            self.db.commit()
            self.db.refresh(conversation)
            return conversation
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_conversation_stats(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """
        获取对话统计信息

        Args:
            conversation_id: 对话ID

        Returns:
            统计信息字典
        """
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None

        messages = self.get_messages(conversation_id, limit=10000)

        return {
            "conversation_id": conversation_id,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "total_messages": len(messages),
            "user_messages": len([m for m in messages if m.role == "user"]),
            "assistant_messages": len([m for m in messages if m.role == "assistant"]),
            "system_messages": len([m for m in messages if m.role == "system"]),
            "total_tokens": conversation.total_tokens,
            "current_model": conversation.current_model,
            "status": conversation.status
        }

    def soft_delete_conversation(self, conversation_id: str) -> bool:
        """软删除对话(标记为deleted)"""
        return self.update_conversation(conversation_id, status="deleted") is not None

    def hard_delete_conversation(self, conversation_id: str) -> bool:
        """硬删除对话"""
        return self.delete_conversation(conversation_id)

    async def send_message(
        self,
        conversation_id: str,
        content: str,
        model_key: str,
        username: str = "anonymous",
    ) -> Tuple[Message, Message]:
        """
        发送消息(非流式)

        Args:
            conversation_id: 对话ID
            content: 消息内容
            model_key: 使用的模型

        Returns:
            (用户消息, 助手消息)元组
        """
        # 1. 保存用户消息
        user_message = self.add_message(
            conversation_id=conversation_id,
            role="user",
            content=content
        )

        # 2. 获取对话上下文
        context = self._build_context(conversation_id, username=username)

        # 3. 获取LLM配置
        llm_config = self._get_llm_config(model_key)

        # 4. 使用Master Agent处理
        mcp_manager = get_mcp_manager()
        agent = MasterAgent(mcp_manager, model_key, llm_config)

        result = await agent.process(content, context)

        # 5. 保存助手消息
        assistant_content = result.get("content", "抱歉,我遇到了一些问题。")
        assistant_message = self.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_content,
            metadata=result.get("metadata", {})
        )

        return user_message, assistant_message

    # 工具结果截断阈值：超过此长度的 tool_result 数据在存库时截断，避免 DB 膨胀
    _MAX_THINKING_TOOL_RESULT_CHARS = 2000

    async def send_message_stream(
        self,
        conversation_id: str,
        content: str,
        model_key: str,
        _continuation_round: int = 0,
        username: str = "anonymous",
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        发送消息(真实流式 SSE)

        每个 AgenticLoop 事件直接 yield 给前端:
          thinking    — LLM 推理中间过程
          tool_call   — 正在调用 MCP 工具
          tool_result — MCP 工具返回结果
          content     — 最终回答文本
          error       — 错误信息

        Args:
            conversation_id: 对话ID
            content: 消息内容
            model_key: 使用的模型
            _continuation_round: 内部参数，自动续接轮次（0=正常用户消息）

        Yields:
            流式响应数据块
        """
        from backend.core.cancel_manager import cancel_manager as _cancel_mgr

        try:
            # Clear any leftover cancel signal from a previous request
            _cancel_mgr.clear(conversation_id)

            # 1. 保存用户/续接消息
            # 自动续接轮次 > 0 时，消息角色改为 'continuation'，区别于真实用户输入
            MAX_AUTO_CONTINUES = 3
            user_role = "continuation" if _continuation_round > 0 else "user"
            user_extra_meta: Optional[Dict[str, Any]] = None
            if _continuation_round > 0:
                user_extra_meta = {
                    "continuation_round": _continuation_round,
                    "max_rounds": MAX_AUTO_CONTINUES,
                }

            save_kwargs: Dict[str, Any] = {}
            if user_extra_meta:
                save_kwargs["extra_metadata"] = user_extra_meta

            # Store attachment metadata (without base64 data) in user message
            if attachments:
                attachment_meta = [
                    {"name": a["name"], "mime_type": a["mime_type"], "size": a["size"]}
                    for a in attachments
                ]
                existing_meta = save_kwargs.get("extra_metadata") or {}
                existing_meta["attachments"] = attachment_meta
                save_kwargs["extra_metadata"] = existing_meta

            user_message = self.add_message(
                conversation_id=conversation_id,
                role=user_role,
                content=content,
                **save_kwargs
            )

            yield {
                "type": "user_message",
                "data": user_message.to_dict()
            }

            # 2. 获取LLM配置（_maybe_summarize 需要 llm_adapter，先拿 config）
            llm_config = self._get_llm_config(model_key)

            # 3. 获取对话上下文（可能先生成 LLM 摘要）
            mcp_manager = get_mcp_manager()
            agent = MasterAgent(mcp_manager, model_key, llm_config)

            llm_summary = await self._maybe_summarize(
                conversation_id, agent.llm_adapter
            )
            context = self._build_context(conversation_id, llm_summary=llm_summary, username=username)

            # Inject current attachments (with base64) for _perceive() to build multimodal blocks
            if attachments:
                context["current_attachments"] = attachments

            if llm_summary:
                yield {
                    "type": "context_compressed",
                    "data": {"message": "对话历史已自动压缩，继续对话。"}
                }

            # 4. 使用 AgenticLoop 流式处理

            final_content = ""
            result_metadata: Dict[str, Any] = {}
            near_limit_data: Optional[Dict[str, Any]] = None
            was_cancelled = False
            # 收集推理过程事件，用于持久化到助手消息的 extra_metadata
            thinking_events: List[Dict[str, Any]] = []
            # 收集文件写入信息，用于历史消息恢复下载链接
            files_written_info: Optional[List[Dict[str, Any]]] = None

            # Get the cancel event for this conversation
            cancel_event = _cancel_mgr.get_event(conversation_id)

            # Stream events from AgenticLoop; catch exceptions so we can
            # still save and emit the assistant_message afterwards.
            try:
                async for event in agent.process_stream(
                    content, context, cancel_event=cancel_event
                ):
                    # Record final text for saving
                    # 使用追加而非覆盖，兼容自动续写场景下多个 content 事件
                    if event.type == "content":
                        final_content += event.data or ""
                    elif event.type == "cancelled":
                        was_cancelled = True
                        # Capture whatever partial content was generated
                        partial = event.data or ""
                        if partial and not final_content:
                            final_content = partial
                    elif event.type == "near_limit":
                        near_limit_data = event.data or {}
                    elif event.type == "error" and not final_content:
                        final_content = event.data or "处理时发生错误"

                    # 收集文件写入信息（用于历史消息恢复下载链接）
                    if event.type == "files_written":
                        files_written_info = (event.data or {}).get("files", [])

                    # 收集 thinking / tool_call / tool_result 事件，存库前截断大结果
                    if event.type in ("thinking", "tool_call", "tool_result"):
                        evt_data = event.data
                        if event.type == "tool_result" and isinstance(evt_data, dict):
                            result_val = evt_data.get("result")
                            if isinstance(result_val, str) and len(result_val) > self._MAX_THINKING_TOOL_RESULT_CHARS:
                                evt_data = dict(evt_data)
                                evt_data["result"] = (
                                    result_val[:self._MAX_THINKING_TOOL_RESULT_CHARS]
                                    + "…（已截断）"
                                )
                        thinking_events.append({
                            "type": event.type,
                            "data": evt_data,
                            "metadata": event.metadata or {},
                        })

                    yield event.to_dict()

            except Exception as stream_err:
                logger.error(
                    f"Error during process_stream: {stream_err}", exc_info=True
                )
                if not final_content:
                    final_content = f"处理时发生错误: {stream_err}"
                yield {"type": "error", "error": str(stream_err)}

            # 5. Always save and emit assistant_message
            if not final_content:
                final_content = "抱歉，我遇到了一些问题。"

            # Append cancel marker when generation was interrupted
            if was_cancelled:
                final_content = final_content.rstrip() + "\n\n---\n*（生成已被用户中断）*"
                # Skip auto-continuation — user cancelled intentionally
                near_limit_data = None

            try:
                # 将推理过程事件存入 extra_metadata，历史加载时可恢复展示
                asst_extra_meta: Dict[str, Any] = {}
                if thinking_events:
                    asst_extra_meta["thinking_events"] = thinking_events
                if was_cancelled:
                    asst_extra_meta["cancelled"] = True
                if files_written_info:
                    asst_extra_meta["files_written"] = files_written_info

                assistant_message = self.add_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=final_content,
                    **({"extra_metadata": asst_extra_meta} if asst_extra_meta else {})
                )
                yield {
                    "type": "assistant_message",
                    "data": assistant_message.to_dict()
                }
            except Exception as save_err:
                logger.error(
                    f"Failed to save assistant message: {save_err}", exc_info=True
                )

            # 6. Auto-continuation: if near_limit event was emitted with pending tasks,
            #    automatically continue up to MAX_AUTO_CONTINUES times.
            if near_limit_data and near_limit_data.get("pending_tasks"):
                pending_tasks = near_limit_data["pending_tasks"]
                conclusions = near_limit_data.get("conclusions", "")
                state = self._get_auto_continue_state(conversation_id)
                count = state.get("count", 0)

                if count < MAX_AUTO_CONTINUES:
                    new_count = count + 1
                    self._set_auto_continue_state(conversation_id, {
                        "count": new_count,
                        "pending_tasks": pending_tasks,
                        "conclusions": conclusions,
                    })
                    yield {
                        "type": "auto_continuing",
                        "data": {
                            "message": f"自动续接对话（第 {new_count}/{MAX_AUTO_CONTINUES} 次）...",
                            "continue_count": new_count,
                            "max_continues": MAX_AUTO_CONTINUES,
                            "pending_tasks": pending_tasks,
                        },
                    }
                    continuation_msg = self._build_continuation_message(
                        conclusions, pending_tasks
                    )
                    async for ev in self.send_message_stream(
                        conversation_id, continuation_msg, model_key,
                        _continuation_round=new_count,
                        username=username,
                    ):
                        yield ev
                else:
                    # Exhausted auto-continues; ask human to confirm
                    self._set_auto_continue_state(conversation_id, {"count": 0})
                    yield {
                        "type": "continuation_approval_required",
                        "data": {
                            "message": (
                                f"已自动续接 {MAX_AUTO_CONTINUES} 次，"
                                "是否继续完成剩余任务？"
                            ),
                            "pending_tasks": pending_tasks,
                            "conclusions": conclusions,
                        },
                    }
            elif not near_limit_data:
                # Normal completion — reset auto-continue counter
                self._set_auto_continue_state(conversation_id, {"count": 0})

        except Exception as e:
            logger.error(f"Error in send_message_stream: {e}", exc_info=True)
            yield {
                "type": "error",
                "error": str(e)
            }

    def _build_context(
        self,
        conversation_id: str,
        llm_summary: str = "",
        username: str = "anonymous",
    ) -> Dict[str, Any]:
        """
        构建对话上下文 (Phase 1.3: 使用 HybridContextManager)

        使用统一的上下文管理器进行智能压缩和优化。

        Args:
            conversation_id: 对话ID
            llm_summary: 预先由 LLM 生成的历史摘要（可选）。
                         有值时替换规则摘要，注入到压缩位置，Claude Code 风格。
        """
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return {
                "conversation_id": conversation_id,
                "title": "",
                "system_prompt": "",
                "history": [],
                "metadata": {},
                "username": username,
            }

        # 获取所有消息 (不再硬编码limit=20)
        messages = self.get_messages(conversation_id, limit=10000)

        # 转换为 UnifiedConversation 格式
        unified_conv = UnifiedConversation(
            conversation_id=conversation_id,
            title=conversation.title,
            model=conversation.current_model,
            system_prompt=conversation.system_prompt,
            total_tokens=conversation.total_tokens or 0,
            message_count=len(messages)
        )

        # 添加消息到统一格式
        # 'continuation' 是前端展示角色，LLM 上下文中映射为 'user'
        for msg in messages:
            llm_role = "user" if msg.role == "continuation" else msg.role
            msg_content = msg.content
            # Annotate historical user messages that had attachments
            extra_meta = msg.extra_metadata or {}
            hist_attachments = extra_meta.get("attachments")
            if hist_attachments and llm_role == "user":
                annotations = ", ".join(
                    f"{a['name']} ({a['mime_type']}, {a['size']} bytes)"
                    for a in hist_attachments
                )
                msg_content = f"{msg_content}\n[附件: {annotations}]"
            unified_msg = UnifiedMessage(
                role=MessageRole(llm_role),
                content=msg_content,
                metadata=extra_meta
            )
            unified_conv.add_message(unified_msg)

        # 使用 HybridContextManager 压缩上下文（可传入 LLM 摘要）
        context_manager = HybridContextManager.create_from_settings()
        compressed_conv = context_manager.compress_conversation(
            unified_conv, llm_summary=llm_summary
        )

        logger.debug(
            f"Context compressed: {len(messages)} -> {len(compressed_conv.messages)} messages "
            f"(strategy: {context_manager.strategy_name})"
        )

        # 转换回 dict 格式 (兼容现有接口)
        return {
            "conversation_id": conversation_id,
            "title": compressed_conv.title,
            "system_prompt": compressed_conv.system_prompt,
            "history": [
                {
                    "role": msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
                    "content": msg.content
                }
                for msg in compressed_conv.messages
            ],
            "metadata": conversation.extra_metadata or {},
            "username": username,
            # 添加上下文管理信息
            "context_info": {
                "strategy": context_manager.strategy_name,
                "max_context_length": context_manager.max_context_length,
                "original_message_count": len(messages),
                "compressed_message_count": len(compressed_conv.messages),
                "total_tokens": compressed_conv.total_tokens
            }
        }

    def _update_conversation_meta(
        self,
        conversation_id: str,
        updates: Dict[str, Any],
    ) -> None:
        """Merge updates into conversation.extra_metadata."""
        try:
            uuid_obj = UUID(conversation_id)
            conv = self.db.query(Conversation).filter(
                Conversation.id == uuid_obj
            ).first()
            if conv:
                meta = dict(conv.extra_metadata or {})
                meta.update(updates)
                conv.extra_metadata = meta
                self.db.commit()
        except Exception as e:
            logger.warning(f"Failed to update conversation meta: {e}")

    def _get_auto_continue_state(self, conversation_id: str) -> Dict[str, Any]:
        """Get auto-continuation state from conversation metadata."""
        try:
            uuid_obj = UUID(conversation_id)
            conv = self.db.query(Conversation).filter(
                Conversation.id == uuid_obj
            ).first()
            if conv:
                meta = conv.extra_metadata or {}
                return meta.get("auto_continue_state", {})
        except Exception:
            pass
        return {}

    def _set_auto_continue_state(
        self, conversation_id: str, state: Dict[str, Any]
    ) -> None:
        """Persist auto-continuation state into conversation metadata."""
        self._update_conversation_meta(
            conversation_id, {"auto_continue_state": state}
        )

    @staticmethod
    def _build_continuation_message(
        conclusions: str, pending_tasks: List[str]
    ) -> str:
        """Build a continuation message from previous conclusions and pending tasks."""
        tasks_text = "\n".join(f"- {t}" for t in pending_tasks)
        return (
            "基于上一轮的分析结论，请继续完成以下未完成的任务：\n\n"
            f"上一轮结论摘要：\n{conclusions}\n\n"
            f"待完成任务：\n{tasks_text}"
        )

    async def _maybe_summarize(
        self,
        conversation_id: str,
        llm_adapter: Any,
    ) -> str:
        """
        Generate LLM summary if conversation history is long enough.

        Returns "" if no summarization is needed (short history).
        Caches result in conversation.extra_metadata['context_summary'].
        Only re-generates when message count changes.
        """
        from backend.config.settings import settings

        messages = self.get_messages(conversation_id, limit=10000)
        max_ctx = settings.max_context_messages

        if len(messages) <= max_ctx:
            return ""

        # Check cache
        conv = self.get_conversation(conversation_id)
        if not conv:
            return ""
        meta = conv.extra_metadata or {}
        cached = meta.get("context_summary", {})
        if cached.get("message_count") == len(messages):
            return cached.get("summary", "")

        # Identify the "middle" messages (same window as SmartCompressionStrategy)
        keep_first = 2
        keep_recent = 10
        middle = messages[keep_first: len(messages) - keep_recent]
        if not middle:
            return ""

        middle_plain = [
            {"role": m.role, "content": m.content}
            for m in middle
        ]

        summarizer = ConversationSummarizer()
        try:
            summary = await summarizer.summarize(middle_plain, llm_adapter)
        except Exception as e:
            logger.warning(f"[_maybe_summarize] Summarizer failed: {e}")
            return ""

        if summary:
            from datetime import datetime
            self._update_conversation_meta(conversation_id, {
                "context_summary": {
                    "summary": summary,
                    "message_count": len(messages),
                    "generated_at": datetime.utcnow().isoformat(),
                }
            })

        return summary

    def _get_llm_config(self, model_key: str) -> Dict[str, Any]:
        """获取LLM配置"""
        from backend.config.settings import settings

        llm_config = self.db.query(LLMConfig).filter(
            LLMConfig.model_key == model_key
        ).first()

        if not llm_config:
            # 返回默认配置
            return {
                "model_type": model_key,
                "api_key": "",
                "api_base_url": "",
                "default_model": model_key,
                "temperature": 0.7,
                "max_tokens": 8192,  # 使用 Claude Sonnet 4.5 的最大输出限制
                "fallback_models": [],
                "enable_fallback": False
            }

        # 解析备用模型列表
        fallback_models = []
        if model_key == "claude" and settings.anthropic_fallback_models:
            fallback_models = [
                m.strip() for m in settings.anthropic_fallback_models.split(",")
                if m.strip()
            ]
        elif llm_config.extra_config and isinstance(llm_config.extra_config, dict):
            # 非 Claude 模型从 extra_config.fallback_models 读取
            extra_fallback = llm_config.extra_config.get("fallback_models", [])
            if isinstance(extra_fallback, list):
                fallback_models = [str(m).strip() for m in extra_fallback if m]

        # 确保类型转换：数据库中存储的是字符串，需要转换为正确的类型
        try:
            temperature = float(llm_config.temperature) if llm_config.temperature else 0.7
        except (ValueError, TypeError):
            temperature = 0.7

        try:
            max_tokens = int(llm_config.max_tokens) if llm_config.max_tokens else 8192
        except (ValueError, TypeError):
            max_tokens = 8192

        return {
            "model_type": llm_config.model_type,
            "api_key": llm_config.api_key,
            "api_base_url": llm_config.api_base_url,
            "default_model": llm_config.default_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "fallback_models": fallback_models,
            "enable_fallback": (
                (model_key == "claude" and settings.anthropic_enable_fallback)
                or (model_key != "claude" and bool(fallback_models))
            )
        }
