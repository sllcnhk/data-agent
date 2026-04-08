# 部署指南

> 版本：v2.3 · 2026-04-08（**Skill 用户使用权限隔离 T1–T6**：SkillMD.owner + `_get_visible_user_skills` + `build_skill_prompt_async(user_id=)` + sub_skill 展开隔离 + Preview API effective_user_id；**无 DB 迁移**，纯代码层变更；v2.2 · 2026-04-07：**SQL→Excel 数据导出**：`migrate_data_export.py` DB 迁移（`export_jobs` 表 + `data:export` 权限）；流式写 xlsx + 多 Sheet 自动分割 + 大整数安全转换；v2.1 · 2026-04-05：**Excel 数据导入**：`migrate_data_import.py` DB 迁移（`import_jobs` 表 + `data:import` 权限）；流式上传支持 100MB 大文件；**文件写入下载**：`files_written` SSE 事件 + `GET /api/v1/files/download` 安全下载端点 + `FILE_OUTPUT_DATE_SUBFOLDER` 配置，**无 DB 迁移**；技能路由可视化：`skill_matched` SSE 事件 + `SkillLoader._last_match_info` + ThoughtProcess 🧠 面板 + `GET /skills/load-errors`，**无 DB 迁移**；侧边栏 Tab UI + is_shared：`migrate_add_is_shared.py` DB 迁移；对话用户隔离：`migrate_conversation_user_isolation.py` DB 迁移 + 所有对话/分组端点补全鉴权；customer_data 用户隔离；对话附件上传）
>
> 本文档说明如何将数据智能分析 Agent 系统从 Windows 开发环境迁移到 Linux 服务器，供团队多人共用。

---

## 目录

