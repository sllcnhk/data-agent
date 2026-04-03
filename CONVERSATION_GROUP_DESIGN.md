# 📁 对话分组功能设计文档

## 📋 需求分析

### 核心需求
1. **对话重命名**：可以修改进行中或历史对话的名称
2. **分组管理**：创建、编辑、删除分组（类似文件夹）
3. **对话分组**：手动将对话移动到指定分组
4. **UI展示**：左侧边栏先展示分组，下面展示未分组的对话

### 用户场景
- 场景1：用户创建"工作"、"学习"等分组，将相关对话归类
- 场景2：用户可以将对话在不同分组间移动
- 场景3：用户可以展开/折叠分组查看其中的对话
- 场景4：用户可以重命名任何对话

---

## 🏗️ 架构设计

### 1. 数据库设计

#### 新增表：conversation_groups

```sql
CREATE TABLE conversation_groups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,              -- 分组名称
    description TEXT,                        -- 分组描述
    icon VARCHAR(50),                        -- 图标（emoji或图标名）
    color VARCHAR(20),                       -- 颜色标识
    sort_order INTEGER DEFAULT 0,            -- 排序顺序
    is_expanded BOOLEAN DEFAULT TRUE,        -- 是否展开（前端状态）
    conversation_count INTEGER DEFAULT 0,    -- 对话数量（冗余字段）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX idx_conversation_groups_sort_order ON conversation_groups(sort_order);
CREATE INDEX idx_conversation_groups_name ON conversation_groups(name);
```

#### 修改表：conversations

```sql
-- 添加外键
ALTER TABLE conversations
ADD COLUMN group_id UUID REFERENCES conversation_groups(id) ON DELETE SET NULL;

-- 添加索引
CREATE INDEX idx_conversations_group_id ON conversations(group_id);
```

### 2. API 设计

#### 分组管理 API

| 方法 | 端点 | 功能 | 请求体 |
|------|------|------|--------|
| GET | `/api/v1/groups` | 获取分组列表 | - |
| POST | `/api/v1/groups` | 创建分组 | `{name, description?, icon?, color?}` |
| GET | `/api/v1/groups/{id}` | 获取分组详情 | - |
| PUT | `/api/v1/groups/{id}` | 更新分组 | `{name?, description?, icon?, color?, sort_order?}` |
| DELETE | `/api/v1/groups/{id}` | 删除分组 | - |
| POST | `/api/v1/groups/reorder` | 批量调整顺序 | `{group_ids: [uuid, ...]}` |

#### 对话分组 API

| 方法 | 端点 | 功能 | 请求体 |
|------|------|------|--------|
| PUT | `/api/v1/conversations/{id}/group` | 移动到分组 | `{group_id: uuid \| null}` |
| PUT | `/api/v1/conversations/{id}/title` | 重命名对话 | `{title: string}` |
| GET | `/api/v1/groups/{id}/conversations` | 获取分组内对话 | Query: limit, offset |

### 3. 数据模型

#### ConversationGroup Model

```python
class ConversationGroup(Base):
    __tablename__ = "conversation_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    icon = Column(String(50))
    color = Column(String(20))
    sort_order = Column(Integer, default=0)
    is_expanded = Column(Boolean, default=True)
    conversation_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    conversations = relationship("Conversation", back_populates="group")

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "sort_order": self.sort_order,
            "is_expanded": self.is_expanded,
            "conversation_count": self.conversation_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
```

#### 更新 Conversation Model

```python
class Conversation(Base):
    # ... 现有字段 ...

    # 新增字段
    group_id = Column(UUID(as_uuid=True), ForeignKey("conversation_groups.id"), nullable=True)

    # 新增关系
    group = relationship("ConversationGroup", back_populates="conversations")

    def to_dict(self):
        # ... 现有字段 ...
        data["group_id"] = str(self.group_id) if self.group_id else None
        return data
```

### 4. 前端设计

#### 类型定义

