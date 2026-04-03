# Context Management Phase 2 & 3 实施计划

**项目**: Data-Agent Context Management Optimization
**阶段**: Phase 2 (Token Budget) + Phase 3 (Semantic Compression)
**时间**: 2 周（Week 2-3）
**日期**: 2026-02-05

---

## 执行摘要

基于 Phase 1 的成功实施（配置统一、Token 计数、智能压缩），现在实施 Phase 2 和 Phase 3，引入：
- **Phase 2**: Token Budget 管理、自适应策略、统一 API
- **Phase 3**: 向量存储、语义压缩、语义缓存、RAG 能力

**参考的主流项目**:
- LangChain (Context Management)
- LlamaIndex (Vector Store + RAG)
- Semantic Kernel (Semantic Memory)
- ChromaDB (Vector Database)
- Redis (Caching)

---

## Phase 2: Token Budget Manager + 自适应策略 (Week 2)

### 2.1 Token Budget Manager

**目标**: 精确管理对话的 token 预算，防止超限

**借鉴**: LangChain的 ConversationTokenBufferMemory

**实现**:

#### A. Token Budget 计算器
```python
class TokenBudgetCalculator:
    """Token 预算计算器"""

    def __init__(self, model: str = "claude-sonnet-4-5"):
        self.model = model
        self.limits = {
            "claude-sonnet-4-5": {
                "context_window": 200000,
                "max_output": 8192,
                "reserved_for_output": 8192,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05  # 5% 安全边距
            },
            "gpt-4-turbo": {
                "context_window": 128000,
                "max_output": 4096,
                "reserved_for_output": 4096,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05
            }
        }

    def calculate_available_tokens(
        self,
        model: str,
        system_prompt_tokens: int,
        current_message_tokens: int
    ) -> int:
        """计算可用的上下文 token 数"""
        config = self.limits.get(model, self.limits["claude-sonnet-4-5"])

        context_window = config["context_window"]
        reserved_output = config["reserved_for_output"]
        safety_margin = int(context_window * config["safety_margin"])

        available = (
            context_window
            - reserved_output
            - system_prompt_tokens
            - current_message_tokens
            - safety_margin
        )

        return max(0, available)

    def estimate_compression_needed(
        self,
        current_tokens: int,
        available_tokens: int
    ) -> float:
        """估算需要的压缩率"""
        if current_tokens <= available_tokens:
            return 0.0  # 不需要压缩

        compression_ratio = 1 - (available_tokens / current_tokens)
        return min(compression_ratio, 0.95)  # 最多压缩 95%
```

#### B. Token Budget Manager
```python
class TokenBudgetManager:
    """Token 预算管理器"""

    def __init__(self):
        self.calculator = TokenBudgetCalculator()
        self.token_counter = get_token_counter()

    def create_budget(
        self,
        model: str,
        system_prompt: str,
        current_message: str
    ) -> Dict[str, Any]:
        """创建 token 预算"""

        # 计算各部分 token
        system_tokens = self.token_counter.count_tokens(system_prompt, model)
        message_tokens = self.token_counter.count_tokens(current_message, model)

        # 计算可用 token
        available_tokens = self.calculator.calculate_available_tokens(
            model, system_tokens, message_tokens
        )

        return {
            "model": model,
            "system_tokens": system_tokens,
            "message_tokens": message_tokens,
            "available_for_history": available_tokens,
            "recommended_max_messages": self._estimate_max_messages(available_tokens),
            "compression_strategy": self._recommend_strategy(available_tokens)
        }

    def _estimate_max_messages(self, available_tokens: int) -> int:
        """估算最大消息数"""
        # 假设平均每条消息 200 tokens
        avg_tokens_per_message = 200
        return int(available_tokens / avg_tokens_per_message)

    def _recommend_strategy(self, available_tokens: int) -> str:
        """推荐压缩策略"""
        if available_tokens > 100000:
            return "full"  # 充足空间，不压缩
        elif available_tokens > 50000:
            return "sliding_window"  # 中等空间，滑动窗口
        else:
            return "smart"  # 空间紧张，智能压缩
```

**文件**: `backend/core/token_budget.py`

---

### 2.2 动态压缩率调整

**目标**: 根据实际 token 使用情况动态调整压缩强度

**借鉴**: LangChain的动态 memory 管理

