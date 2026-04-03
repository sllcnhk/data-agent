# Claude Code 模型/厂商切换完整指南

> 适用环境：VS Code + Claude Code 插件（官方 Anthropic 扩展）
> 本地环境：Windows 11 · `c:\Users\shiguangping\data-agent`

---

## 一、Claude Code 的 API 配置存储位置

### 1.1 全局配置文件

| 配置类型 | 路径 |
|---|---|
| Claude Code 全局配置 | `C:\Users\shiguangping\.claude\settings.json` |
| 每项目配置 | `<project_root>\.claude\settings.json` |
| VS Code 用户设置 | `C:\Users\shiguangping\AppData\Roaming\Code\User\settings.json` |

`~/.claude/settings.json` 示例结构：
```json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com"
  }
}
```

### 1.2 关键环境变量

| 变量名 | 作用 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic / 代理服务的 API Key |
| `ANTHROPIC_BASE_URL` | API 请求基地址（可指向本地代理） |
| `ANTHROPIC_MODEL` | 指定默认模型 ID（可选） |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | 最大输出 token 数 |

> **优先级**：Shell 环境变量 > `~/.claude/settings.json` 中的 env > VS Code 工作区 settings.json

---

## 二、为何需要代理层

Claude Code 内部使用 **Anthropic Messages API 格式**（`/v1/messages`）。
千问（Qwen）/ DeepSeek 使用 **OpenAI Chat Completions 格式**（`/v1/chat/completions`）。

两者格式不兼容，因此不能直接把 `ANTHROPIC_BASE_URL` 指向 DashScope 或 DeepSeek 的地址。

