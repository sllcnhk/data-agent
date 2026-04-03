# 数据智能分析Agent系统 - 前端

基于React + TypeScript + Ant Design构建的现代化前端应用。

## 技术栈

- **框架**: React 18
- **语言**: TypeScript
- **UI库**: Ant Design 5
- **图表**: Recharts
- **路由**: React Router v6
- **状态管理**: Zustand
- **构建工具**: Vite
- **HTTP客户端**: Axios

## 项目结构

```
frontend/
├── public/                 # 静态资源
├── src/
│   ├── components/         # 公共组件
│   │   ├── AppLayout.tsx # 应用布局
│   │   └── ChartComponent.tsx # 图表组件
│   ├── pages/            # 页面组件
│   │   ├── Dashboard.tsx # 系统仪表盘
│   │   ├── Agents.tsx    # Agent管理
│   │   ├── Tasks.tsx     # 任务管理
│   │   └── Skills.tsx    # 技能中心
│   ├── services/         # API服务
│   │   └── api.ts        # API客户端
│   ├── store/            # 状态管理
│   │   └── useAgentStore.ts # Agent Store
│   ├── hooks/            # 自定义Hooks
│   │   └── useApi.ts     # API调用Hook
│   ├── types/            # 类型定义
│   │   └── api.ts        # API类型
│   ├── App.tsx           # 根组件
│   ├── main.tsx          # 入口文件
│   └── index.css         # 全局样式
├── package.json          # 项目配置
├── tsconfig.json         # TypeScript配置
├── vite.config.ts        # Vite配置
└── .env                  # 环境变量
```

## 功能特性

### 1. 系统仪表盘 (`/dashboard`)
- 实时显示系统健康状态
- Agent状态统计
- 任务执行情况可视化
- 系统性能指标

### 2. Agent管理 (`/agents`)
- 查看所有Agent列表
- 创建新Agent
- 删除Agent
- 查看Agent详情和指标
- 实时监控Agent状态

### 3. 任务管理 (`/tasks`)
- 提交新任务
- 查看任务状态
- 任务历史记录
- 任务详情查看
- 智能任务路由建议

### 4. 技能中心 (`/skills`)
- 浏览所有可用技能
- 执行技能
- 查看技能详情和Schema
- 技能示例演示

## 安装和运行

### 安装依赖

```bash
cd frontend
npm install
```

### 开发环境运行

```bash
npm run dev
```

访问 http://localhost:3000

### 构建生产版本

```bash
npm run build
```

### 预览生产版本

```bash
npm run preview
```

## 环境变量

创建 `.env` 文件并配置：

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_APP_TITLE=数据智能分析Agent系统
```

## API配置

### 代理配置

开发环境中，Vite已配置代理，将 `/api` 请求转发到后端服务器：

```typescript
// vite.config.ts
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
    },
  },
},
```

### API服务

API客户端位于 `src/services/api.ts`，提供以下服务：

- `agentApi` - Agent相关API
- `taskApi` - 任务相关API
- `skillApi` - 技能相关API
- `systemApi` - 系统相关API

## 状态管理

使用Zustand进行状态管理，主要Store：

- `useAgentStore` - Agent和任务状态

## 自定义Hooks

### useAsync

用于异步数据获取：

```typescript
const { data, loading, error, execute } = useAsync(
  () => agentApi.getAgents(),
  true
);
```

### useIntervalAsync

用于定时刷新数据：

```typescript
const { data, loading, error, refresh } = useIntervalAsync(
  () => systemApi.healthCheck(),
  5000 // 5秒刷新一次
);
```

## 组件开发

### ChartComponent

通用的图表组件，支持多种图表类型：

```typescript
<ChartComponent
  type="bar"
  data={data}
  xKey="name"
  yKey="value"
  title="图表标题"
  height={300}
/>
```

支持的图表类型：
- `line` - 折线图
- `area` - 面积图
- `bar` - 柱状图
- `pie` - 饼图
- `scatter` - 散点图

## 开发指南

### 添加新页面

1. 在 `src/pages/` 下创建页面组件
2. 在 `App.tsx` 中添加路由
3. 在侧边栏菜单中添加导航项

### 添加新API

1. 在 `src/services/api.ts` 中添加API函数
2. 在 `src/types/api.ts` 中添加类型定义

### 添加新组件

1. 在 `src/components/` 下创建组件
2. 在需要使用的页面中导入

## 最佳实践

### 1. 组件设计
- 保持组件小而专注
- 使用TypeScript严格类型检查
- 合理使用memo优化性能

### 2. 状态管理
- 本地状态使用useState
- 全局状态使用Zustand
- 避免不必要的状态提升

### 3. API调用
- 使用自定义Hook封装异步逻辑
- 统一错误处理
- 适当添加加载状态

### 4. 样式
- 使用Ant Design组件
- 全局样式统一管理
- 避免行内样式

## 常见问题

### Q: 前端无法连接到后端API
A: 检查后端服务器是否启动，确保API_BASE_URL配置正确

### Q: 图表不显示
A: 检查数据格式是否正确，确保xKey和yKey与数据结构匹配

### Q: 如何添加新的图表类型
A: 在ChartComponent中添加新的渲染逻辑

## 许可证

MIT License
