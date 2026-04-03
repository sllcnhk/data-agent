# data-agent RBAC 架构设计

> **文档类型**：架构设计（待实现）
> **编写日期**：2026-03-17
> **状态**：设计完成，未开始开发
> **预计实现版本**：v2.0

---

## 1. 目标与边界

### 1.1 近期目标（本文档覆盖范围）

1. **多用户支持**：每个用户独立账号，对话历史和用户技能相互隔离
2. **本地账号管理**：管理员后台创建账号，支持用户名+密码登录
3. **角色权限控制**：角色决定菜单可见性和 API 访问权限
4. **前端菜单权限**：无权限的菜单项完全不渲染（不是灰色）

### 1.2 远期目标（预留接口，本文档不实现）

- Lark（飞书）OAuth SSO 登录
- 企业微信 / 钉钉 SSO 登录
- LDAP / SAML 集成

### 1.3 设计原则

- **AuthProvider 可插拔**：本地/Lark/企微切换不影响权限模型
- **JWT 格式统一**：无论哪种登录方式，下游权限验证代码完全透明
- **向后兼容**：`ENABLE_AUTH=false`（默认）时系统行为与现在完全一致，不破坏现有流程
- **最小权限原则**：新建账号默认赋予 `viewer` 角色（最低权限），由管理员升级

---

## 2. 数据模型

### 2.1 实体关系图

```
┌──────────────┐    N:M    ┌──────────────┐    N:M    ┌──────────────────┐
│    users     │──────────▶│    roles     │──────────▶│   permissions    │
│──────────────│           │──────────────│           │──────────────────│
│ id (UUID)    │           │ id (UUID)    │           │ id (UUID)        │
│ username     │           │ name         │           │ resource         │
│ display_name │           │ description  │           │ action           │
│ email        │           │ is_system    │           │ description      │
│ auth_source  │           │ created_at   │           └──────────────────┘
│ external_id  │           └──────────────┘
│ hashed_pw    │              (关联表)
│ is_active    │           user_roles: user_id / role_id / assigned_at / assigned_by
│ is_superadmin│           role_permissions: role_id / permission_id
│ last_login_at│
│ extra_meta   │  ← Lark 用户信息、头像 URL 等扩展字段（JSON）
│ created_at   │
│ updated_at   │
└──────────────┘

┌──────────────────────────┐
│     refresh_tokens       │
│ id (UUID) / jti          │
│ user_id                  │
│ expires_at               │
│ revoked                  │
│ created_at               │
└──────────────────────────┘
```

### 2.2 `users` 表关键字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `auth_source` | enum | `local` \| `lark` \| `wecom` \| `dingtalk` |
| `external_id` | str/null | OAuth 登录时的外部 ID（如 Lark open_id）；本地账号为 null |
| `hashed_pw` | str/null | bcrypt 哈希；SSO 登录账号为 null |
| `is_superadmin` | bool | 超级管理员，绕过所有权限检查，作为系统保底账号 |
| `extra_meta` | JSON | 扩展字段：头像、部门、Lark 用户名等 |

### 2.3 `auth_source` 枚举

```python
class AuthSource(str, Enum):
    LOCAL   = "local"    # 本地账号（用户名+密码）
    LARK    = "lark"     # 飞书 OAuth
    WECOM   = "wecom"    # 企业微信（预留）
    DINGTALK = "dingtalk" # 钉钉（预留）
```

---

## 3. 权限矩阵

### 3.1 权限定义（resource + action）

| resource | action | 说明 |
|---------|--------|------|
| `chat` | `use` | 使用对话功能 |
| `skills.user` | `read` | 查看自己的用户技能 |
| `skills.user` | `write` | 创建/编辑/删除自己的用户技能 |
| `skills.project` | `read` | 查看项目技能 |
| `skills.project` | `write` | 创建/编辑/删除项目技能 |
| `skills.system` | `read` | 查看系统技能（所有人） |
| `models` | `read` | 查看 LLM 模型配置 |
| `models` | `write` | 新增/修改/删除 LLM Config |
| `users` | `read` | 查看用户列表 |
| `users` | `write` | 创建/停用/修改用户 |
| `users` | `assign_role` | 分配/撤销角色 |
| `settings` | `read` | 查看系统设置 |
| `settings` | `write` | 修改系统设置 |

### 3.2 预置角色

