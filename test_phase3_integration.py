"""
Phase 3 Complete Integration Test

测试 Phase 3 所有组件的集成:
- Vector Store Manager (ChromaDB)
- Embedding Service
- Semantic Compression Strategy
"""
import sys
import os
import shutil
from typing import List

# 添加 backend 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from backend.core.vector_store import VectorStoreManager
from backend.core.embedding_service import EmbeddingService
from backend.core.semantic_compression import SemanticCompressionStrategy
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole


def test_embedding_service():
    """测试 Embedding Service"""
    print("\n[TEST 1] Embedding Service")
    print("-" * 60)

    try:
        service = EmbeddingService(provider="mock")

        # 测试单个文本
        text = "How to connect to a database?"
        embedding = service.embed(text)

        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)

        # 测试批量
        texts = ["Message 1", "Message 2", "Message 3"]
        embeddings = service.embed_batch(texts)

        assert len(embeddings) == 3
        assert all(len(emb) == 384 for emb in embeddings)

        # 测试相似度
        text1 = "database connection"
        text2 = "connect to database"
        text3 = "weather is sunny"

        emb1 = service.embed(text1)
        emb2 = service.embed(text2)
        emb3 = service.embed(text3)

        sim_12 = service.cosine_similarity(emb1, emb2)
        sim_13 = service.cosine_similarity(emb1, emb3)

        print(f"[PASS] Embedding Service functional")
        print(f"  Dimension: {service.get_dimension()}")
        print(f"  Similarity (related): {sim_12:.4f}")
        print(f"  Similarity (unrelated): {sim_13:.4f}")

        return service

    except Exception as e:
        print(f"[FAIL] Embedding Service failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_vector_store():
    """测试 Vector Store Manager"""
    print("\n[TEST 2] Vector Store Manager (ChromaDB)")
    print("-" * 60)

    test_dir = "./test_data/phase3_integration"

    try:
        # 清理旧数据
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)

        # 初始化
        vector_store = VectorStoreManager(
            persist_directory=test_dir,
            collection_name="phase3_test"
        )

        # 添加消息
        conversation_id = "conv_001"
        messages = [
            ("msg_001", "How to connect to PostgreSQL?", "user"),
            ("msg_002", "Use psycopg2 library to connect", "assistant"),
            ("msg_003", "What is connection pooling?", "user"),
            ("msg_004", "Connection pooling reuses connections", "assistant"),
            ("msg_005", "The weather is sunny today", "user"),
        ]

        for msg_id, content, role in messages:
            vector_store.add_message(
                message_id=msg_id,
                content=content,
                conversation_id=conversation_id,
                role=role
            )

        # 查询相似消息
        results = vector_store.query_similar(
            query_text="database connection",
            conversation_id=conversation_id,
            n_results=3
        )

        assert len(results) > 0

        print(f"[PASS] Vector Store functional")
        print(f"  Messages added: {len(messages)}")
        print(f"  Query results: {len(results)}")
        for i, result in enumerate(results[:2], 1):
            print(f"  {i}. (sim={result.get('similarity', 0):.4f}) {result.get('content', '')[:50]}...")

        return vector_store, test_dir, conversation_id

    except Exception as e:
        print(f"[FAIL] Vector Store failed: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None


def test_semantic_compression():
    """测试 Semantic Compression Strategy"""
    print("\n[TEST 3] Semantic Compression Strategy")
    print("-" * 60)

    try:
        strategy = SemanticCompressionStrategy(
            keep_first=2,
            keep_last=3,
            similarity_threshold=0.6,
            embedding_provider="mock"
        )

        # 创建长对话
        conv = UnifiedConversation(
            conversation_id="test_compression",
            title="Test Conversation"
        )

        # 添加消息
        messages = [
            ("user", "I want to learn about databases"),
            ("assistant", "Sure! What would you like to know?"),
            # 中间无关消息
            ("user", "What's the weather like?"),
            ("assistant", "I don't have weather information"),
            ("user", "Do you like music?"),
            ("assistant", "I'm an AI assistant"),
            # 相关消息
            ("user", "How does database indexing work?"),
            ("assistant", "Indexes improve query performance"),
            ("user", "What about connection pooling?"),
            ("assistant", "Pooling reuses connections"),
            # 最近消息
            ("user", "Tell me more about database optimization"),
            ("assistant", "Optimization involves many techniques"),
            ("user", "How to optimize queries?"),
        ]

        for role, content in messages:
            conv.add_message(UnifiedMessage(
                role=MessageRole(role),
                content=content
            ))

        original_count = len(conv.messages)

        # 压缩
        compressed = strategy.compress(conv)
        compressed_count = len(compressed.messages)

        print(f"[PASS] Semantic Compression functional")
        print(f"  Original messages: {original_count}")
        print(f"  Compressed messages: {compressed_count}")
        print(f"  Reduction: {(1 - compressed_count/original_count)*100:.1f}%")

        # 验证首尾保留
        non_system = [m for m in compressed.messages if m.role != MessageRole.SYSTEM]
        assert non_system[0].content == "I want to learn about databases"
        assert non_system[-1].content == "How to optimize queries?"

        print(f"  First/last messages preserved: [OK]")

        return strategy

    except Exception as e:
        print(f"[FAIL] Semantic Compression failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_integrated_workflow():
    """测试完整集成工作流"""
    print("\n[TEST 4] Integrated Workflow")
    print("-" * 60)

    test_dir = "./test_data/phase3_workflow"

    try:
        # 清理
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)

        # 1. 创建服务
        embedding_service = EmbeddingService(provider="mock")
        vector_store = VectorStoreManager(
            persist_directory=test_dir,
            collection_name="workflow_test"
        )
        compression_strategy = SemanticCompressionStrategy(
            keep_first=1,
            keep_last=2,
            similarity_threshold=0.6,
            embedding_provider="mock"
        )

        # 2. 创建对话
        conv = UnifiedConversation(
            conversation_id="workflow_001",
            title="Integrated Workflow Test"
        )

        # 3. 模拟对话流程
        dialogue = [
            ("user", "How do I start with Python?"),
            ("assistant", "Start by installing Python from python.org"),
            ("user", "What are variables?"),
            ("assistant", "Variables store data in your program"),
            ("user", "The weather is nice"),
            ("assistant", "I'm here to help with programming"),
            ("user", "How do I use lists?"),
            ("assistant", "Lists store multiple items: my_list = [1, 2, 3]"),
            ("user", "What about dictionaries?"),
            ("assistant", "Dictionaries store key-value pairs"),
            ("user", "Can you explain functions?"),
            ("assistant", "Functions are reusable blocks of code"),
        ]

        for role, content in dialogue:
            msg = UnifiedMessage(role=MessageRole(role), content=content)
            conv.add_message(msg)

        # 4. 压缩对话 (模拟 token 管理)
        original_size = len(conv.messages)
        compressed_conv = compression_strategy.compress(conv)
        compressed_size = len(compressed_conv.messages)

        print(f"  [Step 1] Conversation compressed")
        print(f"    Original: {original_size} messages")
        print(f"    Compressed: {compressed_size} messages")

        # 5. 存储到向量数据库
        for i, msg in enumerate(compressed_conv.messages):
            if msg.role != MessageRole.SYSTEM:  # 跳过系统消息
                vector_store.add_message(
                    message_id=f"msg_{i:03d}",
                    content=msg.content,
                    conversation_id=conv.conversation_id,
                    role=str(msg.role)
                )

        stats = vector_store.get_collection_stats()
        print(f"  [Step 2] Messages stored to vector DB")
        print(f"    Total in DB: {stats['total_messages']} messages")

        # 6. 语义查询
        query = "how to use Python functions"
        results = vector_store.query_similar(
            query_text=query,
            conversation_id=conv.conversation_id,
            n_results=2
        )

        print(f"  [Step 3] Semantic query executed")
        print(f"    Query: '{query}'")
        print(f"    Results: {len(results)} matches")
        for i, result in enumerate(results, 1):
            print(f"      {i}. (sim={result['similarity']:.4f}) {result['content'][:50]}...")

        # 7. 验证集成
        assert compressed_size < original_size, "Compression should reduce size"
        assert stats['total_messages'] > 0, "Messages should be stored"
        assert len(results) > 0, "Query should return results"

        print(f"\n[PASS] Integrated workflow successful")
        print(f"  [OK] Conversation creation")
        print(f"  [OK] Semantic compression")
        print(f"  [OK] Vector storage (ChromaDB)")
        print(f"  [OK] Semantic query")

        return True

    except Exception as e:
        print(f"[FAIL] Integrated workflow failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # 清理 (可能因为 ChromaDB 文件锁定而失败，这是正常的)
        try:
            if os.path.exists(test_dir):
                import time
                time.sleep(0.5)  # 给 ChromaDB 时间关闭文件
                shutil.rmtree(test_dir)
        except Exception:
            pass  # 忽略清理错误


def cleanup():
    """清理测试数据"""
    print("\n[CLEANUP] Removing test data")
    print("-" * 60)

    test_dirs = [
        "./test_data/phase3_integration",
        "./test_data/phase3_workflow"
    ]

    for test_dir in test_dirs:
        try:
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)
                print(f"  Removed: {test_dir}")
        except Exception as e:
            print(f"  [WARN] Could not remove {test_dir}: {e}")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Phase 3 Complete Integration Test")
    print("=" * 60)

    all_passed = True

    try:
        # Test 1: Embedding Service
        embedding_service = test_embedding_service()
        if embedding_service is None:
            all_passed = False

        # Test 2: Vector Store
        vector_store, test_dir, conv_id = test_vector_store()
        if vector_store is None:
            all_passed = False

        # Test 3: Semantic Compression
        compression_strategy = test_semantic_compression()
        if compression_strategy is None:
            all_passed = False

        # Test 4: Integrated Workflow
        workflow_success = test_integrated_workflow()
        if not workflow_success:
            all_passed = False

        # 结果
        print("\n" + "=" * 60)
        if all_passed:
            print("[SUCCESS] Phase 3 Integration - All tests passed!")
            print("=" * 60)
            print("\n[OK] Embedding Service")
            print("[OK] Vector Store Manager (ChromaDB)")
            print("[OK] Semantic Compression Strategy")
            print("[OK] Integrated Workflow")
            print("\nPhase 3 (Semantic Compression & Vector Store) completed successfully.")
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
        cleanup()


if __name__ == "__main__":
    sys.exit(main())
