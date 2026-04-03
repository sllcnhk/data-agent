# ChromaDB & Phase 3 功能集成指南

## 概览

本指南将帮助您将 Phase 3 的所有功能（向量存储、语义压缩、自动向量化、语义缓存）集成到您的实际系统中。

---

## 1. 检查当前状态

### 1.1 验证 ChromaDB 安装

```bash
# 检查 ChromaDB 是否已安装
conda run -n dataagent python -c "import chromadb; print(f'ChromaDB {chromadb.__version__} installed')"

# 预期输出: ChromaDB 0.4.22 installed
```

### 1.2 检查数据目录

```bash
# 查看是否有 ChromaDB 数据
ls -la ./data/chroma/

# 如果目录不存在，需要初始化
```

---

## 2. 基础集成：向量存储

### 2.1 在您的代码中初始化 VectorStore

```python
# 在您的主应用启动时
from backend.core.vector_store import VectorStoreManager

# 初始化向量存储
vector_store = VectorStoreManager(
    persist_directory="./data/chroma",
    collection_name="conversation_messages"
)

print(f"VectorStore initialized: {vector_store.get_collection_stats()}")
```

### 2.2 在对话处理流程中集成

找到您处理用户消息的代码（可能在 `backend/conversation/` 或类似位置）：

```python
# 原有代码
def handle_user_message(conversation_id: str, user_message: str):
    # ... 您的现有逻辑 ...

    # 获取 LLM 响应
    assistant_response = call_llm(user_message)

    # === 添加向量存储 ===
    from backend.core.vector_store import VectorStoreManager

    vector_store = VectorStoreManager()

    # 存储用户消息
    vector_store.add_message(
        message_id=f"user_{timestamp}",
        content=user_message,
        conversation_id=conversation_id,
        role="user"
    )

    # 存储助手响应
    vector_store.add_message(
        message_id=f"assistant_{timestamp}",
        content=assistant_response,
        conversation_id=conversation_id,
        role="assistant"
    )

    return assistant_response
```

### 2.3 使用语义搜索增强上下文

```python
def prepare_context_with_semantic_search(conversation_id: str, query: str):
    from backend.core.vector_store import VectorStoreManager

    vector_store = VectorStoreManager()

    # 查询历史中相关的消息
    relevant_history = vector_store.query_similar(
        query_text=query,
        conversation_id=conversation_id,
        n_results=5  # 获取 5 条最相关的历史消息
    )

    # 构建增强的上下文
    context = []
    for result in relevant_history:
        context.append({
            "content": result["content"],
            "similarity": result["similarity"],
            "metadata": result["metadata"]
        })

    return context
```

---

## 3. 自动向量化管理（Phase 3.4）

### 3.1 启动自动向量化服务

在您的应用启动时：

```python
# app_startup.py 或 main.py
from backend.core.auto_vectorization import AutoVectorizationManager
from backend.core.vector_store import VectorStoreManager

# 初始化
vector_store = VectorStoreManager()
auto_vec_manager = AutoVectorizationManager(
    vector_store=vector_store,
    batch_size=10,
    worker_threads=2,
    enable_auto_start=True
)

print("Auto-vectorization manager started")

# 在应用关闭时
def on_shutdown():
    auto_vec_manager.stop(wait=True, timeout=10.0)
    print("Auto-vectorization manager stopped")
```

### 3.2 异步提交消息

```python
# 在您的消息处理逻辑中
from backend.core.auto_vectorization import get_auto_vectorization_manager
from backend.core.conversation_format import UnifiedMessage, MessageRole

def handle_message_async(conversation_id: str, message: UnifiedMessage):
    # 获取全局管理器
    manager = get_auto_vectorization_manager()

    # 异步提交（不阻塞主流程）
    manager.submit_message(
        conversation_id=conversation_id,
        message=message,
        priority=1  # 高优先级
    )

    # 继续您的逻辑...
```

### 3.3 监控向量化进度