| 角色 | 说明 | 权限集合 |
|------|------|---------|
| `viewer` | 只读访客 | `chat:use` |
| `analyst` | 数据分析师（推荐默认） | `chat:use` + `skills.user:read/write` + `skills.project:read` + `skills.system:read` |
| `admin` | 项目管理员 | analyst 全部 + `skills.project:write` + `models:read/write` + `settings:read/write` |
| `superadmin` | 超级管理员 | 全部，含 `users:*` |

> **Bootstrap 规则**：第一个账号（通过 `create_superadmin.py` 脚本创建）自动设置 `is_superadmin=True`，绕过所有权限检查，确保系统初始化后始终有一个可操作的管理员账号。

---

## 4. 认证流程

### 4.1 本地账号登录（近期实现）

```
POST /auth/login
  body: { username, password }
  →  users 表查找（auth_source='local'）
  →  bcrypt.verify(password, hashed_pw)
  →  生成 access_token（JWT, HS256, 8h exp）
  →  生成 refresh_token（存 DB, 14天有效）
  →  响应: { access_token, refresh_token, token_type: "bearer", expires_in: 28800 }

POST /auth/refresh
  body: { refresh_token }
  →  查 DB 验证 jti + 未过期 + 未撤销
  →  颁发新 access_token + 轮换 refresh_token（旧 token 标记 revoked=true）

POST /auth/logout
  body: { refresh_token }
  →  DB 中标记 refresh_token.revoked = true

GET /auth/me
  header: Authorization: Bearer <access_token>
  →  解析 JWT → 查 user + roles
  →  响应: { id, username, display_name, email, auth_source, permissions: ["chat:use", "skills.user:read", ...] }
```

### 4.2 JWT Payload 格式（统一，与 auth_source 无关）

```json
{
  "sub": "user-uuid-here",
  "username": "zhangsan",
  "roles": ["analyst"],
  "exp": 1234567890,
  "iat": 1234560000,
  "jti": "unique-token-id"
}
```

### 4.3 Lark SSO（预留接口，不实现逻辑）

```
GET /auth/lark/login
  →  重定向到 Lark OAuth URL（client_id + redirect_uri + state）
  →  实现时读取 settings.lark_app_id, settings.lark_app_secret

GET /auth/lark/callback?code=xxx&state=yyy
  →  用 code 换取 Lark access_token
  →  调用 Lark /user/v3/me 获取 open_id + name + email
  →  查找 users WHERE auth_source='lark' AND external_id=open_id
  →  不存在 → 自动注册（赋 analyst 角色，管理员可后续调整）
  →  颁发系统 JWT（与本地账号格式完全相同）
```

**所有下游权限逻辑对 `auth_source` 完全透明**，切换 SSO 供应商零改动。

### 4.4 AuthProvider 抽象接口（可插拔设计）

```python
# backend/core/auth/base.py

class AuthProvider(ABC):
    """所有认证供应商的基类"""

    @abstractmethod
    async def authenticate(self, credentials: dict) -> Optional[User]:
        """验证凭据，返回 User 或 None"""

    @abstractmethod
    def get_oauth_url(self, state: str) -> Optional[str]:
        """生成 OAuth 跳转 URL（本地账号返回 None）"""

    @abstractmethod
    async def handle_callback(self, code: str, state: str) -> Optional[User]:
        """处理 OAuth 回调，返回 User（本地账号不实现）"""
```

---

## 5. API 权限控制

### 5.1 FastAPI 依赖注入模式

```python
# backend/api/deps.py

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析 Bearer token，返回当前用户；401 if missing/expired"""
    ...

def require_permission(resource: str, action: str):
    """
    工厂函数，返回 FastAPI Depends。
    superadmin 用户直接通过，无需检查 permission 表。
    """
    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.is_superadmin:
            return user
        perm_key = f"{resource}:{action}"
        user_perms = await get_user_permissions(user.id)
        if perm_key not in user_perms:
            raise HTTPException(status_code=403, detail=f"权限不足: 需要 {perm_key}")
        return user
    return Depends(_check)

# 使用示例
@router.post("/project-skills")
async def create_project_skill(
    skill: UserSkillCreate,
    user: User = Depends(require_permission("skills.project", "write")),
):
    ...
```

### 5.2 `ENABLE_AUTH=false` 兼容模式

```python
async def get_current_user_optional() -> Optional[User]:
    """
    ENABLE_AUTH=false 时返回内置匿名用户（所有权限），
    ENABLE_AUTH=true 时走正常 JWT 验证。
    """
    if not settings.enable_auth:
        return AnonymousUser(id="default", is_superadmin=True)
    ...
```