```typescript
// types/conversation.ts
interface ConversationGroup {
  id: string
  name: string
  description?: string
  icon?: string
  color?: string
  sort_order: number
  is_expanded: boolean
  conversation_count: number
  created_at: string
  updated_at: string
}

interface Conversation {
  // ... 现有字段 ...
  group_id?: string  // 新增
}

// Store State
interface ChatState {
  // 新增
  groups: ConversationGroup[]
  setGroups: (groups: ConversationGroup[]) => void
  addGroup: (group: ConversationGroup) => void
  updateGroup: (id: string, data: Partial<ConversationGroup>) => void
  deleteGroup: (id: string) => void

  // 修改
  moveConversationToGroup: (conversationId: string, groupId: string | null) => void
  renameConversation: (conversationId: string, title: string) => void
}
```

#### UI 组件结构

```
ConversationSidebar (新)
├─ NewConversationButton
├─ GroupSection (新)
│  └─ GroupItem[] (可展开/折叠)
│     ├─ GroupHeader
│     │  ├─ Icon + Name
│     │  ├─ ConversationCount
│     │  └─ Actions (编辑/删除)
│     └─ ConversationList (该组内的对话)
│        └─ ConversationItem[]
│
└─ UngroupedSection
   ├─ Header ("未分组")
   └─ ConversationList (未分组的对话)
      └─ ConversationItem[]

ConversationItem (修改)
├─ Title (可重命名)
├─ PinIcon
├─ MessageCount
├─ Time
└─ Actions
   ├─ Rename
   ├─ Move to Group
   └─ Delete
```

### 5. API 服务层

```typescript
// services/groupApi.ts
export const groupApi = {
  listGroups: () => axios.get('/api/v1/groups'),
  createGroup: (data: CreateGroupRequest) => axios.post('/api/v1/groups', data),
  updateGroup: (id: string, data: UpdateGroupRequest) => axios.put(`/api/v1/groups/${id}`, data),
  deleteGroup: (id: string) => axios.delete(`/api/v1/groups/${id}`),
  reorderGroups: (groupIds: string[]) => axios.post('/api/v1/groups/reorder', { group_ids: groupIds }),
  getGroupConversations: (id: string, params?: PaginationParams) =>
    axios.get(`/api/v1/groups/${id}/conversations`, { params })
}

// services/conversationApi.ts (扩展)
export const conversationApi = {
  // ... 现有方法 ...

  moveToGroup: (id: string, groupId: string | null) =>
    axios.put(`/api/v1/conversations/${id}/group`, { group_id: groupId }),

  renameConversation: (id: string, title: string) =>
    axios.put(`/api/v1/conversations/${id}/title`, { title })
}
```

---

## 🎨 UI/UX 设计

### 左侧边栏布局

```
┌──────────────────────────────┐
│  [+ New Conversation]        │ ← 固定在顶部
├──────────────────────────────┤
│  📁 工作 (5) ▾                │ ← 分组（可折叠）
│    ├─ 项目需求分析 (12)     │
│    ├─ 📌 代码审查记录 (8)   │ ← 置顶
│    └─ API设计讨论 (3)        │
│                              │
│  📁 学习 (3) ▸                │ ← 折叠状态
│                              │
│  [+ 新建分组]                │ ← 可选
├──────────────────────────────┤
│  未分组                       │
│    ├─ 临时对话 (2)           │
│    └─ 测试 (1)               │
└──────────────────────────────┘
```

### 交互细节

1. **重命名对话**
   - 双击标题进入编辑模式
   - 或右键菜单 → "重命名"
   - Enter保存，Esc取消

2. **移动到分组**
   - 拖拽对话到分组
   - 或右键菜单 → "移动到分组" → 选择分组

3. **分组管理**
   - 分组右键菜单：重命名、删除、设置图标/颜色
   - 拖拽调整分组顺序
   - 点击展开/折叠图标

4. **视觉反馈**
   - 拖拽时显示目标位置指示
   - 保存时显示 loading 状态
   - 操作成功/失败的 toast 提示

---

## 🔄 实现流程

### Phase 1: 后端基础 ✅
1. 创建数据库迁移脚本
2. 实现 ConversationGroup 模型
3. 实现分组管理 API
4. 实现对话分组 API
5. 编写单元测试

