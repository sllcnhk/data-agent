# 🔄 Claude 模型自动故障转移功能

## 📋 概述

自动故障转移（Auto Failover）是一个智能容错机制，当主模型不可用时，系统会自动尝试使用备用模型，确保服务的高可用性。

### 功能特点

- ✅ **自动切换**：主模型失败时自动尝试备用模型
- ✅ **透明处理**：应用层无需感知，自动完成切换
- ✅ **详细日志**：记录所有尝试过的模型和结果
- ✅ **可配置**：支持启用/禁用，自定义备用模型列表
- ✅ **零停机**：确保服务连续性

---

## 🔧 配置说明

### 环境变量配置 (.env)

在 `backend/.env` 文件中添加以下配置：

```env
# 主模型
ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-5

# 备用模型列表（逗号分隔，按优先级排序）
ANTHROPIC_FALLBACK_MODELS=claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001

# 是否启用自动故障转移
ANTHROPIC_ENABLE_FALLBACK=True
```

### 配置说明

| 配置项 | 说明 | 示例值 |
|--------|------|--------|
| `ANTHROPIC_DEFAULT_MODEL` | 主模型名称 | `claude-sonnet-4-5` |
| `ANTHROPIC_FALLBACK_MODELS` | 备用模型列表 | `model1,model2,model3` |
| `ANTHROPIC_ENABLE_FALLBACK` | 启用故障转移 | `True` / `False` |

---

## 🚀 工作原理

### 执行流程

```
1. 尝试主模型
   ├─ 成功 → 返回结果
   └─ 失败 ↓

2. 尝试备用模型1
   ├─ 成功 → 返回结果（标记为使用了fallback）
   └─ 失败 ↓

3. 尝试备用模型2
   ├─ 成功 → 返回结果（标记为使用了fallback）
   └─ 失败 ↓

4. 所有模型都失败
   └─ 抛出错误（包含所有尝试的详情）
```

### 示例场景

#### 场景1：正常操作
```
请求 → claude-sonnet-4-5 (主模型) ✅ → 返回结果
```

#### 场景2：主模型失败，自动切换
```
请求 → claude-sonnet-4-5 (主模型) ❌
     → claude-sonnet-4-5-20250929 (备用1) ✅ → 返回结果
```

#### 场景3：连续故障转移
```
请求 → claude-sonnet-4-5 (主模型) ❌
     → claude-sonnet-4-5-20250929 (备用1) ❌
     → claude-haiku-4-5-20251001 (备用2) ✅ → 返回结果
```

---

## 📊 测试验证

### 运行测试

```bash
cd C:\Users\shiguangping\data-agent\backend
python test_failover_simple.py
```

### 测试结果

```
Test Summary
============
✓ Normal Operation              [PASS]
✓ Auto Failover                 [PASS]
✓ All Fail                      [PASS]

Results: 3/3 tests passed
```

---

## 📝 响应元数据

当使用自动故障转移时，响应中会包含额外的元数据：

```python
{
    "model": "claude-sonnet-4-5-20250929",  # 实际使用的模型
    "content": "...",                         # 响应内容
    "metadata": {
        "used_fallback": True,                # 是否使用了备用模型
        "attempted_models": [                 # 尝试过的模型列表
            "claude-sonnet-4-5",              # 失败
            "claude-sonnet-4-5-20250929"      # 成功
        ],
        "raw_response": {...}                 # 原始API响应
    }
}
```

---

## 🔍 日志示例

### 正常操作
```
[CHAT] Primary model: claude-sonnet-4-5
[CHAT] Fallback disabled, will only try primary model
[TRY_MODEL] Attempting with model: claude-sonnet-4-5
[TRY_MODEL] ✅ Success with model: claude-sonnet-4-5
[CHAT] ✅ Successfully used model: claude-sonnet-4-5
```

### 故障转移
```
[CHAT] Primary model: claude-sonnet-4-5
[CHAT] Fallback enabled, will try 3 models: ['claude-sonnet-4-5', 'claude-sonnet-4-5-20250929', 'claude-haiku-4-5-20251001']
[TRY_MODEL] Attempting with model: claude-sonnet-4-5
[TRY_MODEL] ❌ Failed with model: claude-sonnet-4-5 - 500: Model unavailable
[CHAT] Model claude-sonnet-4-5 failed, trying next...
[TRY_MODEL] Attempting with model: claude-sonnet-4-5-20250929
[TRY_MODEL] ✅ Success with model: claude-sonnet-4-5-20250929
[CHAT] ✅ Successfully used model: claude-sonnet-4-5-20250929
```

---

## ⚙️ 代码实现

### ClaudeAdapter 关键代码