**实现**:

```python
class DynamicCompressionAdjuster:
    """动态压缩调整器"""

    def __init__(self, target_utilization: float = 0.75):
        self.target_utilization = target_utilization
        self.history = []  # 历史压缩记录

    def adjust_compression_params(
        self,
        current_tokens: int,
        available_tokens: int,
        strategy_name: str
    ) -> Dict[str, Any]:
        """调整压缩参数"""

        utilization = current_tokens / available_tokens if available_tokens > 0 else 1.0

        # 记录历史
        self.history.append({
            "tokens": current_tokens,
            "available": available_tokens,
            "utilization": utilization
        })

        # 根据利用率调整
        if utilization > self.target_utilization:
            # 需要更激进的压缩
            if strategy_name == "sliding_window":
                return {
                    "strategy": "smart",
                    "keep_first": 2,
                    "keep_last": 8  # 减少保留
                }
            elif strategy_name == "smart":
                return {
                    "strategy": "smart",
                    "keep_first": 1,
                    "keep_last": 5  # 更激进
                }
        else:
            # 可以放松压缩
            if strategy_name == "smart":
                return {
                    "strategy": "smart",
                    "keep_first": 3,
                    "keep_last": 15  # 保留更多
                }

        # 默认不调整
        return {"strategy": strategy_name}
```

**文件**: `backend/core/dynamic_compression.py`

---

### 2.3 自适应策略选择器

**目标**: 根据对话特征自动选择最优压缩策略

**借鉴**: LlamaIndex的智能索引选择

**实现**:

```python
class AdaptiveStrategySelector:
    """自适应策略选择器"""

    def __init__(self):
        self.token_budget_manager = TokenBudgetManager()
        self.dynamic_adjuster = DynamicCompressionAdjuster()

    def select_strategy(
        self,
        conversation: UnifiedConversation,
        model: str,
        system_prompt: str,
        current_message: str
    ) -> Tuple[str, Dict[str, Any]]:
        """
        自动选择最优策略

        Returns:
            (strategy_name, strategy_params)
        """

        # 1. 计算 token 预算
        budget = self.token_budget_manager.create_budget(
            model, system_prompt, current_message
        )

        # 2. 分析对话特征
        features = self._analyze_conversation(conversation)

        # 3. 选择策略
        strategy = self._select_based_on_features(budget, features)

        # 4. 动态调整
        params = self.dynamic_adjuster.adjust_compression_params(
            features["total_tokens"],
            budget["available_for_history"],
            strategy
        )

        return strategy, params

    def _analyze_conversation(self, conversation: UnifiedConversation) -> Dict:
        """分析对话特征"""
        messages = conversation.messages

        # 计算统计信息
        total_messages = len(messages)
        total_tokens = sum(msg.metadata.get("tokens", 0) for msg in messages)
        avg_tokens_per_message = total_tokens / total_messages if total_messages > 0 else 0

        # 检测对话类型
        has_code = any("```" in msg.content for msg in messages)
        has_long_messages = any(len(msg.content) > 2000 for msg in messages)
        is_technical = self._is_technical_conversation(messages)

        return {
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "avg_tokens_per_message": avg_tokens_per_message,
            "has_code": has_code,
            "has_long_messages": has_long_messages,
            "is_technical": is_technical
        }

    def _select_based_on_features(
        self,
        budget: Dict,
        features: Dict
    ) -> str:
        """基于特征选择策略"""

        available = budget["available_for_history"]
        total_tokens = features["total_tokens"]

        # 规则 1: 空间充足
        if total_tokens < available * 0.5:
            return "full"

        # 规则 2: 技术对话且有代码
        if features["is_technical"] and features["has_code"]:
            return "smart"  # 保留代码上下文

        # 规则 3: 消息很多但token不多
        if features["total_messages"] > 50 and features["avg_tokens_per_message"] < 100:
            return "sliding_window"  # 简单滑动

        # 规则 4: 默认智能压缩
        return "smart"

    def _is_technical_conversation(self, messages: List) -> bool:
        """判断是否为技术对话"""
        technical_keywords = [
            "function", "class", "import", "def", "async",
            "error", "bug", "debug", "api", "database"
        ]

        technical_count = 0
        for msg in messages[-10:]:  # 检查最近10条
            content_lower = msg.content.lower()
            if any(kw in content_lower for kw in technical_keywords):
                technical_count += 1

        return technical_count >= 3
