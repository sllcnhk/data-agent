"""
完整测试对话流程
测试创建对话、获取对话、发送消息等功能
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

print("=" * 70)
print("对话流程完整测试")
print("=" * 70)
print()

# 步骤 1：获取 LLM 配置
print("[步骤 1/5] 获取 LLM 配置...")
try:
    response = requests.get(f"{BASE_URL}/llm-configs?enabled_only=true", timeout=10)
    if response.status_code == 200:
        data = response.json()
        configs = data.get('data', [])
        if configs:
            model_key = configs[0].get('model_key')
            print(f"✓ 找到 {len(configs)} 个配置，使用模型: {model_key}")
        else:
            print("✗ 没有可用的 LLM 配置")
            exit(1)
    else:
        print(f"✗ 获取配置失败: HTTP {response.status_code}")
        exit(1)
except Exception as e:
    print(f"✗ 获取配置失败: {e}")
    exit(1)

print()

# 步骤 2：创建对话
print("[步骤 2/5] 创建测试对话...")
try:
    payload = {
        "title": f"流程测试对话 - {int(time.time())}",
        "model_key": model_key,
        "system_prompt": "你是一个测试助手，请简洁回答问题。"
    }
    response = requests.post(
        f"{BASE_URL}/conversations",
        json=payload,
        timeout=10
    )

    if response.status_code == 200:
        data = response.json()
        if data.get('success'):
            conv_id = data['data']['id']
            print(f"✓ 对话创建成功")
            print(f"  - ID: {conv_id}")
            print(f"  - 标题: {data['data']['title']}")
        else:
            print(f"✗ 创建失败: {data}")
            exit(1)
    else:
        print(f"✗ 创建失败: HTTP {response.status_code}")
        print(f"  响应: {response.text}")
        exit(1)
except Exception as e:
    print(f"✗ 创建失败: {e}")
    exit(1)

print()

# 步骤 3：获取对话详情
print("[步骤 3/5] 获取对话详情...")
try:
    response = requests.get(
        f"{BASE_URL}/conversations/{conv_id}",
        params={"include_messages": True, "message_limit": 100},
        timeout=10
    )

    if response.status_code == 200:
        data = response.json()
        conversation = data.get('conversation', {})
        messages = data.get('messages', [])
        print(f"✓ 对话详情获取成功")
        print(f"  - 标题: {conversation.get('title')}")
        print(f"  - 模型: {conversation.get('current_model')}")
        print(f"  - 消息数: {len(messages)}")
        print(f"  - system_prompt: {conversation.get('system_prompt', 'None')}")
    else:
        print(f"✗ 获取失败: HTTP {response.status_code}")
        print(f"  响应: {response.text}")
        # 不退出，继续测试
except Exception as e:
    print(f"✗ 获取失败: {e}")
    import traceback
    traceback.print_exc()

print()

# 步骤 4：发送消息（非流式）
print("[步骤 4/5] 发送测试消息（非流式）...")
try:
    payload = {
        "content": "1+1等于多少？请用一句话回答。",
        "stream": False
    }
    response = requests.post(
        f"{BASE_URL}/conversations/{conv_id}/messages",
        json=payload,
        timeout=30
    )

    if response.status_code == 200:
        data = response.json()
        if data.get('success'):
            user_msg = data['data']['user_message']
            assistant_msg = data['data']['assistant_message']
            print(f"✓ 消息发送成功")
            print(f"  - 用户消息ID: {user_msg['id']}")
            print(f"  - 助手消息ID: {assistant_msg['id']}")
            print(f"  - 助手回复: {assistant_msg['content'][:100]}...")
        else:
            print(f"✗ 发送失败: {data}")
    else:
        print(f"✗ 发送失败: HTTP {response.status_code}")
        print(f"  响应: {response.text}")
except Exception as e:
    print(f"✗ 发送失败: {e}")
    import traceback
    traceback.print_exc()

print()

# 步骤 5：再次获取对话（验证消息已保存）
print("[步骤 5/5] 验证消息已保存...")
try:
    response = requests.get(
        f"{BASE_URL}/conversations/{conv_id}",
        params={"include_messages": True},
        timeout=10
    )

    if response.status_code == 200:
        data = response.json()
        messages = data.get('messages', [])
        print(f"✓ 消息验证成功")
        print(f"  - 当前消息数: {len(messages)}")
        if messages:
            print(f"  - 最新消息:")
            for i, msg in enumerate(messages[-2:], 1):  # 显示最后2条
                role = msg.get('role')
                content = msg.get('content', '')[:50]
                print(f"    [{i}] {role}: {content}...")
    else:
        print(f"✗ 验证失败: HTTP {response.status_code}")
except Exception as e:
    print(f"✗ 验证失败: {e}")

print()
print("=" * 70)
print("测试完成")
print("=" * 70)
print()
print("如果所有步骤都成功，前端应该可以正常使用了。")
print()
print("测试的对话ID:", conv_id)
print("可以在前端打开这个对话进行验证。")