---

## 6. 用户技能路径隔离

多用户场景下，用户技能按 user_id 存放独立子目录：

```
.claude/skills/
├── system/                 ← 全局只读（所有用户可见）
├── project/                ← 全局，admin 维护（所有用户可见）
└── user/
    ├── <user_id_1>/        ← 用户1的技能（只有用户1可见/修改）
    │   └── my-skill.md
    └── <user_id_2>/        ← 用户2的技能
        └── other.md
```

**改动范围**：
- `SkillLoader.__init__` 支持 `user_id` 参数，加载 `user/{user_id}/` 子目录
- `build_skill_prompt_async(message, user_id=...)` 透传 user_id 到 skill 加载
- `AgenticLoop` 从请求上下文中提取 user_id 传入 skill loader
- Skills API 所有 user-defined 端点改为使用认证用户的 user_id（替换硬编码 "default"）
- `FilesystemPermissionProxy` 的写权限路径改为 `.claude/skills/user/{user_id}/`
- **无认证模式（ENABLE_AUTH=false）**：user_id="default"，路径退回 `.claude/skills/user/`（与现在相同）

---

## 7. 前端实现

### 7.1 菜单权限过滤

```typescript
// frontend/src/store/useAuthStore.ts
interface AuthState {
  user: UserInfo | null;
  permissions: string[];  // e.g. ["chat:use", "skills.user:read", "models:read"]
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

// frontend/src/components/Layout/Sidebar.tsx
const menuItems = [
  { key: 'chat',     label: '对话',     icon: <MessageOutlined />, perm: 'chat:use'           },
  { key: 'skills',   label: '技能中心', icon: <ThunderboltOutlined />, perm: 'skills.user:read'   },
  { key: 'models',   label: '模型配置', icon: <ApiOutlined />,     perm: 'models:read'        },
  { key: 'users',    label: '用户管理', icon: <TeamOutlined />,    perm: 'users:read'         },
  { key: 'settings', label: '系统设置', icon: <SettingOutlined />, perm: 'settings:read'      },
];

// 无 perm → 菜单项不渲染（完全隐藏，不是灰色）
const visibleItems = menuItems.filter(item =>
  !item.perm || permissions.includes(item.perm)
);
```

### 7.2 Token 管理策略

| Token 类型 | 存储位置 | 有效期 | 说明 |
|-----------|---------|-------|------|
| `access_token` | JS memory（Zustand store） | 8h | 不写 localStorage，防 XSS |
| `refresh_token` | httpOnly Cookie | 14天 | 防 JS 读取，随请求自动携带 |

axios 拦截器自动附加 `Authorization: Bearer <access_token>`，401 时自动触发 refresh，refresh 失败则跳登录页。

### 7.3 Lark 登录入口（占位）

```typescript
// Login.tsx
<Button
  disabled  // 后端返回 501 前保持禁用
  onClick={() => window.location.href = '/auth/lark/login'}
  icon={<img src="/lark-icon.svg" />}
>
  使用飞书登录
</Button>
```

---

## 8. 新增 API 端点一览

基础路径：`/api/v1`

### 认证 `/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/login` | 本地账号登录，颁发 JWT |
| POST | `/auth/refresh` | 刷新 access_token（refresh_token 轮换） |
| POST | `/auth/logout` | 登出，撤销 refresh_token |
| GET  | `/auth/me` | 当前用户信息 + 权限列表 |
| GET  | `/auth/lark/login` | Lark OAuth 跳转（501 占位） |
| GET  | `/auth/lark/callback` | Lark OAuth 回调（501 占位） |

