# 代理配置快速指南

## 快速启用 Claude 代理

### 1. 编辑 .env 文件

```bash
# 找到 Claude 配置部分，修改以下三行：
ANTHROPIC_ENABLE_PROXY=true
ANTHROPIC_PROXY_HTTP=http://10.0.3.248:3128
ANTHROPIC_PROXY_HTTPS=http://10.0.3.248:3128
```

### 2. 重启服务

```bash
cd C:\Users\shiguangping\data-agent
start-all.bat
```

### 3. 验证配置

检查日志输出，应该看到：

```
[INIT] Proxies: {'http://': 'http://10.0.3.248:3128', 'https://': 'http://10.0.3.248:3128'}
[TRY_MODEL] Using proxies: {'http://': 'http://10.0.3.248:3128', 'https://': 'http://10.0.3.248:3128'}
```

---

## 常见场景

### 场景 1: 只有 Claude 需要代理

```bash
# Claude 使用代理
ANTHROPIC_ENABLE_PROXY=true
ANTHROPIC_PROXY_HTTP=http://10.0.3.248:3128
ANTHROPIC_PROXY_HTTPS=http://10.0.3.248:3128

# 其他模型直连
OPENAI_ENABLE_PROXY=false
GOOGLE_ENABLE_PROXY=false
```

### 场景 2: 禁用所有代理

```bash
ANTHROPIC_ENABLE_PROXY=false
OPENAI_ENABLE_PROXY=false
GOOGLE_ENABLE_PROXY=false
```

---

## 注意事项

### ⚠️ 代理地址格式

❌ 错误:
```bash
ANTHROPIC_PROXY_HTTP=10.0.3.248:3128
```

✅ 正确:
```bash
ANTHROPIC_PROXY_HTTP=http://10.0.3.248:3128
```

### ⚠️ 代理IP地址

用户配置中的 `10.03.248` 可能有误，应该是：
- `10.0.3.248` （中转服务使用的IP段）

建议确认正确的代理服务器地址。

---

## 测试代理配置

```bash
cd backend
python test_proxy_feature.py
```

---

## 完整配置参考

```bash
# ================================
# Claude 代理配置
# ================================
ANTHROPIC_ENABLE_PROXY=true
ANTHROPIC_PROXY_HTTP=http://10.0.3.248:3128
ANTHROPIC_PROXY_HTTPS=http://10.0.3.248:3128

# ================================
# OpenAI 代理配置（可选）
# ================================
OPENAI_ENABLE_PROXY=false
OPENAI_PROXY_HTTP=
OPENAI_PROXY_HTTPS=

# ================================
# Google 代理配置（可选）
# ================================
GOOGLE_ENABLE_PROXY=false
GOOGLE_PROXY_HTTP=
GOOGLE_PROXY_HTTPS=
```

---

**更多详情**: 参见 [PROXY_FEATURE_REPORT.md](./PROXY_FEATURE_REPORT.md)
