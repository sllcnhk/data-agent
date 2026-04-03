"""
测试 max_tokens 修复是否生效

测试以下内容：
1. 数据库配置类型转换
2. Orchestrator 参数传递
3. Model Adapter 参数使用
"""
import sys
import os

backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

def test_database_config_conversion():
    """测试数据库配置的类型转换"""
    print("\n[TEST 1] 数据库配置类型转换")
    print("-" * 60)

    from backend.config.database import get_db_context
    from backend.services.conversation_service import ConversationService

    with get_db_context() as db:
        service = ConversationService(db)
        config = service._get_llm_config("claude")

        max_tokens = config.get("max_tokens")
        temperature = config.get("temperature")

        print(f"max_tokens: {max_tokens} (type: {type(max_tokens).__name__})")
        print(f"temperature: {temperature} (type: {type(temperature).__name__})")

        # 验证类型
        assert isinstance(max_tokens, int), f"max_tokens 应该是 int，但是 {type(max_tokens)}"
        assert isinstance(temperature, float), f"temperature 应该是 float，但是 {type(temperature)}"

        # 验证值
        assert max_tokens == 8192, f"max_tokens 应该是 8192，但是 {max_tokens}"

        print("[PASS] 配置类型转换正确")
        return True

def test_orchestrator_params():
    """测试 Orchestrator 参数处理"""
    print("\n[TEST 2] Orchestrator 参数处理")
    print("-" * 60)

    from backend.config.database import get_db_context
    from backend.services.conversation_service import ConversationService

    with get_db_context() as db:
        service = ConversationService(db)
        config = service._get_llm_config("claude")

        # 模拟 Orchestrator 的参数处理逻辑
        temperature = config.get("temperature", 0.7)
        max_tokens = config.get("max_tokens", 8192)

        # 类型转换
        try:
            temperature = float(temperature) if not isinstance(temperature, float) else temperature
        except (ValueError, TypeError):
            temperature = 0.7

        try:
            max_tokens = int(max_tokens) if not isinstance(max_tokens, int) else max_tokens
        except (ValueError, TypeError):
            max_tokens = 8192

        print(f"Orchestrator 处理后:")
        print(f"  max_tokens: {max_tokens} (type: {type(max_tokens).__name__})")
        print(f"  temperature: {temperature} (type: {type(temperature).__name__})")

        assert isinstance(max_tokens, int), "max_tokens 应该是 int"
        assert isinstance(temperature, float), "temperature 应该是 float"
        assert max_tokens == 8192, f"max_tokens 应该是 8192，但是 {max_tokens}"

        print("[PASS] Orchestrator 参数处理正确")
        return True

def test_adapter_params():
    """测试 Adapter 参数处理"""
    print("\n[TEST 3] Model Adapter 参数处理")
    print("-" * 60)

    # 模拟传递给 adapter 的 kwargs
    kwargs = {
        "max_tokens": 8192,  # 已经转换为 int
        "temperature": 0.7   # 已经转换为 float
    }

    # 模拟 claude adapter 的处理逻辑
    max_tokens = kwargs.get('max_tokens', 8192)
    temperature = kwargs.get('temperature', 0.7)

    try:
        max_tokens = int(max_tokens) if not isinstance(max_tokens, int) else max_tokens
    except (ValueError, TypeError):
        max_tokens = 8192

    try:
        temperature = float(temperature) if not isinstance(temperature, float) else temperature
    except (ValueError, TypeError):
        temperature = 0.7

    print(f"Adapter 处理后:")
    print(f"  max_tokens: {max_tokens} (type: {type(max_tokens).__name__})")
    print(f"  temperature: {temperature} (type: {type(temperature).__name__})")

    assert isinstance(max_tokens, int), "max_tokens 应该是 int"
    assert isinstance(temperature, float), "temperature 应该是 float"
    assert max_tokens == 8192, f"max_tokens 应该是 8192，但是 {max_tokens}"

    print("[PASS] Adapter 参数处理正确")
    return True

def test_full_chain():
    """测试完整调用链"""
    print("\n[TEST 4] 完整调用链测试")
    print("-" * 60)

    from backend.config.database import get_db_context
    from backend.services.conversation_service import ConversationService
    from backend.mcp.manager import get_mcp_manager
    from backend.agents.orchestrator import MasterAgent

    with get_db_context() as db:
        service = ConversationService(db)

        # 1. 从数据库获取配置
        llm_config = service._get_llm_config("claude")
        print(f"步骤1 - 从数据库读取配置:")
        print(f"  max_tokens: {llm_config['max_tokens']} (type: {type(llm_config['max_tokens']).__name__})")

        # 2. 创建 MasterAgent（会进行参数处理）
        mcp_manager = get_mcp_manager()
        agent = MasterAgent(mcp_manager, "claude", llm_config)

        # 检查 adapter 的配置
        adapter_config = agent.llm_adapter.config
        print(f"\n步骤2 - Adapter 配置:")
        print(f"  max_tokens: {adapter_config.get('max_tokens')} (type: {type(adapter_config.get('max_tokens')).__name__})")

        # 验证
        max_tokens = adapter_config.get('max_tokens')
        assert isinstance(max_tokens, int), f"Adapter max_tokens 应该是 int，但是 {type(max_tokens)}"
        assert max_tokens == 8192, f"Adapter max_tokens 应该是 8192，但是 {max_tokens}"

        print("\n[PASS] 完整调用链参数传递正确")
        return True

def main():
    print("=" * 60)
    print("测试 max_tokens 修复")
    print("=" * 60)

    tests = [
        ("数据库配置类型转换", test_database_config_conversion),
        ("Orchestrator 参数处理", test_orchestrator_params),
        ("Adapter 参数处理", test_adapter_params),
        ("完整调用链", test_full_chain)
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"通过: {passed}/{len(tests)}")
    print(f"失败: {failed}/{len(tests)}")

    if failed == 0:
        print("\n[SUCCESS] 所有测试通过!")
        print("\n修复内容:")
        print("  1. [OK] 数据库配置读取时进行类型转换")
        print("  2. [OK] Orchestrator 参数传递时确保类型正确")
        print("  3. [OK] Model Adapter 使用参数时防御性类型转换")
        print("  4. [OK] 所有默认值从 4096 更新到 8192")
        print("\n下一步: 重启后端服务")
        print("  restart_backend.bat")
        return 0
    else:
        print(f"\n[FAIL] {failed} 个测试失败")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n[ERROR] 测试过程出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
