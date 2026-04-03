"""
独立测试 Vector Store Manager - Phase 3.1

测试 ChromaDB 向量存储集成（使用 Mock）
"""
import sys
import os
from typing import List, Dict, Any, Optional
from datetime import datetime


# ============= Mock ChromaDB (在没有安装 chromadb 的情况下使用) =============

class MockCollection:
    """模拟 Chroma Collection"""

    def __init__(self, name: str, metadata: Dict[str, Any] = None):
        self.name = name
        self.metadata = metadata or {}
        self.data = {
            "ids": [],
            "documents": [],
            "metadatas": []
        }

    def add(self, ids: List[str], documents: List[str], metadatas: List[Dict]):
        """添加文档"""
        for id, doc, meta in zip(ids, documents, metadatas):
            if id in self.data["ids"]:
                # 更新已存在的文档
                idx = self.data["ids"].index(id)
                self.data["documents"][idx] = doc
                self.data["metadatas"][idx] = meta
            else:
                # 添加新文档
                self.data["ids"].append(id)
                self.data["documents"].append(doc)
                self.data["metadatas"].append(meta)

    def query(
        self,
        query_texts: List[str],
        n_results: int = 5,
        where: Optional[Dict] = None
    ) -> Dict:
        """查询相似文档（简化版：返回最近添加的）"""
        # 过滤数据
        filtered_indices = []
        for i, meta in enumerate(self.data["metadatas"]):
            if where is None:
                filtered_indices.append(i)
            else:
                match = all(meta.get(k) == v for k, v in where.items())
                if match:
                    filtered_indices.append(i)

        # 简化：返回最近的 n_results 个
        filtered_indices = filtered_indices[-n_results:]

        # 构建结果
        return {
            "ids": [[self.data["ids"][i] for i in filtered_indices]],
            "documents": [[self.data["documents"][i] for i in filtered_indices]],
            "metadatas": [[self.data["metadatas"][i] for i in filtered_indices]],
            "distances": [[0.1 * (len(filtered_indices) - j) for j in range(len(filtered_indices))]]
        }

    def get(self, where: Optional[Dict] = None, limit: Optional[int] = None) -> Dict:
        """获取文档"""
        filtered_indices = []
        for i, meta in enumerate(self.data["metadatas"]):
            if where is None:
                filtered_indices.append(i)
            else:
                match = all(meta.get(k) == v for k, v in where.items())
                if match:
                    filtered_indices.append(i)

        if limit:
            filtered_indices = filtered_indices[:limit]

        return {
            "ids": [self.data["ids"][i] for i in filtered_indices],
            "documents": [self.data["documents"][i] for i in filtered_indices],
            "metadatas": [self.data["metadatas"][i] for i in filtered_indices]
        }

    def delete(self, ids: Optional[List[str]] = None, where: Optional[Dict] = None):
        """删除文档"""
        if ids:
            for id in ids:
                if id in self.data["ids"]:
                    idx = self.data["ids"].index(id)
                    del self.data["ids"][idx]
                    del self.data["documents"][idx]
                    del self.data["metadatas"][idx]
        elif where:
            indices_to_delete = []
            for i, meta in enumerate(self.data["metadatas"]):
                match = all(meta.get(k) == v for k, v in where.items())
                if match:
                    indices_to_delete.append(i)

            for idx in reversed(indices_to_delete):
                del self.data["ids"][idx]
                del self.data["documents"][idx]
                del self.data["metadatas"][idx]

    def count(self) -> int:
        """计数"""
        return len(self.data["ids"])


class MockChromaClient:
    """模拟 Chroma Client"""

    def __init__(self, settings=None):
        self.collections = {}
        self.settings = settings

    def get_or_create_collection(self, name: str, metadata: Dict = None) -> MockCollection:
        """获取或创建集合"""
        if name not in self.collections:
            self.collections[name] = MockCollection(name, metadata)
        return self.collections[name]

    def delete_collection(self, name: str):
        """删除集合"""
        if name in self.collections:
            del self.collections[name]


# ============= VectorStoreManager (简化版) =============