### 用户管理 `/users`

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/users` | `users:write` | 创建本地账号 |
| GET  | `/users` | `users:read` | 用户列表（分页） |
| GET  | `/users/{id}` | `users:read` | 用户详情 |
| PUT  | `/users/{id}` | 自改无需权限，改他人需 `users:write` | 修改 display_name / is_active |
| PUT  | `/users/{id}/password` | 自改 | 修改密码（需提供旧密码） |
| POST | `/users/{id}/roles` | `users:assign_role` | 分配角色 |
| DELETE | `/users/{id}/roles/{role_id}` | `users:assign_role` | 撤销角色 |

### 角色管理 `/roles`

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET  | `/roles` | `users:read` | 角色列表（含权限详情） |

---

## 9. 新增文件清单

```
backend/
├── core/auth/
│   ├── __init__.py
│   ├── base.py              # AuthProvider 抽象基类
│   ├── jwt.py               # create_access_token / decode_token
│   ├── password.py          # bcrypt hash + verify
│   └── providers/
│       ├── local.py         # 本地账号 Provider
│       └── lark.py          # Lark Provider（骨架 + TODO）
├── models/
│   ├── user.py              # User ORM
│   ├── role.py              # Role ORM
│   ├── permission.py        # Permission ORM
│   ├── user_role.py         # UserRole 关联表 ORM
│   ├── role_permission.py   # RolePermission 关联表 ORM
│   └── refresh_token.py     # RefreshToken ORM
├── api/
│   ├── auth.py              # 认证端点（login/refresh/logout/me/lark-*）
│   └── users.py             # 用户管理端点
└── scripts/
    └── create_superadmin.py # Bootstrap 脚本

frontend/src/
├── pages/
│   ├── Login.tsx            # 登录页（含 Lark 占位按钮）
│   └── Users.tsx            # 用户管理页
└── store/
    └── useAuthStore.ts      # Zustand auth store

alembic/versions/
└── xxxx_rbac_tables.py      # DB migration（含初始数据）

test_auth.py                 # 认证单元测试
test_rbac.py                 # 权限中间件测试
test_user_skill_isolation.py # 技能隔离测试
```

---

## 10. 修改文件清单

| 文件 | 改动内容 |
|------|---------|
| `backend/api/deps.py` | 新增 `get_current_user` / `require_permission` / `get_current_user_optional` |
| `backend/api/skills.py` | 所有 user-defined 端点改用 `get_current_user`，user_id 从 token 提取 |
| `backend/config/settings.py` | 新增 `enable_auth` / `jwt_secret` / `jwt_algorithm` / `access_token_expire_minutes` / `lark_app_id` / `lark_app_secret` |
| `backend/skills/skill_loader.py` | `SkillLoader` 支持 per-user 子目录；`build_skill_prompt_async` 透传 user_id |
| `backend/agents/agentic_loop.py` | 从请求上下文提取 user_id 传入 `_build_system_prompt` |
| `backend/core/filesystem_permission_proxy.py` | 写权限路径改为 `.claude/skills/user/{user_id}/` |
| `backend/main.py` | 挂载 `/auth` 和 `/users` 路由 |
| `frontend/src/services/api.ts` | axios 拦截器 + Bearer token + 401 自动 refresh |
| `frontend/src/components/Layout/Sidebar.tsx` | 按 permissions 动态渲染菜单 |

---

## 11. 实施顺序

```
Phase A（数据库）: ORM 模型 + Alembic migration + Bootstrap 脚本
    ↓
Phase B（认证核心）: JWT 工具 + bcrypt + 本地 Provider + /auth/* API
    ↓
Phase C（依赖注入）: get_current_user + require_permission + ENABLE_AUTH 兼容模式
    ↓
Phase D（用户管理 API）: /users/* CRUD + 角色分配
    ↓
Phase E（技能隔离）: per-user 子目录 + API user_id 注入 + 权限路径更新
    ↓
Phase F（前端）: 登录页 + AuthStore + axios 拦截器 + 菜单权限 + 用户管理页 + Lark 占位按钮
    ↓
Phase G（测试）: test_auth + test_rbac + test_user_skill_isolation + 回归

各 Phase 可独立交付并测试，ENABLE_AUTH=false 时全程不影响现有功能。
```

---

## 12. `.env` 新增配置项

```ini
# ================================
# 用户认证配置
# ================================
# 是否启用用户认证（false=单用户模式，保持当前行为）
ENABLE_AUTH=false

# JWT 签名密钥（启用认证时必须设置为随机长字符串）
JWT_SECRET=your-very-long-random-secret-here

# JWT 算法
JWT_ALGORITHM=HS256

# Access Token 有效期（分钟）
ACCESS_TOKEN_EXPIRE_MINUTES=480

# Refresh Token 有效期（天）
REFRESH_TOKEN_EXPIRE_DAYS=14

# ================================
# Lark OAuth（预留，不启用时留空）
# ================================
LARK_APP_ID=
LARK_APP_SECRET=
LARK_REDIRECT_URI=http://localhost:3000/auth/lark/callback
```

---

*文档由 Claude Sonnet 4.6 生成 · 2026-03-17 · 待实现*