```python
# 创建监控端点或定时任务
def monitor_vectorization():
    from backend.core.auto_vectorization import get_auto_vectorization_manager

    manager = get_auto_vectorization_manager()
    stats = manager.get_stats()

    print(f"Vectorization Stats:")
    print(f"  Total submitted: {stats['total_submitted']}")
    print(f"  Total processed: {stats['total_processed']}")
    print(f"  Total failed: {stats['total_failed']}")
    print(f"  Current queue size: {stats['current_queue_size']}")
    print(f"  Last processed: {stats['last_processed_time']}")
```

---

## 4. 语义缓存（Phase 3.5）

### 4.1 初始化语义缓存

```python
# 在应用启动时
from backend.core.semantic_cache import SemanticCache

# 初始化缓存
semantic_cache = SemanticCache(
    similarity_threshold=0.85,  # 相似度阈值（越高越严格）
    max_cache_size=1000,        # 最大缓存条目数
    default_ttl=3600,           # 1 小时过期
    enable_lru=True             # 启用 LRU 淘汰
)

print("Semantic cache initialized")
```

### 4.2 使用装饰器缓存 LLM 调用

```python
from backend.core.semantic_cache import SemanticCacheDecorator, get_semantic_cache

# 获取全局缓存
cache = get_semantic_cache(similarity_threshold=0.85)

# 装饰您的 LLM 调用函数
@SemanticCacheDecorator(cache)
def call_llm(query: str) -> str:
    # 您的 LLM 调用逻辑
    response = your_llm_client.generate(query)
    return response

# 使用时会自动缓存
response1 = call_llm("What is machine learning?")  # Cache MISS, call LLM
response2 = call_llm("What is machine learning?")  # Cache HIT, no LLM call
```

### 4.3 手动缓存控制

```python
from backend.core.semantic_cache import get_semantic_cache

cache = get_semantic_cache()

# 检查缓存
cached_response = cache.get("What is Python?")
if cached_response:
    response, similarity = cached_response
    print(f"Cache HIT (similarity={similarity:.4f})")
    return response
else:
    print("Cache MISS")
    # 调用 LLM
    response = call_llm("What is Python?")
    # 存入缓存
    cache.set("What is Python?", response, ttl=3600)
    return response
```

### 4.4 监控缓存效果

```python
from backend.core.semantic_cache import get_semantic_cache

cache = get_semantic_cache()

# 获取统计
stats = cache.get_stats()
print(f"Cache Statistics:")
print(f"  Hit rate: {stats['hit_rate']:.2%}")
print(f"  Total queries: {stats['total_queries']}")
print(f"  Cache hits: {stats['cache_hits']}")
print(f"  Cache size: {stats['cache_size']}")

# 估算节省
savings = cache.estimate_savings(
    avg_query_tokens=500,
    avg_response_tokens=500,
    cost_per_1k_tokens=0.01  # $0.01 per 1K tokens
)
print(f"\nCost Savings:")
print(f"  Saved tokens: {savings['saved_tokens']}")
print(f"  Saved cost: ${savings['saved_cost_usd']:.2f}")
```

---

## 5. 完整集成示例

### 5.1 集成所有功能的对话处理流程

