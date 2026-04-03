"""
验证 max_tokens 配置是否正确更新到 8192

这个脚本将检查:
1. .env 文件配置
2. settings 配置
3. 数据库中的 LLM 配置
"""
import sys
import os

# 添加项目根目录到 Python 路径
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

def check_env_file():
    """检查 .env 文件"""
    print("\n[1] 检查 .env 文件配置")
    print("-" * 60)

    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        print("[FAIL] .env 文件不存在")
        return False

    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()

    if 'ANTHROPIC_MAX_TOKENS=8192' in content:
        print("[PASS] ANTHROPIC_MAX_TOKENS=8192")
        return True
    elif 'ANTHROPIC_MAX_TOKENS=4096' in content:
        print("[FAIL] ANTHROPIC_MAX_TOKENS=4096 (应为 8192)")
        return False
    else:
        print("[WARN] 未找到 ANTHROPIC_MAX_TOKENS 配置")
        return False

def check_settings():
    """检查 settings 配置"""
    print("\n[2] 检查 Settings 配置")
    print("-" * 60)

    try:
        from backend.config.settings import settings
        max_tokens = settings.anthropic_max_tokens

        if max_tokens == 8192:
            print(f"[PASS] anthropic_max_tokens = {max_tokens}")
            return True
        else:
            print(f"[FAIL] anthropic_max_tokens = {max_tokens} (应为 8192)")
            return False
    except Exception as e:
        print(f"[ERROR] 无法加载 settings: {e}")
        return False

def check_database():
    """检查数据库配置"""
    print("\n[3] 检查数据库 LLM 配置")
    print("-" * 60)

    try:
        from backend.config.database import get_db_context
        from backend.models.llm_config import LLMConfig

        with get_db_context() as db:
            configs = db.query(LLMConfig).all()

            if not configs:
                print("[WARN] 数据库中没有 LLM 配置")
                return True

            all_pass = True
            for config in configs:
                try:
                    tokens = int(config.max_tokens) if config.max_tokens else 0
                    status = "[PASS]" if tokens >= 8192 else "[FAIL]"
                    print(f"{status} {config.model_name} ({config.model_key}): {config.max_tokens}")

                    if tokens < 8192:
                        all_pass = False
                except ValueError:
                    print(f"[WARN] {config.model_name}: max_tokens 值异常 ({config.max_tokens})")

            return all_pass

    except Exception as e:
        print(f"[ERROR] 无法检查数据库: {e}")
        return False

def main():
    print("=" * 60)
    print("验证 max_tokens 配置升级到 8192")
    print("=" * 60)

    results = {
        "env_file": check_env_file(),
        "settings": check_settings(),
        "database": check_database()
    }

    # 总结
    print("\n" + "=" * 60)
    print("验证总结")
    print("=" * 60)

    all_pass = all(results.values())

    for name, result in results.items():
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status} {name}")

    if all_pass:
        print("\n[SUCCESS] 所有配置验证通过!")
        print("\n下一步: 重启后端服务")
        print("  方式1: restart_backend.bat")
        print("  方式2: stop-all.bat && start-all.bat")
        print("\n重启后，系统将使用新的 max_tokens=8192 配置")
        return 0
    else:
        print("\n[FAIL] 部分配置验证失败")
        print("\n请检查:")
        print("  1. 是否正确执行了 update_max_tokens.py")
        print("  2. .env 文件是否正确修改")
        print("  3. 数据库连接是否正常")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n[ERROR] 验证过程出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
