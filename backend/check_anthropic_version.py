"""
Check anthropic library version and available methods
"""
import anthropic
import sys

print("=" * 70)
print("Anthropic Library Information")
print("=" * 70)
print()

print(f"anthropic version: {anthropic.__version__}")
print(f"anthropic file location: {anthropic.__file__}")
print()

print("Creating client...")
try:
    client = anthropic.Anthropic(api_key="dummy-key-for-testing")
    print(f"Client type: {type(client)}")
    print()

    print("Client attributes (non-private):")
    attrs = [a for a in dir(client) if not a.startswith('_')]
    for attr in attrs:
        print(f"  - {attr}")
    print()

    # Check specific attributes
    print("Checking key attributes:")
    print(f"  - Has 'completions': {hasattr(client, 'completions')}")
    print(f"  - Has 'messages': {hasattr(client, 'messages')}")
    print(f"  - Has 'completion': {hasattr(client, 'completion')}")
    print()

    if hasattr(client, 'completions'):
        print("Examining 'completions' attribute:")
        completions = client.completions
        print(f"  - Type: {type(completions)}")
        print(f"  - Attributes: {[a for a in dir(completions) if not a.startswith('_')]}")
        print()

        if hasattr(completions, 'create'):
            import inspect
            sig = inspect.signature(completions.create)
            print(f"  - create() signature: {sig}")

    # Check base_url
    if hasattr(client, 'base_url'):
        print(f"Base URL: {client.base_url}")
    if hasattr(client, '_base_url'):
        print(f"_base_url: {client._base_url}")

    print()
    print("Checking API version constants:")
    if hasattr(anthropic, 'AI_PROMPT'):
        print(f"  - AI_PROMPT: {anthropic.AI_PROMPT}")
    if hasattr(anthropic, 'HUMAN_PROMPT'):
        print(f"  - HUMAN_PROMPT: {anthropic.HUMAN_PROMPT}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 70)