```python
# conversation_handler.py

from backend.core.vector_store import VectorStoreManager
from backend.core.auto_vectorization import get_auto_vectorization_manager
from backend.core.semantic_cache import get_semantic_cache
from backend.core.semantic_compression import SemanticCompressionStrategy
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole

class EnhancedConversationHandler:
    """增强的对话处理器 - 集成所有 Phase 3 功能"""

    def __init__(self):
        # 初始化组件
        self.vector_store = VectorStoreManager()
        self.auto_vec_manager = get_auto_vectorization_manager(
            vector_store=self.vector_store
        )
        self.semantic_cache = get_semantic_cache(similarity_threshold=0.85)
        self.compression_strategy = SemanticCompressionStrategy(
            keep_first=2,
            keep_last=10,
            similarity_threshold=0.7
        )

    def handle_message(
        self,
        conversation_id: str,
        user_message: str,
        conversation: UnifiedConversation
    ) -> str:
        """处理用户消息"""

        # 1. 检查语义缓存
        cached = self.semantic_cache.get(user_message)
        if cached:
            response, similarity = cached
            print(f"[Cache HIT] similarity={similarity:.4f}")
            return response

        # 2. 如果对话太长，进行语义压缩
        if len(conversation.messages) > 20:
            print(f"[Compressing] {len(conversation.messages)} messages")
            conversation = self.compression_strategy.compress(conversation)
            print(f"[Compressed] to {len(conversation.messages)} messages")

        # 3. 使用语义搜索增强上下文
        relevant_history = self.vector_store.query_similar(
            query_text=user_message,
            conversation_id=conversation_id,
            n_results=5
        )

        # 4. 准备增强的上下文
        enhanced_context = self._build_context(
            conversation,
            relevant_history
        )

        # 5. 调用 LLM
        response = self._call_llm(enhanced_context, user_message)

        # 6. 异步向量化（不阻塞）
        user_msg = UnifiedMessage(role=MessageRole.USER, content=user_message)
        assistant_msg = UnifiedMessage(role=MessageRole.ASSISTANT, content=response)

        self.auto_vec_manager.submit_message(
            conversation_id, user_msg, priority=1
        )
        self.auto_vec_manager.submit_message(
            conversation_id, assistant_msg, priority=1
        )

        # 7. 缓存响应
        self.semantic_cache.set(user_message, response, ttl=3600)

        return response

    def _build_context(self, conversation, relevant_history):
        """构建增强的上下文"""
        # 合并当前对话和相关历史
        context = []

        # 添加相关历史（如果有）
        if relevant_history:
            context.append({
                "role": "system",
                "content": "[Relevant history from previous conversations]"
            })
            for item in relevant_history[:3]:  # 最多 3 条
                context.append({
                    "content": item["content"],
                    "similarity": item["similarity"]
                })

        # 添加当前对话
        for msg in conversation.messages:
            context.append({
                "role": str(msg.role),
                "content": msg.content
            })

        return context

    def _call_llm(self, context, query):
        """调用 LLM（您的实现）"""
        # 这里调用您实际的 LLM
        # return your_llm_client.generate(context, query)
        return f"LLM response to: {query}"

    def get_stats(self):
        """获取所有组件的统计信息"""
        return {
            "vector_store": self.vector_store.get_collection_stats(),
            "auto_vectorization": self.auto_vec_manager.get_stats(),
            "semantic_cache": self.semantic_cache.get_stats(),
            "cache_savings": self.semantic_cache.estimate_savings()
        }

# 使用示例
handler = EnhancedConversationHandler()

# 处理消息
response = handler.handle_message(
    conversation_id="conv_001",
    user_message="How to connect to database?",
    conversation=my_conversation
)

# 查看统计
stats = handler.get_stats()
print(f"Cache hit rate: {stats['semantic_cache']['hit_rate']:.2%}")
print(f"Vectorization queue: {stats['auto_vectorization']['current_queue_size']}")
```

---

## 6. 配置建议

### 6.1 开发环境配置

```python
# config/development.py

VECTOR_STORE_CONFIG = {
    "persist_directory": "./data/chroma_dev",
    "collection_name": "dev_messages"
}

AUTO_VECTORIZATION_CONFIG = {
    "batch_size": 5,
    "worker_threads": 1,
    "enable_auto_start": True
}

SEMANTIC_CACHE_CONFIG = {
    "similarity_threshold": 0.8,  # 开发环境可以宽松一些
    "max_cache_size": 100,
    "default_ttl": 1800,  # 30 分钟
    "enable_lru": True
}

EMBEDDING_SERVICE_CONFIG = {
    "provider": "mock"  # 开发环境使用 mock
}
```

### 6.2 生产环境配置