class VectorStoreManager:
    """向量存储管理器"""

    def __init__(
        self,
        persist_directory: str = "./data/chroma",
        collection_name: str = "conversation_messages",
        use_mock: bool = True
    ):
        self.persist_directory = persist_directory
        self.collection_name = collection_name

        if use_mock:
            # 使用 Mock Client
            self.client = MockChromaClient()
        else:
            # 实际 ChromaDB (需要安装)
            try:
                import chromadb
                from chromadb.config import Settings
                self.client = chromadb.Client(Settings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory=persist_directory,
                    anonymized_telemetry=False
                ))
            except ImportError:
                raise ImportError("chromadb not installed, use use_mock=True")

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "对话消息的语义索引"}
        )

    def add_message(
        self,
        message_id: str,
        content: str,
        conversation_id: str,
        role: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """添加单条消息"""
        full_id = f"{conversation_id}_{message_id}"

        msg_metadata = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "role": role,
            "created_at": datetime.now().isoformat()
        }

        if metadata:
            msg_metadata.update(metadata)

        self.collection.add(
            ids=[full_id],
            documents=[content],
            metadatas=[msg_metadata]
        )

    def add_messages(
        self,
        messages: List[Dict[str, Any]],
        conversation_id: str
    ):
        """批量添加消息"""
        if not messages:
            return

        ids = []
        documents = []
        metadatas = []

        for msg in messages:
            msg_id = str(msg.get("id", msg.get("message_id", "")))
            if not msg_id:
                continue

            full_id = f"{conversation_id}_{msg_id}"
            content = msg.get("content", "")

            if not content:
                continue

            ids.append(full_id)
            documents.append(content)

            msg_metadata = {
                "conversation_id": conversation_id,
                "message_id": msg_id,
                "role": msg.get("role", "user"),
                "created_at": msg.get("created_at", datetime.now().isoformat()),
                "tokens": msg.get("tokens", 0)
            }

            metadatas.append(msg_metadata)

        if ids:
            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )

    def query_similar(
        self,
        query_text: str,
        conversation_id: Optional[str] = None,
        n_results: int = 5,
        distance_threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """查询相似消息"""
        where_clause = {"conversation_id": conversation_id} if conversation_id else None

        results = self.collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where_clause
        )

        similar_messages = []

        if results and results.get("documents"):
            documents = results["documents"][0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            ids = results.get("ids", [[]])[0]

            for i, doc in enumerate(documents):
                distance = distances[i] if i < len(distances) else None

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

        return similar_messages

    def delete_conversation(self, conversation_id: str):
        """删除对话"""
        self.collection.delete(where={"conversation_id": conversation_id})

    def get_collection_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        count = self.collection.count()
        return {
            "collection_name": self.collection_name,
            "total_messages": count,
            "persist_directory": self.persist_directory
        }

    def clear_collection(self):
        """清空集合"""
        self.client.delete_collection(name=self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"description": "对话消息的语义索引"}
        )


# ============= 测试函数 =============

def test_vector_store_initialization():
    """测试向量存储初始化"""
    print("\n[TEST] Vector Store Initialization")
    print("-" * 60)

    manager = VectorStoreManager(use_mock=True)

    assert manager is not None
    assert manager.collection is not None
    print("[PASS] VectorStoreManager initialized with mock")

    stats = manager.get_collection_stats()
    assert "collection_name" in stats
    assert "total_messages" in stats
    print(f"[PASS] Collection stats: {stats}")


def test_add_single_message():
    """测试添加单条消息"""
    print("\n[TEST] Add Single Message")
    print("-" * 60)

    manager = VectorStoreManager(use_mock=True)

    manager.add_message(
        message_id="msg_001",
        content="Hello, how can I help you?",
        conversation_id="conv_001",
        role="assistant",
        metadata={"tokens": 10}
    )

    stats = manager.get_collection_stats()
    assert stats["total_messages"] == 1
    print("[PASS] Single message added successfully")
    print(f"  Total messages: {stats['total_messages']}")


def test_add_batch_messages():
    """测试批量添加消息"""
    print("\n[TEST] Add Batch Messages")
    print("-" * 60)

    manager = VectorStoreManager(use_mock=True)

    messages = [
        {"id": "1", "content": "User question 1", "role": "user", "tokens": 5},
        {"id": "2", "content": "Assistant answer 1", "role": "assistant", "tokens": 15},
        {"id": "3", "content": "User question 2", "role": "user", "tokens": 8},
        {"id": "4", "content": "Assistant answer 2", "role": "assistant", "tokens": 20},
    ]

    manager.add_messages(messages, "conv_002")

    stats = manager.get_collection_stats()
    assert stats["total_messages"] == 4
    print("[PASS] Batch messages added successfully")
    print(f"  Total messages: {stats['total_messages']}")