**解决方案：引入本地翻译代理 —— [LiteLLM Proxy](https://github.com/BerriAI/litellm)**

```
Claude Code (Anthropic格式请求)
        │
        ▼
LiteLLM Proxy (localhost:4000)  ← 本地运行
        │ 翻译协议
        ├──→ Anthropic API (claude-sonnet-4-6 等)
        ├──→ 阿里云 DashScope API (qwen-coder-plus 等)
        └──→ DeepSeek API (deepseek-coder 等)
```

---

## 三、本地环境一次性安装

### 3.1 安装 LiteLLM

```bash
# 使用 data-agent 项目的 conda 环境
d:/ProgramData/Anaconda3/envs/dataagent/python.exe -m pip install litellm[proxy]
```

或者创建专用虚拟环境：
```bash
d:/ProgramData/Anaconda3/Scripts/conda create -n litellm python=3.11 -y
d:/ProgramData/Anaconda3/envs/litellm/python.exe -m pip install litellm[proxy]
```

### 3.2 创建 LiteLLM 配置文件

创建 `C:\Users\shiguangping\Model-Switching-Related\litellm_config.yaml`：

```yaml
model_list:
  # ── Anthropic Claude（直连官方） ──────────────────────────────
  - model_name: claude-sonnet-46
    litellm_params:
      model: claude/claude-sonnet-4-6
      api_key: sk-ant-YOUR_ANTHROPIC_KEY_HERE

  - model_name: claude-opus-46
    litellm_params:
      model: claude/claude-opus-4-6
      api_key: sk-ant-YOUR_ANTHROPIC_KEY_HERE

  # ── 阿里云千问 Qwen ───────────────────────────────────────────
  - model_name: qwen-coder-plus
    litellm_params:
      model: openai/qwen-coder-plus   # openai/ 前缀告知 litellm 用 openai 格式
      api_key: sk-YOUR_DASHSCOPE_KEY_HERE
      api_base: https://dashscope.aliyuncs.com/compatible-mode/v1

  - model_name: qwen-coder-turbo
    litellm_params:
      model: openai/qwen-coder-turbo
      api_key: sk-YOUR_DASHSCOPE_KEY_HERE
      api_base: https://dashscope.aliyuncs.com/compatible-mode/v1

  # ── DeepSeek ─────────────────────────────────────────────────
  - model_name: deepseek-coder
    litellm_params:
      model: openai/deepseek-coder
      api_key: sk-YOUR_DEEPSEEK_KEY_HERE
      api_base: https://api.deepseek.com/v1

  - model_name: deepseek-chat
    litellm_params:
      model: openai/deepseek-chat
      api_key: sk-YOUR_DEEPSEEK_KEY_HERE
      api_base: https://api.deepseek.com/v1

litellm_settings:
  # 让 litellm 对外暴露 /v1/messages（Anthropic 格式）
  use_client_credentials_pass_through_routes: true
  drop_params: true          # 忽略不支持的参数，避免报错

general_settings:
  master_key: "local-proxy-secret"  # 可自定义，Claude Code 用此作为 API key
```

---

## 四、启动脚本（一键切换核心）

### 4.1 目录结构

```
C:\Users\shiguangping\Model-Switching-Related\
├── litellm_config.yaml          # LiteLLM 代理配置
├── switch-claude.bat            # 切换到 Claude 官方
├── switch-qwen.bat              # 切换到千问
├── switch-deepseek.bat          # 切换到 DeepSeek
├── start-proxy.bat              # 启动 LiteLLM 代理
└── Claude-Code-Model-Switching-Guide.md  # 本文档
```

### 4.2 `start-proxy.bat` — 启动代理服务

```bat
@echo off
title LiteLLM Proxy
echo Starting LiteLLM Proxy on port 4000...
d:\ProgramData\Anaconda3\envs\dataagent\python.exe -m litellm ^
  --config "C:\Users\shiguangping\Model-Switching-Related\litellm_config.yaml" ^
  --port 4000
```

### 4.3 `switch-claude.bat` — 切换到 Claude 官方

```bat
@echo off
echo Switching Claude Code to: Anthropic Official

:: 写入 ~/.claude/settings.json
set CONFIG_FILE=%USERPROFILE%\.claude\settings.json
(
echo {
echo   "env": {
echo     "ANTHROPIC_API_KEY": "sk-ant-YOUR_ANTHROPIC_KEY_HERE",
echo     "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
echo     "ANTHROPIC_MODEL": "claude-sonnet-4-6"
echo   }
echo }
) > "%CONFIG_FILE%"

echo Done. Reload VS Code window (Ctrl+Shift+P -> Developer: Reload Window)
```

### 4.4 `switch-qwen.bat` — 切换到千问（需代理运行）

```bat
@echo off
echo Switching Claude Code to: Qwen Coder Plus (via LiteLLM Proxy)

set CONFIG_FILE=%USERPROFILE%\.claude\settings.json
(
echo {
echo   "env": {
echo     "ANTHROPIC_API_KEY": "local-proxy-secret",
echo     "ANTHROPIC_BASE_URL": "http://localhost:4000",
echo     "ANTHROPIC_MODEL": "qwen-coder-plus"
echo   }
echo }
) > "%CONFIG_FILE%"

echo Done. Ensure LiteLLM proxy is running (start-proxy.bat)
echo Reload VS Code window (Ctrl+Shift+P -> Developer: Reload Window)
```

### 4.5 `switch-deepseek.bat` — 切换到 DeepSeek（需代理运行）

```bat
@echo off
echo Switching Claude Code to: DeepSeek Coder (via LiteLLM Proxy)

set CONFIG_FILE=%USERPROFILE%\.claude\settings.json
(
echo {
echo   "env": {
echo     "ANTHROPIC_API_KEY": "local-proxy-secret",
echo     "ANTHROPIC_BASE_URL": "http://localhost:4000",
echo     "ANTHROPIC_MODEL": "deepseek-coder"
echo   }
echo }
) > "%CONFIG_FILE%"

echo Done. Ensure LiteLLM proxy is running (start-proxy.bat)
echo Reload VS Code window (Ctrl+Shift+P -> Developer: Reload Window)
```

---

## 五、VS Code 工作区配置方案（更优雅）

在 VS Code 中通过 workspace settings 覆盖，无需修改全局配置：

**`.vscode/settings.json`（项目级）：**
```json
{
  "claude-code.env.ANTHROPIC_API_KEY": "local-proxy-secret",
  "claude-code.env.ANTHROPIC_BASE_URL": "http://localhost:4000",
  "claude-code.env.ANTHROPIC_MODEL": "qwen-coder-plus"
}
```

> 注意：VS Code 的 `claude-code.*` 设置键名以官方插件实际支持为准；
> 若插件不支持此方式，回退到修改 `~/.claude/settings.json`。

---

## 六、关于「同一对话中切换厂商」

### 6.1 结论

**不能在同一个 Claude Code 会话内无缝切换厂商。**

原因：
- Claude Code 在会话启动时读取一次 API 配置
- 会话中间修改 `settings.json` 不会立即生效
- 切换厂商 = 切换底层模型，上下文格式/token 计数都不同

### 6.2 实用变通方案

| 场景 | 推荐做法 |
|---|---|
| 想继续当前任务，但换用便宜模型 | 在对话框输入 `/compact` 压缩上下文 → 运行 switch-xxx.bat → VS Code Reload Window → 开新对话，粘贴关键摘要 |
| Token 快到限额时切换 | 让 Claude 先输出「任务摘要」，切换后把摘要发给新会话继续 |
| 代码任务 → 千问/DeepSeek | 提前把需求和代码上下文整理成 prompt，切换后重新投入 |

### 6.3 「伪无缝」方案（进阶）

在 `data-agent` 项目中已有 `backend/core/conversation_summarizer.py`，可以：
1. 调用 `/api/conversations/{id}/summarize` 拿到摘要
2. 切换模型
3. 用摘要作为新会话的系统提示

---

## 七、获取各厂商 API Key

| 厂商 | 控制台地址 | 模型推荐 |
|---|---|---|
| Anthropic | https://console.anthropic.com | claude-sonnet-4-6（最新） |
| 阿里云千问 | https://dashscope.console.aliyun.com | qwen-coder-plus / qwen-coder-turbo |
| DeepSeek | https://platform.deepseek.com | deepseek-coder / deepseek-chat |

---

## 八、完整操作流程（首次使用）

```
第一步：安装 LiteLLM
  d:/ProgramData/Anaconda3/envs/dataagent/python.exe -m pip install "litellm[proxy]"

第二步：填写 litellm_config.yaml 中的真实 API Key

第三步：双击 start-proxy.bat，保持窗口开着

第四步：双击 switch-qwen.bat 或 switch-deepseek.bat

第五步：在 VS Code 中 Ctrl+Shift+P → Developer: Reload Window

第六步：打开 Claude Code，开始对话即用千问/DeepSeek
```

---

## 九、验证是否生效

在 Claude Code 对话框输入：
```
你现在是哪个模型？请告诉我你的模型名称和提供商。
```

或者检查 LiteLLM 代理日志，可以看到转发请求到哪个后端。

---

## 十、故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| `Connection refused localhost:4000` | 代理未启动 | 运行 start-proxy.bat |
| `401 Unauthorized` | API Key 填错 | 检查 litellm_config.yaml 中的 key |
| 回复乱码/截断 | 模型参数不兼容 | 在 litellm_settings 加 `drop_params: true` |
| 切换后还是 Claude | settings.json 未生效 | Reload Window 后再试 |
| `model not found` | 模型名拼写错误 | 核对 litellm_config.yaml 中的 model_name |

---

## 十一、费用对比参考（2026年初价格，仅供参考）

| 模型 | 输入（/1M tokens） | 输出（/1M tokens） | 适合场景 |
|---|---|---|---|
| claude-sonnet-4-6 | $3 | $15 | 复杂推理、代码架构 |
| qwen-coder-plus | ¥3.5 | ¥7 | 日常编码、调试 |
| deepseek-coder | $0.14 | $0.28 | 轻量代码补全 |

> 策略建议：复杂架构设计用 Claude，日常编码用千问/DeepSeek，节省 token 配额。
