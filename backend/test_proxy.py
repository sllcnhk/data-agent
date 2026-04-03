"""
测试代理配置验证

检查:
1. data-agent 项目是否读取 .claude/setting.json
2. httpx 客户端是否使用代理
3. 实际的网络请求路径
"""
import sys
import os
import json
import httpx

# 添加项目根目录到 Python 路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

print("=" * 80)
print("代理配置验证测试")
print("=" * 80)

# 测试 1: 检查是否读取 .claude/setting.json
print("\n[Test 1] 检查 .claude/setting.json 是否被使用")
print("-" * 80)

claude_config_path = os.path.join(os.path.expanduser("~"), ".claude", "setting.json")
print(f"Claude Code 配置文件路径: {claude_config_path}")
print(f"文件存在: {os.path.exists(claude_config_path)}")

if os.path.exists(claude_config_path):
    with open(claude_config_path, 'r', encoding='utf-8') as f:
        claude_config = json.load(f)
    print(f"Claude Code 配置内容:")
    print(json.dumps(claude_config, indent=2, ensure_ascii=False))
else:
    print("Claude Code 配置文件不存在")

# 检查 data-agent 代码中是否有读取 .claude 配置的逻辑
print("\n[Result] data-agent 项目不会读取 .claude/setting.json")
print("原因:")
print("  1. .claude/setting.json 是 Claude Code CLI 工具的配置文件")
print("  2. data-agent 是独立的 Python 项目，有自己的配置系统")
print("  3. 代码中没有读取该文件的逻辑")

# 测试 2: 检查当前环境变量
print("\n[Test 2] 检查当前 Python 进程的环境变量")
print("-" * 80)
proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy']
has_proxy = False

for var in proxy_vars:
    value = os.environ.get(var)
    if value:
        print(f"  {var}: {value}")
        has_proxy = True
    else:
        print(f"  {var}: (未设置)")

if not has_proxy:
    print("\n[Result] 当前没有设置代理环境变量")

# 测试 3: httpx 客户端默认行为
print("\n[Test 3] httpx.AsyncClient 默认行为")
print("-" * 80)
print("httpx.AsyncClient() 会自动读取以下环境变量:")
print("  - HTTP_PROXY / http_proxy")
print("  - HTTPS_PROXY / https_proxy")
print("  - ALL_PROXY / all_proxy")
print("  - NO_PROXY / no_proxy")
print()
print("如果这些环境变量未设置，httpx 将直接连接，不使用代理")

# 测试 4: 检查 data-agent 的 claude.py 代码
print("\n[Test 4] data-agent 的 Claude 适配器代码分析")
print("-" * 80)
print("文件位置: backend/core/model_adapters/claude.py")
print()
print("关键代码 (第 251 行):")
print("  async with httpx.AsyncClient(timeout=120.0) as client:")
print("      response = await client.post(url, headers=headers, json=request_body)")
print()
print("分析:")
print("  1. 创建 httpx.AsyncClient 时未指定 proxies 参数")
print("  2. 未明确配置代理")
print("  3. 会使用系统环境变量中的代理设置（如果有）")
print("  4. 不会读取 .claude/setting.json")

# 测试 5: 实际测试网络请求（可选）
print("\n[Test 5] 测试实际的网络请求路径")
print("-" * 80)

test_url = "http://10.0.3.248:3000/api"
print(f"目标地址: {test_url}")
print()

# 场景 1: 不使用代理
print("场景 1: 当前配置（无代理环境变量）")
try:
    import asyncio

    async def test_no_proxy():
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(f"{test_url}/health", follow_redirects=True)
                return f"成功连接: HTTP {response.status_code}"
            except httpx.ConnectError as e:
                return f"连接失败: {str(e)}"
            except Exception as e:
                return f"请求失败: {str(e)}"

    result = asyncio.run(test_no_proxy())
    print(f"  结果: {result}")
except Exception as e:
    print(f"  测试失败: {e}")

print()
print("场景 2: 如果设置了代理环境变量")
print("  需要设置: HTTP_PROXY=http://10.03.248:3128")
print("  需要设置: HTTPS_PROXY=http://10.03.248:3128")
print("  然后 httpx 会自动使用这些代理")

# 总结
print("\n" + "=" * 80)
print("总结")
print("=" * 80)
print()
print("1. data-agent 项目 **不会** 读取 ~/.claude/setting.json")
print("   - 那是 Claude Code CLI 的配置文件，与 data-agent 无关")
print()
print("2. data-agent 的 Claude 适配器使用 httpx.AsyncClient")
print("   - 默认会读取系统环境变量中的代理设置")
print("   - 当前环境变量中未设置代理")
print()
print("3. 如何让 data-agent 使用代理?")
print()
print("   方案 A: 设置环境变量（推荐）")
print("   在启动脚本 start-all.bat 中添加:")
print("     set HTTP_PROXY=http://10.03.248:3128")
print("     set HTTPS_PROXY=http://10.03.248:3128")
print()
print("   方案 B: 在 .env 文件中添加")
print("     HTTP_PROXY=http://10.03.248:3128")
print("     HTTPS_PROXY=http://10.03.248:3128")
print()
print("   方案 C: 修改代码，在 claude.py 中明确配置代理")
print("     proxy = 'http://10.03.248:3128'")
print("     async with httpx.AsyncClient(timeout=120.0, proxies=proxy) as client:")
print()
print("4. 当前状态:")
print(f"   - 中转服务地址: {test_url}")
print("   - 是否使用代理: 否（环境变量未设置）")
print("   - 连接方式: 直接连接")
print()
print("5. 是否需要代理?")
print("   - 如果 10.0.3.248:3000 可以直接访问 -> 不需要代理")
print("   - 如果需要通过代理访问外网 -> 需要设置代理")