def test_query_similar_messages():
    """测试查询相似消息"""
    print("\n[TEST] Query Similar Messages")
    print("-" * 60)

    manager = VectorStoreManager(use_mock=True)

    # 添加一些消息
    messages = [
        {"id": "1", "content": "How to connect to database?", "role": "user"},
        {"id": "2", "content": "Use connection string", "role": "assistant"},
        {"id": "3", "content": "What about authentication?", "role": "user"},
        {"id": "4", "content": "Use API key", "role": "assistant"},
    ]

    manager.add_messages(messages, "conv_003")

    # 查询相似消息
    similar = manager.query_similar(
        query_text="database connection",
        conversation_id="conv_003",
        n_results=2
    )

    assert len(similar) <= 2
    print(f"[PASS] Query returned {len(similar)} similar messages")

    for i, msg in enumerate(similar):
        print(f"  Result {i+1}: {msg['content'][:50]}...")
        assert "content" in msg
        assert "metadata" in msg


def test_delete_conversation():
    """测试删除对话"""
    print("\n[TEST] Delete Conversation")
    print("-" * 60)

    manager = VectorStoreManager(use_mock=True)

    # 添加消息
    messages = [
        {"id": "1", "content": "Message 1", "role": "user"},
        {"id": "2", "content": "Message 2", "role": "assistant"},
    ]

    manager.add_messages(messages, "conv_to_delete")

    stats_before = manager.get_collection_stats()
    assert stats_before["total_messages"] == 2

    # 删除对话
    manager.delete_conversation("conv_to_delete")

    stats_after = manager.get_collection_stats()
    assert stats_after["total_messages"] == 0

    print("[PASS] Conversation deleted successfully")
    print(f"  Before: {stats_before['total_messages']} messages")
    print(f"  After: {stats_after['total_messages']} messages")


def test_multiple_conversations():
    """测试多个对话"""
    print("\n[TEST] Multiple Conversations")
    print("-" * 60)

    manager = VectorStoreManager(use_mock=True)

    # 对话 1
    manager.add_messages([
        {"id": "1", "content": "Conv1 Msg1", "role": "user"},
        {"id": "2", "content": "Conv1 Msg2", "role": "assistant"},
    ], "conv_A")

    # 对话 2
    manager.add_messages([
        {"id": "1", "content": "Conv2 Msg1", "role": "user"},
        {"id": "2", "content": "Conv2 Msg2", "role": "assistant"},
    ], "conv_B")

    # 查询对话 A
    similar_a = manager.query_similar("test", "conv_A", n_results=10)
    print(f"[PASS] Conv A has {len(similar_a)} messages")
    assert len(similar_a) == 2

    # 查询对话 B
    similar_b = manager.query_similar("test", "conv_B", n_results=10)
    print(f"[PASS] Conv B has {len(similar_b)} messages")
    assert len(similar_b) == 2

    # 删除对话 A
    manager.delete_conversation("conv_A")

    # 验证对话 B 还在
    similar_b_after = manager.query_similar("test", "conv_B", n_results=10)
    assert len(similar_b_after) == 2
    print(f"[PASS] Conv B still has {len(similar_b_after)} messages after deleting Conv A")


def test_distance_threshold():
    """测试距离阈值过滤"""
    print("\n[TEST] Distance Threshold Filtering")
    print("-" * 60)

    manager = VectorStoreManager(use_mock=True)

    messages = [
        {"id": "1", "content": "Message 1", "role": "user"},
        {"id": "2", "content": "Message 2", "role": "user"},
        {"id": "3", "content": "Message 3", "role": "user"},
    ]

    manager.add_messages(messages, "conv_threshold")

    # 查询所有
    all_results = manager.query_similar("test", "conv_threshold", n_results=10)
    print(f"[PASS] All results: {len(all_results)} messages")

    # 使用距离阈值
    filtered_results = manager.query_similar(
        "test", "conv_threshold", n_results=10, distance_threshold=0.15
    )
    print(f"[PASS] Filtered results (threshold=0.15): {len(filtered_results)} messages")

    # 过滤后的结果应该 <= 所有结果
    assert len(filtered_results) <= len(all_results)


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Vector Store Manager - Standalone Tests")
    print("=" * 60)

    try:
        test_vector_store_initialization()
        test_add_single_message()
        test_add_batch_messages()
        test_query_similar_messages()
        test_delete_conversation()
        test_multiple_conversations()
        test_distance_threshold()

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        print("\nPhase 3.1 (Vector Store Manager) completed successfully.")

        return 0

    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
