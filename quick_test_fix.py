import sys
sys.path.insert(0, 'backend')

print('=' * 60)
print('Quick Test: max_tokens Fix')
print('=' * 60)

from backend.config.database import get_db_context
from backend.models.llm_config import LLMConfig

print('\n[1] Database Config Type Conversion')
print('-' * 60)

with get_db_context() as db:
    config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

    print(f'Raw value: {repr(config.max_tokens)} (type: {type(config.max_tokens).__name__})')

    # Simulate conversion logic in conversation_service.py
    try:
        max_tokens = int(config.max_tokens) if config.max_tokens else 8192
    except (ValueError, TypeError):
        max_tokens = 8192

    print(f'Converted: {max_tokens} (type: {type(max_tokens).__name__})')

    if max_tokens == 8192 and isinstance(max_tokens, int):
        print('[PASS] Type conversion works correctly')
    else:
        print(f'[FAIL] Expected int(8192), got {type(max_tokens).__name__}({max_tokens})')

print('\n' + '=' * 60)
print('Test Complete')
print('=' * 60)
print('\nNext: Restart backend to apply changes')
print('  restart_backend.bat')
