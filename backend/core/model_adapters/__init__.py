"""
模型适配器模块

提供统一的LLM接口,支持多个模型提供商
"""
from backend.core.model_adapters.base import BaseModelAdapter
from backend.core.model_adapters.claude import ClaudeAdapter

# 可选适配器 - 条件导入（如果依赖库不可用，不阻止系统启动）
__all__ = [
    "BaseModelAdapter",
    "ClaudeAdapter",
    "ModelAdapterFactory",
]

# 尝试导入 OpenAI 适配器
try:
    from backend.core.model_adapters.openai import OpenAIAdapter
    __all__.append("OpenAIAdapter")
except ImportError as e:
    print(f"[WARNING] OpenAI adapter not available: {e}")
    OpenAIAdapter = None

# 尝试导入 Gemini 适配器
try:
    from backend.core.model_adapters.gemini import GeminiAdapter
    __all__.append("GeminiAdapter")
except ImportError as e:
    print(f"[WARNING] Gemini adapter not available: {e}")
    GeminiAdapter = None

# 尝试导入 Qianwen 适配器
try:
    from backend.core.model_adapters.qianwen import QianwenAdapter
    __all__.append("QianwenAdapter")
except ImportError as e:
    print(f"[WARNING] Qianwen adapter not available: {e}")
    QianwenAdapter = None

# 尝试导入 Doubao 适配器
try:
    from backend.core.model_adapters.doubao import DoubaoAdapter
    __all__.append("DoubaoAdapter")
except ImportError as e:
    print(f"[WARNING] Doubao adapter not available: {e}")
    DoubaoAdapter = None

# 必须在最后导入 factory，因为它依赖上面的适配器
from backend.core.model_adapters.factory import ModelAdapterFactory