```python
# config/production.py

VECTOR_STORE_CONFIG = {
    "persist_directory": "/var/data/chroma_prod",
    "collection_name": "prod_messages"
}

AUTO_VECTORIZATION_CONFIG = {
    "batch_size": 20,
    "worker_threads": 4,
    "max_queue_size": 5000,
    "enable_auto_start": True
}

SEMANTIC_CACHE_CONFIG = {
    "similarity_threshold": 0.85,  # 生产环境更严格
    "max_cache_size": 10000,
    "default_ttl": 3600,  # 1 小时
    "enable_lru": True
}

EMBEDDING_SERVICE_CONFIG = {
    "provider": "local",
    "model_name": "all-MiniLM-L6-v2"  # 使用本地模型
}

# 或使用 OpenAI
EMBEDDING_SERVICE_CONFIG_OPENAI = {
    "provider": "openai",
    "model_name": "text-embedding-ada-002",
    "api_key": "your-openai-key"
}
```

---

## 7. 性能优化建议

### 7.1 向量存储优化

```python
# 批量操作而不是单条
messages_to_add = []
for msg in conversation.messages:
    messages_to_add.append({
        "id": msg.id,
        "content": msg.content,
        "role": str(msg.role),
        "created_at": msg.created_at
    })

# 一次性添加
vector_store.add_messages(messages_to_add, conversation_id)
```

### 7.2 缓存优化

```python
# 根据查询类型使用不同的 TTL
def set_cache_with_smart_ttl(cache, query, response):
    # 短查询（可能频繁）-> 长 TTL
    if len(query) < 50:
        ttl = 7200  # 2 hours
    # 长查询（可能不常见）-> 短 TTL
    else:
        ttl = 1800  # 30 minutes

    cache.set(query, response, ttl=ttl)
```

### 7.3 自动向量化优化

```python
# 设置优先级策略
def submit_with_priority(manager, conversation_id, message):
    # 用户消息 -> 高优先级
    if message.role == MessageRole.USER:
        priority = 10
    # 助手响应 -> 中优先级
    elif message.role == MessageRole.ASSISTANT:
        priority = 5
    # 系统消息 -> 低优先级
    else:
        priority = 1

    manager.submit_message(conversation_id, message, priority=priority)
```

---

## 8. 监控和维护

### 8.1 创建监控脚本

```python
# monitor.py

import time
from backend.core.vector_store import VectorStoreManager
from backend.core.auto_vectorization import get_auto_vectorization_manager
from backend.core.semantic_cache import get_semantic_cache

def monitor_all():
    """监控所有组件"""
    vector_store = VectorStoreManager()
    auto_vec = get_auto_vectorization_manager()
    cache = get_semantic_cache()

    while True:
        print("\n=== System Status ===")

        # 向量存储
        vs_stats = vector_store.get_collection_stats()
        print(f"Vector Store: {vs_stats['total_messages']} messages")

        # 自动向量化
        av_stats = auto_vec.get_stats()
        print(f"Auto-Vectorization:")
        print(f"  Queue: {av_stats['current_queue_size']}")
        print(f"  Processed: {av_stats['total_processed']}")
        print(f"  Failed: {av_stats['total_failed']}")

        # 语义缓存
        cache_stats = cache.get_stats()
        print(f"Semantic Cache:")
        print(f"  Hit rate: {cache_stats['hit_rate']:.2%}")
        print(f"  Size: {cache_stats['cache_size']}/{cache_stats['max_cache_size']}")

        # 成本节省
        savings = cache.estimate_savings()
        print(f"  Saved: ${savings['saved_cost_usd']:.2f}")

        time.sleep(60)  # 每分钟更新一次

if __name__ == "__main__":
    monitor_all()
```

### 8.2 定期维护任务

```python
# maintenance.py

def daily_maintenance():
    """每日维护任务"""
    from backend.core.semantic_cache import get_semantic_cache
    from backend.core.auto_vectorization import get_auto_vectorization_manager

    cache = get_semantic_cache()
    auto_vec = get_auto_vectorization_manager()

    # 清理过期缓存（自动）
    print("Cleaning expired cache entries...")

    # 清理已处理消息缓存
    print("Clearing processed messages cache...")
    auto_vec.clear_processed_cache()

    # 导出缓存（备份）
    print("Exporting cache...")
    cache_data = cache.export_cache()
    # 保存到文件
    import json
    with open("cache_backup.json", "w") as f:
        json.dump(cache_data, f)

    print(f"Maintenance complete: {len(cache_data)} cache entries backed up")
```

