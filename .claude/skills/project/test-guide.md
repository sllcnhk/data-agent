---
name: test-guide
version: "1.0"
description: 本项目测试编写规范——命名约定、运行方式、清理机制
triggers:
  - 写测试
  - 新增测试
  - 单元测试
  - 集成测试
  - test case
  - unittest
  - pytest
  - 测试用例
  - 测试数据
category: project
priority: high
---

# 本项目测试编写规范

## 1. 测试数据命名（必须遵守）

**所有测试中创建的数据库实体（用户、角色等）必须使用统一前缀**，让清理机制能识别并删除。

```python
# ✅ 正确：从 test_utils 导入工厂函数
from test_utils import make_test_username, make_test_rolename

username = make_test_username("alice")  # → _t_alice_a3f2c1_
rolename = make_test_rolename("analyst")  # → _t_analyst_9b1d3e_

# ❌ 错误：hardcode 前缀
username = f"test_user_{uuid.uuid4().hex[:6]}"   # 不会被清理！
username = "alice_test"                            # 不会被清理！
username = f"_sk_user_{uuid.uuid4().hex[:6]}"     # 旧风格，不推荐
```

**命名格式**：`_t_{label}_{6位hex}_`
- 以 `_t_` 开头（统一标识符）
- `label` 描述实体用途（user/role/admin 等）
- 随机尾缀保证并发无冲突

## 2. 测试文件结构

### 2a. unittest.TestCase 文件（推荐格式）

```python
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from test_utils import make_test_username, make_test_rolename

class TestXxx(unittest.TestCase):
    def setUp(self):
        self.username = make_test_username("alice")
        # ... 创建测试数据

    def test_something(self):
        # ...
        pass

    def tearDown(self):
        # 可选：单测级别的清理（session 级清理由 conftest.py 兜底）
        pass

# ✅ __main__ 块必须用 pytest.main()，否则 conftest.py 不会触发
if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
```

### 2b. 异步自定义 runner 文件

```python
import asyncio, sys, atexit
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).parent))

from test_utils import make_test_username

async def test_something():
    username = make_test_username("alice")
    # ...

async def run_all():
    # 运行所有测试，统计通过/失败
    ...

# ✅ __main__ 块必须注入 cleanup
if __name__ == "__main__":
    try:
        from conftest import _cleanup_test_data as _ctd
        atexit.register(_ctd, label="post-run")  # 进程退出时必然执行（含 sys.exit）
        _ctd(label="pre-run")
    except Exception:
        pass
    asyncio.run(run_all())
```

## 3. 运行测试（必须用 pytest）

```bash
# ✅ 正确：使用 pytest（conftest.py 自动触发）
/d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_rbac.py -v -s
/d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_rbac.py test_skill_e2e.py -v

# ✅ 也可以直接 python（因为 __main__ 块已调用 pytest.main）
/d/ProgramData/Anaconda3/envs/dataagent/python.exe test_rbac.py

# ❌ 错误：不要用 -m unittest（conftest.py 不触发）
# python -m unittest test_rbac.py  ← 不会清理！
```

## 4. 清理机制说明

本项目有三道清理保障（深度防御）：

| 层 | 机制 | 触发时机 |
|----|------|---------|
| L1 | `conftest.py` session 级 fixture | pytest 运行开始/结束时（Group A 文件）|
| L2 | `atexit.register(_cleanup_test_data)` | 进程退出时，含 sys.exit()（Group B 文件）|
| L3 | `.claude/settings.json` Stop hook | Claude Code 每次会话结束时（兜底）|

**识别规则**：用户名/角色名匹配正则 `^_[a-z][a-z0-9]*_`（如 `_t_`、`_rbact_`、`_e2e_` 等均覆盖）。

## 5. 禁止事项

- ❌ 不得在测试结束后留下真实名称的用户（如 `alice`、`bob`）
- ❌ 不得跳过 `test_utils` 而直接拼接前缀字符串
- ❌ 不得使用 `python -m unittest` 运行测试（不触发 conftest）
- ❌ 不得在测试文件 `__main__` 中用 `unittest.TextTestRunner`（旧方式）

## 6. 新测试文件检查清单

新建测试文件前确认：
- [ ] 导入 `from test_utils import make_test_username, make_test_rolename`
- [ ] `__main__` 块使用 `pytest.main([__file__, "-v", "-s"])` 或 atexit 模式
- [ ] 不在代码中 hardcode 前缀字符串
- [ ] 运行后数据库中没有残留测试数据（`backend/scripts/cleanup_test_data.py --dry-run` 验证）
