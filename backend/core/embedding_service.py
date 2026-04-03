"""
Embedding Service - Phase 3.2

支持本地和 API 两种 embedding 方式

借鉴: LangChain 的 Embeddings 抽象
"""
from abc import ABC, abstractmethod
from typing import List, Optional
import logging
import numpy as np

logger = logging.getLogger(__name__)


class BaseEmbedding(ABC):
    """Embedding 基类"""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """
        文本转向量

        Args:
            text: 输入文本

        Returns:
            向量表示
        """
        pass

    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        批量文本转向量

        Args:
            texts: 文本列表

        Returns:
            向量列表
        """
        pass

    def get_embedding_dimension(self) -> int:
        """获取向量维度"""
        return len(self.embed_text("test"))


class SentenceTransformerEmbedding(BaseEmbedding):
    """本地 Sentence Transformer Embedding"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        使用 sentence-transformers

        Args:
            model_name: 模型名称
                - all-MiniLM-L6-v2: 384维，快速，英文
                - paraphrase-multilingual-MiniLM-L12-v2: 384维，多语言
                - paraphrase-multilingual-mpnet-base-v2: 768维，多语言，更好
        """
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
            self.model_name = model_name
            logger.info(f"SentenceTransformerEmbedding initialized: {model_name}")
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )

    def embed_text(self, text: str) -> List[float]:
        """文本转向量"""
        try:
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Failed to embed text: {e}", exc_info=True)
            raise

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        try:
            embeddings = self.model.encode(texts, convert_to_numpy=True)
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Failed to embed texts: {e}", exc_info=True)
            raise


class OpenAIEmbedding(BaseEmbedding):
    """OpenAI Embedding API"""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-ada-002",
        api_base: Optional[str] = None
    ):
        """
        初始化 OpenAI Embedding

        Args:
            api_key: OpenAI API key
            model: 模型名称 (text-embedding-ada-002, text-embedding-3-small, etc.)
            api_base: API base URL (可选，用于自定义端点)
        """
        self.api_key = api_key
        self.model = model
        self.api_base = api_base

        try:
            from openai import OpenAI
            client_kwargs = {"api_key": api_key}
            if api_base:
                client_kwargs["base_url"] = api_base

            self.client = OpenAI(**client_kwargs)
            logger.info(f"OpenAIEmbedding initialized: {model}")
        except ImportError:
            raise ImportError(
                "openai not installed. "
                "Install with: pip install openai"
            )

    def embed_text(self, text: str) -> List[float]:
        """文本转向量"""
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Failed to embed text with OpenAI: {e}", exc_info=True)
            raise

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts
            )
            return [data.embedding for data in response.data]
        except Exception as e:
            logger.error(f"Failed to embed texts with OpenAI: {e}", exc_info=True)
            raise


class MockEmbedding(BaseEmbedding):
    """Mock Embedding (用于测试)"""

    def __init__(self, dimension: int = 384):
        """
        初始化 Mock Embedding

        Args:
            dimension: 向量维度
        """
        self.dimension = dimension
        logger.info(f"MockEmbedding initialized: {dimension}D")

    def embed_text(self, text: str) -> List[float]:
        """文本转向量 (使用简单哈希)"""
        # 简单的 mock: 基于文本长度和哈希生成伪向量
        hash_val = hash(text)
        np.random.seed(hash_val % (2**31))
        embedding = np.random.randn(self.dimension)
        # 归一化
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        return [self.embed_text(text) for text in texts]

    def get_embedding_dimension(self) -> int:
        """获取向量维度"""
        return self.dimension


class EmbeddingService:
    """Embedding 服务管理"""

    def __init__(
        self,
        provider: str = "local",
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None
    ):
        """
        初始化服务

        Args:
            provider: "local", "openai", 或 "mock"
            model_name: 模型名称 (可选)
            api_key: API key (用于 openai provider)
            api_base: API base URL (可选)
        """
        self.provider = provider

        if provider == "local":
            # 本地 sentence-transformers
            model = model_name or "all-MiniLM-L6-v2"
            try:
                self.embedder = SentenceTransformerEmbedding(model)
            except ImportError:
                logger.warning("sentence-transformers not available, using mock")
                self.embedder = MockEmbedding()

        elif provider == "openai":
            # OpenAI API
            if not api_key:
                raise ValueError("api_key is required for OpenAI provider")
            model = model_name or "text-embedding-ada-002"
            self.embedder = OpenAIEmbedding(api_key, model, api_base)

        elif provider == "mock":
            # Mock (用于测试)
            dimension = int(model_name) if model_name and model_name.isdigit() else 384
            self.embedder = MockEmbedding(dimension)

        else:
            raise ValueError(f"Unknown provider: {provider}")

        logger.info(f"EmbeddingService initialized: provider={provider}")

    def embed(self, text: str) -> List[float]:
        """
        文本转向量

        Args:
            text: 输入文本

        Returns:
            向量表示
        """
        return self.embedder.embed_text(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量转换

        Args:
            texts: 文本列表

        Returns:
            向量列表
        """
        return self.embedder.embed_texts(texts)

    def get_dimension(self) -> int:
        """获取向量维度"""
        return self.embedder.get_embedding_dimension()

    def cosine_similarity(
        self,
        embedding1: List[float],
        embedding2: List[float]
    ) -> float:
        """
        计算余弦相似度

        Args:
            embedding1: 向量1
            embedding2: 向量2

        Returns:
            相似度 (0-1)
        """
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        # 归一化
        vec1_norm = vec1 / np.linalg.norm(vec1)
        vec2_norm = vec2 / np.linalg.norm(vec2)

        # 余弦相似度
        similarity = np.dot(vec1_norm, vec2_norm)

        return float(similarity)


# 全局实例
_embedding_service = None


def get_embedding_service(
    provider: str = "mock",
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None
) -> EmbeddingService:
    """
    获取 EmbeddingService 单例

    Args:
        provider: "local", "openai", 或 "mock"
        model_name: 模型名称
        api_key: API key (OpenAI)
        api_base: API base URL

    Returns:
        EmbeddingService 实例
    """
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            api_base=api_base
        )
    return _embedding_service
