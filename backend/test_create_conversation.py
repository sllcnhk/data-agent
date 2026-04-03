"""
详细测试创建对话功能
捕获所有错误信息以便定位问题
"""
import requests
import json
import sys

BASE_URL = "http://localhost:8000/api/v1"

print("=" * 70)
print("详细测试创建对话功能")
print("=" * 70)
print()

# 第一步：检查后端是否启动
print("[步骤 1/5] 检查后端服务...")
try:
    response = requests.get(f"{BASE_URL.rsplit('/api/v1', 1)[0]}/health", timeout=5)
    if response.status_code == 200:
        print("✓ 后端服务正常运行")
    else:
        print(f"✗ 后端服务异常: HTTP {response.status_code}")
        sys.exit(1)
except requests.exceptions.ConnectionError:
    print("✗ 无法连接到后端服务，请确保后端已启动")
    print("  URL:", BASE_URL)
    sys.exit(1)
except Exception as e:
    print(f"✗ 检查后端失败: {e}")
    sys.exit(1)

print()

# 第二步：获取可用的 LLM 配置
print("[步骤 2/5] 获取可用的 LLM 配置...")
try:
    response = requests.get(f"{BASE_URL}/llm-configs?enabled_only=true", timeout=10)
    print(f"  状态码: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        configs = data.get('data', [])
        print(f"✓ 找到 {len(configs)} 个 LLM 配置")

        if configs:
            for i, config in enumerate(configs, 1):
                model_key = config.get('model_key')
                model_name = config.get('model_name')
                enabled = config.get('enabled')
                print(f"  [{i}] {model_key} - {model_name} (enabled: {enabled})")

            # 使用第一个配置
            test_model_key = configs[0].get('model_key')
            print(f"\n  将使用模型: {test_model_key}")
        else:
            print("✗ 没有可用的 LLM 配置")
            print("  请先在系统中配置至少一个LLM模型")
            sys.exit(1)
    else:
        print(f"✗ 获取 LLM 配置失败: HTTP {response.status_code}")
        print(f"  响应内容: {response.text}")
        sys.exit(1)
except Exception as e:
    print(f"✗ 获取 LLM 配置失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# 第三步：测试创建对话（详细请求信息）
print("[步骤 3/5] 测试创建对话...")
payload = {
    "title": "测试对话 - " + str(__import__('time').time()),
    "model_key": test_model_key,
    "system_prompt": "你是一个测试助手"
}

print(f"  请求 URL: {BASE_URL}/conversations")
print(f"  请求方法: POST")
print(f"  请求头: Content-Type: application/json")
print(f"  请求体: {json.dumps(payload, ensure_ascii=False, indent=2)}")
print()

try:
    response = requests.post(
        f"{BASE_URL}/conversations",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10
    )

    print(f"  响应状态码: {response.status_code}")
    print(f"  响应头: {dict(response.headers)}")
    print()

    if response.status_code == 200:
        data = response.json()
        print("✓ 创建对话成功")
        print(f"  响应数据: {json.dumps(data, ensure_ascii=False, indent=2)}")

        if data.get('success'):
            conv_data = data.get('data', {})
            conv_id = conv_data.get('id')
            print()
            print(f"✓ 对话详情:")
            print(f"  - ID: {conv_id}")
            print(f"  - 标题: {conv_data.get('title')}")
            print(f"  - 模型: {conv_data.get('current_model')}")
            print(f"  - 创建时间: {conv_data.get('created_at')}")
        else:
            print(f"✗ 响应 success 字段为 False")
    else:
        print(f"✗ 创建对话失败: HTTP {response.status_code}")
        print(f"  响应内容: {response.text}")

        # 尝试解析 JSON 错误
        try:
            error_data = response.json()
            print(f"  错误详情: {json.dumps(error_data, ensure_ascii=False, indent=2)}")
        except:
            pass

        sys.exit(1)

except requests.exceptions.Timeout:
    print("✗ 请求超时（10秒）")
    print("  可能原因：")
    print("  1. 后端处理太慢")
    print("  2. 数据库连接问题")
    print("  3. 网络问题")
    sys.exit(1)
except requests.exceptions.ConnectionError as e:
    print(f"✗ 连接错误: {e}")
    print("  可能原因：")
    print("  1. 后端服务突然关闭")
    print("  2. 防火墙阻止")
    print("  3. 端口被占用")
    sys.exit(1)
except Exception as e:
    print(f"✗ 创建对话失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# 第四步：验证对话是否真的创建了
print("[步骤 4/5] 验证对话是否创建...")
try:
    response = requests.get(f"{BASE_URL}/conversations?status=active&limit=10", timeout=10)
    if response.status_code == 200:
        data = response.json()
        conversations = data.get('conversations', [])
        total = data.get('total', 0)
        print(f"✓ 当前有 {total} 个活跃对话")

        # 查找刚创建的对话
        if conversations:
            print(f"  最新的 {min(3, len(conversations))} 个对话:")
            for conv in conversations[:3]:
                print(f"    • {conv.get('title')} (模型: {conv.get('current_model')})")
    else:
        print(f"⚠ 获取对话列表失败: HTTP {response.status_code}")
except Exception as e:
    print(f"⚠ 验证失败: {e}")

print()

# 第五步：检查日志文件
print("[步骤 5/5] 检查日志文件...")
import os
log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "backend.log")
if os.path.exists(log_file):
    print(f"✓ 日志文件存在: {log_file}")
    print(f"  文件大小: {os.path.getsize(log_file)} 字节")
    print()
    print("  最后 20 行日志:")
    print("  " + "-" * 66)
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines[-20:]:
                print(f"  {line.rstrip()}")
    except Exception as e:
        print(f"  无法读取日志: {e}")
else:
    print(f"⚠ 日志文件不存在: {log_file}")

print()
print("=" * 70)
print("测试完成")
print("=" * 70)
print()
print("如果测试成功，前端创建对话应该也能正常工作。")
print("如果失败，请检查上面的错误信息和日志文件。")
