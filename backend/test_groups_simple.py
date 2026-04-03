"""
简化版分组API测试
"""
import requests
import json

BASE_URL = "http://localhost:8000/api/v1"

print("Testing Groups API...")
print()

# 1. 创建分组
print("1. Creating groups...")
group_data = {"name": "Work", "description": "Work related", "icon": "briefcase", "color": "#1890ff"}
response = requests.post(f"{BASE_URL}/groups", json=group_data)
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    result = response.json()
    group_id = result["data"]["id"]
    print(f"   Group ID: {group_id}")
    print(f"   Group Name: {result['data']['name']}")
else:
    print(f"   Error: {response.text}")
    group_id = None

print()

# 2. 获取分组列表
print("2. Listing groups...")
response = requests.get(f"{BASE_URL}/groups")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    result = response.json()
    print(f"   Total: {result['total']}")
    for g in result['groups']:
        print(f"   - {g['name']} (ID: {g['id'][:8]}...)")

print()

# 3. 创建对话
print("3. Creating conversation...")
conv_data = {"title": "Test Conversation", "model_key": "claude"}
response = requests.post(f"{BASE_URL}/conversations", json=conv_data)
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    result = response.json()
    conv_id = result["data"]["id"]
    print(f"   Conversation ID: {conv_id}")
    print(f"   Title: {result['data']['title']}")
else:
    print(f"   Error: {response.text}")
    conv_id = None

print()

if group_id and conv_id:
    # 4. 移动对话到分组
    print("4. Moving conversation to group...")
    response = requests.put(
        f"{BASE_URL}/conversations/{conv_id}/group",
        json={"group_id": group_id}
    )
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"   Message: {result['message']}")

    print()

    # 5. 重命名对话
    print("5. Renaming conversation...")
    response = requests.put(
        f"{BASE_URL}/conversations/{conv_id}/title",
        json={"title": "Updated Test Conversation"}
    )
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"   New title: {result['data']['title']}")

    print()

    # 6. 获取分组内对话
    print("6. Getting conversations in group...")
    response = requests.get(f"{BASE_URL}/groups/{group_id}/conversations")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"   Total conversations: {result['total']}")
        for conv in result['conversations']:
            print(f"   - {conv['title']}")

print()
print("=" * 60)
print("All tests completed!")
print("Check http://localhost:8000/api/docs for full API documentation")
