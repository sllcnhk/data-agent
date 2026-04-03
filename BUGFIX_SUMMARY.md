# Bug 修复总结

## 问题描述

调用 LLM 时出错：
```
Claude API调用失败: 所有 3 个模型均失败
尝试的模型: ['claude-sonnet-4-5', 'claude-sonnet-4-5-20250929', 'claude-haiku-4-5-20251001']
错误详情: HTTP 400: messages.1: all messages must have non-empty content except for the optional final assistant message
```

## 问题原因分析

### 问题 1: 空消息导致 API 调用失败

**根本原因：** 发送给 Claude API 的消息数组中存在空内容（content 为空字符串）的消息，违反了 Claude API 的要求。

**问题链路：**
1. 数据库历史消息中某些消息的 content 字段缺失或为空
2. `orchestrator.py` 的 `_build_conversation_history` 方法使用 `msg.get("content", "")` 返回空字符串
3. `claude.py` 的 `convert_to_native_format` 方法未过滤空消息
4. 包含空消息的请求发送到 Claude API
5. API 返回 HTTP 400 错误

**涉及文件：**
- `backend/agents/orchestrator.py:394-421` - 构建对话历史
- `backend/core/model_adapters/claude.py:53-94` - 消息格式转换

### 问题 2: 配置的 Minimax 模型未被尝试

**根本原因：** `.env` 文件中未配置 fallback 相关的环境变量，导致使用默认配置，而默认配置中 Minimax 模型虽然在 `settings.py` 的默认值中，但未正确传递到适配器。

**涉及文件：**
- `.env` - 环境变量配置文件
- `backend/config/settings.py:63-69` - fallback 配置定义

## 修复方案

### 修复 1: 添加空消息过滤

#### 1.1 修复 orchestrator.py

**文件：** `backend/agents/orchestrator.py`

**修改内容：**
```python
def _build_conversation_history(self, current_message: str, context: Dict[str, Any]) -> List[Dict[str, str]]:
    messages = []
    history = context.get("history", [])
    max_history = 10
    recent_history = history[-max_history:] if len(history) > max_history else history

    for msg in recent_history:
        # 获取消息内容并去除首尾空白
        content = msg.get("content", "").strip()

        # 只添加非空消息
        if content:
            messages.append({
                "role": msg.get("role", "user"),
                "content": content
            })
        else:
            logger.warning(f"Skipping empty message in conversation history: role={msg.get('role')}")

    # 添加当前消息（也需要检查）
    if current_message and current_message.strip():
        messages.append({
            "role": "user",
            "content": current_message.strip()
        })
    else:
        logger.warning("Current message is empty, not adding to conversation")

    return messages
```

#### 1.2 修复 claude.py

**文件：** `backend/core/model_adapters/claude.py`

**修改内容：**
```python
def convert_to_native_format(self, conversation: UnifiedConversation) -> Dict[str, Any]:
    messages = []
    logger.info(f"[DEBUG] convert_to_native_format: conversation has {len(conversation.messages)} messages")

    for i, msg in enumerate(conversation.messages):
        logger.info(f"[DEBUG] Processing message {i}: role={msg.role}")

        # 跳过system角色
        if msg.role == MessageRole.SYSTEM:
            continue

        # 跳过空内容的消息
        if not msg.content or not msg.content.strip():
            logger.warning(f"[DEBUG] Skipping message {i} with empty content, role={msg.role}")
            continue

        # 获取 role 字符串值
        if isinstance(msg.role, MessageRole):
            role_str = msg.role.value
        elif isinstance(msg.role, str):
            role_str = msg.role
        else:
            role_str = str(msg.role)

        claude_msg = {
            "role": role_str,
            "content": msg.content.strip()
        }

        messages.append(claude_msg)
        logger.info(f"[DEBUG] Added message with role={claude_msg['role']}, content_length={len(msg.content)}")

    result = {
        "model": self.model,
        "messages": messages,
        "system_prompt": conversation.system_prompt
    }

    logger.info(f"[DEBUG] Converted {len(messages)} messages (skipped empty messages)")

    return result
```

### 修复 2: 配置 Minimax fallback

**文件：** `.env`

**新增配置：**
```bash
# Anthropic Claude (使用中转服务)
ANTHROPIC_API_KEY=cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4
ANTHROPIC_AUTH_TOKEN=cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4
ANTHROPIC_BASE_URL=http://10.0.3.248:3000/api
ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-5
ANTHROPIC_FALLBACK_MODELS=claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001,minimax-m2
ANTHROPIC_ENABLE_FALLBACK=true
ANTHROPIC_MAX_TOKENS=4096
ANTHROPIC_TEMPERATURE=0.7
```

