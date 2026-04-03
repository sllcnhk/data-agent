"""
测试对话分组API
"""
import sys
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import requests
import json

BASE_URL = "http://localhost:8000/api/v1"

def test_groups_api():
    """测试分组管理API"""
    print("=" * 80)
    print("Testing Conversation Groups API")
    print("=" * 80)
    print()

    # 1. 创建分组
    print("Test 1: Create Groups")
    print("-" * 80)

    groups_to_create = [
        {"name": "工作", "description": "工作相关对话", "icon": "💼", "color": "#1890ff"},
        {"name": "学习", "description": "学习相关对话", "icon": "📚", "color": "#52c41a"},
        {"name": "个人", "description": "个人事务", "icon": "👤", "color": "#722ed1"},
    ]

    created_groups = []

    for group_data in groups_to_create:
        try:
            response = requests.post(f"{BASE_URL}/groups", json=group_data)
            if response.status_code == 200:
                result = response.json()
                created_groups.append(result["data"])
                print(f"  ✓ Created: {result['data']['name']} (ID: {result['data']['id'][:8]}...)")
            else:
                print(f"  ✗ Failed to create {group_data['name']}: {response.status_code}")
                print(f"    {response.text}")
        except Exception as e:
            print(f"  ✗ Error creating {group_data['name']}: {e}")

    print()

    # 2. 获取分组列表
    print("Test 2: List Groups")
    print("-" * 80)

    try:
        response = requests.get(f"{BASE_URL}/groups")
        if response.status_code == 200:
            result = response.json()
            print(f"  ✓ Total groups: {result['total']}")
            for group in result['groups']:
                print(f"    - {group['icon']} {group['name']} (对话数: {group['conversation_count']})")
        else:
            print(f"  ✗ Failed: {response.status_code}")
    except Exception as e:
        print(f"  ✗ Error: {e}")

    print()

    # 3. 创建测试对话
    print("Test 3: Create Test Conversations")
    print("-" * 80)

    conversations_to_create = [
        {"title": "项目需求讨论", "model_key": "claude"},
        {"title": "Python 学习笔记", "model_key": "claude"},
        {"title": "个人日记", "model_key": "claude"},
    ]

    created_conversations = []

    for conv_data in conversations_to_create:
        try:
            response = requests.post(f"{BASE_URL}/conversations", json=conv_data)
            if response.status_code == 200:
                result = response.json()
                created_conversations.append(result["data"])
                print(f"  ✓ Created: {result['data']['title']} (ID: {result['data']['id'][:8]}...)")
            else:
                print(f"  ✗ Failed to create {conv_data['title']}: {response.status_code}")
        except Exception as e:
            print(f"  ✗ Error creating {conv_data['title']}: {e}")

    print()

    if not created_groups or not created_conversations:
        print("⚠ Skipping remaining tests due to missing data")
        return

    # 4. 移动对话到分组
    print("Test 4: Move Conversations to Groups")
    print("-" * 80)

    moves = [
        (created_conversations[0], created_groups[0]),  # 项目需求 -> 工作
        (created_conversations[1], created_groups[1]),  # Python学习 -> 学习
        (created_conversations[2], created_groups[2]),  # 个人日记 -> 个人
    ]

    for conv, group in moves:
        try:
            response = requests.put(
                f"{BASE_URL}/conversations/{conv['id']}/group",
                json={"group_id": group['id']}
            )
            if response.status_code == 200:
                print(f"  ✓ Moved '{conv['title']}' -> {group['icon']} {group['name']}")
            else:
                print(f"  ✗ Failed to move '{conv['title']}': {response.status_code}")
        except Exception as e:
            print(f"  ✗ Error moving '{conv['title']}': {e}")

    print()

    # 5. 重命名对话
    print("Test 5: Rename Conversation")
    print("-" * 80)

    try:
        old_title = created_conversations[0]['title']
        new_title = "项目需求分析 v2"

        response = requests.put(
            f"{BASE_URL}/conversations/{created_conversations[0]['id']}/title",
            json={"title": new_title}
        )

        if response.status_code == 200:
            print(f"  ✓ Renamed: '{old_title}' -> '{new_title}'")
        else:
            print(f"  ✗ Failed to rename: {response.status_code}")
    except Exception as e:
        print(f"  ✗ Error renaming: {e}")

    print()

    # 6. 获取分组内的对话
    print("Test 6: Get Conversations in Group")
    print("-" * 80)

    try:
        group = created_groups[0]
        response = requests.get(f"{BASE_URL}/groups/{group['id']}/conversations")

        if response.status_code == 200:
            result = response.json()
            print(f"  ✓ Group: {group['icon']} {group['name']}")
            print(f"    Total conversations: {result['total']}")
            for conv in result['conversations']:
                print(f"      - {conv['title']}")
        else:
            print(f"  ✗ Failed: {response.status_code}")
    except Exception as e:
        print(f"  ✗ Error: {e}")

    print()

    # 7. 移出分组
    print("Test 7: Remove from Group")
    print("-" * 80)

    try:
        conv = created_conversations[2]
        response = requests.put(
            f"{BASE_URL}/conversations/{conv['id']}/group",
            json={"group_id": None}
        )

        if response.status_code == 200:
            print(f"  ✓ Removed '{conv['title']}' from group (now ungrouped)")
        else:
            print(f"  ✗ Failed: {response.status_code}")
    except Exception as e:
        print(f"  ✗ Error: {e}")

    print()

    # 8. 更新分组
    print("Test 8: Update Group")
    print("-" * 80)

    try:
        group = created_groups[0]
        response = requests.put(
            f"{BASE_URL}/groups/{group['id']}",
            json={"name": "工作项目", "description": "所有工作相关的项目"}
        )

        if response.status_code == 200:
            result = response.json()
            print(f"  ✓ Updated group: {result['data']['name']}")
        else:
            print(f"  ✗ Failed: {response.status_code}")
    except Exception as e:
        print(f"  ✗ Error: {e}")

    print()

    # 9. 获取更新后的分组列表
    print("Test 9: List Updated Groups")
    print("-" * 80)

    try:
        response = requests.get(f"{BASE_URL}/groups")
        if response.status_code == 200:
            result = response.json()
            print(f"  ✓ Total groups: {result['total']}")
            for group in result['groups']:
                print(f"    - {group['icon']} {group['name']} (对话数: {group['conversation_count']})")
        else:
            print(f"  ✗ Failed: {response.status_code}")
    except Exception as e:
        print(f"  ✗ Error: {e}")

    print()

    # 总结
    print("=" * 80)
    print("✅ All Tests Completed!")
    print("=" * 80)
    print()
    print("Summary:")
    print(f"  - Created {len(created_groups)} groups")
    print(f"  - Created {len(created_conversations)} conversations")
    print(f"  - Tested move to group, rename, remove from group, and update group")
    print()
    print("You can now:")
    print("  1. Check the database to see the groups and grouped conversations")
    print("  2. View http://localhost:8000/api/docs to explore all group APIs")
    print("  3. Start implementing the frontend components")


if __name__ == "__main__":
    test_groups_api()
