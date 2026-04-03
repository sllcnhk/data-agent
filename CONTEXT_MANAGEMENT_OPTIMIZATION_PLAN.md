# Context 管理优化方案 - 企业级架构设计

**项目**: data-agent
**架构师**: Claude Code (Senior LLM Context Management Architect)
**日期**: 2026-02-01
**版本**: 1.0

---

## 执行摘要

本文档提供了一个全面的 Context 管理优化方案，基于 2026 年业界最佳实践（OpenAI、Anthropic、Google 等一流公司），针对 data-agent 项目的现状设计了分阶段实施计划。

**目标**: 将现有的简单滑动窗口机制升级为**企业级智能 Context 管理系统**，支持：
- 🎯 智能 Token 预算管理
- 🧠 RAG 语义增强检索
- 💾 多级缓存架构
- 📊 自适应压缩策略
- 🔄 跨会话记忆管理

---

## 目录

1. [业界最佳实践研究](#1-业界最佳实践研究)
2. [现状分析与问题诊断](#2-现状分析与问题诊断)
3. [优化目标架构](#3-优化目标架构)
4. [分阶段实施规划](#4-分阶段实施规划)
5. [技术选型与决策](#5-技术选型与决策)
6. [性能指标与监控](#6-性能指标与监控)
7. [风险评估与应对](#7-风险评估与应对)

---

## 1. 业界最佳实践研究

### 1.1 Context Window 现状 (2026)

| 提供商 | 模型 | 标称容量 | 可靠容量 | 备注 |
|--------|------|----------|----------|------|
| **Anthropic** | Claude 4 Sonnet | 200K tokens | ~130K tokens | 1M beta (tier 4+) |
| **OpenAI** | GPT-4 Turbo | 128K tokens | ~100K tokens | 接近上限时性能下降 |
| **Google** | Gemini Pro | 128K tokens | ~100K tokens | - |

**关键发现**:
- 标称容量约 65-75% 处性能最可靠
- 超过 75% 后性能急剧下降（非线性）
- 保守利用（~75%）产出更高质量结果

**来源**: [Best LLMs for Extended Context Windows in 2026](https://research.aimultiple.com/ai-context-window/)

### 1.2 Anthropic 官方最佳实践

#### A. Context Engineering

> "The challenge isn't just crafting the perfect prompt—it's thoughtfully curating what information enters the model's limited attention budget at each step."

**核心原则**:
1. **精心策划输入**: 找到最小的高信号 tokens 集合
2. **动态调整**: 根据任务阶段调整 context 内容
3. **信息分级**: 区分必要信息和辅助信息

**来源**: [Anthropic - Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

#### B. 新特性 (2025年9月)

**Context Editing**:
- 自动清理过时的 tool calls
- 保持对话流畅性
- 动态释放 context 空间

**Memory Tool**:
- 持久化关键信息
- 跨会话记忆管理
- **结合使用性能提升 39%**

**来源**: [Claude AI Gains Persistent Memory](https://www.reworked.co/digital-workplace/claude-ai-gains-persistent-memory-in-latest-anthropic-update/)

#### C. Subagents 策略

在对话早期或任务初期使用 subagents：
- 验证细节
- 探索特定问题
- **保留主 agent 的 context 容量**

**Extended Thinking**:
- 临时思考块用于内部推理
- 自动排除以回收内存
- 动态 context 管理

**来源**: [Claude Code Best Practices](https://www.anthropic.com/engineering/claude-code-best-practices)

### 1.3 RAG (Retrieval-Augmented Generation) 最佳实践

#### 核心工作流

```
用户查询
  ↓
语义检索 (Vector DB)
  ↓
Top-K 相关片段 (3-5 chunks)
  ↓
精简 Prompt (查询 + 片段)
  ↓
LLM 生成
```

**优势**:
- 100 页文档 → 仅注入 3 个关键条款
- 降低 Token 消耗 **70-90%**
- 提高响应准确性
- 支持动态知识更新

**来源**: [Top Techniques to Manage Context Lengths](https://agenta.ai/blog/top-6-techniques-to-manage-context-length-in-llms)

#### Semantic Caching

**实现方式**:
- 使用 FAISS/Chroma 存储查询向量
- 相似查询直接返回缓存结果
- **减少 LLM 调用 50-80%**
- 降低成本和延迟

**来源**: [Semantic Cache with FAISS - Hugging Face](https://huggingface.co/learn/cookbook/en/semantic_cache_chroma_vector_database)

### 1.4 MCP (Model Context Protocol) Memory

**跨会话记忆管理**:
- 持久化上下文感知的记忆
- 根据当前 context 决定检索内容
- 跨对话和编码会话共享记忆

**实现方式**:
- 事件驱动的记忆存储
- 语义检索（非关键词匹配）
- 自动过期和优先级管理

**来源**: [AI Apps with MCP Memory Benchmark](https://research.aimultiple.com/memory-mcp/)

---

## 2. 现状分析与问题诊断

### 2.1 架构现状

```
┌─────────────────────────────────────────────────┐
│           Current Architecture (Flawed)         │
├─────────────────────────────────────────────────┤
│                                                 │
│  API Layer                                      │
│    └─> send_message()                           │
│           ↓ (每次请求)                          │
│  Service Layer                                  │
│    └─> _build_context(limit=20) ← 硬编码       │
│           ↓                                     │
│  Agent Layer                                    │
│    └─> _build_conversation_history(max=10)     │
│           ↓ ← 硬编码，不一致！                   │
│  Database                                       │
│    └─> 获取最近 N 条消息                        │
│                                                 │
│  ❌ 问题:                                        │
│    - 配置不一致 (20 vs 10)                      │
│    - 未使用 HybridContextManager                │
│    - 无 Token 计数                              │
│    - 无智能压缩                                  │
│    - 无缓存机制                                  │
└─────────────────────────────────────────────────┘
```

### 2.2 关键问题列表

| 优先级 | 问题 | 影响 | 现状 |
|--------|------|------|------|
| 🔴 P0 | Context limit 不一致 | 行为不可预测 | Service=20, Agent=10, Config=20 |
| 🔴 P0 | 语义压缩未实现 | 配置无效 | SemanticCompression 回退到滑动窗口 |
| 🔴 P0 | Token 计数缺失 | 无法真正管理 Token 预算 | 数据库字段存在但未更新 |
| 🟠 P1 | HybridContextManager 未集成 | 已有代码但未使用 | 完整实现但被绕过 |
| 🟠 P1 | 快照功能未集成 | 数据库表空置 | context_snapshots 表 0 条记录 |
| 🟠 P1 | 缓存缺失 | 每次都查询数据库 | Redis 配置存在但未用于 context |
| 🟡 P2 | 性能问题 | 长对话加载慢 | 默认加载 100 条消息 |
| 🟡 P2 | 无压缩触发机制 | 被动管理 | 无自动化流程 |

### 2.3 代码质量分析

**✅ 优点**:
- 数据模型设计完整（Conversation, Message, ContextSnapshot）
- 压缩策略框架清晰（4种策略）
- 单元测试覆盖充分
- 统一的消息格式标准

**❌ 缺陷**:
- 实现与设计脱节
- 配置管理混乱
- 性能优化缺失
- 功能未激活

---

## 3. 优化目标架构

### 3.1 整体架构 (Target State)

```
┌───────────────────────────────────────────────────────────────┐
│                  Optimized Architecture                        │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │             API Layer                                    │ │
│  │  - send_message()                                        │ │
│  │  - Token 预算检查                                        │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │        Context Manager (Unified)                         │ │
│  │  ┌────────────────────────────────────────────────────┐ │ │
│  │  │  Strategy Router                                    │ │ │
│  │  │  - 根据对话阶段选择策略                              │ │ │
│  │  │  - Token 预算动态调整                                │ │ │
│  │  └────────────────────────────────────────────────────┘ │ │
│  │                                                           │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │ │
│  │  │ Sliding  │  │  Smart   │  │ Semantic │  │  Full   │ │ │
│  │  │  Window  │  │Compress  │  │   RAG    │  │         │ │ │
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │           Multi-Level Cache                              │ │
│  │  ┌────────────┐  ┌────────────┐  ┌───────────────────┐ │ │
│  │  │  L1: In-   │→ │ L2: Redis  │→ │ L3: Vector DB     │ │ │
│  │  │   Memory   │  │  (Session) │  │  (Semantic Cache) │ │ │
│  │  └────────────┘  └────────────┘  └───────────────────┘ │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │         Storage Layer                                    │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │ │
│  │  │  PostgreSQL  │  │  Vector DB   │  │   Snapshot   │  │ │
│  │  │  (Messages)  │  │  (Chroma)    │  │   Storage    │  │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │         Monitoring & Analytics                           │ │
│  │  - Token 使用统计                                         │ │
│  │  - 压缩效果跟踪                                           │ │
│  │  - 缓存命中率                                             │ │
│  └─────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

### 3.2 核心组件设计

#### A. **Unified Context Manager**

**职责**:
1. 统一 context 管理入口
2. 策略路由和选择
3. Token 预算管理
4. 缓存协调

**接口**:
```python
class UnifiedContextManager:
    def get_context_for_request(
        self,
        conversation_id: str,
        current_message: str,
        max_tokens: int = 150000  # 基于 Claude 4 的 75% 利用率
    ) -> ContextBundle:
        """获取优化后的 context"""

    def update_context_after_response(
        self,
        conversation_id: str,
        response: UnifiedMessage
    ) -> None:
        """响应后更新 context"""
```

#### B. **Token Budget Manager**

**职责**:
1. 精确计算 Token 数量
2. 预算分配和监控
3. 超限警告和处理

**实现**:
```python
class TokenBudgetManager:
    def count_tokens(self, text: str, model: str) -> int:
        """使用 tiktoken 精确计数"""

    def allocate_budget(self, total: int) -> Dict[str, int]:
        """分配预算: system_prompt, history, response_buffer"""
        return {
            "system_prompt": total * 0.05,  # 5%
            "history": total * 0.70,        # 70%
            "response_buffer": total * 0.25  # 25%
        }
```

#### C. **RAG Semantic Layer**

**职责**:
1. 向量化历史消息
2. 语义检索相关内容
3. 智能分块和排序

**实现**:
```python
class RAGSemanticLayer:
    def __init__(self, vector_db: VectorDatabase):
        self.vector_db = vector_db  # Chroma/FAISS
        self.embedder = EmbeddingModel()  # text-embedding-3-large

    def retrieve_relevant_history(
        self,
        query: str,
        conversation_id: str,
        top_k: int = 5
    ) -> List[UnifiedMessage]:
        """检索最相关的历史消息"""
```

#### D. **Multi-Level Cache**

```python
class MultiLevelCache:
    def __init__(self):
        self.l1 = InMemoryCache(max_size=100)  # LRU
        self.l2 = RedisCache()
        self.l3 = SemanticCache(vector_db)

    def get(self, key: str, query: str = None) -> Optional[Any]:
        """三级缓存查找"""
        # L1 → L2 → L3 (Semantic)
```

### 3.3 Context 策略决策树

```
开始
  ↓
对话是否 < 10条消息?
  ├─ Yes → Full Context (无需压缩)
  └─ No ↓
Token 是否超过 70% 预算?
  ├─ No → Sliding Window (保留最近 30 条)
  └─ Yes ↓
是否有重要的早期上下文?
  ├─ Yes → Smart Compression (保留开头 + 摘要 + 最近)
  └─ No ↓
是否需要语义检索?
  ├─ Yes → RAG Semantic (检索相关片段)
  └─ No → Create Snapshot + Reset
```

### 3.4 数据流设计

```
用户消息
  ↓
1. Token 预算检查
  ├─ 充足 → 继续
  └─ 超限 → 触发压缩
  ↓
2. 缓存查询 (L1 → L2 → L3)
  ├─ 命中 → 返回缓存
  └─ 未命中 → 继续
  ↓
3. 策略选择
  ↓
4. Context 构建
  ├─ 从数据库加载消息
  ├─ 应用压缩策略
  └─ Token 计数验证
  ↓
5. 生成 Prompt
  ↓
6. LLM 调用
  ↓
7. 响应处理
  ├─ 保存消息
  ├─ 更新 Token 统计
  └─ 更新缓存
  ↓
8. 监控记录
```

---

## 4. 分阶段实施规划

### Phase 0: 准备工作 (1天)

**目标**: 建立基础设施和测试环境

**任务**:
- [x] ✅ 现状分析完成
- [x] ✅ 架构设计完成
- [ ] 🔄 安装依赖包
  ```bash
  pip install tiktoken chromadb sentence-transformers openai
  ```
- [ ] 🔄 创建测试数据集
- [ ] 🔄 建立性能基线

**产出**:
- 依赖环境就绪
- 测试数据准备完成
- 基线性能指标记录

---

### Phase 1: 修复现有问题 (2-3天)

**目标**: 统一配置、实现 Token 计数、激活 HybridContextManager

#### 1.1 统一 Context 配置

**问题**: Service=20, Agent=10, Config=20 不一致

**修复**:

1. **统一配置源** (`backend/config/settings.py`):
```python
# Context 管理配置
max_context_messages: int = Field(default=30, env="MAX_CONTEXT_MESSAGES")
max_context_tokens: int = Field(default=150000, env="MAX_CONTEXT_TOKENS")  # 新增
context_compression_strategy: str = Field(default="smart", env="CONTEXT_COMPRESSION_STRATEGY")
context_utilization_target: float = Field(default=0.75, env="CONTEXT_UTILIZATION_TARGET")
```

2. **移除硬编码**:
   - ❌ `conversation_service._build_context(limit=20)`
   - ❌ `orchestrator._build_conversation_history(max_history=10)`
   - ✅ 都改为使用 `settings.max_context_messages`

#### 1.2 实现 Token 计数

**安装 tiktoken**:
```bash
pip install tiktoken
```

**实现 TokenCounter**:
```python
# backend/core/token_counter.py
import tiktoken

class TokenCounter:
    def __init__(self):
        self.encoders = {
            "claude": tiktoken.get_encoding("cl100k_base"),
            "gpt-4": tiktoken.get_encoding("cl100k_base"),
            "gpt-3.5": tiktoken.get_encoding("cl100k_base"),
        }

    def count_tokens(self, text: str, model: str = "claude") -> int:
        encoder = self.encoders.get(model, self.encoders["claude"])
        return len(encoder.encode(text))

    def count_messages(self, messages: List[UnifiedMessage], model: str = "claude") -> int:
        total = 0
        for msg in messages:
            total += self.count_tokens(msg.content, model)
            total += 4  # 每条消息的格式化开销
        return total
```

**集成到 ConversationService**:
```python
def add_message(self, conversation_id: str, role: str, content: str, ...) -> Message:
    # 计算 Token
    token_count = self.token_counter.count_tokens(content, model=conversation.current_model)

    # 创建消息
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        total_tokens=token_count,  # ← 填充字段
        ...
    )

    # 更新对话统计
    conversation.total_tokens += token_count
```

#### 1.3 激活 HybridContextManager

**当前**: Service 和 Agent 层直接构建 context，绕过了 `HybridContextManager`

**修改 ConversationService**:
```python
class ConversationService:
    def __init__(self, db: Session):
        self.db = db
        self.context_manager = HybridContextManager.create_from_settings()  # ← 新增
        self.token_counter = TokenCounter()  # ← 新增

    def _build_context(self, conversation_id: str) -> Dict[str, Any]:
        """构建上下文 - 使用 HybridContextManager"""
        # 1. 获取对话和消息
        conversation = self.get_conversation(conversation_id)
        messages = self.get_messages(conversation_id, limit=None)  # 全部加载

        # 2. 转换为统一格式
        unified_conv = self.to_unified_conversation(conversation, messages)

        # 3. 使用 ContextManager 压缩
        compressed_conv = self.context_manager.compress_conversation(
            unified_conv,
            max_messages=settings.max_context_messages,
            max_tokens=settings.max_context_tokens
        )

        # 4. 返回
        return {
            "history": [
                {"role": msg.role, "content": msg.content}
                for msg in compressed_conv.messages
            ],
            "compression_stats": {
                "original_messages": len(messages),
                "compressed_messages": len(compressed_conv.messages),
                "strategy_used": self.context_manager.current_strategy
            }
        }
```

**修改 MasterAgent**:
```python
class MasterAgent:
    def __init__(self, mcp_manager, model_key, llm_config):
        ...
        # 移除 _build_conversation_history 中的硬编码逻辑
        # 直接使用 conversation_context["history"]
```

#### 1.4 单元测试

**测试文件**: `backend/tests/test_phase1_fixes.py`

```python
def test_unified_config():
    """测试配置统一"""
    from config.settings import settings
    assert settings.max_context_messages == 30
    assert settings.max_context_tokens == 150000

def test_token_counting():
    """测试 Token 计数"""
    counter = TokenCounter()
    text = "Hello, world!"
    tokens = counter.count_tokens(text)
    assert tokens > 0
    assert isinstance(tokens, int)

def test_context_manager_integration():
    """测试 ContextManager 集成"""
    service = ConversationService(db)
    context = service._build_context(conversation_id)

    assert "compression_stats" in context
    assert context["compression_stats"]["strategy_used"] in ["full", "sliding_window", "smart", "semantic"]
```

**验收标准**:
- ✅ 所有配置来源统一
- ✅ Token 计数精确（误差 < 5%）
- ✅ HybridContextManager 正常工作
- ✅ 测试覆盖率 > 90%

---

### Phase 2: 智能 Context 管理 (3-4天)

**目标**: 实现智能压缩、Token 预算管理、自适应策略

#### 2.1 Token Budget Manager

**新增模块**: `backend/core/token_budget_manager.py`

```python
class TokenBudgetManager:
    def __init__(self, model: str = "claude", target_utilization: float = 0.75):
        self.model = model
        self.target_utilization = target_utilization
        self.token_counter = TokenCounter()

        # 模型容量
        self.model_limits = {
            "claude-sonnet-4-5": 200000,
            "claude-haiku-4-5": 200000,
            "gpt-4-turbo": 128000,
            "gpt-3.5-turbo": 16000,
        }

    def get_available_budget(self, model: str) -> int:
        """获取可用预算（考虑目标利用率）"""
        max_tokens = self.model_limits.get(model, 100000)
        return int(max_tokens * self.target_utilization)

    def allocate_budget(self, total_budget: int) -> Dict[str, int]:
        """分配预算到各个部分"""
        return {
            "system_prompt": int(total_budget * 0.05),   # 5%
            "context_history": int(total_budget * 0.70), # 70%
            "current_message": int(total_budget * 0.05), # 5%
            "response_buffer": int(total_budget * 0.20), # 20%
        }

    def check_budget(self,
                    conversation: UnifiedConversation,
                    current_message: str,
                    model: str) -> BudgetCheckResult:
        """检查是否超出预算"""
        budget = self.get_available_budget(model)
        allocation = self.allocate_budget(budget)

        # 计算当前使用
        system_tokens = self.token_counter.count_tokens(conversation.system_prompt or "")
        history_tokens = self.token_counter.count_messages(conversation.messages)
        message_tokens = self.token_counter.count_tokens(current_message)

        used = system_tokens + history_tokens + message_tokens
        available = allocation["context_history"] + allocation["current_message"]

        return BudgetCheckResult(
            within_budget=used <= available,
            used_tokens=used,
            available_tokens=available,
            utilization_rate=used / available,
            needs_compression=used > available * 0.9
        )
```

#### 2.2 改进 SmartCompressionStrategy

**当前问题**: 简单的文本截断摘要

**改进方案**: 使用 LLM 生成高质量摘要

```python
class ImprovedSmartCompressionStrategy(ContextCompressionStrategy):
    def __init__(self, keep_first: int = 3, keep_recent: int = 10, use_llm_summary: bool = True):
        self.keep_first = keep_first
        self.keep_recent = keep_recent
        self.use_llm_summary = use_llm_summary
        self.llm_adapter = None  # 初始化时注入

    async def _summarize_with_llm(self, messages: List[UnifiedMessage]) -> str:
        """使用 LLM 生成摘要"""
        prompt = f"""请总结以下对话的关键信息，保留重要的上下文和决策：

{self._format_messages_for_summary(messages)}

要求：
1. 提取关键事实和决策点
2. 保留重要的技术细节
3. 简洁但信息完整
4. 使用列表格式
"""

        summary_conv = UnifiedConversation(system_prompt="你是一个专业的对话摘要助手。")
        summary_conv.add_user_message(prompt)

        response = await self.llm_adapter.chat(summary_conv, max_tokens=500)
        return response.content

    async def compress(self, conversation: UnifiedConversation, max_messages: int = 20) -> UnifiedConversation:
        """智能压缩"""
        if len(conversation.messages) <= max_messages:
            return conversation

        # 三段式压缩
        first_messages = conversation.messages[:self.keep_first]
        recent_messages = conversation.messages[-self.keep_recent:]
        middle_messages = conversation.messages[self.keep_first:-self.keep_recent]

        # 生成摘要
        if self.use_llm_summary and len(middle_messages) > 0:
            summary = await self._summarize_with_llm(middle_messages)
        else:
            summary = self._simple_summarize(middle_messages)

        # 构建新对话
        compressed = UnifiedConversation(
            system_prompt=conversation.system_prompt,
            conversation_id=conversation.conversation_id
        )

        # 添加消息
        for msg in first_messages:
            compressed.add_message(msg)

        # 添加摘要消息
        compressed.add_assistant_message(
            f"[历史对话摘要 - {len(middle_messages)} 条消息]\n\n{summary}",
            metadata={"is_summary": True, "compressed_count": len(middle_messages)}
        )

        for msg in recent_messages:
            compressed.add_message(msg)

        return compressed
```

#### 2.3 自适应策略选择

**新增模块**: `backend/core/adaptive_strategy_selector.py`

```python
class AdaptiveStrategySelector:
    def select_strategy(
        self,
        conversation: UnifiedConversation,
        budget_check: BudgetCheckResult,
        conversation_metadata: Dict[str, Any]
    ) -> str:
        """根据情况自适应选择策略"""

        message_count = len(conversation.messages)
        token_usage = budget_check.utilization_rate
        has_important_early_context = self._check_early_context_importance(conversation)

        # 决策树
        if message_count < 10:
            return "full"

        if token_usage < 0.5:
            return "sliding_window"

        if token_usage < 0.8:
            if has_important_early_context:
                return "smart"
            else:
                return "sliding_window"

        # 超过 80% 使用语义检索
        return "semantic"

    def _check_early_context_importance(self, conversation: UnifiedConversation) -> bool:
        """检查早期上下文是否重要"""
        if len(conversation.messages) < 5:
            return False

        early_messages = conversation.messages[:3]

        # 启发式规则
        for msg in early_messages:
            # 包含系统指令
            if "system" in msg.role or msg.metadata.get("is_system_instruction"):
                return True

            # 包含重要关键词
            important_keywords = ["目标", "要求", "规则", "约束", "配置"]
            if any(kw in msg.content for kw in important_keywords):
                return True

        return False
```

#### 2.4 集成到统一入口

**新增**: `backend/core/unified_context_manager.py`

```python
class UnifiedContextManager:
    def __init__(self):
        self.token_budget_manager = TokenBudgetManager()
        self.strategy_selector = AdaptiveStrategySelector()
        self.context_manager = HybridContextManager.create_from_settings()
        self.token_counter = TokenCounter()

    async def get_context_for_request(
        self,
        conversation: UnifiedConversation,
        current_message: str,
        model: str = "claude-sonnet-4-5"
    ) -> ContextBundle:
        """统一入口 - 获取优化的 context"""

        # 1. 检查预算
        budget_check = self.token_budget_manager.check_budget(
            conversation, current_message, model
        )

        # 2. 选择策略
        strategy = self.strategy_selector.select_strategy(
            conversation, budget_check, {}
        )

        # 3. 应用压缩
        self.context_manager.set_strategy(strategy)
        compressed_conversation = await self.context_manager.compress_conversation(
            conversation,
            max_messages=settings.max_context_messages,
            max_tokens=budget_check.available_tokens
        )

        # 4. 返回
        return ContextBundle(
            conversation=compressed_conversation,
            strategy_used=strategy,
            budget_check=budget_check,
            compression_stats={
                "original_messages": len(conversation.messages),
                "compressed_messages": len(compressed_conversation.messages),
                "original_tokens": budget_check.used_tokens,
                "compressed_tokens": self.token_counter.count_messages(compressed_conversation.messages)
            }
        )
```

#### 2.5 测试

**集成测试**: `backend/tests/test_phase2_smart_management.py`

```python
@pytest.mark.asyncio
async def test_adaptive_strategy_selection():
    """测试自适应策略选择"""
    manager = UnifiedContextManager()

    # 场景 1: 短对话 → full
    short_conv = create_conversation(message_count=5)
    context = await manager.get_context_for_request(short_conv, "test")
    assert context.strategy_used == "full"

    # 场景 2: 中等对话 → sliding_window
    medium_conv = create_conversation(message_count=20)
    context = await manager.get_context_for_request(medium_conv, "test")
    assert context.strategy_used == "sliding_window"

    # 场景 3: 长对话 + 重要早期上下文 → smart
    long_conv = create_conversation(message_count=50, has_important_early_context=True)
    context = await manager.get_context_for_request(long_conv, "test")
    assert context.strategy_used == "smart"

@pytest.mark.asyncio
async def test_token_budget_enforcement():
    """测试 Token 预算强制执行"""
    manager = UnifiedContextManager()

    # 创建超长对话
    very_long_conv = create_conversation(message_count=200)
    context = await manager.get_context_for_request(very_long_conv, "test")

    # 验证压缩后符合预算
    assert context.budget_check.within_budget
    assert context.compression_stats["compressed_tokens"] < context.budget_check.available_tokens
```

**验收标准**:
- ✅ Token 预算管理精确
- ✅ 自适应策略选择正确率 > 95%
- ✅ 压缩后保持在预算内
- ✅ 重要信息不丢失

---

### Phase 3: RAG 语义增强 (3-4天)

**目标**: 实现真正的语义检索，支持向量化存储和检索

#### 3.1 技术选型

基于业界最佳实践：

| 组件 | 选择 | 理由 |
|------|------|------|
| 向量数据库 | **Chroma** | 轻量级、易集成、10k-200k vectors |
| Embedding 模型 | **text-embedding-3-large** | OpenAI 最新模型，性价比高 |
| 备选 Embedding | **voyage-3-large** | 性能更好但需付费 |
| 分块策略 | **语义分块** | 保持语义完整性 |

**依赖安装**:
```bash
pip install chromadb sentence-transformers openai
```

#### 3.2 向量化存储层

**新增**: `backend/core/vector_store.py`

```python
import chromadb
from chromadb.config import Settings
from typing import List, Dict, Any
import openai

class ConversationVectorStore:
    def __init__(self, persist_directory: str = "./data/vector_db"):
        self.client = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=persist_directory
        ))

        # 创建 collection
        self.collection = self.client.get_or_create_collection(
            name="conversation_messages",
            metadata={"description": "Conversation message embeddings"}
        )

        # Embedding 模型
        openai.api_key = os.getenv("OPENAI_API_KEY")

    def embed_text(self, text: str) -> List[float]:
        """使用 OpenAI text-embedding-3-large"""
        response = openai.embeddings.create(
            input=text,
            model="text-embedding-3-large"
        )
        return response.data[0].embedding

    def add_messages(
        self,
        messages: List[UnifiedMessage],
        conversation_id: str
    ) -> None:
        """向量化并存储消息"""
        for msg in messages:
            if not msg.content or msg.content.strip() == "":
                continue

            # 生成向量
            embedding = self.embed_text(msg.content)

            # 存储
            self.collection.add(
                ids=[f"{conversation_id}_{msg.id}"],
                embeddings=[embedding],
                documents=[msg.content],
                metadatas=[{
                    "conversation_id": conversation_id,
                    "role": msg.role,
                    "timestamp": msg.timestamp or "",
                    "token_count": msg.token_count or 0
                }]
            )

    def search_relevant_messages(
        self,
        query: str,
        conversation_id: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """语义检索相关消息"""
        # 向量化查询
        query_embedding = self.embed_text(query)

        # 检索
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where={"conversation_id": conversation_id}
        )

        # 返回
        return [
            {
                "content": doc,
                "metadata": meta,
                "distance": dist
            }
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0]
            )
        ]
```

#### 3.3 实现 SemanticCompressionStrategy

**修改**: `backend/core/context_manager.py`

```python
class SemanticCompressionStrategy(ContextCompressionStrategy):
    def __init__(self, vector_store: ConversationVectorStore, top_k: int = 10, keep_recent: int = 5):
        self.vector_store = vector_store
        self.top_k = top_k
        self.keep_recent = keep_recent

    async def compress(
        self,
        conversation: UnifiedConversation,
        max_messages: int = 20,
        current_query: str = None
    ) -> UnifiedConversation:
        """语义压缩 - RAG 增强"""

        if len(conversation.messages) <= max_messages:
            return conversation

        # 1. 保留最近的消息
        recent_messages = conversation.messages[-self.keep_recent:]

        # 2. 从剩余消息中检索最相关的
        if current_query:
            query = current_query
        else:
            # 使用最后一条用户消息作为查询
            last_user_msg = conversation.get_last_user_message()
            query = last_user_msg.content if last_user_msg else ""

        if query:
            relevant_results = self.vector_store.search_relevant_messages(
                query=query,
                conversation_id=conversation.conversation_id,
                top_k=self.top_k
            )

            # 获取相关消息的 ID
            relevant_ids = {meta["id"] for result in relevant_results for meta in [result["metadata"]]}

            # 从原始消息中提取相关消息
            relevant_messages = [
                msg for msg in conversation.messages[:-self.keep_recent]
                if f"{conversation.conversation_id}_{msg.id}" in relevant_ids
            ]
        else:
            relevant_messages = []

        # 3. 构建压缩后的对话
        compressed = UnifiedConversation(
            system_prompt=conversation.system_prompt,
            conversation_id=conversation.conversation_id
        )

        # 添加相关历史（按时间排序）
        for msg in sorted(relevant_messages, key=lambda m: m.timestamp or ""):
            compressed.add_message(msg)

        # 添加最近消息
        for msg in recent_messages:
            compressed.add_message(msg)

        return compressed
```

#### 3.4 自动向量化

**修改 ConversationService**:

```python
class ConversationService:
    def __init__(self, db: Session):
        ...
        self.vector_store = ConversationVectorStore()  # ← 新增

    def add_message(self, ...) -> Message:
        # 保存到数据库
        message = ...
        self.db.add(message)
        self.db.commit()

        # 异步向量化 (后台任务)
        background_tasks.add_task(
            self.vector_store.add_messages,
            [unified_message],
            conversation_id
        )

        return message
```

#### 3.5 Semantic Caching

**新增**: `backend/core/semantic_cache.py`

```python
class SemanticCache:
    def __init__(self, vector_store: ConversationVectorStore, similarity_threshold: float = 0.95):
        self.vector_store = vector_store
        self.similarity_threshold = similarity_threshold
        self.response_cache = {}  # 简单的内存缓存

    def check_cache(self, query: str, conversation_id: str) -> Optional[str]:
        """检查是否有相似查询的缓存响应"""
        results = self.vector_store.search_relevant_messages(
            query=query,
            conversation_id=f"cache_{conversation_id}",
            top_k=1
        )

        if results and results[0]["distance"] < (1 - self.similarity_threshold):
            cache_key = results[0]["metadata"]["cache_key"]
            return self.response_cache.get(cache_key)

        return None

    def add_to_cache(self, query: str, response: str, conversation_id: str) -> None:
        """添加到语义缓存"""
        cache_key = f"{conversation_id}_{hash(query)}"

        # 存储响应
        self.response_cache[cache_key] = response

        # 向量化查询
        self.vector_store.collection.add(
            ids=[cache_key],
            embeddings=[self.vector_store.embed_text(query)],
            documents=[query],
            metadatas=[{
                "conversation_id": f"cache_{conversation_id}",
                "cache_key": cache_key,
                "timestamp": datetime.utcnow().isoformat()
            }]
        )
```

#### 3.6 测试

**集成测试**: `backend/tests/test_phase3_rag.py`

```python
def test_vector_store():
    """测试向量存储和检索"""
    vector_store = ConversationVectorStore()

    # 添加消息
    messages = [
        UnifiedMessage(role="user", content="什么是 RAG?"),
        UnifiedMessage(role="assistant", content="RAG 是检索增强生成..."),
        UnifiedMessage(role="user", content="如何实现 RAG?"),
    ]
    vector_store.add_messages(messages, "test_conv")

    # 检索
    results = vector_store.search_relevant_messages(
        "RAG 实现方法",
        "test_conv",
        top_k=2
    )

    assert len(results) == 2
    assert "如何实现" in results[0]["content"]

@pytest.mark.asyncio
async def test_semantic_compression():
    """测试语义压缩"""
    vector_store = ConversationVectorStore()
    strategy = SemanticCompressionStrategy(vector_store)

    # 创建长对话
    conversation = create_long_conversation(message_count=100)

    # 压缩
    compressed = await strategy.compress(
        conversation,
        max_messages=20,
        current_query="最新的产品需求是什么？"
    )

    # 验证：包含相关历史 + 最近消息
    assert len(compressed.messages) <= 20
    assert any("产品需求" in msg.content for msg in compressed.messages)

def test_semantic_cache():
    """测试语义缓存"""
    cache = SemanticCache(vector_store)

    # 添加到缓存
    cache.add_to_cache("什么是机器学习?", "机器学习是...", "test_conv")

    # 查询相似问题
    result = cache.check_cache("机器学习是什么?", "test_conv")
    assert result is not None
    assert "机器学习" in result
```

**验收标准**:
- ✅ 向量化存储成功
- ✅ 语义检索准确率 > 85%
- ✅ 语义缓存命中率 > 60% (相似查询)
- ✅ 响应时间 < 200ms (缓存命中)

---

### Phase 4: 性能优化 (2-3天)

**目标**: 多级缓存、异步处理、性能监控

#### 4.1 多级缓存架构

**新增**: `backend/core/multi_level_cache.py`

```python
class MultiLevelCache:
    def __init__(self):
        # L1: 进程内内存缓存 (LRU)
        self.l1_cache = LRUCache(maxsize=100)

        # L2: Redis 缓存
        self.l2_cache = get_redis()

        # L3: 语义缓存
        self.l3_cache = SemanticCache(vector_store)

    def get_context(
        self,
        conversation_id: str,
        current_message: str = None
    ) -> Optional[ContextBundle]:
        """三级缓存查找"""

        # L1 查找 (最快)
        key = f"context:{conversation_id}"
        result = self.l1_cache.get(key)
        if result:
            logger.info(f"[CACHE] L1 hit for {conversation_id}")
            return result

        # L2 查找 (快)
        result = self.l2_cache.get(key)
        if result:
            logger.info(f"[CACHE] L2 hit for {conversation_id}")
            # 回填 L1
            self.l1_cache.set(key, result)
            return pickle.loads(result)

        # L3 语义缓存查找 (如果有查询)
        if current_message:
            result = self.l3_cache.check_cache(current_message, conversation_id)
            if result:
                logger.info(f"[CACHE] L3 semantic hit for {conversation_id}")
                return result

        logger.info(f"[CACHE] Miss for {conversation_id}")
        return None

    def set_context(
        self,
        conversation_id: str,
        context: ContextBundle,
        ttl: int = 300
    ) -> None:
        """设置到所有缓存层"""
        key = f"context:{conversation_id}"

        # L1
        self.l1_cache.set(key, context)

        # L2
        self.l2_cache.setex(key, ttl, pickle.dumps(context))
```

#### 4.2 异步向量化

**使用 Celery 异步任务**:

```python
# backend/tasks/vectorization_tasks.py
from celery import Celery

app = Celery('tasks', broker=settings.celery_broker_url)

@app.task
def vectorize_message_async(message_id: str, conversation_id: str):
    """异步向量化消息"""
    with get_db_context() as db:
        message = db.query(Message).filter(Message.id == message_id).first()
        if message:
            vector_store = ConversationVectorStore()
            unified_message = UnifiedMessage(
                role=message.role,
                content=message.content,
                id=message.id
            )
            vector_store.add_messages([unified_message], conversation_id)
```

#### 4.3 性能监控

**新增**: `backend/core/context_metrics.py`

```python
class ContextMetrics:
    def __init__(self):
        self.redis = get_redis()

    def record_compression(
        self,
        conversation_id: str,
        strategy: str,
        original_messages: int,
        compressed_messages: int,
        original_tokens: int,
        compressed_tokens: int,
        duration_ms: float
    ):
        """记录压缩指标"""
        metrics = {
            "strategy": strategy,
            "compression_ratio": compressed_messages / original_messages,
            "token_reduction": (original_tokens - compressed_tokens) / original_tokens,
            "duration_ms": duration_ms,
            "timestamp": datetime.utcnow().isoformat()
        }

        # 存储到 Redis (时序数据)
        key = f"metrics:compression:{conversation_id}"
        self.redis.zadd(key, {json.dumps(metrics): time.time()})
        self.redis.expire(key, 86400 * 7)  # 7天过期

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        l1_hits = int(self.redis.get("cache:l1:hits") or 0)
        l1_misses = int(self.redis.get("cache:l1:misses") or 0)
        l2_hits = int(self.redis.get("cache:l2:hits") or 0)
        l2_misses = int(self.redis.get("cache:l2:misses") or 0)

        total_requests = l1_hits + l1_misses
        l1_hit_rate = l1_hits / total_requests if total_requests > 0 else 0
        l2_hit_rate = l2_hits / (l2_hits + l2_misses) if (l2_hits + l2_misses) > 0 else 0

        return {
            "l1_hit_rate": l1_hit_rate,
            "l2_hit_rate": l2_hit_rate,
            "total_requests": total_requests
        }
```

#### 4.4 测试

**性能测试**: `backend/tests/test_phase4_performance.py`

```python
def test_cache_performance():
    """测试缓存性能"""
    cache = MultiLevelCache()

    # 预热缓存
    context = create_test_context()
    cache.set_context("test_conv", context)

    # 测试 L1 命中
    start = time.time()
    result = cache.get_context("test_conv")
    l1_time = (time.time() - start) * 1000

    assert result is not None
    assert l1_time < 1  # < 1ms

    # 清空 L1
    cache.l1_cache.clear()

    # 测试 L2 命中
    start = time.time()
    result = cache.get_context("test_conv")
    l2_time = (time.time() - start) * 1000

    assert result is not None
    assert l2_time < 10  # < 10ms

@pytest.mark.benchmark
def test_compression_performance(benchmark):
    """基准测试：压缩性能"""
    manager = UnifiedContextManager()
    conversation = create_long_conversation(message_count=100)

    result = benchmark(
        manager.get_context_for_request,
        conversation,
        "test message",
        "claude-sonnet-4-5"
    )

    # 验证性能
    assert benchmark.stats["mean"] < 0.5  # 平均 < 500ms
```

**验收标准**:
- ✅ L1 缓存命中 < 1ms
- ✅ L2 缓存命中 < 10ms
- ✅ 压缩操作 < 500ms (100条消息)
- ✅ 整体缓存命中率 > 70%

---

### Phase 5: 集成测试和交付 (2-3天)

**目标**: 端到端测试、文档编写、部署指南

#### 5.1 集成测试

**测试场景**: `backend/tests/test_integration_context.py`

```python
@pytest.mark.integration
class TestContextManagementIntegration:
    """端到端集成测试"""

    async def test_full_conversation_flow(self):
        """测试完整对话流程"""
        # 1. 创建对话
        conversation = create_conversation()

        # 2. 短对话阶段 (< 10条)
        for i in range(5):
            await send_message(conversation.id, f"Message {i}")

        # 验证：使用 full 策略
        context = get_context(conversation.id)
        assert context.strategy_used == "full"

        # 3. 中等对话阶段 (10-50条)
        for i in range(20):
            await send_message(conversation.id, f"Message {i+5}")

        # 验证：使用 sliding_window 策略
        context = get_context(conversation.id)
        assert context.strategy_used == "sliding_window"

        # 4. 长对话阶段 (50+ 条)
        for i in range(100):
            await send_message(conversation.id, f"Message {i+25}")

        # 验证：使用 smart 或 semantic 策略
        context = get_context(conversation.id)
        assert context.strategy_used in ["smart", "semantic"]

        # 5. 验证 Token 预算
        assert context.budget_check.within_budget
        assert context.budget_check.utilization_rate < 0.80

    async def test_rag_retrieval_accuracy(self):
        """测试 RAG 检索准确性"""
        conversation = create_conversation_with_topics({
            "product_requirements": ["需求1", "需求2", "需求3"],
            "technical_decisions": ["决策1", "决策2"],
            "random_chat": ["闲聊1", "闲聊2", "闲聊3", "闲聊4"]
        })

        # 查询产品需求
        context = get_context_with_query(
            conversation.id,
            "最新的产品需求是什么？"
        )

        # 验证：检索到相关消息
        relevant_count = sum(
            1 for msg in context.conversation.messages
            if "需求" in msg.content
        )
        total_count = len(context.conversation.messages)

        assert relevant_count / total_count > 0.5  # 50%+ 相关

    def test_cache_effectiveness(self):
        """测试缓存有效性"""
        conversation_id = "test_conv"

        # 第一次请求 (未命中)
        start = time.time()
        context1 = get_context(conversation_id)
        first_time = time.time() - start

        # 第二次请求 (L1 命中)
        start = time.time()
        context2 = get_context(conversation_id)
        second_time = time.time() - start

        # 验证：缓存加速
        assert second_time < first_time * 0.1  # 至少快 10 倍
```

#### 5.2 性能基准测试

**基准测试**: `backend/benchmarks/context_benchmarks.py`

```python
import pytest
from locust import HttpUser, task, between

class ContextBenchmarkUser(HttpUser):
    wait_time = between(1, 2)

    @task
    def send_message(self):
        """模拟发送消息"""
        self.client.post(
            f"/conversations/{self.conversation_id}/messages",
            json={
                "content": "Test message",
                "stream": False
            }
        )

    def on_start(self):
        # 创建测试对话
        response = self.client.post("/conversations", json={"title": "Benchmark Test"})
        self.conversation_id = response.json()["id"]

# 运行基准测试
# locust -f context_benchmarks.py --headless -u 10 -r 1 -t 1m
```

#### 5.3 文档编写

**用户指南**: `docs/CONTEXT_MANAGEMENT_GUIDE.md`

```markdown
# Context 管理用户指南

## 配置

### 基本配置

在 `.env` 文件中：

\`\`\`bash
# Context 管理配置
MAX_CONTEXT_MESSAGES=30
MAX_CONTEXT_TOKENS=150000
CONTEXT_COMPRESSION_STRATEGY=smart
CONTEXT_UTILIZATION_TARGET=0.75

# RAG 配置
ENABLE_SEMANTIC_COMPRESSION=true
VECTOR_DB_TYPE=chroma
VECTOR_DB_PATH=./data/vector_db

# 缓存配置
ENABLE_CONTEXT_CACHE=true
CONTEXT_CACHE_TTL=300
\`\`\`

## 策略选择

### Full (完整)
- **何时使用**: 对话 < 10条消息
- **优点**: 保留所有信息
- **缺点**: 长对话不适用

### Sliding Window (滑动窗口)
- **何时使用**: 中等长度对话，无重要早期上下文
- **优点**: 简单高效
- **缺点**: 可能丢失早期重要信息

### Smart Compression (智能压缩)
- **何时使用**: 长对话 + 重要早期上下文
- **优点**: 保留关键信息
- **缺点**: 需要 LLM 调用生成摘要

### Semantic (语义检索)
- **何时使用**: 超长对话，Token 使用 > 80%
- **优点**: 最相关的信息
- **缺点**: 需要向量数据库

## 监控

查看 Context 管理统计：

\`\`\`bash
GET /api/metrics/context
\`\`\`

返回：
\`\`\`json
{
  "cache_hit_rate": 0.75,
  "avg_compression_ratio": 0.65,
  "strategy_distribution": {
    "full": 0.2,
    "sliding_window": 0.5,
    "smart": 0.25,
    "semantic": 0.05
  }
}
\`\`\`
```

**开发者指南**: `docs/CONTEXT_MANAGEMENT_DEVELOPMENT.md`

#### 5.4 部署检查清单

**部署前检查**: `DEPLOYMENT_CHECKLIST.md`

```markdown
# Context 管理功能部署检查清单

## 依赖检查
- [ ] tiktoken 已安装 (`pip install tiktoken`)
- [ ] chromadb 已安装 (`pip install chromadb`)
- [ ] sentence-transformers 已安装
- [ ] OpenAI API Key 已配置

## 数据库迁移
- [ ] 运行迁移脚本 (`python backend/scripts/migrate_context.py`)
- [ ] 验证 context_snapshots 表结构
- [ ] 创建向量数据库目录

## 配置验证
- [ ] `.env` 文件配置完整
- [ ] Redis 连接正常
- [ ] PostgreSQL 连接正常
- [ ] 向量数据库目录有写权限

## 功能测试
- [ ] 运行单元测试 (100% 通过)
- [ ] 运行集成测试 (100% 通过)
- [ ] 运行性能测试 (满足基准)

## 监控设置
- [ ] Context 指标已启用
- [ ] 日志级别配置正确
- [ ] 告警规则已设置

## 回滚计划
- [ ] 数据库备份已创建
- [ ] 回滚脚本已准备
- [ ] 回滚流程已测试
```

#### 5.5 验收标准

**最终验收**:

| 类别 | 指标 | 目标 | 实际 | 状态 |
|------|------|------|------|------|
| **功能** | 策略正确率 | > 95% | - | ⏳ |
| **功能** | 信息保留率 | > 90% | - | ⏳ |
| **性能** | 缓存命中率 | > 70% | - | ⏳ |
| **性能** | 响应时间 (缓存) | < 50ms | - | ⏳ |
| **性能** | 响应时间 (未缓存) | < 500ms | - | ⏳ |
| **质量** | 单元测试覆盖率 | > 90% | - | ⏳ |
| **质量** | 集成测试通过率 | 100% | - | ⏳ |
| **稳定性** | Token 预算遵守率 | 100% | - | ⏳ |
| **稳定性** | 压缩成功率 | > 99% | - | ⏳ |

---

## 5. 技术选型与决策

### 5.1 向量数据库选择：Chroma

**理由**:
1. ✅ 轻量级，易于集成和部署
2. ✅ 支持 10k-200k vectors (符合当前规模)
3. ✅ Python 原生支持，API 友好
4. ✅ 本地持久化，不依赖外部服务
5. ✅ 开源免费

**备选方案**:
- **FAISS**: 更快但需要更多配置，适合更大规模
- **Pinecone**: 托管服务，成本高，适合生产环境扩展

**来源**: [Vector Database Comparison (2025)](https://liquidmetal.ai/casesAndBlogs/vector-comparison/), [Chroma vs FAISS Guide](https://mohamedbakrey094.medium.com/chromadb-vs-faiss-a-comprehensive-guide-for-vector-search-and-ai-applications-39762ed1326f)

### 5.2 Embedding 模型选择：OpenAI text-embedding-3-large

**理由**:
1. ✅ 2026年主流选择之一
2. ✅ 性价比高 ($0.00013/1K tokens)
3. ✅ 1536维，平衡准确性和性能
4. ✅ 与 OpenAI API 统一管理

**备选方案**:
- **Google Gemini Embedding**: MTEB 排行第一，但 API 访问受限
- **Voyage AI voyage-3-large**: 性能最佳，但成本更高

**来源**: [Complete Guide to RAG and Vector Databases (2026)](https://solvedbycode.ai/blog/complete-guide-rag-vector-databases-2026)

### 5.3 Token 计数工具：tiktoken

**理由**:
1. ✅ OpenAI 官方库，最准确
2. ✅ 支持多种模型编码
3. ✅ 性能优秀（Rust 实现）

### 5.4 缓存策略：三级缓存

**L1 (进程内内存)**:
- 容量：100 对话
- TTL：无限制 (LRU 淘汰)
- 命中时间：< 1ms

**L2 (Redis)**:
- 容量：1000 对话
- TTL：5 分钟
- 命中时间：< 10ms

**L3 (语义缓存)**:
- 容量：无限制
- TTL：24 小时
- 命中时间：< 100ms

---

## 6. 性能指标与监控

### 6.1 关键指标 (KPIs)

| 指标 | 当前 | 目标 | 改进 |
|------|------|------|------|
| **平均 Token 使用率** | - | 60-75% | - |
| **Context 压缩率** | 0% | 40-60% | - |
| **缓存命中率** | 0% | 70%+ | - |
| **响应时间 (中位数)** | - | < 200ms | - |
| **P95 响应时间** | - | < 500ms | - |
| **信息保留率** | - | > 90% | - |

### 6.2 监控仪表板

**指标收集**:
- Prometheus + Grafana
- 自定义 Context 管理面板
- 实时告警

**关键图表**:
1. Token 使用率趋势
2. 策略分布饼图
3. 缓存命中率趋势
4. 压缩效果对比
5. 响应时间分布

### 6.3 日志和追踪

**日志级别**:
```python
logger.info(f"[CONTEXT] Strategy selected: {strategy}, tokens: {tokens}")
logger.debug(f"[CONTEXT] Compression: {before} → {after} messages")
logger.warning(f"[CONTEXT] Token budget exceeded: {usage}/{budget}")
```

**分布式追踪** (可选):
- OpenTelemetry 集成
- 追踪 Context 管理全流程

---

## 7. 风险评估与应对

### 7.1 技术风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 向量数据库性能不足 | 中 | 高 | 提前进行负载测试，准备 FAISS 备选方案 |
| LLM 摘要质量低 | 中 | 中 | A/B 测试，保留简单摘要作为备选 |
| Token 计数不准确 | 低 | 高 | 使用官方 tiktoken，增加 10% buffer |
| 缓存一致性问题 | 中 | 中 | 设置合理 TTL，消息更新时清除缓存 |

### 7.2 运维风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 向量数据库磁盘占用 | 高 | 中 | 定期清理，设置保留策略 |
| Redis OOM | 中 | 高 | 设置 maxmemory 和淘汰策略 |
| 性能回退 | 低 | 高 | 金丝雀部署，实时监控，快速回滚 |

### 7.3 回滚计划

**触发条件**:
- 错误率 > 5%
- P95 延迟 > 1s
- 缓存命中率 < 30%

**回滚步骤**:
1. 禁用新功能开关
2. 恢复旧版 context 管理逻辑
3. 清除缓存
4. 验证系统恢复正常
5. 分析失败原因

---

## 8. 时间表和里程碑

```
Week 1: Phase 0 + Phase 1
  Day 1-2: 环境准备 + 统一配置
  Day 3-4: Token 计数 + HybridContextManager 集成
  Day 5:   单元测试 + 验收

Week 2: Phase 2
  Day 6-7: Token Budget Manager + 改进压缩策略
  Day 8-9: 自适应策略选择 + 统一入口
  Day 10:  集成测试 + 验收

Week 3: Phase 3
  Day 11-12: 向量存储 + Semantic Compression
  Day 13-14: 自动向量化 + Semantic Cache
  Day 15:    RAG 测试 + 验收

Week 4: Phase 4 + Phase 5
  Day 16-17: 多级缓存 + 性能优化
  Day 18-19: 集成测试 + 性能测试
  Day 20-21: 文档 + 部署 + 最终交付
```

---

## 9. 参考资源

### 业界最佳实践

- [Best LLMs for Extended Context Windows in 2026](https://research.aimultiple.com/ai-context-window/)
- [Anthropic - Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Claude Code Best Practices](https://www.anthropic.com/engineering/claude-code-best-practices)
- [How Claude Code Got Better by Protecting More Context](https://hyperdev.matsuoka.com/p/how-claude-code-got-better-by-protecting)
- [Top Techniques to Manage Context Lengths](https://agenta.ai/blog/top-6-techniques-to-manage-context-length-in-llms)
- [How Should I Manage Memory for my LLM Chatbot](https://www.vellum.ai/blog/how-should-i-manage-memory-for-my-llm-chatbot)

### RAG 和向量数据库

- [Vector Database Comparison (2025)](https://liquidmetal.ai/casesAndBlogs/vector-comparison/)
- [Complete Guide to RAG and Vector Databases (2026)](https://solvedbycode.ai/blog/complete-guide-rag-vector-databases-2026)
- [Semantic Cache with FAISS - Hugging Face](https://huggingface.co/learn/cookbook/en/semantic_cache_chroma_vector_database)
- [ChromaDB vs FAISS Guide](https://mohamedbakrey094.medium.com/chromadb-vs-faiss-a-comprehensive-guide-for-vector-search-and-ai-applications-39762ed1326f)
- [Learn How to Build Reliable RAG Applications (2026)](https://dev.to/pavanbelagatti/learn-how-to-build-reliable-rag-applications-in-2026-1b7p)

### 记忆管理

- [AI Apps with MCP Memory Benchmark](https://research.aimultiple.com/memory-mcp/)
- [Claude AI Gains Persistent Memory](https://www.reworked.co/digital-workplace/claude-ai-gains-persistent-memory-in-latest-anthropic-update/)

---

## 附录

### A. 术语表

- **Context Window**: 模型可接受的最大输入 Token 数量
- **RAG**: Retrieval-Augmented Generation，检索增强生成
- **Embedding**: 文本的向量表示
- **Semantic Search**: 基于语义相似度的检索
- **Token Budget**: 预分配的 Token 额度
- **Utilization Rate**: Token 使用率

### B. API 参考

见 `docs/API_REFERENCE.md`

### C. 故障排查

见 `docs/TROUBLESHOOTING.md`

---

**文档版本**: 1.0
**最后更新**: 2026-02-01
**维护者**: data-agent 开发团队
**审核者**: Claude Code (Senior LLM Context Management Architect)