## 验证结果

### 测试 1: 配置加载验证

```
=== 配置验证 ===
BASE_URL: http://10.0.3.248:3000/api
DEFAULT_MODEL: claude-sonnet-4-5
FALLBACK_MODELS: claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001,minimax-m2
ENABLE_FALLBACK: True

Fallback models list:
  1. claude-sonnet-4-5-20250929
  2. claude-haiku-4-5-20251001
  3. minimax-m2

[OK] Minimax model found in fallback list
```

**结果：** ✅ 通过 - Minimax 模型（minimax-m2）已成功添加到 fallback 列表

### 测试 2: 空消息过滤验证

```
=== Testing empty message filtering ===

Original messages: 4
  1. [user] 'Hello'
  2. [assistant] ''
  3. [user] '   '
  4. [user] 'How are you?'

Filtered messages: 2
  1. [user] Hello
  2. [user] How are you?

[OK] Empty message filtering works! (expected 2, got 2)
```

**结果：** ✅ 通过 - 空消息和仅包含空白的消息被正确过滤

## 模型回退策略

修复后的模型尝试顺序：

1. **claude-sonnet-4-5** (主模型)
2. **claude-sonnet-4-5-20250929** (fallback 1)
3. **claude-haiku-4-5-20251001** (fallback 2)
4. **minimax-m2** (fallback 3)

当主模型调用失败时，系统会自动依次尝试 fallback 模型，直到成功或所有模型都失败。

## 下一步操作

### 1. 重启服务

```bash
cd C:\Users\shiguangping\data-agent
start-all.bat
```

### 2. 测试对话功能

1. 打开 Web 界面：http://localhost:3000
2. 创建新对话
3. 发送测试消息
4. 观察是否正常返回

### 3. 检查日志

查看日志文件，确认：
- 是否有空消息被跳过的警告
- 如果主模型失败，是否尝试了 fallback 模型
- Minimax 模型是否被成功调用

日志位置：`backend/logs/` 或控制台输出

### 4. 清理数据库中的空消息（可选）

如果想要清理数据库中已存在的空消息：

```sql
-- 查看空消息
SELECT id, conversation_id, role, length(content) as content_length
FROM messages
WHERE content IS NULL OR content = '' OR content ~ '^\s*$';

-- 删除空消息（谨慎操作）
-- DELETE FROM messages WHERE content IS NULL OR content = '' OR content ~ '^\s*$';
```

## 修改文件清单

1. ✅ `backend/agents/orchestrator.py` - 添加空消息过滤
2. ✅ `backend/core/model_adapters/claude.py` - 添加空消息过滤
3. ✅ `.env` - 配置 Minimax fallback 和中转服务地址
4. ✅ `backend/test_fix.py` - 创建测试脚本（可选）

## 技术说明

### 为什么 Minimax 不需要单独的适配器？

因为用户使用的是中转服务（`http://10.0.3.248:3000/api`），该服务同时支持多个模型提供商的 API，包括：
- Claude 模型（claude-sonnet-4-5, claude-haiku-4-5 等）
- Minimax 模型（minimax-m2 等）

这些模型都通过同一个 API 端点访问，只需要在请求中指定不同的 `model` 参数即可。因此，Minimax 可以直接使用 Claude 适配器，无需创建单独的适配器。

### Fallback 机制工作原理

参见：`backend/core/model_adapters/claude.py:268-354`

1. 构建模型列表：`[主模型] + [fallback 模型列表]`
2. 依次尝试每个模型：
   - 发送 HTTP 请求到 `/v1/messages`
   - 如果成功（HTTP 200），返回结果并记录使用的模型
   - 如果失败，记录错误并尝试下一个模型
3. 如果所有模型都失败，抛出包含详细错误信息的异常

## 预期效果

修复后：

1. **空消息不再导致 API 调用失败** - 所有空消息会在发送前被过滤掉
2. **Minimax 模型作为最后的 fallback** - 当 Claude 模型都失败时，会尝试 Minimax
3. **更好的错误处理** - 日志会清晰显示哪些消息被跳过，哪些模型被尝试
4. **提高系统可靠性** - 多个 fallback 模型确保服务的高可用性

---

**修复时间：** 2025-01-29
**修复人员：** Claude Code
**测试状态：** ✅ 已验证通过