```

**文件**: `backend/core/adaptive_strategy.py`

---

### 2.4 统一 Context API 入口

**目标**: 提供简洁的 API，隐藏复杂性

**借鉴**: Semantic Kernel的统一接口设计

**实现**:

```python
class UnifiedContextManager:
    """统一上下文管理器 - 对外的简洁 API"""

    def __init__(self):
        self.hybrid_manager = HybridContextManager()
        self.token_budget_manager = TokenBudgetManager()
        self.adaptive_selector = AdaptiveStrategySelector()
        self.token_counter = get_token_counter()

    def prepare_context(
        self,
        conversation_id: str,
        model: str,
        system_prompt: str,
        current_message: str,
        max_tokens: Optional[int] = None,
        strategy: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        准备对话上下文 - 统一入口

        Args:
            conversation_id: 对话ID
            model: 模型名称
            system_prompt: 系统提示
            current_message: 当前消息
            max_tokens: 最大token（可选）
            strategy: 指定策略（可选，默认自动选择）

        Returns:
            {
                "messages": [...],
                "system_prompt": "...",
                "context_info": {...},
                "budget_info": {...}
            }
        """

        # 1. 获取对话
        with get_db_context() as db:
            service = ConversationService(db)
            conversation = service.get_conversation(conversation_id)
            messages = service.get_messages(conversation_id, limit=10000)

        # 2. 转换为统一格式
        unified_conv = self._to_unified_conversation(conversation, messages)

        # 3. 创建 token 预算
        budget = self.token_budget_manager.create_budget(
            model, system_prompt, current_message
        )

        # 4. 选择策略（自动或手动）
        if strategy is None:
            strategy, params = self.adaptive_selector.select_strategy(
                unified_conv, model, system_prompt, current_message
            )
        else:
            params = {}

        # 5. 应用压缩
        self.hybrid_manager.set_strategy(strategy)
        compressed_conv = self.hybrid_manager.compress_conversation(
            unified_conv, **params
        )

        # 6. 构建返回
        return {
            "messages": [
                {
                    "role": msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
                    "content": msg.content
                }
                for msg in compressed_conv.messages
            ],
            "system_prompt": system_prompt,
            "context_info": {
                "strategy": strategy,
                "original_message_count": len(messages),
                "compressed_message_count": len(compressed_conv.messages),
                "compression_ratio": 1 - len(compressed_conv.messages) / len(messages) if messages else 0,
                "estimated_tokens": self.token_counter.estimate_conversation_tokens(
                    system_prompt, compressed_conv.messages, model
                )
            },
            "budget_info": budget
        }

    def _to_unified_conversation(self, conversation, messages):
        """转换为统一格式"""
        unified_conv = UnifiedConversation(
            conversation_id=str(conversation.id),
            title=conversation.title,
            system_prompt=conversation.metadata.get("system_prompt", "")
        )

        for msg in messages:
            unified_msg = UnifiedMessage(
                role=MessageRole(msg.role),
                content=msg.content,
                metadata={"tokens": msg.total_tokens or 0}
            )
            unified_conv.add_message(unified_msg)

        return unified_conv
```

**文件**: `backend/core/unified_context.py`

---

### 2.5 Phase 2 集成测试

**测试文件**: `backend/tests/test_phase2_integration.py`

```python
import pytest
from backend.core.token_budget import TokenBudgetManager
from backend.core.adaptive_strategy import AdaptiveStrategySelector
from backend.core.unified_context import UnifiedContextManager

class TestPhase2Integration:

    def test_token_budget_calculation(self):
        """测试 token 预算计算"""
        manager = TokenBudgetManager()

        budget = manager.create_budget(
            model="claude-sonnet-4-5",
            system_prompt="You are a helpful assistant.",
            current_message="Hello, how are you?"
        )

        assert budget["available_for_history"] > 0
        assert budget["recommended_max_messages"] > 0
        assert budget["compression_strategy"] in ["full", "sliding_window", "smart"]

    def test_adaptive_strategy_selection(self):
        """测试自适应策略选择"""
        selector = AdaptiveStrategySelector()

        # 创建测试对话
        conversation = self._create_test_conversation(50)

        strategy, params = selector.select_strategy(
            conversation,
            model="claude-sonnet-4-5",
            system_prompt="Test",
            current_message="Test"
        )

        assert strategy in ["full", "sliding_window", "smart", "semantic"]
        assert isinstance(params, dict)

    def test_unified_context_api(self):
        """测试统一 API"""
        manager = UnifiedContextManager()

        # 需要真实的对话ID（集成测试）
        # 这里使用 mock
        pass

    def _create_test_conversation(self, message_count: int):
        """创建测试对话"""
        from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole

        conv = UnifiedConversation()
        for i in range(message_count):
            role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
            msg = UnifiedMessage(
                role=role,
                content=f"Test message {i}",
                metadata={"tokens": 20}
            )
            conv.add_message(msg)

        return conv
```

---

## Phase 3: 向量存储 + 语义压缩 (Week 3)

### 3.1 向量数据库集成 (Chroma)

**目标**: 集成 ChromaDB 作为向量存储

**借鉴**: LlamaIndex + ChromaDB 最佳实践

**为什么选择 Chroma**:
- ✅ 轻量级，易于集成
- ✅ 支持本地和服务器模式
- ✅ Python 原生支持
- ✅ 自动持久化
- ✅ 丰富的查询功能

**安装**:
```bash
pip install chromadb==0.4.22
```

**实现**:

```python
import chromadb
from chromadb.config import Settings
from typing import List, Dict, Optional

class VectorStoreManager:
    """向量存储管理器"""

    def __init__(self, persist_directory: str = "./data/chroma"):
        """
        初始化 Chroma 客户端

        Args:
            persist_directory: 持久化目录
        """
        self.client = chromadb.Client(Settings(
            persist_directory=persist_directory,
            anonymized_telemetry=False
        ))

        # 创建或获取集合
        self.collection = self.client.get_or_create_collection(
            name="conversation_messages",
            metadata={"description": "对话消息的语义索引"}
        )

    def add_messages(
        self,
        messages: List[Dict],
        conversation_id: str
    ):
        """
        添加消息到向量存储

        Args:
            messages: 消息列表
            conversation_id: 对话ID
        """
        if not messages:
            return

        # 准备数据
        ids = [f"{conversation_id}_{msg['id']}" for msg in messages]
        documents = [msg['content'] for msg in messages]
        metadatas = [
            {
                "conversation_id": conversation_id,
                "role": msg["role"],
                "created_at": msg["created_at"],
                "tokens": msg.get("tokens", 0)
            }
            for msg in messages
        ]

        # 添加到集合（Chroma 会自动生成 embeddings）
        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )

    def query_similar(
        self,
        query_text: str,
        conversation_id: str,
        n_results: int = 5
    ) -> List[Dict]:
        """
        查询语义相似的消息

        Args:
            query_text: 查询文本
            conversation_id: 对话ID
            n_results: 返回结果数

        Returns:
            相似消息列表
        """
        results = self.collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where={"conversation_id": conversation_id}
        )

        # 格式化结果
        similar_messages = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                similar_messages.append({
                    "content": doc,
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i] if "distances" in results else None
                })

        return similar_messages

    def delete_conversation(self, conversation_id: str):
        """删除对话的所有向量"""
        self.collection.delete(
            where={"conversation_id": conversation_id}
        )
```

**文件**: `backend/core/vector_store.py`

---

### 3.2 Embedding 服务

**目标**: 支持本地和 API 两种 embedding 方式

**借鉴**: LangChain的 Embeddings 抽象

**实现**:

```python
from abc import ABC, abstractmethod
from typing import List
import numpy as np

class BaseEmbedding(ABC):
    """Embedding 基类"""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """文本转向量"""
        pass

    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量文本转向量"""
        pass

class SentenceTransformerEmbedding(BaseEmbedding):
    """本地 Sentence Transformer Embedding"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        使用 sentence-transformers

        Args:
            model_name: 模型名称
                - all-MiniLM-L6-v2: 384维，快速，英文
                - paraphrase-multilingual-MiniLM-L12-v2: 384维，多语言
                - text-embedding-ada-002: OpenAI（需要API）
        """
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
        except ImportError:
            raise ImportError(
                "请安装 sentence-transformers: "
                "pip install sentence-transformers"
            )

    def embed_text(self, text: str) -> List[float]:
        """文本转向量"""
        embedding = self.model.encode(text)
        return embedding.tolist()

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        embeddings = self.model.encode(texts)
        return embeddings.tolist()

class OpenAIEmbedding(BaseEmbedding):
    """OpenAI Embedding API"""

    def __init__(self, api_key: str, model: str = "text-embedding-ada-002"):
        self.api_key = api_key
        self.model = model

        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
        except ImportError:
            raise ImportError("请安装 openai: pip install openai")

    def embed_text(self, text: str) -> List[float]:
        """文本转向量"""
        response = self.client.embeddings.create(
            model=self.model,
            input=text
        )
        return response.data[0].embedding

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        response = self.client.embeddings.create(
            model=self.model,
            input=texts
        )
        return [data.embedding for data in response.data]

class EmbeddingService:
    """Embedding 服务管理"""

    def __init__(self, provider: str = "local"):
        """
        初始化服务

        Args:
            provider: "local" 或 "openai"
        """
        if provider == "local":
            self.embedder = SentenceTransformerEmbedding()
        elif provider == "openai":
            from backend.config.settings import settings
            self.embedder = OpenAIEmbedding(settings.openai_api_key)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def embed(self, text: str) -> List[float]:
        """文本转向量"""
        return self.embedder.embed_text(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量转换"""
        return self.embedder.embed_texts(texts)
```

**文件**: `backend/core/embedding_service.py`

---

### 3.3 真正的 Semantic Compression 策略

**目标**: 基于语义相似度的智能压缩

**借鉴**: LlamaIndex的语义检索

**实现**:

```python
from typing import List
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage
from backend.core.vector_store import VectorStoreManager
from backend.core.embedding_service import EmbeddingService

class SemanticCompressionStrategy(BaseContextStrategy):
    """语义压缩策略 - 真正的实现"""

    def __init__(
        self,
        keep_first: int = 2,
        keep_last: int = 10,
        similarity_threshold: float = 0.7
    ):
        self.keep_first = keep_first
        self.keep_last = keep_last
        self.similarity_threshold = similarity_threshold

        self.vector_store = VectorStoreManager()
        self.embedding_service = EmbeddingService(provider="local")

    def compress(
        self,
        conversation: UnifiedConversation
    ) -> UnifiedConversation:
        """
        语义压缩

        策略:
        1. 保留最前面的 N 条消息（上下文建立）
        2. 保留最后的 M 条消息（最近对话）
        3. 中间消息：
           - 计算与当前查询的语义相似度
           - 保留高相关性的消息
           - 低相关性的消息生成摘要
        """
        messages = conversation.messages

        if len(messages) <= (self.keep_first + self.keep_last):
            return conversation  # 无需压缩

        # 1. 提取最近的消息作为查询上下文
        recent_context = " ".join([
            msg.content for msg in messages[-self.keep_last:]
        ])

        # 2. 分段
        first_messages = messages[:self.keep_first]
        middle_messages = messages[self.keep_first:-self.keep_last]
        last_messages = messages[-self.keep_last:]

        # 3. 对中间消息进行语义筛选
        if middle_messages:
            relevant_messages = self._select_relevant_messages(
                middle_messages,
                recent_context
            )

            # 4. 生成摘要
            if len(relevant_messages) < len(middle_messages):
                summary_message = self._create_summary_message(
                    middle_messages,
                    relevant_messages
                )
                selected_middle = relevant_messages + [summary_message]
            else:
                selected_middle = relevant_messages
        else:
            selected_middle = []

        # 5. 组合
        compressed = UnifiedConversation(
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            system_prompt=conversation.system_prompt
        )

        for msg in first_messages + selected_middle + last_messages:
            compressed.add_message(msg)

        return compressed

    def _select_relevant_messages(
        self,
        messages: List[UnifiedMessage],
        query_context: str
    ) -> List[UnifiedMessage]:
        """选择相关的消息"""

        # 计算查询的 embedding
        query_embedding = self.embedding_service.embed(query_context)

        # 计算每条消息的 embedding 和相似度
        message_scores = []
        for msg in messages:
            msg_embedding = self.embedding_service.embed(msg.content)
            similarity = self._cosine_similarity(query_embedding, msg_embedding)
            message_scores.append((msg, similarity))

        # 筛选高相关性消息
        relevant = [
            msg for msg, score in message_scores
            if score >= self.similarity_threshold
        ]

        # 如果太少，至少保留前50%
        if len(relevant) < len(messages) * 0.5:
            sorted_messages = sorted(message_scores, key=lambda x: x[1], reverse=True)
            relevant = [msg for msg, _ in sorted_messages[:len(messages) // 2]]

        return relevant

    def _cosine_similarity(
        self,
        vec1: List[float],
        vec2: List[float]
    ) -> float:
        """计算余弦相似度"""
        import numpy as np

        v1 = np.array(vec1)
        v2 = np.array(vec2)

        dot_product = np.dot(v1, v2)
        norm_v1 = np.linalg.norm(v1)
        norm_v2 = np.linalg.norm(v2)

        if norm_v1 == 0 or norm_v2 == 0:
            return 0.0

        return dot_product / (norm_v1 * norm_v2)

    def _create_summary_message(
        self,
        all_messages: List[UnifiedMessage],
        kept_messages: List[UnifiedMessage]
    ) -> UnifiedMessage:
        """创建摘要消息"""

        # 找出被移除的消息
        kept_ids = {id(msg) for msg in kept_messages}
        removed = [msg for msg in all_messages if id(msg) not in kept_ids]

        if not removed:
            return None

        # 生成摘要
        summary_parts = []
        for msg in removed:
            role = msg.role.value if hasattr(msg.role, 'value') else str(msg.role)
            summary_parts.append(f"[{role}]: {msg.content[:100]}...")

        summary_content = (
            f"[Semantic Compression Summary: {len(removed)} messages with low relevance]\n"
            + "\n".join(summary_parts[:5])  # 最多显示5条
        )

        return UnifiedMessage(
            role=MessageRole.SYSTEM,
            content=summary_content
        )
```

**文件**: `backend/core/context_manager.py` (更新)

---

### 3.4 自动向量化和索引管理

**目标**: 消息保存时自动向量化

**实现**:

```python
class AutoVectorizer:
    """自动向量化管理器"""

    def __init__(self):
        self.vector_store = VectorStoreManager()
        self.embedding_service = EmbeddingService()

    def index_message(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: str,
        tokens: int = 0
    ):
        """索引单条消息"""

        self.vector_store.add_messages(
            messages=[{
                "id": message_id,
                "role": role,
                "content": content,
                "created_at": created_at,
                "tokens": tokens
            }],
            conversation_id=conversation_id
        )

    def index_conversation(
        self,
        conversation_id: str,
        messages: List[Dict]
    ):
        """索引整个对话"""

        self.vector_store.add_messages(
            messages=messages,
            conversation_id=conversation_id
        )

    def remove_conversation_index(self, conversation_id: str):
        """移除对话索引"""
        self.vector_store.delete_conversation(conversation_id)
```

**集成到 ConversationService**:

```python
# 在 conversation_service.py 的 add_message 方法中
def add_message(self, conversation_id: str, role: str, content: str, **kwargs):
    # ... 现有代码 ...

    # 新增：自动向量化
    if settings.enable_semantic_compression:
        try:
            from backend.core.auto_vectorizer import AutoVectorizer
            vectorizer = AutoVectorizer()
            vectorizer.index_message(
                message_id=str(message.id),
                conversation_id=conversation_id,
                role=role,
                content=content,
                created_at=message.created_at.isoformat(),
                tokens=message.total_tokens or 0
            )
        except Exception as e:
            logger.warning(f"Failed to vectorize message: {e}")

    return message
```

---

### 3.5 Semantic Cache 系统

**目标**: 基于语义相似度的缓存

**借鉴**: Redis + Semantic Search

**实现**:

```python
import redis
import json
import hashlib
from typing import Optional, List, Dict

class SemanticCache:
    """语义缓存系统"""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_client = redis.from_url(redis_url)
        self.embedding_service = EmbeddingService()
        self.ttl = 3600  # 1小时
        self.similarity_threshold = 0.95  # 非常相似才命中

    def get(
        self,
        query: str,
        conversation_id: str,
        model: str
    ) -> Optional[str]:
        """
        查询缓存

        Args:
            query: 查询文本
            conversation_id: 对话ID
            model: 模型名称

        Returns:
            缓存的响应（如果存在）
        """

        # 1. 计算查询的 embedding
        query_embedding = self.embedding_service.embed(query)

        # 2. 从 Redis 获取该对话的所有缓存
        cache_key = f"semantic_cache:{conversation_id}:{model}"
        cached_items = self.redis_client.hgetall(cache_key)

        if not cached_items:
            return None

        # 3. 计算相似度，找最相似的
        best_match = None
        best_similarity = 0.0

        for query_hash, cached_data in cached_items.items():
            data = json.loads(cached_data)
            cached_embedding = data["embedding"]

            similarity = self._cosine_similarity(query_embedding, cached_embedding)

            if similarity > best_similarity and similarity >= self.similarity_threshold:
                best_similarity = similarity
                best_match = data

        if best_match:
            return best_match["response"]

        return None

    def set(
        self,
        query: str,
        response: str,
        conversation_id: str,
        model: str
    ):
        """
        设置缓存

        Args:
            query: 查询文本
            response: 响应文本
            conversation_id: 对话ID
            model: 模型名称
        """

        # 1. 计算 embedding
        query_embedding = self.embedding_service.embed(query)

        # 2. 生成 hash
        query_hash = hashlib.md5(query.encode()).hexdigest()

        # 3. 存储
        cache_key = f"semantic_cache:{conversation_id}:{model}"
        cache_data = json.dumps({
            "query": query,
            "response": response,
            "embedding": query_embedding,
            "timestamp": time.time()
        })

        self.redis_client.hset(cache_key, query_hash, cache_data)
        self.redis_client.expire(cache_key, self.ttl)

    def clear(self, conversation_id: str):
        """清除对话的缓存"""
        pattern = f"semantic_cache:{conversation_id}:*"
        for key in self.redis_client.scan_iter(match=pattern):
            self.redis_client.delete(key)

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        import numpy as np
        v1 = np.array(vec1)
        v2 = np.array(vec2)
        dot_product = np.dot(v1, v2)
        norm = np.linalg.norm(v1) * np.linalg.norm(v2)
        return dot_product / norm if norm > 0 else 0.0
```

**文件**: `backend/core/semantic_cache.py`

---

### 3.6 Phase 3 集成测试

**测试文件**: `backend/tests/test_phase3_integration.py`

```python
import pytest
from backend.core.vector_store import VectorStoreManager
from backend.core.embedding_service import EmbeddingService
from backend.core.semantic_cache import SemanticCache

class TestPhase3Integration:

    def test_vector_store_basic(self):
        """测试向量存储基础功能"""
        store = VectorStoreManager(persist_directory="./test_chroma")

        # 添加消息
        messages = [
            {
                "id": "1",
                "role": "user",
                "content": "Python 如何定义函数？",
                "created_at": "2024-01-01",
                "tokens": 20
            },
            {
                "id": "2",
                "role": "assistant",
                "content": "使用 def 关键字定义函数",
                "created_at": "2024-01-01",
                "tokens": 30
            }
        ]

        store.add_messages(messages, "test_conv_1")

        # 查询相似消息
        similar = store.query_similar(
            "如何创建 Python 函数？",
            "test_conv_1",
            n_results=2
        )

        assert len(similar) > 0
        assert "函数" in similar[0]["content"]

    def test_embedding_service(self):
        """测试 Embedding 服务"""
        service = EmbeddingService(provider="local")

        # 单个文本
        embedding = service.embed("Hello world")
        assert len(embedding) > 0
        assert isinstance(embedding[0], float)

        # 批量文本
        embeddings = service.embed_batch(["Hello", "World"])
        assert len(embeddings) == 2

    def test_semantic_cache(self):
        """测试语义缓存"""
        cache = SemanticCache()

        # 设置缓存
        cache.set(
            query="Python 怎么定义函数？",
            response="使用 def 关键字",
            conversation_id="test_conv",
            model="claude"
        )

        # 查询相似问题（应该命中）
        result = cache.get(
            query="如何在 Python 中定义函数？",
            conversation_id="test_conv",
            model="claude"
        )

        # 注意：只有相似度 > 0.95 才会命中
        # 这个测试可能不会命中，因为问法差异较大
        if result:
            assert "def" in result
```

---

## 依赖安装

```bash
# Phase 2 依赖
pip install redis==5.0.1

# Phase 3 依赖
pip install chromadb==0.4.22
pip install sentence-transformers==2.3.1
pip install numpy==1.24.3
```

**更新 requirements.txt**:
```
# Phase 2 & 3
redis==5.0.1
chromadb==0.4.22
sentence-transformers==2.3.1
numpy==1.24.3
```

---

## 配置更新

**backend/config/settings.py**:

```python
# Phase 2 配置
enable_adaptive_strategy: bool = Field(default=True, env="ENABLE_ADAPTIVE_STRATEGY")
token_budget_safety_margin: float = Field(default=0.05, env="TOKEN_BUDGET_SAFETY_MARGIN")

# Phase 3 配置
enable_semantic_compression: bool = Field(default=True, env="ENABLE_SEMANTIC_COMPRESSION")
enable_semantic_cache: bool = Field(default=True, env="ENABLE_SEMANTIC_CACHE")
vector_db_type: str = Field(default="chroma", env="VECTOR_DB_TYPE")
vector_db_path: str = Field(default="./data/chroma", env="VECTOR_DB_PATH")
embedding_provider: str = Field(default="local", env="EMBEDDING_PROVIDER")  # local or openai
embedding_model: str = Field(default="all-MiniLM-L6-v2", env="EMBEDDING_MODEL")
```

**.env**:
```bash
# Phase 2
ENABLE_ADAPTIVE_STRATEGY=true
TOKEN_BUDGET_SAFETY_MARGIN=0.05

# Phase 3
ENABLE_SEMANTIC_COMPRESSION=true
ENABLE_SEMANTIC_CACHE=true
VECTOR_DB_TYPE=chroma
VECTOR_DB_PATH=./data/chroma
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

---

## 实施时间表

### Week 2 (Phase 2) - 预计 5 天

| 天 | 任务 | 预计时间 |
|----|------|---------|
| Day 1 | Token Budget Manager | 4h |
| Day 1-2 | Dynamic Compression + Adaptive Strategy | 6h |
| Day 2 | Unified Context API | 3h |
| Day 3 | Phase 2 集成测试 | 4h |
| Day 3 | 文档和验收 | 2h |

### Week 3 (Phase 3) - 预计 5 天

| 天 | 任务 | 预计时间 |
|----|------|---------|
| Day 1 | Chroma 集成 + Embedding 服务 | 5h |
| Day 2 | Semantic Compression 实现 | 5h |
| Day 3 | Auto Vectorizer | 3h |
| Day 3 | Semantic Cache | 4h |
| Day 4 | Phase 3 集成测试 + RAG 测试 | 5h |
| Day 5 | 完整测试 + 交付文档 | 4h |

---

## 验收标准

### Phase 2 验收标准

- [ ] Token Budget Manager 能正确计算可用 token
- [ ] 动态压缩能根据使用情况调整
- [ ] 自适应策略能根据对话特征选择最优策略
- [ ] 统一 API 可以一行代码准备上下文
- [ ] 所有单元测试通过
- [ ] 性能开销 < 5%

### Phase 3 验收标准

- [ ] Chroma 集成成功，能存储和查询向量
- [ ] Embedding 服务本地模式工作正常
- [ ] Semantic Compression 能基于相似度压缩
- [ ] 消息自动向量化
- [ ] Semantic Cache 命中率 > 20%（相似查询）
- [ ] RAG 查询返回相关消息
- [ ] 所有测试通过

---

## 风险和缓解

### 风险 1: 向量化性能
**风险**: 每条消息向量化可能很慢
**缓解**:
- 使用轻量级本地模型（all-MiniLM-L6-v2）
- 异步向量化
- 批量处理

### 风险 2: 内存占用
**风险**: Chroma 和模型占用内存
**缓解**:
- 使用持久化存储
- 按需加载模型
- 定期清理旧向量

### 风险 3: Semantic Compression 准确性
**风险**: 可能移除重要消息
**缓解**:
- 保留最近消息
- 降低相似度阈值
- 提供手动策略切换

---

## 下一步行动

1. **今天**: 开始实施 Phase 2.1 (Token Budget Manager)
2. **审查**: 每完成一个子任务，进行代码审查
3. **测试**: 逐步测试，不要积压
4. **文档**: 边写边记录

**准备好开始了吗？** 🚀
