import requests
import json
import time

def test_api():
    base_url = "http://localhost:8000"
    
    # Wait for server to be ready
    time.sleep(2)
    
    tests = [
        ("GET /", "/"),
        ("GET /health", "/health"),
        ("GET /api/v1/", "/api/v1/"),
        ("GET /api/v1/agents", "/api/v1/agents"),
        ("GET /api/v1/skills", "/api/v1/skills"),
        ("GET /api/v1/tasks", "/api/v1/tasks"),
    ]
    
    print("=" * 60)
    print("API Test Results")
    print("=" * 60)
    
    for name, path in tests:
        try:
            url = base_url + path
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                print(f"✓ {name}: {response.status_code}")
                try:
                    data = response.json()
                    print(f"  Response: {json.dumps(data, indent=2)[:100]}...")
                except:
                    print(f"  Response: {response.text[:100]}...")
            else:
                print(f"✗ {name}: {response.status_code}")
                print(f"  Error: {response.text[:100]}")
        except Exception as e:
            print(f"✗ {name}: ERROR - {str(e)[:100]}")
        
        print()
    
    print("=" * 60)

if __name__ == "__main__":
    test_api()
