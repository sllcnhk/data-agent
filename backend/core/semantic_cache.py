"""
Semantic Cache System - Phase 3.5

语义缓存系统 - 基于语义相似度的 LLM 响应缓存

功能:
- 语义相似度匹配
- 缓存命中/未命中跟踪
- TTL (Time-To-Live) 过期策略
- LRU (Least Recently Used) 淘汰策略
- 成本节省统计
"""
import logging
import time
import hashlib
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import OrderedDict

from backend.core.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""
    query: str
    response: str
    query_embedding: List[float]
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    ttl_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.ttl_seconds is None:
            return False
        age = (datetime.now() - self.created_at).total_seconds()
        return age > self.ttl_seconds

    def update_access(self):
        """更新访问信息"""
        self.last_accessed = datetime.now()
        self.access_count += 1


class SemanticCache:
    """语义缓存系统"""

    def __init__(
        self,
        embedding_service: Optional[EmbeddingService] = None,
        similarity_threshold: float = 0.85,
        max_cache_size: int = 1000,
        default_ttl: Optional[int] = 3600,  # 1 hour
        enable_lru: bool = True
    ):
        """
        初始化语义缓存

        Args:
            embedding_service: Embedding 服务（可选，默认 mock）
            similarity_threshold: 相似度阈值 (0-1，越高越严格)
            max_cache_size: 最大缓存条目数
            default_ttl: 默认 TTL（秒，None 表示永不过期）
            enable_lru: 是否启用 LRU 淘汰策略
        """
        self.embedding_service = embedding_service or EmbeddingService(provider="mock")
        self.similarity_threshold = similarity_threshold
        self.max_cache_size = max_cache_size
        self.default_ttl = default_ttl
        self.enable_lru = enable_lru

        # 缓存存储（使用 OrderedDict 实现 LRU）
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()

        # 统计信息
        self.stats = {
            "total_queries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "total_saved_tokens": 0,
            "total_saved_cost": 0.0,
            "avg_similarity": 0.0,
            "evictions": 0
        }

        logger.info(
            f"SemanticCache initialized: "
            f"threshold={similarity_threshold}, "
            f"max_size={max_cache_size}, "
            f"ttl={default_ttl}s"
        )

    def get(
        self,
        query: str,
        min_similarity: Optional[float] = None
    ) -> Optional[Tuple[str, float]]:
        """
        获取缓存的响应

        Args:
            query: 查询文本
            min_similarity: 最小相似度阈值（可选，覆盖默认值）

        Returns:
            (响应, 相似度) 或 None（未命中）
        """
        self.stats["total_queries"] += 1

        threshold = min_similarity if min_similarity is not None else self.similarity_threshold

        # 清理过期条目
        self._cleanup_expired()

        if not self.cache:
            self.stats["cache_misses"] += 1
            return None

        # 计算查询的 embedding
        try:
            query_embedding = self.embedding_service.embed(query)
        except Exception as e:
            logger.error(f"Failed to embed query: {e}")
            self.stats["cache_misses"] += 1
            return None

        # 查找最相似的缓存条目
        best_match = None
        best_similarity = 0.0

        for cache_id, entry in self.cache.items():
            # 跳过过期条目
            if entry.is_expired():
                continue

            # 计算相似度
            similarity = self.embedding_service.cosine_similarity(
                query_embedding, entry.query_embedding
            )

            if similarity >= threshold and similarity > best_similarity:
                best_similarity = similarity
                best_match = (cache_id, entry)

        # 处理结果
        if best_match:
            cache_id, entry = best_match

            # 更新访问信息
            entry.update_access()

            # LRU: 移到末尾（最近访问）
            if self.enable_lru:
                self.cache.move_to_end(cache_id)

            # 更新统计
            self.stats["cache_hits"] += 1
            self.stats["avg_similarity"] = (
                (self.stats["avg_similarity"] * (self.stats["cache_hits"] - 1) + best_similarity)
                / self.stats["cache_hits"]
            )

            logger.debug(
                f"Cache HIT: similarity={best_similarity:.4f}, "
                f"query='{query[:50]}...', "
                f"cached_query='{entry.query[:50]}...'"
            )

            return (entry.response, best_similarity)
        else:
            self.stats["cache_misses"] += 1
            logger.debug(f"Cache MISS: query='{query[:50]}...'")
            return None

    def set(
        self,
        query: str,
        response: str,
        ttl: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        设置缓存条目

        Args:
            query: 查询文本
            response: 响应文本
            ttl: TTL（秒，None 使用默认值）
            metadata: 额外元数据

        Returns:
            缓存 ID
        """
        # 生成缓存 ID
        cache_id = self._generate_cache_id(query)

        # 计算 embedding
        try:
            query_embedding = self.embedding_service.embed(query)
        except Exception as e:
            logger.error(f"Failed to embed query for caching: {e}")
            raise

        # 创建缓存条目
        entry = CacheEntry(
            query=query,
            response=response,
            query_embedding=query_embedding,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            ttl_seconds=ttl if ttl is not None else self.default_ttl,
            metadata=metadata or {}
        )

        # 检查缓存大小
        if len(self.cache) >= self.max_cache_size:
            self._evict_one()

        # 添加到缓存
        self.cache[cache_id] = entry

        # LRU: 移到末尾
        if self.enable_lru:
            self.cache.move_to_end(cache_id)

        logger.debug(f"Cache SET: id={cache_id}, query='{query[:50]}...'")

        return cache_id

    def invalidate(self, cache_id: str) -> bool:
        """
        使缓存条目失效

        Args:
            cache_id: 缓存 ID

        Returns:
            是否成功删除
        """
        if cache_id in self.cache:
            del self.cache[cache_id]
            logger.debug(f"Cache invalidated: {cache_id}")
            return True
        return False

    def clear(self):
        """清空缓存"""
        count = len(self.cache)
        self.cache.clear()
        logger.info(f"Cache cleared: {count} entries removed")

    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        hit_rate = (
            self.stats["cache_hits"] / self.stats["total_queries"]
            if self.stats["total_queries"] > 0 else 0.0
        )

        return {
            **self.stats,
            "cache_size": len(self.cache),
            "max_cache_size": self.max_cache_size,
            "hit_rate": hit_rate,
            "miss_rate": 1.0 - hit_rate
        }

    def estimate_savings(
        self,
        avg_query_tokens: int = 500,
        avg_response_tokens: int = 500,
        cost_per_1k_tokens: float = 0.01
    ) -> Dict[str, Any]:
        """
        估算节省的成本

        Args:
            avg_query_tokens: 平均查询 tokens
            avg_response_tokens: 平均响应 tokens
            cost_per_1k_tokens: 每 1K tokens 成本（美元）

        Returns:
            节省统计
        """
        total_tokens_per_request = avg_query_tokens + avg_response_tokens
        saved_tokens = self.stats["cache_hits"] * total_tokens_per_request
        saved_cost = (saved_tokens / 1000) * cost_per_1k_tokens

        return {
            "cache_hits": self.stats["cache_hits"],
            "saved_tokens": saved_tokens,
            "saved_cost_usd": saved_cost,
            "avg_tokens_per_hit": total_tokens_per_request,
            "hit_rate": self.get_stats()["hit_rate"]
        }

    def _cleanup_expired(self):
        """清理过期条目"""
        expired = [
            cache_id for cache_id, entry in self.cache.items()
            if entry.is_expired()
        ]

        for cache_id in expired:
            del self.cache[cache_id]

        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired entries")

    def _evict_one(self):
        """淘汰一个条目（LRU）"""
        if not self.cache:
            return

        if self.enable_lru:
            # LRU: 删除最旧的（第一个）
            cache_id, _ = self.cache.popitem(last=False)
        else:
            # 删除访问次数最少的
            min_access = min(entry.access_count for entry in self.cache.values())
            for cache_id, entry in self.cache.items():
                if entry.access_count == min_access:
                    del self.cache[cache_id]
                    break

        self.stats["evictions"] += 1
        logger.debug(f"Evicted cache entry: {cache_id}")

    def _generate_cache_id(self, query: str) -> str:
        """生成缓存 ID"""
        return hashlib.md5(query.encode()).hexdigest()

    def get_similar_queries(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.5
    ) -> List[Tuple[str, float]]:
        """
        获取相似的缓存查询

        Args:
            query: 查询文本
            top_k: 返回前 K 个
            min_similarity: 最小相似度

        Returns:
            [(查询, 相似度), ...]
        """
        try:
            query_embedding = self.embedding_service.embed(query)
        except Exception as e:
            logger.error(f"Failed to embed query: {e}")
            return []

        similarities = []
        for entry in self.cache.values():
            if entry.is_expired():
                continue

            similarity = self.embedding_service.cosine_similarity(
                query_embedding, entry.query_embedding
            )

            if similarity >= min_similarity:
                similarities.append((entry.query, similarity))

        # 排序并返回 top K
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]

    def export_cache(self) -> List[Dict[str, Any]]:
        """
        导出缓存数据

        Returns:
            缓存条目列表
        """
        return [
            {
                "query": entry.query,
                "response": entry.response,
                "created_at": entry.created_at.isoformat(),
                "last_accessed": entry.last_accessed.isoformat(),
                "access_count": entry.access_count,
                "ttl_seconds": entry.ttl_seconds,
                "metadata": entry.metadata
            }
            for entry in self.cache.values()
            if not entry.is_expired()
        ]

    def import_cache(self, cache_data: List[Dict[str, Any]]) -> int:
        """
        导入缓存数据

        Args:
            cache_data: 缓存条目列表

        Returns:
            导入的条目数
        """
        count = 0
        for data in cache_data:
            try:
                self.set(
                    query=data["query"],
                    response=data["response"],
                    ttl=data.get("ttl_seconds"),
                    metadata=data.get("metadata", {})
                )
                count += 1
            except Exception as e:
                logger.error(f"Failed to import cache entry: {e}")

        logger.info(f"Imported {count}/{len(cache_data)} cache entries")
        return count


class SemanticCacheDecorator:
    """语义缓存装饰器"""

    def __init__(
        self,
        cache: SemanticCache,
        min_similarity: Optional[float] = None
    ):
        """
        初始化装饰器

        Args:
            cache: 语义缓存实例
            min_similarity: 最小相似度阈值
        """
        self.cache = cache
        self.min_similarity = min_similarity

    def __call__(self, func):
        """
        装饰器函数

        Args:
            func: 被装饰的函数（接受 query: str，返回 str）

        Returns:
            装饰后的函数
        """
        def wrapper(query: str, *args, **kwargs) -> str:
            # 尝试从缓存获取
            cached = self.cache.get(query, min_similarity=self.min_similarity)

            if cached:
                response, similarity = cached
                logger.info(f"Cache hit (similarity={similarity:.4f})")
                return response

            # 缓存未命中，调用原函数
            logger.info("Cache miss, calling original function")
            response = func(query, *args, **kwargs)

            # 缓存响应
            self.cache.set(query, response)

            return response

        return wrapper


# 全局单例
_semantic_cache = None


def get_semantic_cache(
    embedding_service: Optional[EmbeddingService] = None,
    **kwargs
) -> SemanticCache:
    """
    获取语义缓存单例

    Args:
        embedding_service: Embedding 服务
        **kwargs: 其他参数

    Returns:
        SemanticCache 实例
    """
    global _semantic_cache

    if _semantic_cache is None:
        _semantic_cache = SemanticCache(
            embedding_service=embedding_service,
            **kwargs
        )

    return _semantic_cache
