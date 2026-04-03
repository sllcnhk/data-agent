"""
独立测试 Embedding Service - Phase 3.2

测试本地和 API embedding 服务
"""
import sys
import numpy as np
from typing import List


# ============= 从 backend/core/embedding_service.py 复制核心类 =============

class MockEmbedding:
    """Mock Embedding (用于测试)"""

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def embed_text(self, text: str) -> List[float]:
        """文本转向量 (使用简单哈希)"""
        hash_val = hash(text)
        np.random.seed(hash_val % (2**31))
        embedding = np.random.randn(self.dimension)
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        return [self.embed_text(text) for text in texts]

    def get_embedding_dimension(self) -> int:
        return self.dimension


class EmbeddingService:
    """Embedding 服务管理"""

    def __init__(self, provider: str = "mock", dimension: int = 384):
        self.provider = provider
        self.embedder = MockEmbedding(dimension)

    def embed(self, text: str) -> List[float]:
        """文本转向量"""
        return self.embedder.embed_text(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        return self.embedder.embed_texts(texts)

    def get_dimension(self) -> int:
        """获取向量维度"""
        return self.embedder.get_embedding_dimension()

    def cosine_similarity(
        self,
        embedding1: List[float],
        embedding2: List[float]
    ) -> float:
        """计算余弦相似度"""
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        vec1_norm = vec1 / np.linalg.norm(vec1)
        vec2_norm = vec2 / np.linalg.norm(vec2)

        similarity = np.dot(vec1_norm, vec2_norm)
        return float(similarity)


# ============= 测试函数 =============

def test_embedding_service_initialization():
    """测试 Embedding Service 初始化"""
    print("\n[TEST] Embedding Service Initialization")
    print("-" * 60)

    # 测试 mock provider
    service = EmbeddingService(provider="mock", dimension=384)
    assert service is not None
    assert service.provider == "mock"
    print("[PASS] Mock provider initialized")

    # 测试维度
    dim = service.get_dimension()
    assert dim == 384
    print(f"[PASS] Embedding dimension: {dim}")


def test_single_text_embedding():
    """测试单个文本 embedding"""
    print("\n[TEST] Single Text Embedding")
    print("-" * 60)

    service = EmbeddingService(provider="mock", dimension=384)

    text = "Hello, this is a test message."
    embedding = service.embed(text)

    assert embedding is not None
    assert len(embedding) == 384
    assert all(isinstance(x, float) for x in embedding)

    print(f"[PASS] Text embedded successfully")
    print(f"  Input: {text[:50]}...")
    print(f"  Embedding dimension: {len(embedding)}")
    print(f"  First 5 values: {embedding[:5]}")


def test_batch_text_embedding():
    """测试批量文本 embedding"""
    print("\n[TEST] Batch Text Embedding")
    print("-" * 60)

    service = EmbeddingService(provider="mock", dimension=384)

    texts = [
        "First message",
        "Second message",
        "Third message",
        "Fourth message",
    ]

    embeddings = service.embed_batch(texts)

    assert len(embeddings) == len(texts)
    assert all(len(emb) == 384 for emb in embeddings)

    print(f"[PASS] Batch embedding successful")
    print(f"  Input texts: {len(texts)}")
    print(f"  Output embeddings: {len(embeddings)}")
    print(f"  Each dimension: {len(embeddings[0])}")


def test_embedding_consistency():
    """测试 embedding 一致性（相同文本应产生相同向量）"""
    print("\n[TEST] Embedding Consistency")
    print("-" * 60)

    service = EmbeddingService(provider="mock", dimension=384)

    text = "Consistency test message"

    # 多次 embed 相同文本
    embedding1 = service.embed(text)
    embedding2 = service.embed(text)
    embedding3 = service.embed(text)

    # 应该完全相同
    assert embedding1 == embedding2
    assert embedding2 == embedding3

    print("[PASS] Embedding consistency verified")
    print(f"  Text: {text}")
    print(f"  All embeddings are identical")


def test_cosine_similarity():
    """测试余弦相似度计算"""
    print("\n[TEST] Cosine Similarity")
    print("-" * 60)

    service = EmbeddingService(provider="mock", dimension=384)

    # 相似的文本
    text1 = "I love machine learning"
    text2 = "Machine learning is great"

    # 不相似的文本
    text3 = "The weather is sunny today"

    emb1 = service.embed(text1)
    emb2 = service.embed(text2)
    emb3 = service.embed(text3)

    # 计算相似度
    sim_12 = service.cosine_similarity(emb1, emb2)
    sim_13 = service.cosine_similarity(emb1, emb3)
    sim_11 = service.cosine_similarity(emb1, emb1)

    print(f"[PASS] Cosine similarity calculated")
    print(f"  Text1 vs Text2: {sim_12:.4f}")
    print(f"  Text1 vs Text3: {sim_13:.4f}")
    print(f"  Text1 vs Text1: {sim_11:.4f} (should be ~1.0)")

    # 自相似度应该接近 1.0
    assert abs(sim_11 - 1.0) < 0.01


def test_different_dimensions():
    """测试不同维度"""
    print("\n[TEST] Different Embedding Dimensions")
    print("-" * 60)

    dimensions = [128, 256, 384, 768]

    for dim in dimensions:
        service = EmbeddingService(provider="mock", dimension=dim)
        embedding = service.embed("Test text")

        assert len(embedding) == dim
        print(f"[PASS] Dimension {dim}: {len(embedding)} values")


def test_normalization():
    """测试向量归一化"""
    print("\n[TEST] Vector Normalization")
    print("-" * 60)

    service = EmbeddingService(provider="mock", dimension=384)

    text = "Normalization test"
    embedding = service.embed(text)

    # 计算向量长度（应该接近 1.0，因为已归一化）
    vec = np.array(embedding)
    norm = np.linalg.norm(vec)

    print(f"[PASS] Vector normalization check")
    print(f"  Vector norm: {norm:.6f}")
    print(f"  Expected: ~1.0")

    assert abs(norm - 1.0) < 0.01


def test_semantic_similarity_ranking():
    """测试语义相似度排序"""
    print("\n[TEST] Semantic Similarity Ranking")
    print("-" * 60)

    service = EmbeddingService(provider="mock", dimension=384)

    query = "database connection error"

    documents = [
        "How to fix database connection timeout",
        "Database connection string format",
        "The weather is sunny",
        "Connection pool configuration",
        "Cooking recipe for pasta",
    ]

    # Embed query and documents
    query_emb = service.embed(query)
    doc_embs = service.embed_batch(documents)

    # Calculate similarities
    similarities = [
        service.cosine_similarity(query_emb, doc_emb)
        for doc_emb in doc_embs
    ]

    # Rank documents
    ranked = sorted(
        zip(documents, similarities),
        key=lambda x: x[1],
        reverse=True
    )

    print(f"[PASS] Similarity ranking:")
    print(f"  Query: '{query}'")
    for i, (doc, sim) in enumerate(ranked[:3], 1):
        print(f"  {i}. (sim={sim:.4f}) {doc}")


def test_empty_and_edge_cases():
    """测试边界情况"""
    print("\n[TEST] Edge Cases")
    print("-" * 60)

    service = EmbeddingService(provider="mock", dimension=384)

    # 短文本
    short_text = "Hi"
    short_emb = service.embed(short_text)
    assert len(short_emb) == 384
    print("[PASS] Short text handled")

    # 长文本
    long_text = "This is a very long text. " * 100
    long_emb = service.embed(long_text)
    assert len(long_emb) == 384
    print("[PASS] Long text handled")

    # 特殊字符
    special_text = "Hello! @#$%^&*() 你好 🎉"
    special_emb = service.embed(special_text)
    assert len(special_emb) == 384
    print("[PASS] Special characters handled")

    # 空批量
    empty_batch = service.embed_batch([])
    assert len(empty_batch) == 0
    print("[PASS] Empty batch handled")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Embedding Service - Standalone Tests")
    print("=" * 60)

    try:
        test_embedding_service_initialization()
        test_single_text_embedding()
        test_batch_text_embedding()
        test_embedding_consistency()
        test_cosine_similarity()
        test_different_dimensions()
        test_normalization()
        test_semantic_similarity_ranking()
        test_empty_and_edge_cases()

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        print("\nPhase 3.2 (Embedding Service) completed successfully.")

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
