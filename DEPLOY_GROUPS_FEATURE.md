# 对话分组功能部署指南

## 快速部署

### 1. 确认环境

确保系统已经按照 [QUICK_START.md](QUICK_START.md) 完成基本配置。

### 2. 运行数据库迁移

**重要**: 必须先运行迁移脚本添加分组表和字段

```bash
cd C:\Users\shiguangping\data-agent\backend
python init_groups.py
```

**预期输出**:
```
Initializing conversation groups...
✓ Table 'conversation_groups' ready
✓ Column 'group_id' ready in conversations table
✓ Index created
✅ Conversation groups initialized successfully!
```

### 3. 启动服务

```bash
cd C:\Users\shiguangping\data-agent
conda activate dataagent
start-all.bat
```

### 4. 验证部署

访问应用: http://localhost:3000

**检查项**:
- [ ] 侧边栏显示"新建对话"按钮和"文件夹+"按钮
- [ ] 可以点击"文件夹+"创建分组
- [ ] 可以在对话菜单中看到"移动到"选项
- [ ] 可以在对话菜单中看到"重命名"选项

## 功能使用

### 创建分组

1. 点击侧边栏顶部的 **文件夹+** 按钮
2. 输入分组名称(例如: "工作项目")
3. 点击"确定"
4. 分组出现在侧边栏顶部

### 移动对话到分组

1. 点击对话右侧的 **...** 按钮
2. 选择"移动到" → 选择目标分组
3. 对话自动移动到该分组下

### 重命名对话

1. 点击对话右侧的 **...** 按钮
2. 选择"重命名"
3. 在弹出框中输入新标题
4. 点击确定

### 管理分组

1. 点击分组右侧的 **...** 按钮
2. 可以选择:
   - **重命名**: 修改分组名称
   - **删除分组**: 删除分组(对话自动移到未分组)

### 展开/折叠分组

- 点击分组名称区域即可展开或折叠分组
- 展开后可以看到分组内的所有对话

## 测试验证

### 后端 API 测试

```bash
cd C:\Users\shiguangping\data-agent\backend
python test_groups_simple.py
```

**所有测试应该通过**:
```
1. Creating groups...           [OK]
2. Listing groups...            [OK]
3. Creating conversation...     [OK]
4. Moving conversation...       [OK]
5. Renaming conversation...     [OK]
6. Getting conversations...     [OK]
All tests completed!
```

### 前端手动测试

参考 [test_groups_integration.md](frontend/test_groups_integration.md) 进行完整的功能测试。

**关键测试点**:
- 创建/重命名/删除分组
- 移动对话到分组
- 重命名对话
- 展开/折叠分组
- 数据持久化(刷新页面后数据保持)

## 常见问题

### Q1: 点击"文件夹+"按钮没有反应?

**检查**:
1. 打开浏览器控制台(F12)查看错误
2. 确认后端服务正常运行
3. 确认数据库迁移已执行

### Q2: 创建分组提示"分组名称已存在"?

**原因**: 分组名称必须唯一

**解决**: 使用不同的名称或删除重名分组

### Q3: 删除分组后,分组内的对话消失了?

**说明**: 这不应该发生!对话应该自动移到"未分组"区域

**检查**:
1. 刷新页面
2. 查看"未分组"区域
3. 如果仍然看不到,检查数据库或后端日志

### Q4: 刷新页面后分组消失了?

**检查**:
1. 确认数据库迁移已正确执行
2. 查看浏览器控制台是否有加载错误
3. 查看后端日志: `backend/logs/app.log`

### Q5: 移动对话到分组后,分组对话数量没更新?

**刷新**: 当前实现会在移动后重新加载分组列表,如果没更新,尝试刷新页面

## API 文档

访问 Swagger 文档查看所有 API:
http://localhost:8000/api/docs

**分组相关 API**:
- `POST /api/v1/groups` - 创建分组
- `GET /api/v1/groups` - 获取分组列表
- `PUT /api/v1/groups/{id}` - 更新分组
- `DELETE /api/v1/groups/{id}` - 删除分组

**对话相关 API**:
- `PUT /api/v1/conversations/{id}/title` - 重命名对话
- `PUT /api/v1/conversations/{id}/group` - 移动对话到分组

## 回滚步骤

如果需要回滚此功能:

1. **前端回滚**: 恢复以下文件的旧版本
   - `frontend/src/pages/Chat.tsx`
   - `frontend/src/store/useChatStore.ts`
   - `frontend/src/services/chatApi.ts`

2. **后端回滚**:
   ```sql
   -- 连接数据库执行
   ALTER TABLE conversations DROP COLUMN group_id;
   DROP TABLE conversation_groups;
   ```

3. **删除新增文件**:
   - `backend/models/conversation_group.py`
   - `backend/api/groups.py`
   - `frontend/src/components/chat/GroupItem.tsx`
   - `frontend/src/components/chat/ConversationSidebar.tsx`

## 性能说明

- 分组数据在页面加载时一次性获取
- 对话列表每次操作后会刷新
- 展开/折叠状态存储在前端内存中
- 建议分组数量不超过 50 个
- 建议每个分组内对话数量不超过 100 个

## 下一步

功能已全部实现并测试通过,可以开始使用!

**推荐工作流**:
1. 创建几个常用分组(如"工作"、"学习"、"项目")
2. 将现有对话移动到对应分组
3. 新对话创建后手动移动到分组
4. 定期整理和重命名对话

**反馈渠道**:
- 发现问题请检查日志
- 记录错误信息和操作步骤
- 必要时提供浏览器控制台截图

---

## 部署清单

部署前检查:
- [ ] 已阅读本文档
- [ ] 已运行数据库迁移
- [ ] 已测试后端 API
- [ ] 已验证前端功能
- [ ] 已了解使用方法

部署完成:
- [ ] 后端服务正常运行
- [ ] 前端页面可访问
- [ ] 可以创建分组
- [ ] 可以移动对话
- [ ] 可以重命名对话
- [ ] 数据持久化正常

**祝使用愉快!** 🎉
