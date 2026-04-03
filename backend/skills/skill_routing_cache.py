"""
Skill Routing Cache
===================
基于 ChromaDB 的 Skill 语义路由结果缓存。

缓存策略：
- Key：消息文本的 MD5 哈希（精确匹配）
- Value：路由结果 JSON + skill_set_version + 时间戳
- TTL：可配置（默认 24h）
- 版本失效：skill 热重载后自动清空旧版本缓存条目
- 降级：ChromaDB 不可用时自动退化为空缓存（get→None，put→静默忽略）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Dict, Optional

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    chromadb = None
    ChromaSettings = None

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "skill_routing_cache"


def _msg_hash(message: str) -> str:
    """返回消息文本的 MD5 十六进制字符串，用作 ChromaDB document ID。"""
    return hashlib.md5(message.encode("utf-8")).hexdigest()


class SkillRoutingCache:
    """
    Skill 路由结果的 ChromaDB 持久化缓存。

    线程安全性：ChromaDB PersistentClient 自身是线程安全的，
    单个 SkillRoutingCache 实例可跨线程共用。

    Usage:
        cache = SkillRoutingCache(db_path="./data/skill_routing_cache",
                                  skill_set_version="v1",
                                  ttl=86400)
        cache.put("用户消息", {"clickhouse-analyst": 0.9})
        result = cache.get("用户消息")  # → {"clickhouse-analyst": 0.9}
    """

    def __init__(
        self,
        db_path: str = "./data/skill_routing_cache",
        skill_set_version: str = "v1",
        ttl: int = 86400,
    ) -> None:
        self._db_path = db_path
        self._skill_set_version = skill_set_version
        self._ttl = ttl
        self._collection = None
        self._available = False
        self._init_chroma()

    def _init_chroma(self) -> None:
        if not CHROMA_AVAILABLE:
            logger.info(
                "[SkillRoutingCache] chromadb not installed, cache disabled (keyword fallback active)"
            )
            return
        try:
            os.makedirs(self._db_path, exist_ok=True)
            client = chromadb.PersistentClient(
                path=self._db_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            # 使用哑向量（dim=1）做纯 KV 存储；embedding_function=None 禁用自动向量化
            self._collection = client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"description": "Skill semantic routing results cache",
                          "hnsw:space": "cosine"},
                embedding_function=None,  # type: ignore[arg-type]
            )
            self._available = True
            logger.debug("[SkillRoutingCache] ChromaDB initialized at %s", self._db_path)
        except Exception as e:
            logger.warning("[SkillRoutingCache] ChromaDB init failed, cache disabled: %s", e)
            self._available = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, message: str) -> Optional[Dict[str, float]]:
        """
        查询消息的路由缓存结果。

        Returns:
            dict: {skill_name: score} 若命中且未过期，否则 None
        """
        if not self._available or self._collection is None:
            return None
        try:
            doc_id = _msg_hash(message)
            result = self._collection.get(ids=[doc_id], include=["metadatas"])
            if not result["ids"]:
                return None

            meta = result["metadatas"][0]

            # 版本校验
            if meta.get("skill_set_version") != self._skill_set_version:
                return None

            # TTL 校验
            created_at = float(meta.get("created_at", 0))
            if time.time() - created_at > self._ttl:
                # 清理过期条目
                try:
                    self._collection.delete(ids=[doc_id])
                except Exception:
                    pass
                return None

            routing_json = meta.get("routing_json", "{}")
            scores: Dict[str, float] = json.loads(routing_json)
            logger.debug("[SkillRoutingCache] hit: msg_hash=%s scores=%s", doc_id[:8], scores)
            return scores

        except Exception as e:
            logger.warning("[SkillRoutingCache] get error (ignored): %s", e)
            return None

    def put(self, message: str, routing: Dict[str, float]) -> None:
        """
        写入路由结果到缓存。

        ChromaDB 不可用时静默忽略，不抛出异常。
        """
        if not self._available or self._collection is None:
            return
        try:
            doc_id = _msg_hash(message)
            meta = {
                "routing_json": json.dumps(routing, ensure_ascii=False),
                "skill_set_version": self._skill_set_version,
                "created_at": str(time.time()),
                "message_preview": message[:100],
            }
            # upsert：已存在则覆盖
            # ChromaDB 要求提供 embeddings/documents/images/uris 之一；
            # 我们做纯 KV 查询（按 id 精确匹配），传入哑向量 [0.0] 满足 API 要求，
            # 向量维度固定为 1 且值不影响查询结果。
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[[0.0]],
                metadatas=[meta],
            )
            logger.debug("[SkillRoutingCache] put: msg_hash=%s routing=%s", doc_id[:8], routing)
        except Exception as e:
            logger.warning("[SkillRoutingCache] put error (ignored): %s", e)

    def update_version(self, new_version: str) -> None:
        """
        更新 skill_set_version。
        旧版本的缓存条目在 get() 时会因版本不匹配而自动失效。
        通常由 SkillLoader.reload_skills() 在热重载后调用。
        """
        self._skill_set_version = new_version
        logger.debug("[SkillRoutingCache] version updated to %s", new_version)

    def invalidate_all(self) -> None:
        """
        清空所有缓存条目（版本升级时调用）。
        ChromaDB 不支持 clear_all，通过删除+重建 collection 实现。
        """
        if not self._available or self._collection is None:
            return
        try:
            client = self._collection._client  # type: ignore[attr-defined]
            client.delete_collection(_COLLECTION_NAME)
            self._collection = client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"description": "Skill semantic routing results cache"},
                embedding_function=None,  # type: ignore[arg-type]
            )
            logger.info("[SkillRoutingCache] cache invalidated (collection recreated)")
        except Exception as e:
            logger.warning("[SkillRoutingCache] invalidate_all error (ignored): %s", e)

    @property
    def is_available(self) -> bool:
        return self._available
