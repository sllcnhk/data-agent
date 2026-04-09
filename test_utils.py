"""
测试工具模块 — 统一测试数据命名规范

所有测试文件应从此处 import TEST_PREFIX 和工厂函数，
不得在各自文件中 hardcode 前缀字符串。

命名规范：
  _t_{label}_{6位hex}_ — 例如 _t_user_a3f2c1_
  • _t_ 是统一标识符，conftest.py 正则 ^_[a-z][a-z0-9]*_ 可覆盖
  • 带随机尾缀保证并发测试不冲突
  • 同一测试文件使用相同 label，方便 grep 定位来源

现有存量前缀（向后兼容，conftest.py 正则已覆盖）：
  _rbact_、_e2e_、_flow_、_ulp_、_sk_、_cdi_
"""
import uuid

# ── 统一前缀 ────────────────────────────────────────────
TEST_PREFIX = "_t_"


def make_test_username(label: str = "user") -> str:
    """生成唯一测试用户名，格式：_t_{label}_{6位hex}_"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}_"


def make_test_rolename(label: str = "role") -> str:
    """生成唯一测试角色名，格式：_t_{label}_{6位hex}_"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}_"


def make_test_email(label: str = "user") -> str:
    """生成唯一测试邮箱"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}@test.local"


def make_test_skill_name(label: str = "skill") -> str:
    """生成唯一测试技能名，格式：_t_{label}_{6位hex}_（slug 化后：t-{label}-{hex}-）"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}_"


def is_test_entity(name: str) -> bool:
    """判断名称是否为测试数据（匹配 _xxx_ 前缀格式，与 conftest.py 逻辑一致）。"""
    import re
    return bool(re.match(r'^_[a-z][a-z0-9]*_', name))
