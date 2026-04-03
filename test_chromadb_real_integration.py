"""
Real ChromaDB Integration Test - Phase 3.1 Verification

测试实际 ChromaDB 连接和功能
"""
import sys
import os
import shutil
from typing import List

# 添加 backend 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from backend.core.vector_store import VectorStoreManager


def test_chromadb_installation():
    """测试 ChromaDB 是否正确安装"""
    print("\n[TEST] ChromaDB Installation Check")
    print("-" * 60)

    try:
        import chromadb
        print(f"[PASS] ChromaDB imported successfully")
        print(f"  Version: {chromadb.__version__}")
        return True
    except ImportError as e:
        print(f"[FAIL] ChromaDB not installed: {e}")
        return False


def test_vector_store_initialization():
    """测试 VectorStoreManager 初始化"""
    print("\n[TEST] VectorStore Initialization")
    print("-" * 60)

    try:
        # 使用临时目录
        test_dir = "./test_data/chroma_test"
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)

        # 初始化 (ChromaDB 自动处理 embeddings)
        vector_store = VectorStoreManager(
            persist_directory=test_dir,
            collection_name="test_messages"
        )

        assert vector_store is not None
        assert vector_store.collection is not None

        print("[PASS] VectorStore initialized successfully")
        print(f"  Collection: {vector_store.collection.name}")
        print(f"  Persist directory: {test_dir}")

        return vector_store, test_dir

    except Exception as e:
        print(f"[FAIL] Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def test_add_messages(vector_store: VectorStoreManager):
    """测试添加消息"""
    print("\n[TEST] Add Messages to ChromaDB")
    print("-" * 60)

    try:
        conversation_id = "test_conv_001"

        messages_data = [
            ("msg_001", "How to connect to a database?", "user"),
            ("msg_002", "You can use connection strings to connect to databases.", "assistant"),
            ("msg_003", "What is connection pooling?", "user"),
            ("msg_004", "Connection pooling is a technique to reuse database connections.", "assistant"),
        ]

        for msg_id, content, role in messages_data:
            vector_store.add_message(
                message_id=msg_id,
                content=content,
                conversation_id=conversation_id,
                role=role
            )

        print(f"[PASS] Added {len(messages_data)} messages")
        print(f"  Conversation ID: {conversation_id}")

        return conversation_id, messages_data

    except Exception as e:
        print(f"[FAIL] Failed to add messages: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def test_query_similar_messages(
    vector_store: VectorStoreManager,
    conversation_id: str
):
    """测试查询相似消息"""
    print("\n[TEST] Query Similar Messages")
    print("-" * 60)

    try:
        query = "database connection"
        results = vector_store.query_similar(
            query_text=query,
            conversation_id=conversation_id,
            n_results=2
        )

        assert results is not None
        assert len(results) > 0

        print(f"[PASS] Query returned {len(results)} results")
        print(f"  Query: '{query}'")
        for i, result in enumerate(results[:3], 1):
            content = result.get("content", "")
            similarity = result.get("similarity", 0)
            print(f"  {i}. (similarity={similarity:.4f}) {content[:60]}...")

        return results

    except Exception as e:
        print(f"[FAIL] Query failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_delete_messages(
    vector_store: VectorStoreManager,
    conversation_id: str
):
    """测试删除消息"""
    print("\n[TEST] Delete Messages")
    print("-" * 60)

    try:
        # 删除对话
        vector_store.delete_conversation(conversation_id)

        # 验证删除
        results = vector_store.query_similar(
            query_text="database",
            conversation_id=conversation_id,
            n_results=10
        )

        assert len(results) == 0, "Messages should be deleted"

        print("[PASS] Messages deleted successfully")
        print(f"  Conversation ID: {conversation_id}")

        return True

    except Exception as e:
        print(f"[FAIL] Delete failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_collection_stats(vector_store: VectorStoreManager):
    """测试获取统计信息"""
    print("\n[TEST] Collection Statistics")
    print("-" * 60)

    try:
        stats = vector_store.get_collection_stats()

        assert stats is not None
        assert "total_messages" in stats

        print("[PASS] Statistics retrieved")
        print(f"  Total messages: {stats['total_messages']}")
        print(f"  Collection name: {stats.get('collection_name', 'N/A')}")

        return stats

    except Exception as e:
        print(f"[FAIL] Stats retrieval failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_persistence(test_dir: str):
    """测试持久化"""
    print("\n[TEST] Persistence")
    print("-" * 60)

    try:
        # 创建并添加数据
        vs1 = VectorStoreManager(
            persist_directory=test_dir,
            collection_name="test_messages"
        )

        vs1.add_message(
            message_id="persist_001",
            content="Persistence test message",
            conversation_id="persist_test",
            role="user"
        )

        # 获取初始数量
        stats1 = vs1.get_collection_stats()
        initial_count = stats1["total_messages"]

        # 重新加载
        vs2 = VectorStoreManager(
            persist_directory=test_dir,
            collection_name="test_messages"
        )

        stats2 = vs2.get_collection_stats()
        loaded_count = stats2["total_messages"]

        assert loaded_count == initial_count, "Data should persist"

        print("[PASS] Data persisted successfully")
        print(f"  Initial count: {initial_count}")
        print(f"  Loaded count: {loaded_count}")

        return True

    except Exception as e:
        print(f"[FAIL] Persistence test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def cleanup(test_dir: str):
    """清理测试数据"""
    print("\n[CLEANUP] Removing test data")
    print("-" * 60)

    try:
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
            print("[PASS] Test data removed")
    except Exception as e:
        print(f"[WARN] Cleanup failed: {e}")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("ChromaDB Real Integration Test")
    print("=" * 60)

    all_passed = True
    test_dir = None

    try:
        # 1. 检查 ChromaDB 安装
        if not test_chromadb_installation():
            print("\n[FATAL] ChromaDB not installed. Cannot continue.")
            return 1

        # 2. 初始化
        vector_store, test_dir = test_vector_store_initialization()
        if vector_store is None:
            all_passed = False
            return 1

        # 3. 添加消息
        conversation_id, messages = test_add_messages(vector_store)
        if conversation_id is None:
            all_passed = False

        # 4. 查询相似消息
        if conversation_id:
            results = test_query_similar_messages(vector_store, conversation_id)
            if results is None:
                all_passed = False

        # 5. 获取统计
        stats = test_collection_stats(vector_store)
        if stats is None:
            all_passed = False

        # 6. 删除消息
        if conversation_id:
            if not test_delete_messages(vector_store, conversation_id):
                all_passed = False

        # 7. 持久化测试
        if test_dir:
            if not test_persistence(test_dir):
                all_passed = False

        # 结果
        print("\n" + "=" * 60)
        if all_passed:
            print("[SUCCESS] All ChromaDB integration tests passed!")
            print("=" * 60)
            print("\nChromaDB is properly installed and functional.")
            return 0
        else:
            print("[PARTIAL] Some tests failed")
            print("=" * 60)
            return 1

    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # 清理
        if test_dir:
            cleanup(test_dir)


if __name__ == "__main__":
    sys.exit(main())