---

## 9. 故障排查

### 9.1 ChromaDB 连接问题

```python
# 测试 ChromaDB 连接
try:
    from backend.core.vector_store import VectorStoreManager
    vs = VectorStoreManager()
    stats = vs.get_collection_stats()
    print(f"ChromaDB OK: {stats['total_messages']} messages")
except Exception as e:
    print(f"ChromaDB ERROR: {e}")
    # 检查:
    # 1. 数据目录权限
    # 2. ChromaDB 版本
    # 3. 磁盘空间
```

### 9.2 自动向量化队列阻塞

```python
# 检查队列状态
from backend.core.auto_vectorization import get_auto_vectorization_manager

manager = get_auto_vectorization_manager()
stats = manager.get_stats()

if stats['current_queue_size'] > 100:
    print("WARNING: Queue is backed up!")
    print(f"Queue size: {stats['current_queue_size']}")
    print(f"Failed: {stats['total_failed']}")

    # 可能的原因:
    # 1. Worker 线程太少
    # 2. ChromaDB 写入慢
    # 3. 提交速度过快

    # 解决方案:
    # manager.stop()
    # manager = AutoVectorizationManager(worker_threads=4)  # 增加线程
    # manager.start()
```

### 9.3 缓存命中率低

```python
from backend.core.semantic_cache import get_semantic_cache

cache = get_semantic_cache()
stats = cache.get_stats()

if stats['hit_rate'] < 0.1:  # 低于 10%
    print("WARNING: Low cache hit rate!")
    print(f"Hit rate: {stats['hit_rate']:.2%}")

    # 可能的原因:
    # 1. 相似度阈值太高
    # 2. 查询变化太大
    # 3. TTL 太短

    # 解决方案:
    # 降低阈值
    cache.similarity_threshold = 0.75

    # 或增加 TTL
    cache.default_ttl = 7200  # 2 hours
```

---

## 10. 下一步

### 10.1 逐步集成建议

1. **第一步**: 先集成基础向量存储（VectorStore）
   - 在对话处理流程中添加消息存储
   - 验证数据正确存储到 ChromaDB

2. **第二步**: 添加语义搜索
   - 在准备上下文时查询相关历史
   - 观察搜索效果

3. **第三步**: 启用自动向量化
   - 异步处理消息向量化
   - 监控队列和性能

4. **第四步**: 集成语义缓存
   - 缓存 LLM 响应
   - 监控命中率和成本节省

5. **第五步**: 优化和调整
   - 根据实际使用情况调整参数
   - 优化性能瓶颈

### 10.2 测试检查清单

- [ ] ChromaDB 数据持久化正常
- [ ] 向量搜索返回相关结果
- [ ] 自动向量化不阻塞主流程
- [ ] 缓存命中率 >20%
- [ ] 队列大小稳定
- [ ] 成本有所降低

---

## 附录：快速参考

### 常用命令

```bash
# 检查 ChromaDB 数据大小
du -sh ./data/chroma/

# 查看 ChromaDB 文件
ls -lah ./data/chroma/

# 备份 ChromaDB
tar -czf chroma_backup_$(date +%Y%m%d).tar.gz ./data/chroma/

# 清理测试数据
rm -rf ./test_data/
```

### 关键配置参数

| 参数 | 推荐值（开发） | 推荐值（生产） | 说明 |
|------|---------------|---------------|------|
| `similarity_threshold` | 0.75-0.80 | 0.85-0.90 | 语义相似度阈值 |
| `cache_ttl` | 1800 (30min) | 3600 (1hr) | 缓存过期时间 |
| `max_cache_size` | 100 | 1000-10000 | 最大缓存条目 |
| `worker_threads` | 1-2 | 2-4 | 向量化线程数 |
| `batch_size` | 5-10 | 10-20 | 批处理大小 |

---

**最后更新**: 2026-02-05
**作者**: Claude Sonnet 4.5