### Phase 2: 前端基础 ✅
1. 定义 TypeScript 类型
2. 实现 groupApi 服务
3. 扩展 useChatStore
4. 编写组件测试

### Phase 3: UI 实现 ✅
1. 实现 GroupItem 组件
2. 实现 ConversationItem 重命名
3. 重构 ConversationSidebar
4. 实现拖拽功能
5. 实现右键菜单

### Phase 4: 集成测试 ✅
1. 端到端功能测试
2. 性能测试
3. 边界情况测试
4. 用户体验优化

---

## 📊 数据流

### 创建分组流程
```
User Input (前端)
  → groupApi.createGroup()
  → POST /api/v1/groups
  → GroupService.create_group()
  → DB INSERT conversation_groups
  → 返回新分组
  → 更新 Store
  → 刷新 UI
```

### 移动对话到分组流程
```
User Drag/Drop (前端)
  → conversationApi.moveToGroup()
  → PUT /api/v1/conversations/{id}/group
  → ConversationService.update_conversation()
  → DB UPDATE conversations SET group_id = ?
  → 更新分组计数器
  → 返回成功
  → 更新 Store
  → 刷新 UI
```

---

## 🧪 测试计划

### 后端测试
- [ ] 创建分组
- [ ] 更新分组
- [ ] 删除分组（对话应设为未分组）
- [ ] 分组排序
- [ ] 对话移动到分组
- [ ] 对话重命名
- [ ] 获取分组内对话列表

### 前端测试
- [ ] 创建分组表单验证
- [ ] 分组展开/折叠
- [ ] 拖拽对话到分组
- [ ] 右键菜单交互
- [ ] 重命名对话
- [ ] 删除分组确认
- [ ] 响应式布局

### 集成测试
- [ ] 完整的分组创建-移动-删除流程
- [ ] 多用户并发操作
- [ ] 大量对话的性能
- [ ] 边界情况处理

---

## 🚀 优化建议

### 性能优化
1. **虚拟滚动**：对话列表过长时使用虚拟滚动
2. **懒加载**：分组内对话按需加载
3. **缓存**：分组列表缓存，减少请求
4. **批量操作**：支持批量移动对话

### 用户体验
1. **拖拽预览**：显示拖拽目标预览
2. **撤销操作**：支持撤销最近的移动/重命名
3. **搜索过滤**：跨分组搜索对话
4. **快捷键**：常用操作的键盘快捷键

### 扩展功能
1. **分组共享**：多用户共享分组（企业版）
2. **智能分组**：基于内容自动建议分组
3. **分组标签**：支持多标签，一个对话属于多个分组
4. **分组导出**：导出整个分组的对话记录

---

## 📝 技术栈

### 后端
- **框架**: FastAPI
- **ORM**: SQLAlchemy
- **数据库**: PostgreSQL
- **验证**: Pydantic

### 前端
- **框架**: React 18 + TypeScript
- **状态管理**: Zustand
- **UI库**: Ant Design
- **拖拽**: react-beautiful-dnd 或 @dnd-kit
- **请求**: Axios

---

## 🎯 成功标准

1. ✅ 用户可以创建、编辑、删除分组
2. ✅ 用户可以将对话移动到任意分组或移出分组
3. ✅ 用户可以重命名任何对话
4. ✅ 左侧边栏先展示分组，再展示未分组对话
5. ✅ 分组可以展开/折叠
6. ✅ 支持拖拽操作
7. ✅ 所有操作有明确的视觉反馈
8. ✅ 数据持久化到数据库
9. ✅ API 响应时间 < 200ms
10. ✅ 前端操作流畅，无卡顿

---

## 📅 时间估算

| 阶段 | 工作量 | 说明 |
|------|--------|------|
| 数据库设计 | 0.5小时 | 已完成设计 |
| 后端开发 | 2小时 | 模型+API+测试 |
| 前端开发 | 3小时 | 组件+Store+集成 |
| 测试验证 | 1小时 | 功能测试+修复 |
| **总计** | **6.5小时** | 1个工作日完成 |

---

开始实施！✨
