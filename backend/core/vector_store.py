"""
Vector Store Manager - Phase 3.1

ChromaDB 向量存储集成

借鉴: LlamaIndex + ChromaDB 最佳实践

为什么选择 Chroma:
- 轻量级，易于集成
- 支持本地和服务器模式
- Python 原生支持
- 自动持久化
- 丰富的查询功能
"""
import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

# 禁用 ChromaDB 遥测（避免 Python 3.8 兼容性问题）
os.environ["ANONYMIZED_TELEMETRY"] = "False"

try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    chromadb = None

logger = logging.getLogger(__name__)


class VectorStoreManager:
    """向量存储管理器"""

    def __init__(
        self,
        persist_directory: str = "./data/chroma",
        collection_name: str = "conversation_messages"
    ):
        """
        初始化 Chroma 客户端

        Args:
            persist_directory: 持久化目录
            collection_name: 集合名称
        """
        if not CHROMA_AVAILABLE:
            raise ImportError(
                "chromadb is not installed. "
                "Please install it with: pip install chromadb==0.4.22"
            )

        self.persist_directory = persist_directory
        self.collection_name = collection_name

        # 确保目录存在
        os.makedirs(persist_directory, exist_ok=True)

        # 初始化 Chroma 客户端 (使用新的 API)
        try:
            # ChromaDB 0.4.x 使用 PersistentClient
            # 禁用遥测以避免 Python 3.8 兼容性问题
            from chromadb.config import Settings
            settings = Settings(anonymized_telemetry=False)

            self.client = chromadb.PersistentClient(
                path=persist_directory,
                settings=settings
            )

            # 创建或获取集合
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"description": "对话消息的语义索引"}
            )

            logger.info(
                f"VectorStoreManager initialized: "
                f"persist_directory={persist_directory}, "
                f"collection={collection_name}"
            )

        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}", exc_info=True)
            raise

    def add_message(
        self,
        message_id: str,
        content: str,
        conversation_id: str,
        role: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        添加单条消息到向量存储

        Args:
            message_id: 消息ID
            content: 消息内容
            conversation_id: 对话ID
            role: 角色 (user/assistant/system)
            metadata: 额外元数据
        """
        try:
            # 构建完整的消息ID
            full_id = f"{conversation_id}_{message_id}"

            # 构建元数据
            msg_metadata = {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "role": role,
                "created_at": datetime.now().isoformat()
            }

            # 合并额外元数据
            if metadata:
                msg_metadata.update(metadata)

            # 添加到集合
            self.collection.add(
                ids=[full_id],
                documents=[content],
                metadatas=[msg_metadata]
            )

            logger.debug(f"Added message to vector store: {full_id}")

        except Exception as e:
            logger.error(f"Failed to add message to vector store: {e}", exc_info=True)
            raise

    def add_messages(
        self,
        messages: List[Dict[str, Any]],
        conversation_id: str
    ):
        """
        批量添加消息到向量存储

        Args:
            messages: 消息列表，每条消息包含 id, content, role 等字段
            conversation_id: 对话ID
        """
        if not messages:
            logger.debug("No messages to add")
            return

        try:
            # 准备数据
            ids = []
            documents = []
            metadatas = []

            for msg in messages:
                msg_id = str(msg.get("id", msg.get("message_id", "")))
                if not msg_id:
                    logger.warning("Message without ID, skipping")
                    continue

                full_id = f"{conversation_id}_{msg_id}"
                content = msg.get("content", "")

                if not content:
                    logger.warning(f"Message {full_id} has no content, skipping")
                    continue

                ids.append(full_id)
                documents.append(content)

                # 构建元数据
                msg_metadata = {
                    "conversation_id": conversation_id,
                    "message_id": msg_id,
                    "role": msg.get("role", "user"),
                    "created_at": msg.get("created_at", datetime.now().isoformat()),
                    "tokens": msg.get("tokens", msg.get("total_tokens", 0))
                }

                metadatas.append(msg_metadata)

            # 批量添加到集合
            if ids:
                self.collection.add(
                    ids=ids,
                    documents=documents,
                    metadatas=metadatas
                )

                logger.info(f"Added {len(ids)} messages to vector store for conversation {conversation_id}")

        except Exception as e:
            logger.error(f"Failed to add messages to vector store: {e}", exc_info=True)
            raise

    def query_similar(
        self,
        query_text: str,
        conversation_id: Optional[str] = None,
        n_results: int = 5,
        distance_threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        查询语义相似的消息

        Args:
            query_text: 查询文本
            conversation_id: 对话ID（可选，如果不指定则搜索所有对话）
            n_results: 返回结果数
            distance_threshold: 距离阈值，超过此值的结果会被过滤

        Returns:
            相似消息列表
        """
        try:
            # 构建查询条件
            where_clause = {"conversation_id": conversation_id} if conversation_id else None

            # 执行查询
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where_clause
            )

            # 格式化结果
            similar_messages = []

            if results and results.get("documents"):
                documents = results["documents"][0]
                metadatas = results.get("metadatas", [[]])[0]
                distances = results.get("distances", [[]])[0]
                ids = results.get("ids", [[]])[0]

                for i, doc in enumerate(documents):
                    distance = distances[i] if i < len(distances) else None

                    # 应用距离阈值过滤
                    if distance_threshold is not None and distance is not None:
                        if distance > distance_threshold:
                            continue

                    similar_messages.append({
                        "id": ids[i] if i < len(ids) else None,
                        "content": doc,
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "distance": distance,
                        "similarity": 1 - distance if distance is not None else None
                    })

            logger.debug(
                f"Found {len(similar_messages)} similar messages for query "
                f"in conversation {conversation_id or 'all'}"
            )

            return similar_messages

        except Exception as e:
            logger.error(f"Failed to query similar messages: {e}", exc_info=True)
            return []

    def get_conversation_messages(
        self,
        conversation_id: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取对话的所有消息

        Args:
            conversation_id: 对话ID
            limit: 限制返回数量

        Returns:
            消息列表
        """
        try:
            # 查询集合中的所有消息
            results = self.collection.get(
                where={"conversation_id": conversation_id},
                limit=limit
            )

            messages = []

            if results and results.get("documents"):
                documents = results["documents"]
                metadatas = results.get("metadatas", [])
                ids = results.get("ids", [])

                for i, doc in enumerate(documents):
                    messages.append({
                        "id": ids[i] if i < len(ids) else None,
                        "content": doc,
                        "metadata": metadatas[i] if i < len(metadatas) else {}
                    })

            logger.debug(f"Retrieved {len(messages)} messages for conversation {conversation_id}")

            return messages

        except Exception as e:
            logger.error(f"Failed to get conversation messages: {e}", exc_info=True)
            return []

    def delete_message(self, conversation_id: str, message_id: str):
        """
        删除单条消息

        Args:
            conversation_id: 对话ID
            message_id: 消息ID
        """
        try:
            full_id = f"{conversation_id}_{message_id}"
            self.collection.delete(ids=[full_id])

            logger.debug(f"Deleted message from vector store: {full_id}")

        except Exception as e:
            logger.error(f"Failed to delete message: {e}", exc_info=True)
            raise

    def delete_conversation(self, conversation_id: str):
        """
        删除对话的所有向量

        Args:
            conversation_id: 对话ID
        """
        try:
            self.collection.delete(
                where={"conversation_id": conversation_id}
            )

            logger.info(f"Deleted all messages for conversation {conversation_id}")

        except Exception as e:
            logger.error(f"Failed to delete conversation: {e}", exc_info=True)
            raise

    def get_collection_stats(self) -> Dict[str, Any]:
        """
        获取集合统计信息

        Returns:
            统计信息字典
        """
        try:
            count = self.collection.count()

            return {
                "collection_name": self.collection_name,
                "total_messages": count,
                "persist_directory": self.persist_directory
            }

        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}", exc_info=True)
            return {
                "collection_name": self.collection_name,
                "total_messages": 0,
                "persist_directory": self.persist_directory,
                "error": str(e)
            }

    def clear_collection(self):
        """清空集合中的所有数据（谨慎使用）"""
        try:
            # 删除集合
            self.client.delete_collection(name=self.collection_name)

            # 重新创建
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "对话消息的语义索引"}
            )

            logger.warning(f"Collection {self.collection_name} cleared")

        except Exception as e:
            logger.error(f"Failed to clear collection: {e}", exc_info=True)
            raise


# 全局实例
_vector_store_manager = None


def get_vector_store_manager(
    persist_directory: str = "./data/chroma",
    collection_name: str = "conversation_messages"
) -> Optional[VectorStoreManager]:
    """
    获取 VectorStoreManager 单例

    Args:
        persist_directory: 持久化目录
        collection_name: 集合名称

    Returns:
        VectorStoreManager 实例，如果 chromadb 未安装则返回 None
    """
    if not CHROMA_AVAILABLE:
        logger.warning("ChromaDB is not available, vector store disabled")
        return None

    global _vector_store_manager
    if _vector_store_manager is None:
        try:
            _vector_store_manager = VectorStoreManager(
                persist_directory=persist_directory,
                collection_name=collection_name
            )
        except Exception as e:
            logger.error(f"Failed to initialize vector store: {e}")
            return None

    return _vector_store_manager
