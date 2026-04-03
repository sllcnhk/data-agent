# 快速开始 - Data Agent

## 🚀 两步启动

### 第一步：首次环境设置（只需做一次）

双击运行或命令行执行：

```cmd
setup-environment.bat
```

⏱️ 需要 5-10 分钟

### 第二步：启动系统

```cmd
start-all.bat
```

⏱️ 等待 10-15 秒后访问：**http://localhost:3000**

---

## ✅ Windows 重启后

**只需运行 `start-all.bat`**，无需重新设置！

环境已永久配置，脚本会自动：
- 激活 Python 3.8 环境
- 验证所有依赖
- 启动前后端服务

---

## 📋 详细说明

- [完整启动指南.md](./完整启动指南.md) - 详细步骤和故障排查
- [问题诊断报告.md](./问题诊断报告.md) - 问题分析和修复记录

## 🔧 辅助脚本

- `验证修复.bat` - 验证代码修复是否正确应用
- `stop-all.bat` - 停止所有服务（如果需要）

---

## ⚠️ 重要提示

**必须使用 Python 3.8+！**

如果看到 Python 版本错误，请确保：
1. 已运行 `setup-environment.bat`
2. conda 环境 `dataagent` 已创建

可以手动验证：
```cmd
conda activate dataagent
python --version  # 应显示 Python 3.8.x
```

---

生成时间：2026-01-22
