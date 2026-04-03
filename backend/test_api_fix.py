"""
测试 API 修复
验证 conversations API 是否正常工作
"""
import requests
import json

BASE_URL = "http://localhost:8000/api/v1"

print("=" * 60)
print("API 修复验证测试")
print("=" * 60)
print()

# 测试1：获取对话列表
print("[测试 1/3] 获取对话列表...")
try:
    response = requests.get(f"{BASE_URL}/conversations?status=active&limit=10")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ 对话列表 API 正常")
        print(f"  - 返回 {len(data.get('conversations', []))} 个对话")
        print(f"  - 总数: {data.get('total', 0)}")
    else:
        print(f"✗ 对话列表 API 错误: HTTP {response.status_code}")
        print(f"  {response.text}")
except Exception as e:
    print(f"✗ 对话列表 API 失败: {e}")

print()

# 测试2：获取LLM配置
print("[测试 2/3] 获取LLM配置...")
try:
    response = requests.get(f"{BASE_URL}/llm-configs?enabled_only=true")
    if response.status_code == 200:
        data = response.json()
        configs = data.get('data', [])
        print(f"✓ LLM配置 API 正常")
        print(f"  - 找到 {len(configs)} 个配置")
        if configs:
            model_key = configs[0].get('model_key')
            print(f"  - 第一个模型: {model_key}")
        else:
            print(f"  ⚠ 没有可用的LLM配置")
            model_key = None
    else:
        print(f"✗ LLM配置 API 错误: HTTP {response.status_code}")
        model_key = None
except Exception as e:
    print(f"✗ LLM配置 API 失败: {e}")
    model_key = None

print()

# 测试3：创建对话（如果有可用的模型）
if model_key:
    print(f"[测试 3/3] 创建对话 (使用 {model_key})...")
    try:
        payload = {
            "title": "API测试对话",
            "model_key": model_key,
            "system_prompt": "你是一个测试助手"
        }
        response = requests.post(
            f"{BASE_URL}/conversations",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                conv_data = data.get('data', {})
                conv_id = conv_data.get('id')
                print(f"✓ 创建对话 API 正常")
                print(f"  - 对话ID: {conv_id}")
                print(f"  - 标题: {conv_data.get('title')}")
                print(f"  - 模型: {conv_data.get('current_model')}")
            else:
                print(f"✗ 创建对话失败: {data}")
        else:
            print(f"✗ 创建对话 API 错误: HTTP {response.status_code}")
            print(f"  {response.text}")
    except Exception as e:
        print(f"✗ 创建对话 API 失败: {e}")
else:
    print("[测试 3/3] 跳过创建对话测试（没有可用的LLM配置）")

print()

# 测试4：MCP服务器
print("[测试 4/4] MCP服务器...")
try:
    response = requests.get(f"{BASE_URL}/mcp/servers")
    if response.status_code == 200:
        data = response.json()
        servers = data.get('data', [])
        print(f"✓ MCP服务器 API 正常")
        print(f"  - 找到 {len(servers)} 个服务器")
        for server in servers:
            print(f"    • {server.get('name')} ({server.get('type')})")
    else:
        print(f"✗ MCP服务器 API 错误: HTTP {response.status_code}")
except Exception as e:
    print(f"✗ MCP服务器 API 失败: {e}")

print()
print("=" * 60)
print("测试完成")
print("=" * 60)
print()
print("如果所有测试通过，前端应该可以正常使用。")
print("现在可以打开 http://localhost:3000 测试前端。")
