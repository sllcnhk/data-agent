"""
Simple test for LLM chat functionality
"""
import requests
import json
import sys

BASE_URL = "http://localhost:8000/api/v1"

print("=" * 70)
print("Testing LLM Chat Functionality")
print("=" * 70)
print()

# Step 1: Get LLM config
print("[Step 1/4] Getting LLM config...")
try:
    response = requests.get(f"{BASE_URL}/llm-configs?enabled_only=true", timeout=10)
    if response.status_code == 200:
        data = response.json()
        configs = data.get('data', [])
        if configs:
            model_key = configs[0].get('model_key')
            print(f"Success: Found {len(configs)} config(s), using model: {model_key}")
        else:
            print("Error: No LLM configs available")
            sys.exit(1)
    else:
        print(f"Error: HTTP {response.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

print()

# Step 2: Create conversation
print("[Step 2/4] Creating test conversation...")
try:
    payload = {
        "title": "LLM Chat Test",
        "model_key": model_key,
        "system_prompt": "You are a test assistant. Answer briefly."
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
            print(f"Success: Conversation created")
            print(f"  - ID: {conv_id}")
        else:
            print(f"Error: {data}")
            sys.exit(1)
    else:
        print(f"Error: HTTP {response.status_code}")
        print(f"  Response: {response.text}")
        sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

print()

# Step 3: Send message (non-streaming)
print("[Step 3/4] Sending test message...")
try:
    payload = {
        "content": "What is 1+1? Answer in one sentence.",
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
            print(f"Success: Message sent")
            print(f"  - User message ID: {user_msg['id']}")
            print(f"  - Assistant message ID: {assistant_msg['id']}")
            print(f"  - Assistant reply: {assistant_msg['content'][:200]}")

            # Check if reply contains error
            if "Error" in assistant_msg['content'] or "error" in assistant_msg['content'].lower():
                print()
                print("WARNING: Assistant reply contains error message!")
                print(f"Full reply: {assistant_msg['content']}")
                sys.exit(1)
        else:
            print(f"Error: {data}")
            sys.exit(1)
    else:
        print(f"Error: HTTP {response.status_code}")
        print(f"  Response: {response.text}")
        sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Step 4: Verify messages saved
print("[Step 4/4] Verifying messages saved...")
try:
    response = requests.get(
        f"{BASE_URL}/conversations/{conv_id}",
        params={"include_messages": True},
        timeout=10
    )

    if response.status_code == 200:
        data = response.json()
        messages = data.get('messages', [])
        print(f"Success: Messages verified")
        print(f"  - Total messages: {len(messages)}")
        if len(messages) != 2:
            print(f"WARNING: Expected 2 messages, got {len(messages)}")
            sys.exit(1)
    else:
        print(f"Error: HTTP {response.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

print()
print("=" * 70)
print("All tests passed!")
print("=" * 70)
print()
print(f"Test conversation ID: {conv_id}")
