"""
更新数据库中所有LLM配置的max_tokens到8192

这个脚本将:
1. 更新所有Claude配置的max_tokens从4096到8192
2. 为其他模型也提升max_tokens限制
"""
import sys
import os

# 添加项目根目录到 Python 路径
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from backend.config.database import get_db_context
from backend.models.llm_config import LLMConfig

def update_max_tokens():
    """更新所有LLM配置的max_tokens"""
    print("=" * 60)
    print("更新 max_tokens 配置到 8192")
    print("=" * 60)

    with get_db_context() as db:
        # 查询所有配置
        configs = db.query(LLMConfig).all()

        if not configs:
            print("没有找到LLM配置，跳过更新")
            return

        updated_count = 0
        for config in configs:
            old_value = config.max_tokens

            # 如果当前是4096或更小，更新到8192
            try:
                current_tokens = int(old_value) if old_value else 4096
                if current_tokens <= 4096:
                    config.max_tokens = "8192"
                    print(f"[OK] 更新 {config.model_name} ({config.model_key}): {old_value} -> 8192")
                    updated_count += 1
                else:
                    print(f"- 跳过 {config.model_name} ({config.model_key}): 已经是 {old_value}")
            except ValueError:
                print(f"[WARN] 跳过 {config.model_name}: max_tokens值异常 ({old_value})")

        # 提交更改
        if updated_count > 0:
            db.commit()
            print(f"\n[SUCCESS] 成功更新 {updated_count} 个配置")
        else:
            print(f"\n[INFO] 没有需要更新的配置")

    print("\n" + "=" * 60)
    print("更新完成！")
    print("=" * 60)
    print("\n下一步: 重启后端服务以应用新配置")
    print("  1. 停止后端: Ctrl+C")
    print("  2. 启动后端: start-backend.bat")
    print("  或使用: restart_backend.bat")

if __name__ == "__main__":
    try:
        update_max_tokens()
    except Exception as e:
        print(f"\n[ERROR] 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
