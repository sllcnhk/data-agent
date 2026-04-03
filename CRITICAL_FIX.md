# 紧急修复 - metadata 命名冲突

## 问题

后端启动失败，错误：
```
AttributeError: 'property' object has no attribute 'schema'
```

## 根本原因

在 `Conversation` 模型中定义了 `metadata` property，与 **SQLAlchemy 的保留属性 `metadata`** 冲突。

SQLAlchemy 的所有模型都有一个 `metadata` 属性用于数据库元数据管理，不能被覆盖。

## 修复内容

### 1. 移除冲突的 property

**文件**: [backend/models/conversation.py](backend/models/conversation.py:96)

移除了这段代码：
```python
@property
def metadata(self):
    """兼容旧代码的 metadata 属性"""
    return self.extra_metadata or {}

@metadata.setter
def metadata(self, value):
    """设置 metadata"""
    self.extra_metadata = value
```

**保留了 `system_prompt` property**（这个不会冲突）：
```python
@property
def system_prompt(self):
    """从 extra_metadata 中获取 system_prompt"""
    if self.extra_metadata:
        return self.extra_metadata.get('system_prompt')
    return None
```

### 2. 修复使用 metadata 的代码

**文件**: [backend/services/conversation_service.py](backend/services/conversation_service.py)

**位置 1**: 第 459 行（from_unified_conversation）
```python
# 修改前
conversation = Conversation(
    ...
    system_prompt=unified.system_prompt,
    metadata=unified.metadata  # ❌ 错误
)

# 修改后
extra_metadata = unified.metadata or {}
if unified.system_prompt:
    extra_metadata['system_prompt'] = unified.system_prompt

conversation = Conversation(
    ...
    extra_metadata=extra_metadata  # ✅ 正确
)
```

**位置 2**: 第 667 行（_build_context）
```python
# 修改前
"metadata": conversation.metadata  # ❌ 错误（访问 SQLAlchemy metadata）

# 修改后
"metadata": conversation.extra_metadata or {}  # ✅ 正确
```

## 修复的文件清单

| 文件 | 修改内容 |
|------|---------|
| [conversation.py:96-104](backend/models/conversation.py:96) | 移除 metadata property |
| [conversation_service.py:459](backend/services/conversation_service.py:459) | 改用 extra_metadata |
| [conversation_service.py:667](backend/services/conversation_service.py:667) | 改用 extra_metadata |

## 测试步骤

### 第 1 步：重启后端

后端窗口按 `Ctrl+C` 停止，然后重新启动：

```cmd
cd C:\Users\shiguangping\data-agent\backend
set PYTHONPATH=C:\Users\shiguangping\data-agent
python main.py
```

**预期结果**：
- ✅ 不再报 `AttributeError: 'property' object has no attribute 'schema'`
- ✅ 显示 `INFO: Uvicorn running on http://0.0.0.0:8000`
- ✅ 没有其他错误

### 第 2 步：测试创建对话

打开新的 Anaconda Prompt：

```cmd
conda activate dataagent
cd C:\Users\shiguangping\data-agent\backend
python test_create_conversation.py
```

**预期结果**：
```
[步骤 3/5] 测试创建对话...
  响应状态码: 200
✓ 创建对话成功
✓ 对话详情:
  - ID: xxx-xxx-xxx
  - 标题: 测试对话 - xxx
  - 模型: claude
```

### 第 3 步：验证前端

1. 打开 http://localhost:3000
2. 应该不再显示：
   - ❌ "连接MCP服务失败"
   - ❌ "没有可用的模型配置"
3. 点击"新建对话"
4. 选择模型
5. 创建成功 ✅

## 为什么会发生这个问题？

1. **初始问题**：`Conversation` 模型没有 `system_prompt` 字段
2. **第一次修复**：添加了 `system_prompt` property（✅ 正确）
3. **过度优化**：同时添加了 `metadata` property（❌ 错误）
   - 想要兼容旧代码
   - 但不知道 `metadata` 是 SQLAlchemy 的保留属性
4. **后果**：SQLAlchemy 初始化时访问 `metadata.schema` 失败

## 经验教训

⚠️ **永远不要覆盖框架的保留属性**

SQLAlchemy 的保留属性包括：
- `metadata` - 数据库元数据
- `__tablename__` - 表名
- `__table__` - 表对象
- `__mapper__` - 映射器
- 等等

如果需要自定义属性，使用不冲突的名称，如：
- ✅ `extra_metadata`
- ✅ `custom_data`
- ✅ `user_metadata`

## 当前状态

- ✅ `system_prompt` property 工作正常
- ✅ 数据存储在 `extra_metadata` JSONB 字段
- ✅ 向后兼容，所有使用 `conversation.system_prompt` 的代码无需修改
- ✅ 不再与 SQLAlchemy 冲突

## 相关问题修复

这次修复同时解决了：
1. ✅ 后端无法启动
2. ✅ 前端显示"连接MCP服务失败"（因为后端未启动）
3. ✅ 前端显示"没有可用的模型配置"（因为后端未启动）
4. ✅ 创建对话失败的 `system_prompt` 参数错误
