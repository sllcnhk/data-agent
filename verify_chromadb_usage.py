"""
验证 ChromaDB 是否真实在使用

运行此脚本来检查：
1. ChromaDB 是否已安装
2. 数据目录是否存在
3. 是否有实际数据
4. 查询功能是否正常
"""
import sys
import os
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))


def check_chromadb_installation():
    """检查 ChromaDB 是否已安装"""
    print("\n" + "="*60)
    print("Step 1: 检查 ChromaDB 安装")
    print("="*60)

    try:
        import chromadb
        print(f"✅ ChromaDB 已安装: v{chromadb.__version__}")
        return True
    except ImportError as e:
        print(f"❌ ChromaDB 未安装: {e}")
        print("\n请运行: conda run -n dataagent pip install chromadb==0.4.22")
        return False


def check_data_directory():
    """检查数据目录"""
    print("\n" + "="*60)
    print("Step 2: 检查数据目录")
    print("="*60)

    data_dir = Path("./data/chroma")

    if data_dir.exists():
        print(f"✅ 数据目录存在: {data_dir.absolute()}")

        # 列出文件
        files = list(data_dir.rglob("*"))
        if files:
            print(f"   包含 {len(files)} 个文件")

            # 显示主要文件
            important_files = [
                f for f in files
                if f.name.endswith(('.sqlite3', '.parquet', '.bin'))
            ]
            if important_files:
                print("   重要文件:")
                for f in important_files[:5]:  # 只显示前 5 个
                    size_mb = f.stat().st_size / (1024*1024)
                    print(f"     - {f.name} ({size_mb:.2f} MB)")

                # 计算总大小
                total_size = sum(f.stat().st_size for f in files) / (1024*1024)
                print(f"   总大小: {total_size:.2f} MB")

                return True
            else:
                print("   ⚠️  数据目录为空")
                return False
        else:
            print("   ⚠️  数据目录为空")
            return False
    else:
        print(f"❌ 数据目录不存在: {data_dir.absolute()}")
        print("   ChromaDB 尚未初始化")
        return False


def check_vector_store():
    """检查向量存储是否可用"""
    print("\n" + "="*60)
    print("Step 3: 检查向量存储")
    print("="*60)

    try:
        from backend.core.vector_store import VectorStoreManager

        # 尝试初始化
        vector_store = VectorStoreManager()
        print("✅ 向量存储初始化成功")

        # 获取统计
        stats = vector_store.get_collection_stats()
        print(f"   集合名称: {stats.get('collection_name', 'N/A')}")
        print(f"   总消息数: {stats.get('total_messages', 0)}")

        if stats.get('total_messages', 0) > 0:
            print("   ✅ 有实际数据存储")
            return True, vector_store
        else:
            print("   ⚠️  暂无数据")
            return False, vector_store

    except Exception as e:
        print(f"❌ 向量存储初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_query_functionality(vector_store):
    """测试查询功能"""
    print("\n" + "="*60)
    print("Step 4: 测试查询功能")
    print("="*60)

    try:
        from backend.core.conversation_format import UnifiedMessage, MessageRole

        # 添加测试消息
        print("添加测试消息...")
        test_messages = [
            ("test_msg_1", "How to use Python?", "user"),
            ("test_msg_2", "Python is a programming language.", "assistant"),
            ("test_msg_3", "What is machine learning?", "user"),
        ]

        for msg_id, content, role in test_messages:
            vector_store.add_message(
                message_id=msg_id,
                content=content,
                conversation_id="verification_test",
                role=role
            )

        print(f"✅ 成功添加 {len(test_messages)} 条消息")

        # 查询相似消息
        print("\n测试语义查询...")
        results = vector_store.query_similar(
            query_text="Python programming",
            conversation_id="verification_test",
            n_results=3
        )

        if results:
            print(f"✅ 查询成功，返回 {len(results)} 条结果:")
            for i, result in enumerate(results, 1):
                print(f"\n   结果 {i}:")
                print(f"     内容: {result['content'][:60]}...")
                print(f"     相似度: {result.get('similarity', 0):.4f}")
        else:
            print("⚠️  查询返回空结果")

        # 清理测试数据
        print("\n清理测试数据...")
        vector_store.delete_conversation("verification_test")
        print("✅ 测试数据已清理")

        return True

    except Exception as e:
        print(f"❌ 查询功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_integration_status():
    """检查集成状态"""
    print("\n" + "="*60)
    print("Step 5: 集成状态总结")
    print("="*60)

    # 检查配置文件
    config_files = [
        "backend/config.py",
        "backend/settings.py",
        "config.py",
        "settings.py"
    ]

    has_config = False
    for config_file in config_files:
        if Path(config_file).exists():
            print(f"✅ 找到配置文件: {config_file}")
            has_config = True

            # 简单检查是否包含 vector_store 配置
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'vector' in content.lower() or 'chroma' in content.lower():
                        print(f"   包含向量存储相关配置")
            except:
                pass

    if not has_config:
        print("⚠️  未找到配置文件")

    # 检查主应用文件
    app_files = [
        "backend/app.py",
        "backend/main.py",
        "app.py",
        "main.py"
    ]

    for app_file in app_files:
        if Path(app_file).exists():
            print(f"\n✅ 找到应用文件: {app_file}")

            # 检查是否导入了向量存储
            try:
                with open(app_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'vector_store' in content or 'VectorStoreManager' in content:
                        print(f"   ✅ 已集成向量存储")
                    else:
                        print(f"   ⚠️  可能尚未集成向量存储")
            except:
                pass


def main():
    """主函数"""
    print("\n" + "="*60)
    print("ChromaDB 使用验证脚本")
    print("="*60)

    all_checks = []

    # 1. 检查安装
    installed = check_chromadb_installation()
    all_checks.append(("ChromaDB 安装", installed))

    if not installed:
        print("\n❌ ChromaDB 未安装，无法继续验证")
        return

    # 2. 检查数据目录
    has_data = check_data_directory()
    all_checks.append(("数据目录", has_data))

    # 3. 检查向量存储
    vector_ok, vector_store = check_vector_store()
    all_checks.append(("向量存储", vector_ok))

    # 4. 测试查询功能
    if vector_store:
        query_ok = test_query_functionality(vector_store)
        all_checks.append(("查询功能", query_ok))

    # 5. 检查集成状态
    check_integration_status()

    # 总结
    print("\n" + "="*60)
    print("验证结果总结")
    print("="*60)

    for check_name, passed in all_checks:
        status = "✅ 通过" if passed else "❌ 未通过"
        print(f"{check_name:<20} {status}")

    passed_count = sum(1 for _, passed in all_checks if passed)
    total_count = len(all_checks)

    print(f"\n总计: {passed_count}/{total_count} 项通过")

    if passed_count == total_count:
        print("\n🎉 ChromaDB 已正确安装并可以使用！")
    elif passed_count >= total_count - 1:
        print("\n✅ ChromaDB 基本可用，但可能尚未完全集成")
        print("\n下一步建议:")
        print("1. 查看集成指南: INTEGRATION_GUIDE.md")
        print("2. 运行集成测试: python test_phase3_integration.py")
    else:
        print("\n⚠️  ChromaDB 可能未正确配置")
        print("\n建议:")
        print("1. 确保 ChromaDB 已安装: conda run -n dataagent pip install chromadb==0.4.22")
        print("2. 查看安装指南: PHASE_3_COMPLETION_REPORT.md")

    print("\n" + "="*60)


if __name__ == "__main__":
    main()