1. [可行性说明](#1-可行性说明)
2. [技术栈与依赖一览](#2-技术栈与依赖一览)
3. [系统要求](#3-系统要求)
4. [目录结构](#4-目录结构)
5. [方案一：直接部署（裸机 / 虚拟机）](#5-方案一直接部署裸机--虚拟机)
6. [方案二：Docker Compose 部署](#6-方案二docker-compose-部署)
7. [Nginx 反向代理配置](#7-nginx-反向代理配置)
8. [systemd 服务管理](#8-systemd-服务管理)
9. [环境变量完整参考](#9-环境变量完整参考)
10. [数据库初始化与迁移](#10-数据库初始化与迁移)
11. [前端构建与静态托管](#11-前端构建与静态托管)
12. [多用户与认证配置](#12-多用户与认证配置)
13. [安全加固清单](#13-安全加固清单)
14. [监控与日志](#14-监控与日志)
15. [更新与维护](#15-更新与维护)
16. [常见问题排查](#16-常见问题排查)

---

## 1 可行性说明

**可以，且推荐迁移到 Linux 服务器供团队使用。**

| 维度 | 说明 |
|------|------|
| 跨平台兼容性 | 后端 FastAPI + Python 完全兼容 Linux；前端静态文件与平台无关 |
| Windows 特有问题 | 开发时的 `localhost` IPv6/IPv4 错位、路径分隔符等问题在 Linux 上不存在 |
| 多用户支持 | 系统内置 RBAC 认证（ENABLE_AUTH=true 后启用），支持用户注册/登录/角色分配 |
| 生产推荐 | Nginx（反向代理 + 静态托管）+ systemd（进程守护）+ PostgreSQL + Redis |
| 容器化选项 | 无内置 Dockerfile，可按本文 [方案二](#6-方案二docker-compose-部署) 自行容器化 |

---

## 2 技术栈与依赖一览

### 后端

| 组件 | 版本要求 | 用途 |
|------|----------|------|
| Python | 3.10+ | 运行时 |
| FastAPI | 0.104+ | Web 框架，HTTP + SSE |
| Uvicorn | 0.24+ | ASGI 服务器 |
| PostgreSQL | 12+ | 元数据存储（对话、任务、用户、角色） |
| Redis | 6+ | 会话缓存、Celery broker |
| ChromaDB | 0.4+ | Skill 语义路由缓存（本地向量库） |
| Celery | 5.3+ | 异步任务队列 |

### 前端

| 组件 | 版本要求 | 用途 |
|------|----------|------|
| Node.js | 18+ | 构建工具 |
| React | 18 | UI 框架 |
| Ant Design | 5.14 | 组件库 |
| Vite | 5 | 构建打包 |

### 外部服务（按需配置）

| 服务 | 必填 | 说明 |
|------|------|------|
| Anthropic Claude API | 是 | 主 LLM，需 API Key 或中转服务地址 |
| ClickHouse | 视业务 | 数据查询引擎，支持多区域（IDN/SG/MX） |
| MySQL | 视业务 | 业务数据库 |
| 飞书（Lark） | 否 | 消息推送，可禁用 |

### MCP 内部端口（进程内通信）

| 服务 | 默认端口 | 说明 |
|------|----------|------|
| ClickHouse MCP | 50051 | 仅本机监听，不对外暴露 |
| MySQL MCP | 50052 | 同上 |
| Filesystem MCP | 50053 | 同上 |
| Lark MCP | 50054 | 同上 |

> MCP 端口为进程内 gRPC，不需要在防火墙开放，仅 8000（后端）和 80/443（Nginx）需要对外。

---

## 3 系统要求

### 最低配置

| 资源 | 最低 | 推荐 |
|------|------|------|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 20 GB | 50 GB+（日志 + 数据输出） |
| OS | Ubuntu 20.04 / CentOS 7+ / Debian 11+ | Ubuntu 22.04 LTS |
| 网络 | 能访问 Anthropic API 或中转服务 | — |

### 软件依赖安装（Ubuntu 22.04 示例）

```bash
# 系统更新
sudo apt update && sudo apt upgrade -y

# Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Node.js 20（通过 NodeSource）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# PostgreSQL 15
sudo apt install -y postgresql postgresql-contrib

# Redis 7
sudo apt install -y redis-server

# Nginx
sudo apt install -y nginx

# 其他工具
sudo apt install -y git curl unzip build-essential libpq-dev
```

---

## 4 目录结构

部署后建议的服务器目录布局：

```
/opt/data-agent/          ← 应用根目录
├── .env                  ← 生产环境变量（不提交到 git）
├── run.py                ← 后端启动入口
├── backend/              ← FastAPI 应用
├── frontend/             ← React 源码（构建后不需要保留）
├── .claude/              ← Skill 文件（技能定义）
│   └── skills/
│       ├── system/       ← 系统技能（只读）
│       ├── project/      ← 项目技能（管理员可编辑）
│       └── user/         ← 用户自定义技能（ENABLE_AUTH=false: flat 目录；ENABLE_AUTH=true: user/{username}/ 子目录）
├── customer_data/        ← Agent 数据输出根目录（需要写权限）
│   └── {username}/       ← 每位用户独立子目录（服务启动后首次对话时自动创建）
│       ├── imports/      ← Excel 数据导入临时文件（导入任务完成/失败后自动清理）
│       └── exports/      ← SQL→Excel 导出文件（下载后可通过 DELETE /jobs/{id} 手动清理）
├── logs/                 ← 后端日志（自动创建）
├── data/                 ← ChromaDB 缓存（自动创建）
│   ├── vector_db/
│   └── skill_routing_cache/
├── uploads/              ← 文件上传临时目录（预留；当前对话附件为 base64 内嵌请求体，Agent 输出文件写入 customer_data/，均不使用此目录）
├── exports/              ← 导出文件目录
└── backups/              ← 数据库备份目录

/var/www/data-agent/      ← 前端静态文件（Nginx 托管）
└── dist/                 ← npm run build 产物
```

---

## 5 方案一：直接部署（裸机 / 虚拟机）

### 5.1 拉取代码

```bash
sudo mkdir -p /opt/data-agent
sudo chown $USER:$USER /opt/data-agent

# 从 git 克隆（或 scp 上传）
git clone <your-repo-url> /opt/data-agent
cd /opt/data-agent
```

### 5.2 Python 虚拟环境

```bash
cd /opt/data-agent
python3.11 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

### 5.3 配置环境变量

```bash
cp .env.example .env
vim .env   # 按第 9 节修改所有必填项
```

关键修改项：

```ini
# 生产环境必改
DEBUG=false
ENVIRONMENT=production
FRONTEND_URL=https://your-domain.com

# 数据库
POSTGRES_HOST=localhost
POSTGRES_PASSWORD=<强密码>
REDIS_HOST=localhost

# LLM
ANTHROPIC_API_KEY=<你的API Key>
ANTHROPIC_BASE_URL=https://api.anthropic.com   # 或中转地址

# 安全（JWT 认证）
JWT_SECRET=<至少64位随机字符串>          # 建议 openssl rand -hex 32 生成
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=120        # access_token 有效期（分钟），须 ≤ SESSION_IDLE_TIMEOUT_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS=14           # refresh_token DB 记录保留时长（天），默认 14 天
SESSION_IDLE_TIMEOUT_MINUTES=120       # Session 空闲超时（分钟），超时后 /auth/refresh 返回 401
ADMIN_SECRET_TOKEN=<管理员操作令牌>    # 用于项目级 Skill 管理 API
ENABLE_AUTH=true

# Filesystem 权限
# 推荐：使用相对路径（相对于项目根目录），部署到任意服务器无需修改
ALLOWED_DIRECTORIES=["customer_data",".claude/skills"]
# FILESYSTEM_WRITE_ALLOWED_DIRS 指向 user/ 目录（含所有子目录 user/{username}/，ENABLE_AUTH=true 时按用户隔离）
FILESYSTEM_WRITE_ALLOWED_DIRS=["customer_data",".claude/skills/user"]
# 兼容：也可使用绝对路径（适用于数据目录与代码目录分离的 Docker 挂载卷场景）
# ALLOWED_DIRECTORIES=["/opt/data-agent/customer_data","/opt/data-agent/.claude/skills"]
# FILESYSTEM_WRITE_ALLOWED_DIRS=["/opt/data-agent/customer_data","/opt/data-agent/.claude/skills/user"]

# 文件写入下载配置
# false（默认）：Agent 自主决定输出文件路径
# true：向 Agent 注入月份子目录提示（customer_data/{username}/YYYY-MM/），便于按月归档和批量清理历史数据
FILE_OUTPUT_DATE_SUBFOLDER=false

# Skill 缓存（使用绝对路径）
VECTOR_DB_PATH=/opt/data-agent/data/vector_db
SKILL_ROUTING_CACHE_PATH=/opt/data-agent/data/skill_routing_cache
```

### 5.4 初始化数据库

```bash
cd /opt/data-agent
source venv/bin/activate

# 创建 PostgreSQL 数据库和用户
sudo -u postgres psql << 'EOF'
CREATE USER dataagent WITH PASSWORD '<强密码>';
CREATE DATABASE data_agent OWNER dataagent;
GRANT ALL PRIVILEGES ON DATABASE data_agent TO dataagent;
\q
EOF

# 运行 Alembic 迁移（建表/字段更新）
python -m alembic upgrade head

# 初始化 RBAC 角色和权限数据（幂等，重复运行无副作用）
# 写入 4 个预置角色（viewer/analyst/admin/superadmin）和 15 条权限定义
# ENABLE_AUTH=true 时必须执行；ENABLE_AUTH=false 时可跳过
python backend/scripts/init_rbac.py
```

### 5.5 构建前端

```bash
cd /opt/data-agent/frontend

# 生产环境 .env 设置（相对路径，通过 Nginx 反代）
echo 'VITE_API_BASE_URL=/api/v1' > .env
echo 'VITE_APP_TITLE=数据智能分析Agent系统' >> .env

npm install
npm run build

# 将构建产物复制到 Nginx 目录
sudo mkdir -p /var/www/data-agent
sudo cp -r dist/* /var/www/data-agent/
sudo chown -R www-data:www-data /var/www/data-agent
```

### 5.6 创建目录并设置权限

```bash
# 应用目录权限
mkdir -p /opt/data-agent/{customer_data,logs,data/vector_db,data/skill_routing_cache,uploads,exports,backups}
chmod 755 /opt/data-agent/customer_data
# customer_data/{username}/ 子目录由服务在用户首次对话时自动创建（mkdir -p），无需手动预建
chmod 755 /opt/data-agent/logs

# 技能目录：system 只读，user 可写
# ENABLE_AUTH=true 时，服务端在 user/ 下自动创建 user/{username}/ 子目录（mkdir -p），
# 继承父目录的 775 权限，无需手动预建各用户子目录
chmod 755 /opt/data-agent/.claude/skills
chmod 755 /opt/data-agent/.claude/skills/system
chmod 755 /opt/data-agent/.claude/skills/project
chmod 775 /opt/data-agent/.claude/skills/user
```

> **技能写入路径规则（FilesystemPermissionProxy Fix-4）**：
> AI 写技能文件时必须包含 `user/{username}/` 层，直接写到 `user/skill.md` 会被拒绝。
> `create_directory user/{username}/` 是合法操作（深度 ≥ 1），`write_file user/skill.md` 会报错并提示正确格式。
>
> **技能读取可见性规则（T1–T6，2026-04-08）**：
> `ENABLE_AUTH=true` 时，每个用户只能在对话 System Prompt 中看到自己创建的技能（`owner == username`）以及无主遗留技能（`owner == ""`）。其他用户的私有技能既不会注入到 System Prompt，也不会出现在 `/skills/preview` 的 `match_details` 中。系统技能（system/）和项目技能（project/）对所有用户可见，不受此限制。

### 5.7 测试启动

```bash
cd /opt/data-agent
source venv/bin/activate

# 生产模式（无热重载）
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info

# 验证
curl http://localhost:8000/health
# 期望返回: {"status":"healthy"}
```

---

## 6 方案二：Docker Compose 部署

> 适合快速交付和隔离环境。以下配置需在项目根目录新建对应文件。

### 6.1 Dockerfile（后端）

新建 `Dockerfile`：

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY backend/ ./backend/
COPY .claude/ ./.claude/
COPY run.py alembic.ini ./
COPY alembic/ ./alembic/

# 创建运行时目录
RUN mkdir -p customer_data logs data/vector_db data/skill_routing_cache uploads exports

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

### 6.2 Dockerfile.frontend（前端构建）

新建 `Dockerfile.frontend`：

```dockerfile
FROM node:20-alpine AS builder

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN echo 'VITE_API_BASE_URL=/api/v1' > .env && \
    echo 'VITE_APP_TITLE=数据智能分析Agent系统' >> .env && \
    npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

### 6.3 docker-compose.yml

新建 `docker-compose.yml`：

```yaml
version: '3.9'

services:
  postgres:
    image: postgres:15-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: data_agent
      POSTGRES_USER: dataagent
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dataagent -d data_agent"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD:-}
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  backend:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file: .env
    environment:
      POSTGRES_HOST: postgres
      REDIS_HOST: redis
      # Docker 容器内代码在 /app，数据目录可挂载到不同位置，使用绝对路径
      ALLOWED_DIRECTORIES: '["/app/customer_data","/app/.claude/skills"]'
      FILESYSTEM_WRITE_ALLOWED_DIRS: '["/app/customer_data","/app/.claude/skills/user"]'
      VECTOR_DB_PATH: /app/data/vector_db
      SKILL_ROUTING_CACHE_PATH: /app/data/skill_routing_cache
    volumes:
      - ./customer_data:/app/customer_data
      - ./.claude/skills:/app/.claude/skills
      - backend_logs:/app/logs
      - backend_data:/app/data
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"   # 可改为仅暴露给 Nginx 内网
    command: >
      sh -c "python -m alembic upgrade head &&
             uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1"

  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"   # 启用 HTTPS 时使用
    volumes:
      - ./docker/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./docker/ssl:/etc/nginx/ssl:ro   # 证书目录（可选）
    depends_on:
      - backend

volumes:
  postgres_data:
  redis_data:
  backend_logs:
  backend_data:
```

### 6.4 Nginx 容器配置

新建 `docker/nginx.conf`（容器内 Nginx 配置，内容同 [第 7 节](#7-nginx-反向代理配置) 的 server 块）。

### 6.5 启动

```bash
# 首次启动
cp .env.example .env
vim .env   # 填写生产配置

docker compose up -d --build

# 查看日志
docker compose logs -f backend

# 验证
curl http://localhost:8000/health
curl http://localhost/
```

---

## 7 Nginx 反向代理配置

Nginx 承担两个职责：**静态托管前端** + **反向代理 `/api/v1` 到后端 8000 端口**。

新建 `/etc/nginx/sites-available/data-agent`：

```nginx
upstream backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name your-domain.com;          # 替换为实际域名或 IP

    # 强制跳转 HTTPS（启用 SSL 后取消注释）
    # return 301 https://$host$request_uri;

    root /var/www/data-agent;
    index index.html;

    # ── 前端静态文件 ──────────────────────────────
    location / {
        try_files $uri $uri/ /index.html;  # SPA 路由回退
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }

    # 带 hash 的静态资源长期缓存
    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # ── API 反向代理 ──────────────────────────────
    location /api/ {
        proxy_pass http://backend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE（Server-Sent Events）必要配置
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;        # SSE 长连接超时
        proxy_send_timeout 300s;
        add_header X-Accel-Buffering no;
        chunked_transfer_encoding on;
    }

    # ── 健康检查直通 ─────────────────────────────
    location /health {
        proxy_pass http://backend;
        proxy_http_version 1.1;
        access_log off;
    }

    # ── 上传限制 ─────────────────────────────────
    client_max_body_size 100m;

    # ── 安全响应头 ────────────────────────────────
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header Referrer-Policy strict-origin-when-cross-origin;
}

# HTTPS 配置（申请 SSL 证书后启用）
# server {
#     listen 443 ssl http2;
#     server_name your-domain.com;
#
#     ssl_certificate     /etc/nginx/ssl/fullchain.pem;
#     ssl_certificate_key /etc/nginx/ssl/privkey.pem;
#     ssl_protocols       TLSv1.2 TLSv1.3;
#     ssl_ciphers         HIGH:!aNULL:!MD5;
#
#     # 其余配置同上 HTTP server 块 location 部分
# }
```

```bash
# 启用配置
sudo ln -s /etc/nginx/sites-available/data-agent /etc/nginx/sites-enabled/
sudo nginx -t        # 检查语法
sudo systemctl reload nginx
```

> **SSE 关键点**：`proxy_buffering off` + `add_header X-Accel-Buffering no` 是 Server-Sent Events 流式推送正常工作的前提，缺少会导致聊天回复卡顿或无响应。

---

## 8 systemd 服务管理

### 8.1 后端服务

新建 `/etc/systemd/system/data-agent-backend.service`：

```ini
[Unit]
Description=Data Agent Backend (FastAPI)
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/data-agent
Environment=PATH=/opt/data-agent/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStartPre=/opt/data-agent/venv/bin/python -m alembic upgrade head
ExecStart=/opt/data-agent/venv/bin/uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    --access-log
Restart=on-failure
RestartSec=5s
KillMode=mixed
TimeoutStopSec=30

# 日志
StandardOutput=append:/opt/data-agent/logs/service.log
StandardError=append:/opt/data-agent/logs/service-error.log

# 安全限制
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable data-agent-backend
sudo systemctl start data-agent-backend
sudo systemctl status data-agent-backend

# 查看日志
journalctl -u data-agent-backend -f
```

### 8.2 文件权限修正

```bash
sudo chown -R www-data:www-data /opt/data-agent/customer_data
# customer_data/{username}/ 子目录由服务在用户首次对话时自动创建；-R 递归赋权可覆盖已创建的子目录
sudo chown -R www-data:www-data /opt/data-agent/logs
sudo chown -R www-data:www-data /opt/data-agent/data
# ENABLE_AUTH=true 时 user/ 下的 {username}/ 子目录由服务自动创建，
# 此处 -R 递归赋权可覆盖已创建的所有子目录
sudo chown -R www-data:www-data /opt/data-agent/.claude/skills/user
```

---

## 9 环境变量完整参考

以下为生产部署的关键配置项，完整列表见 `.env.example`。

### 必填项

```ini
# ── 应用 ────────────────────────────────────────
DEBUG=false
ENVIRONMENT=production
HOST=0.0.0.0
PORT=8000
FRONTEND_URL=https://your-domain.com        # 前端访问地址（CORS 白名单）

# ── PostgreSQL ────────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=data_agent
POSTGRES_USER=dataagent
POSTGRES_PASSWORD=<强密码>

# ── Redis ─────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=<Redis 密码，建议设置>

# ── LLM（至少配置一个） ────────────────────────
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-6

# ── 安全（JWT 认证）─────────────────────────
JWT_SECRET=<64+ 位随机字符串>               # 建议 openssl rand -hex 32 生成
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=120            # access_token 有效期（分钟），须 ≤ SESSION_IDLE_TIMEOUT_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS=14               # refresh_token DB 记录保留时长（天），默认 14 天
SESSION_IDLE_TIMEOUT_MINUTES=120           # Session 空闲超时（分钟），超时后 /auth/refresh 返回 401
ENABLE_AUTH=true
ADMIN_SECRET_TOKEN=<管理员令牌>             # 用于项目级 Skill 管理 API

# ── 文件系统 ─────────────────────────────────
# 相对路径（推荐）：相对于项目根目录，部署到任意服务器无需修改
ALLOWED_DIRECTORIES=["customer_data",".claude/skills"]
FILESYSTEM_WRITE_ALLOWED_DIRS=["customer_data",".claude/skills/user"]
# 绝对路径（可选）：数据目录与代码目录分离时使用（如 Docker 挂载卷场景）
# ALLOWED_DIRECTORIES=["/opt/data-agent/customer_data","/opt/data-agent/.claude/skills"]
# FILESYSTEM_WRITE_ALLOWED_DIRS=["/opt/data-agent/customer_data","/opt/data-agent/.claude/skills/user"]

# ── 路径（绝对路径） ──────────────────────────
VECTOR_DB_PATH=/opt/data-agent/data/vector_db
SKILL_ROUTING_CACHE_PATH=/opt/data-agent/data/skill_routing_cache
LOG_DIR=/opt/data-agent/logs
```

### 可选：LLM 中转代理

```ini
# 如果服务器无法直连 Anthropic，使用 HTTP 代理
ANTHROPIC_ENABLE_PROXY=true
ANTHROPIC_PROXY_HTTP=http://proxy-host:3128
ANTHROPIC_PROXY_HTTPS=http://proxy-host:3128

# 或使用自建中转服务
ANTHROPIC_BASE_URL=http://your-proxy-server:3000/api
ANTHROPIC_AUTH_TOKEN=<中转服务令牌>
```

### 可选：ClickHouse（数据查询）

系统支持**动态多区域配置**：`.env` 中按 `CLICKHOUSE_{ENV}_{FIELD}` 格式追加任意环境，重启后自动注册对应 MCP 服务器（`clickhouse-{env}` admin 连接 + `clickhouse-{env}-ro` 只读连接）。

```ini
# 格式：CLICKHOUSE_{ENV}_{FIELD}，ENV 大小写不敏感（idn/sg/mx/thai/br/my 等）
# 每个区域 6 个 admin 字段（HOST 必填，其余有默认值）+ 可选 readonly 凭证

# 示例：IDN 区域（已有）
CLICKHOUSE_IDN_HOST=10.x.x.x
CLICKHOUSE_IDN_PORT=9000
CLICKHOUSE_IDN_HTTP_PORT=8123
CLICKHOUSE_IDN_DATABASE=crm
CLICKHOUSE_IDN_USER=admin_user
CLICKHOUSE_IDN_PASSWORD=<密码>

# 可选：只读凭证（填写后自动注册 clickhouse-idn-ro）
# HOST/PORT/DATABASE 留空时自动继承 admin 值（支持读副本独立 host）
CLICKHOUSE_IDN_READONLY_USER=readonly_user
CLICKHOUSE_IDN_READONLY_PASSWORD=<密码>

# 新增区域（以 THAI 为例，追加以下内容后重启即可生效）
CLICKHOUSE_THAI_HOST=122.x.x.x
CLICKHOUSE_THAI_PORT=9000
CLICKHOUSE_THAI_DATABASE=crm
CLICKHOUSE_THAI_USER=admin_user
CLICKHOUSE_THAI_PASSWORD=<密码>
# THAI 只读凭证（可选）
# CLICKHOUSE_THAI_READONLY_USER=...
# CLICKHOUSE_THAI_READONLY_PASSWORD=...
```

**启动验证**：重启后查看日志确认新区域已注册：
```bash
grep "MCPManager.*Initialization complete" /opt/data-agent/logs/backend.log
# 期望：[MCPManager] Initialization complete: 3 server(s) registered: clickhouse-idn, clickhouse-thai, ...
```

### 可选：Skill 语义路由

```ini
SKILL_MATCH_MODE=hybrid        # keyword | llm | hybrid（推荐）
SKILL_SEMANTIC_THRESHOLD=0.45  # 低于此分数的语义命中不注入
SKILL_SEMANTIC_CACHE_TTL=86400 # 路由缓存 24 小时
```

### 可选：CORS

```ini
# 多域名用逗号分隔
CORS_ORIGINS=https://your-domain.com,https://www.your-domain.com
CORS_ALLOW_CREDENTIALS=true
```

### 性能调优

```ini
# PostgreSQL 连接池
POSTGRES_POOL_SIZE=20
POSTGRES_MAX_OVERFLOW=0

# Redis 连接池
REDIS_MAX_CONNECTIONS=50

# 上下文窗口
MAX_CONTEXT_MESSAGES=30
MAX_CONTEXT_TOKENS=150000

# 限流（每用户每分钟）
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=60
```

> **存储说明（推理过程持久化）**：每条助手消息的推理事件（thinking_events）以 JSONB 格式存入 `messages.extra_metadata`，单条消息额外增加约 1–5 KB（取决于推理深度和工具调用次数；工具返回内容超过 2000 字符时自动截断）。高频使用场景建议定期执行 `VACUUM ANALYZE messages;` 并为 `extra_metadata` 列创建 GIN 索引。
>
> **continuation 消息**：自动续接产生的续接提示消息以 `role='continuation'` 存入 `messages` 表，每次自动续接最多写入 3 条（消息内容极小，约 0.5–1 KB/条）。
>
> **对话打断消息**：用户点击「停止生成」后，已生成的部分内容以普通 assistant 消息形式保存，`extra_metadata["cancelled"]=true`，内容末尾追加中断标记（约 30 字节额外存储，无单独迁移需求）。**无需新增环境变量或基础设施**，`cancel_manager` 使用内存 asyncio.Event，进程重启后自动清空（不需要 Redis）。
>
> **对话附件元数据**：用户发送附件（图片/PDF/文本/CSV/JSON）时，系统将 base64 数据发送给 LLM 识别，但**仅将元数据**（文件名、MIME 类型、文件大小）存入 `messages.extra_metadata["attachments"]` JSONB 数组。单个附件元数据约 100–200 字节。历史消息中的附件以文本注解形式呈现给 LLM（如 `[附件: report.pdf (application/pdf, 12345 bytes)]`），**不会重复存储 base64 内容**。**无需数据库迁移**（复用现有 `extra_metadata` JSONB 列）。20MB 附件经 base64 编码后约 27MB，Nginx `client_max_body_size 100m` 已足够覆盖。
>
> **Agent 文件写入元数据**：Agent 通过 `write_file` 工具写出的文件（CSV/JSON/Excel 等），路径信息存入 `messages.extra_metadata["files_written"]`（`[{path, name, size, mime_type}]`）。文件实体存储在 `customer_data/{username}/` 目录（非数据库），用户通过 `GET /api/v1/files/download?path=...` 下载。**无需数据库迁移**（复用现有 `extra_metadata` JSONB 列）。
>
> **Excel 数据导入任务记录**：每次导入任务以记录形式写入 `import_jobs` 表（UUID PK），包含状态、进度（已导入行数/批次）、错误信息和配置快照。上传的 Excel 临时文件存于 `customer_data/{username}/imports/`，任务完成或失败后自动删除（`os.unlink`）。**需执行 `migrate_data_import.py`**（新建 `import_jobs` 表 + 种子 `data:import` 权限）。文件大小上限 100MB，采用 1MB 分块流式写盘（不全量加载内存），支持大文件上传。
>
> **SQL→Excel 数据导出任务记录**：每次导出任务写入 `export_jobs` 表（UUID PK），包含状态（pending/running/completed/failed/cancelling/cancelled）、行级/批次/Sheet 三层进度字段、输出文件路径和 JSONB 配置快照。导出 xlsx 文件存于 `customer_data/{username}/exports/`，通过 `DELETE /data-export/jobs/{id}` 手动触发文件删除（`os.unlink`）。**需执行 `migrate_data_export.py`**（新建 `export_jobs` 表 + 种子 `data:export` 权限）。采用 `openpyxl.Workbook(write_only=True)` 流式写 xlsx（低内存峰值），超过 100 万行自动分 Sheet。

---

## 10 数据库初始化与迁移

### 初始化（首次部署）

```bash
cd /opt/data-agent
source venv/bin/activate

# 运行所有 Alembic 迁移（建表/字段更新）
python -m alembic upgrade head

# 初始化 RBAC 角色和权限数据（ENABLE_AUTH=true 时必须执行）
python backend/scripts/init_rbac.py

# 对话用户隔离迁移（ENABLE_AUTH=true 时必须执行，为 conversations/conversation_groups 加 user_id FK）
# 预览（不执行）：
python backend/scripts/migrate_conversation_user_isolation.py --dry-run
# 正式执行：
python backend/scripts/migrate_conversation_user_isolation.py

# is_shared 字段迁移（群组框架预留，始终执行；幂等，已存在列时自动跳过）
python backend/scripts/migrate_add_is_shared.py

# Excel 数据导入迁移（创建 import_jobs 表 + data:import 权限；幂等）
python backend/scripts/migrate_data_import.py

# SQL→Excel 数据导出迁移（创建 export_jobs 表 + data:export 权限；幂等）
python backend/scripts/migrate_data_export.py

# 验证表结构
python -c "
from backend.core.database import get_engine
from sqlalchemy import inspect
eng = get_engine()
tables = inspect(eng).get_table_names()
print('Tables:', tables)
"
```

### 版本升级时迁移

```bash
# 拉取新代码后
git pull

# 安装新依赖
source venv/bin/activate
pip install -r requirements.txt

# 执行数据库迁移
python -m alembic upgrade head

# 若有角色/权限数据变化，重新运行初始化脚本（幂等）
python backend/scripts/init_rbac.py

# 若本次升级包含对话用户隔离（v1.7+），执行 DB 迁移（幂等，已执行过会跳过）
python backend/scripts/migrate_conversation_user_isolation.py

# 若本次升级包含 is_shared 字段（v1.8+），执行 DB 迁移（幂等，已执行过会跳过）
python backend/scripts/migrate_add_is_shared.py

# v1.9+ 技能路由可视化：无 DB 迁移，仅代码更新即可

# v2.1+ Excel 数据导入：执行 DB 迁移（幂等，已执行过会跳过）
python backend/scripts/migrate_data_import.py

# v2.2+ SQL→Excel 数据导出：执行 DB 迁移（幂等，已执行过会跳过）
python backend/scripts/migrate_data_export.py

# v2.3+ Skill 用户使用权限隔离（T1–T6）：无 DB 迁移，纯代码层变更，代码更新后重启服务即可
# 注：ENABLE_AUTH=true 环境建议验证 .claude/skills/user/ 下已有技能文件位于 {username}/ 子目录
# （如有遗留的 user/*.md 文件，init_rbac.py 中的 _migrate_user_skills_to_superadmin 可一次性迁移）

# 重启服务
sudo systemctl restart data-agent-backend
```

> **推理过程数据说明**：升级后，历史对话消息的 `thinking_events` 字段为空（旧消息无此数据），新产生的助手消息才会携带推理事件。旧消息的推理面板折叠后不显示内容属正常现象，无需迁移历史数据。

### 备份

```bash
# PostgreSQL 备份
pg_dump -U dataagent data_agent | gzip > /opt/data-agent/backups/db_$(date +%Y%m%d_%H%M%S).sql.gz

# 定时备份（crontab -e）
0 2 * * * pg_dump -U dataagent data_agent | gzip > /opt/data-agent/backups/db_$(date +\%Y\%m\%d).sql.gz
```

---

## 11 前端构建与静态托管

### 开发模式（不推荐生产）

Vite dev server 仅供开发使用，**生产环境必须编译为静态文件**。

### 生产构建

```bash
cd /opt/data-agent/frontend

# 确认 .env 使用相对路径（通过 Nginx 反代，无需指定绝对 URL）
cat .env
# 应为：
# VITE_API_BASE_URL=/api/v1
# VITE_APP_TITLE=数据智能分析Agent系统

npm install
npm run build          # 产物在 frontend/dist/

# 部署到 Nginx 目录
sudo cp -r dist/* /var/www/data-agent/
sudo chown -R www-data:www-data /var/www/data-agent/
```

### 每次前端更新

```bash
cd /opt/data-agent/frontend
git pull
npm install            # 依赖有变化时
npm run build
sudo cp -r dist/* /var/www/data-agent/
```

> **不需要重启后端**，Nginx 直接服务静态文件，前端更新不影响 Python 进程。

---

## 12 多用户与认证配置

### 启用认证

```ini
# .env
ENABLE_AUTH=true
JWT_SECRET=<至少 64 位随机字符串，用 openssl rand -hex 32 生成>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=120        # 须 ≤ SESSION_IDLE_TIMEOUT_MINUTES，确保空闲检测在 /auth/refresh 触发
REFRESH_TOKEN_EXPIRE_DAYS=14
SESSION_IDLE_TIMEOUT_MINUTES=120       # Session 空闲超时；超时后 /auth/refresh 返回 401
```

### 默认角色体系

| 角色 | 说明 | 关键权限 |
|------|------|----------|
| `viewer` | 只读用户 | `chat:use` |
| `analyst` | 数据分析师 | `chat:use` + `skills.user:读写` + `skills.project/system:读` |
| `admin` | 管理员 | analyst 全部 + `skills.project:写` + `models:读` + `settings:读` + `settings:写`（**无** `users:*`）|
| `superadmin` | 超级管理员 | 全部 15 项权限（含 `users:读写/角色分配`、`data:import/export`），`is_superadmin=true` |

> **注意**：`admin` 角色无用户管理权限（无 `users:read/write/assign_role`）。用户管理功能仅 `is_superadmin=true` 的账号可用。

### 创建第一个管理员用户

**推荐方式**：先通过 `init_rbac.py` 初始化角色，再用 API 创建账号并分配 superadmin 角色。若需要直接写库创建初始 superadmin：

```bash
cd /opt/data-agent
source venv/bin/activate

python -c "
from backend.core.database import get_session
from backend.models.user import User
from backend.core.auth.password import hash_password

with get_session() as db:
    user = User(
        username='admin',
        hashed_password=hash_password('Admin@123456'),
        display_name='系统管理员',
        is_superadmin=True,
        auth_source='local'
    )
    db.add(user)
    db.commit()
    print('Admin user created:', user.id)
"
```

> **字段说明**：`hashed_password`（非 `password_hash`）；导入路径 `backend.core.auth.password`（非 `backend.core.auth`）。

### 对话用户隔离说明

启用 `ENABLE_AUTH=true` 后，对话和分组数据自动按用户隔离：

- **普通用户**：只能看到和操作自己创建的对话/分组。
- **superadmin**：侧边栏显示双 Tab——「我的对话」和「其他用户(N)」；可在 Tab2 浏览（只读）所有用户的对话；打开他人对话时聊天输入区显示只读 banner，无法发送消息。
- **数据隔离**：通过 `conversations.user_id` / `conversation_groups.user_id` FK 实现，需先执行 `migrate_conversation_user_isolation.py`。
- **is_shared 字段**：`conversations.is_shared` 字段为群组聊天预留，默认 `false`；需执行 `migrate_add_is_shared.py`（幂等）。
- **存量数据**：迁移脚本自动将历史对话/分组归属 superadmin，不影响已有对话记录。
- **ENABLE_AUTH=false**：user_id 写入 NULL，list 端点不过滤，行为与升级前一致（向后兼容）。

### 关闭认证（内网单用户场景）

```ini
ENABLE_AUTH=false
# 后端将返回匿名用户（id=default, username=default），前端无需登录即可使用
```

### 项目级 Skill 管理

需要配置 `ADMIN_SECRET_TOKEN`，管理员通过前端 Skill 管理页面输入此 Token 才能增删项目技能：

```ini
ADMIN_SECRET_TOKEN=<随机字符串，与团队共享>
```

---

## 13 安全加固清单

### 部署前必做

- [ ] `DEBUG=false`，`ENVIRONMENT=production`
- [ ] `JWT_SECRET` 使用 `openssl rand -hex 32` 生成强随机值（≥64 位）
- [ ] PostgreSQL 和 Redis 设置非空密码
- [ ] `ENABLE_AUTH=true`，创建管理员账号
- [ ] `ADMIN_SECRET_TOKEN` 设置非空值
- [ ] `.env` 文件权限 `chmod 600 /opt/data-agent/.env`
- [ ] 防火墙只开放 80/443（Nginx）端口，不直接暴露 8000
- [ ] MCP 内部端口（50051-50054）不对外开放

### 推荐配置

- [ ] 申请 SSL 证书（Let's Encrypt）并启用 HTTPS
- [ ] Nginx 配置安全响应头（见第 7 节）
- [ ] 定期备份 PostgreSQL（见第 10 节）
- [ ] 为 PostgreSQL 创建最小权限用户（非 superuser）
- [ ] 配置 ClickHouse 只读账号（Analyst Agent 使用）
- [ ] 设置文件系统写入目录白名单（`FILESYSTEM_WRITE_ALLOWED_DIRS`）
- [ ] 确认 `/mcp/` API 访问权限（`ENABLE_AUTH=true` 时需 admin+ 角色才能访问 GET `/mcp/servers`、`/mcp/stats` 等端点；普通用户无法查看 MCP 服务器配置）

### 获取 SSL 证书（Let's Encrypt）

```bash
sudo apt install -y certbot python3-certbot-nginx

sudo certbot --nginx -d your-domain.com

# 自动续期
sudo certbot renew --dry-run
```

---

## 14 监控与日志

### 日志位置

| 日志 | 路径 | 说明 |
|------|------|------|
| 后端应用日志 | `/opt/data-agent/logs/backend.log` | 滚动，10MB × 5 备份 |
| 后端服务日志 | `/opt/data-agent/logs/service.log` | systemd 标准输出 |
| Nginx 访问日志 | `/var/log/nginx/access.log` | HTTP 请求记录 |
| Nginx 错误日志 | `/var/log/nginx/error.log` | Nginx 错误 |
| PostgreSQL 日志 | `/var/log/postgresql/` | 数据库日志 |

### 查看实时日志

```bash
# 后端实时日志
tail -f /opt/data-agent/logs/backend.log

# systemd 日志
journalctl -u data-agent-backend -f --since "1 hour ago"

# Nginx 实时访问
tail -f /var/log/nginx/access.log | grep /api/
```

### 健康检查

```bash
# 后端健康
curl -s http://localhost:8000/health
# 期望: {"status":"healthy"}

# 通过 Nginx 健康
curl -s http://your-domain.com/health

# PostgreSQL
sudo -u postgres psql -c "SELECT 1;" data_agent

# Redis
redis-cli ping
# 期望: PONG
```

### Prometheus 监控（可选）

```ini
# .env
ENABLE_METRICS=true
METRICS_PORT=9090
```

访问 `http://localhost:9090/metrics` 获取 Prometheus 指标。

---

## 15 更新与维护

### 更新应用代码

```bash
cd /opt/data-agent
git pull

# 后端依赖更新（如有）
source venv/bin/activate
pip install -r requirements.txt

# 数据库迁移（如有）
python -m alembic upgrade head

# 重启后端
sudo systemctl restart data-agent-backend

# 更新前端（如有前端变更）
cd frontend
npm install
npm run build
sudo cp -r dist/* /var/www/data-agent/
```

### 更新 Skill 文件

Skill 文件支持热重载，**无需重启后端**：

```bash
# 修改/新增 .claude/skills/ 下的 .md 文件后，系统自动检测并加载
# 手动触发重载（可选）
curl -X POST http://localhost:8000/api/v1/skills/reload \
  -H "X-Admin-Token: <ADMIN_SECRET_TOKEN>"
```

### 查看 Skill 加载状态

```bash
curl http://localhost:8000/api/v1/skills/status
```

### 诊断技能加载错误

当 Skill 文件存在格式问题（缺少 YAML frontmatter、字段缺失等）时，系统会静默跳过该文件。使用以下接口排查：

```bash
# 查询所有加载失败的技能文件（需 analyst 角色 JWT）
curl http://localhost:8000/api/v1/skills/load-errors \
  -H "Authorization: Bearer <ACCESS_TOKEN>"

# 响应示例
[
  {
    "filepath": ".claude/skills/user/superadmin/my-skill.md",
    "error": "Missing frontmatter (---) in skill file"
  }
]
```

常见原因：
- 文件首行不是 `---`（缺少 YAML frontmatter）
- `name` 或 `triggers` 字段缺失
- `triggers` 列表中有行内注释（`- keyword  # 注释`，解析器不支持）

也可在对话推理面板 🧠 **技能路由** 展开区直接看到 load_errors 列表，无需额外 API 调用。

### 数据库维护

```bash
# 清理过期会话（可加入 crontab）
python -c "
from backend.core.database import get_session
from backend.models.conversation import Conversation
# ... 清理逻辑
"

# 压缩 PostgreSQL 表
sudo -u postgres psql data_agent -c "VACUUM ANALYZE;"
```

---

## 16 常见问题排查

### Q: 前端显示 "Network Error"，无法登录

**A:** 检查 `frontend/.env` 中的 `VITE_API_BASE_URL`。

```bash
cat /opt/data-agent/frontend/.env
# 必须为：VITE_API_BASE_URL=/api/v1
# 不能是绝对 URL（如 http://localhost:8000/api/v1）
```

若已修改，需重新构建前端：`npm run build && sudo cp -r dist/* /var/www/data-agent/`

---

### Q: SSE（流式回复）断流或一次性返回

**A:** Nginx 缺少 SSE 配置，检查 nginx.conf 中 `/api/` location 块是否包含：

```nginx
proxy_buffering off;
proxy_cache off;
add_header X-Accel-Buffering no;
```

---

### Q: 后端启动失败，报 `could not connect to server`

**A:** PostgreSQL 未启动或连接信息有误：

```bash
sudo systemctl status postgresql
sudo -u postgres psql -c "\l" | grep data_agent
```

---

### Q: 后端报 `REDIS_HOST connection refused`

**A:** Redis 未启动：

```bash
sudo systemctl start redis-server
sudo systemctl enable redis-server
redis-cli ping
```

---

### Q: 用户反映 "频繁被强制登出" 或 "关闭浏览器后需要重新登录"

**A:** 这是预期行为，说明 Session 过期机制工作正常：

- **浏览器关闭后需重登**：refresh_token Cookie 是 Session Cookie（无 max_age/expires），浏览器关闭时自动清除。这是安全设计，无法绕过。
- **2 小时后被登出**：`SESSION_IDLE_TIMEOUT_MINUTES=120`（默认），超过 2 小时无 API 活动则 `/auth/refresh` 返回 401。若需要更长的空闲超时，修改 `.env` 并重启：

```ini
# .env — 将空闲超时延长到 8 小时（同时调整 access_token TTL）
ACCESS_TOKEN_EXPIRE_MINUTES=480
SESSION_IDLE_TIMEOUT_MINUTES=480
```

> **重要**：`ACCESS_TOKEN_EXPIRE_MINUTES` 须 ≤ `SESSION_IDLE_TIMEOUT_MINUTES`。若 access_token 有效期比空闲超时长，用户可在超时后仍凭有效 token 访问，空闲检测永远不触发。

---

### Q: Skill 语义路由不工作

**A:** 检查 ChromaDB 目录权限和 LLM 配置：

```bash
ls -la /opt/data-agent/data/skill_routing_cache/
# 确保 www-data 有写权限

# 临时切换为纯关键词模式（写入 .env 后重启）
SKILL_MATCH_MODE=keyword
```

---

### Q: MCP 端口冲突

**A:** 检查 50051-50054 端口占用：

```bash
ss -tlnp | grep -E '5005[0-9]'
# 如有冲突，修改 .env 中 MCP_CLICKHOUSE_PORT 等配置
```

---

### Q: Windows 开发机上 `localhost` 无法访问

**A:** Windows 11 DNS 优先解析 `localhost` 为 IPv6 (`::1`)，使用 `127.0.0.1` 替代，或确保 `frontend/.env` 使用相对路径 `/api/v1`（走 Vite 代理）。

---

### Q: 如何新增一个 ClickHouse 区域（如 THAI/BR/MY）

**A:** 在 `.env` 追加对应环境的配置项，重启后端即可，无需修改代码：

```ini
# 以新增 THAI 区域为例
CLICKHOUSE_THAI_HOST=122.8.155.77
CLICKHOUSE_THAI_PORT=9000
CLICKHOUSE_THAI_HTTP_PORT=8123
CLICKHOUSE_THAI_DATABASE=crm
CLICKHOUSE_THAI_USER=wizadmin
CLICKHOUSE_THAI_PASSWORD=<密码>
```

重启后验证：
```bash
# 查看启动日志
grep "Initialization complete" /opt/data-agent/logs/backend.log
# 期望包含 clickhouse-thai

# 或通过 API 查询（需 admin 权限）
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/mcp/servers
# 响应中应包含 name: "clickhouse-thai"
```

如需只读连接，同时追加：
```ini
CLICKHOUSE_THAI_READONLY_USER=readonly_user
CLICKHOUSE_THAI_READONLY_PASSWORD=<密码>
# HOST/PORT/DATABASE 留空时自动继承 admin 值
```

---

### Q: 点击「停止生成」后 AI 没有立即停下

**A:** 有以下几种情况：

1. **正在执行 MCP 工具调用**：系统在工具调用完成后才响应取消信号（约 <5 秒延迟），属正常行为。
2. **网络延迟导致取消请求未到达后端**：后端收到 POST `/cancel` 才会设置取消信号，检查网络连接。
3. **多 worker 场景**：`cancel_manager` 使用内存 `asyncio.Event`，多 worker 模式下（`--workers > 1`）取消信号不能跨 worker 传递。**生产环境必须使用单 worker**（`--workers 1`）。如需多 worker，未来可改用 Redis 信号。

---

### Q: 上传文件失败，413 错误

**A:** Nginx 默认限制 1MB，需配置 `client_max_body_size`：

```nginx
# nginx.conf server 块内
client_max_body_size 100m;
```

---

### Q: Excel 数据导入时上传大文件（50MB+）超时

**A:** 上传超时通常由以下原因引起：

1. **Nginx 请求体大小限制**：确认 Nginx 配置了足够大的 `client_max_body_size`（需 ≥ 100m）：
   ```nginx
   client_max_body_size 100m;
   ```

2. **Nginx 代理超时**：确认 nginx.conf 中 `/api/` location 块的超时足够长：
   ```nginx
   proxy_read_timeout 600s;
   proxy_send_timeout 600s;
   ```

3. **前端 axios 超时**：数据导入上传接口的客户端超时已设置为 10 分钟（600000ms），无需调整。

4. **后端 Vite 代理超时（仅开发环境）**：`vite.config.ts` 中 `/api` 代理的 `timeout` 已设置为 600000ms，无需调整。

5. **文件大小超限**：系统上限 100MB；超过时服务器返回 413。确认 Excel 文件是否真的超过了 100MB。

> 后端采用 1MB 分块流式写盘，上传 60MB 文件约需 30–60 秒（取决于网络带宽）；openpyxl 解析预览在线程池中运行，不阻塞事件循环。

---

### Q: 图片/文件附件无法上传或 AI 无法识别附件内容

**A:** 检查以下几点：

1. **文件类型是否支持**：当前仅支持 `image/*`（JPEG/PNG/GIF/WEBP）、`application/pdf`、以及文本类型（`text/plain`、`text/csv`、`text/markdown`、`application/json`）。其他类型（如 Word `.docx`、Excel `.xlsx`）会被前端拒绝并显示错误提示。

2. **文件大小**：单文件限制 20MB。上传后若出现 413 错误，见上文 Q&A 配置 `client_max_body_size`。

3. **Base64 编码**：附件以 base64 编码嵌入 JSON 请求体，前端无需配置 multipart/form-data。

4. **AI 无法识别**：Claude API 对图片识别支持最好；PDF 仅提供文本内容（无图片提取）；文本文件会被解码并注入为文本块。若 AI 返回「无法读取文件内容」，检查文件是否为有效的 UTF-8 编码文本。

5. **Windows 系统 MIME 类型问题**：部分系统 `file.type` 为空字符串，前端会按文件扩展名推断 MIME（如 `.md` → `text/markdown`）。若仍失败，检查浏览器控制台是否有 JavaScript 错误。

---

### Q: 刷新页面后，历史消息的「推理过程」面板为空

**A:** 有两种情况：

1. **升级前的旧消息**：升级到推理过程持久化版本之前产生的消息没有存储推理数据，面板为空属正常现象，无需处理。
2. **升级后的新消息也没有推理数据**：检查 PostgreSQL 的 `messages` 表中 `extra_metadata` 列是否正常写入：
   ```sql
   SELECT id, extra_metadata->>'thinking_events' IS NOT NULL AS has_events
   FROM messages WHERE role='assistant' ORDER BY created_at DESC LIMIT 5;
   ```
   若均为 NULL，检查后端日志有无写入错误，并确认 Alembic 迁移已正常执行。

---

### Q: 消息列表出现横幅式卡片而非气泡（续接提示横幅）

**A:** 这是正常行为，不是 Bug。当 Agent 推理轮次接近上限（剩余 ≤ 5 轮）并自动开启下一轮时，系统会在消息流中插入一条 `role='continuation'` 的记录，前端以横幅卡片渲染，显示续接轮次（如 `[1/3]`）和剩余任务列表。无需任何配置即可正常工作。

若不希望出现自动续接行为（如测试环境），可在 `agent_config.yaml` 中为对应 Agent 降低 `max_iterations` 值，使 Agent 在单轮内完成任务，不触发近限制综合。

---

## 附录：快速部署检查表

```
[ ] 系统依赖安装完毕（Python 3.11+, Node.js 18+, PostgreSQL, Redis, Nginx）
[ ] 代码上传到服务器
[ ] Python 虚拟环境创建，依赖安装完毕
[ ] .env 配置完毕（必填项全部填写；ALLOWED_DIRECTORIES 使用相对路径如 ["customer_data",".claude/skills"] 即可，数据目录与代码分离时改为绝对路径）
[ ] PostgreSQL 数据库和用户创建，Alembic 迁移执行（python -m alembic upgrade head）
[ ] RBAC 数据初始化（python backend/scripts/init_rbac.py，ENABLE_AUTH=true 时必须）
[ ] 对话用户隔离迁移（python backend/scripts/migrate_conversation_user_isolation.py，首次部署执行）
[ ] is_shared 字段迁移（python backend/scripts/migrate_add_is_shared.py，幂等，始终执行）
[ ] Excel 数据导入迁移（python backend/scripts/migrate_data_import.py，幂等，始终执行）
[ ] SQL→Excel 数据导出迁移（python backend/scripts/migrate_data_export.py，幂等，始终执行）
[ ] 前端 npm run build，dist/ 复制到 /var/www/data-agent/
[ ] Nginx 配置含 SSE 相关头，nginx -t 通过，reload
[ ] systemd 服务配置，enable + start，status 为 active
[ ] curl http://localhost:8000/health 返回 {"status":"healthy"}
[ ] 浏览器访问 http://your-domain.com 前端加载正常
[ ] 登录功能验证（ENABLE_AUTH=true 时）
[ ] 防火墙规则确认（仅 80/443 对外，8000 仅本机）
[ ] SSL 证书配置（生产环境必须）
[ ] 定时备份脚本配置
```