```python
class ClaudeAdapter(BaseModelAdapter):
    def __init__(self, api_key: str, **kwargs):
        # ...
        self.fallback_models = kwargs.get("fallback_models", [])
        self.enable_fallback = kwargs.get("enable_fallback", True)

    async def chat(self, conversation, **kwargs):
        # 构建要尝试的模型列表
        models_to_try = [self.model]
        if self.enable_fallback and self.fallback_models:
            models_to_try.extend(self.fallback_models)

        # 依次尝试每个模型
        for i, model_name in enumerate(models_to_try):
            success, result = await self._try_model_request(
                model_name=model_name,
                messages=messages,
                system_prompt=system_prompt,
                **kwargs
            )

            if success:
                # 返回成功的结果
                return UnifiedMessage(
                    model=model_name,
                    content=result["content"][0]["text"],
                    metadata={
                        "used_fallback": i > 0,
                        "attempted_models": models_to_try[:i+1]
                    }
                )

        # 所有模型都失败
        raise RuntimeError("所有模型均失败")
```

---

## 💡 最佳实践

### 1. 备用模型选择
- **优先级顺序**：按性能和可用性排序
- **模型多样性**：选择不同系列的模型（如 Sonnet + Haiku）
- **成本考虑**：备用模型可以选择更经济的选项

### 2. 数量建议
- **推荐**：2-3 个备用模型
- **最少**：1 个备用模型
- **最多**：不超过 5 个（避免过长的重试时间）

### 3. 监控建议
- 定期检查日志中的故障转移频率
- 如果频繁使用备用模型，考虑更换主模型
- 设置告警，当所有模型都失败时通知

### 4. 性能优化
- 备用模型应与主模型在同一服务区域
- 确保备用模型有足够的配额
- 定期测试所有模型的可用性

---

## 🎯 使用场景

### 适用场景

1. **生产环境**：确保服务的高可用性
2. **高并发场景**：分散请求到多个模型
3. **成本优化**：主模型不可用时使用更便宜的备用
4. **灾备方案**：应对服务商的临时故障

### 不适用场景

1. **严格的一致性要求**：如果必须使用特定模型
2. **调试阶段**：可能会掩盖实际问题
3. **单元测试**：应该禁用以确保测试准确性

---

## 🔧 故障排查

### 问题1：备用模型没有生效

**检查清单**：
- [ ] `ANTHROPIC_ENABLE_FALLBACK=True` 已设置
- [ ] `ANTHROPIC_FALLBACK_MODELS` 配置正确
- [ ] 备用模型名称在中转服务中可用
- [ ] 查看日志确认是否加载了配置

### 问题2：所有模型都失败

**可能原因**：
1. 中转服务不可用
2. 认证令牌无效
3. 所有模型名称都不正确
4. 网络连接问题

**解决方法**：
```bash
# 运行测试脚本验证
python test_models.py

# 检查中转服务状态
curl http://10.0.3.248:3000/health
```

### 问题3：性能下降

**原因分析**：
- 频繁的模型切换增加延迟
- 主模型不稳定导致经常故障转移

**优化建议**：
1. 更换更稳定的主模型
2. 减少备用模型数量
3. 优化超时设置

---

## 📈 性能指标

### 典型延迟

| 场景 | 延迟 |
|------|------|
| 主模型成功 | ~1-3秒 |
| 一次故障转移 | ~3-6秒 |
| 两次故障转移 | ~6-9秒 |

### 成功率提升

```
单模型可用性: 99%
双模型系统:   99.99% (1 - 0.01²)
三模型系统:   99.9999% (1 - 0.01³)
```

---

## 🆚 对比表

| 特性 | 无故障转移 | 有故障转移 |
|------|-----------|-----------|
| 可用性 | 99% | 99.99%+ |
| 故障恢复 | 手动 | 自动 |
| 停机时间 | 分钟级 | 秒级 |
| 运维成本 | 高 | 低 |
| 复杂度 | 低 | 中 |

---

## 📚 相关文件

- [backend/config/settings.py](backend/config/settings.py#L60) - 配置定义
- [backend/core/model_adapters/claude.py](backend/core/model_adapters/claude.py#L127) - 实现逻辑
- [backend/core/model_adapters/factory.py](backend/core/model_adapters/factory.py#L186) - 配置传递
- [backend/.env](backend/.env#L44) - 环境变量
- [test_failover_simple.py](backend/test_failover_simple.py) - 测试脚本

---

## 🎓 总结

自动故障转移功能提供了：

✅ **高可用性**：确保服务连续性
✅ **自动化**：无需人工干预
✅ **透明性**：应用层无感知
✅ **可观测性**：详细的日志和元数据
✅ **灵活性**：可配置的行为

**开始使用**：
1. 更新 `.env` 配置
2. 重启服务
3. 运行测试验证
4. 监控日志确认正常工作

现在您的系统已经具备了企业级的容错能力！🚀
