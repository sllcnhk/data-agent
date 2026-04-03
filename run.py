#!/usr/bin/env python
"""
数据智能分析Agent系统启动脚本
"""
import sys
import os
import logging

# 添加backend到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# ──────────────────────────────────────────────────────────
# uvicorn 0.24 设计缺陷：reload 模式下始终强制监听 CWD，
# 导致 logs/backend.log 每次写入都产生 "N changes detected"
# 日志刷屏（变化被 FileFilter 过滤，服务不会重启，但消息已打出）。
# 修复方案：将 watchfiles.main 日志级别设为 WARNING，
# 屏蔽无意义的 INFO "N changes detected" 消息；
# 真正触发代码重载时 uvicorn 自身会打印 Reloading... 提示。
# ──────────────────────────────────────────────────────────
logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

if __name__ == "__main__":
    import uvicorn
    from backend.main import app

    print("=" * 60)
    print("数据智能分析Agent系统 v1.0.0")
    print("=" * 60)
    print()
    print("启动信息:")
    print("  - API文档: http://localhost:8000/api/docs")
    print("  - ReDoc文档: http://localhost:8000/api/redoc")
    print("  - 健康检查: http://localhost:8000/health")
    print()
    print("=" * 60)
    print()

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["backend"],    # 提示 uvicorn 主要关注 backend/
        reload_includes=["*.py"],   # 只对 .py 文件变化触发重载
        log_level="info"
    )
