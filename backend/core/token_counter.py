"""
Token 计数模块

使用 tiktoken 库精确计算不同模型的 token 使用量
如果 tiktoken 不可用(Python < 3.8),使用估算方法
"""
from typing import Optional, Dict, List
import logging

# 尝试导入 tiktoken,如果失败则使用降级方案
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    import warnings
    warnings.warn(
        "tiktoken is not available (requires Python >= 3.8). "
        "Using fallback token estimation. "
        "Please upgrade to Python 3.8+ for accurate token counting.",
        RuntimeWarning
    )

logger = logging.getLogger(__name__)


class TokenCounter:
    """
    Token 计数器

    支持多种模型的 token 精确计数:
    - Claude 系列 (cl100k_base)
    - GPT-4 系列 (cl100k_base)
    - GPT-3.5 系列 (cl100k_base)
    - 其他模型使用默认编码
    """

    def __init__(self):
        """初始化 TokenCounter"""
        self._use_tiktoken = TIKTOKEN_AVAILABLE

        if self._use_tiktoken:
            # 模型编码器缓存
            self._encoders: Dict[str, 'tiktoken.Encoding'] = {}

            # 模型到编码器的映射
            self._model_encoding_map = {
                # Claude 系列
                "claude": "cl100k_base",
                "claude-3": "cl100k_base",
                "claude-3.5": "cl100k_base",
                "claude-4": "cl100k_base",
                "claude-sonnet": "cl100k_base",
                "claude-opus": "cl100k_base",
                "claude-haiku": "cl100k_base",

                # OpenAI GPT-4 系列
                "gpt-4": "cl100k_base",
                "gpt-4-turbo": "cl100k_base",
                "gpt-4o": "cl100k_base",

                # OpenAI GPT-3.5 系列
                "gpt-3.5-turbo": "cl100k_base",
                "gpt-35-turbo": "cl100k_base",

                # Minimax (使用 Claude 编码作为近似)
                "minimax": "cl100k_base",

                # 默认
                "default": "cl100k_base"
            }
            logger.info("TokenCounter initialized with tiktoken")
        else:
            logger.warning("TokenCounter initialized with fallback estimation (tiktoken not available)")

    def _get_encoder(self, model: str) -> 'tiktoken.Encoding':
        """
        获取模型对应的编码器

        Args:
            model: 模型名称

        Returns:
            tiktoken.Encoding 对象
        """
        # 标准化模型名称
        model_lower = model.lower()

        # 查找匹配的编码名称
        encoding_name = self._model_encoding_map.get("default", "cl100k_base")
        for key, value in self._model_encoding_map.items():
            if key in model_lower:
                encoding_name = value
                break

        # 从缓存中获取或创建新的编码器
        if encoding_name not in self._encoders:
            try:
                self._encoders[encoding_name] = tiktoken.get_encoding(encoding_name)
                logger.debug(f"Loaded encoder: {encoding_name}")
            except Exception as e:
                logger.warning(f"Failed to load encoder {encoding_name}: {e}, using default")
                self._encoders[encoding_name] = tiktoken.get_encoding("cl100k_base")

        return self._encoders[encoding_name]

    def _estimate_tokens_fallback(self, text: str) -> int:
        """
        降级方案: 粗略估算 token 数量

        基于经验规则:
        - 英文: ~1 token per 4 characters
        - 中文: ~1 token per 1.5 characters
        - 混合文本: 取平均

        Args:
            text: 要估算的文本

        Returns:
            估算的 token 数量
        """
        if not text:
            return 0

        # 统计中英文字符
        chinese_chars = sum(1 for char in text if '\u4e00' <= char <= '\u9fff')
        total_chars = len(text)
        english_chars = total_chars - chinese_chars

        # 分别估算
        chinese_tokens = chinese_chars / 1.5
        english_tokens = english_chars / 4.0

        return int(chinese_tokens + english_tokens)

    def count_tokens(self, text: str, model: str = "claude") -> int:
        """
        计算文本的 token 数量

        Args:
            text: 要计算的文本
            model: 模型名称,用于选择正确的编码器

        Returns:
            token 数量
        """
        if not text:
            return 0

        # 如果 tiktoken 可用,使用精确计数
        if self._use_tiktoken:
            try:
                encoder = self._get_encoder(model)
                tokens = encoder.encode(text)
                return len(tokens)
            except Exception as e:
                logger.error(f"Error counting tokens with tiktoken: {e}", exc_info=True)
                # 降级到估算
                return self._estimate_tokens_fallback(text)
        else:
            # 使用估算方法
            return self._estimate_tokens_fallback(text)

    def count_messages_tokens(
        self,
        messages: List[Dict[str, str]],
        model: str = "claude"
    ) -> Dict[str, int]:
        """
        计算消息列表的 token 使用量

        Args:
            messages: 消息列表,格式为 [{"role": "user", "content": "..."}]
            model: 模型名称

        Returns:
            包含 prompt_tokens, completion_tokens, total_tokens 的字典
        """
        prompt_tokens = 0
        completion_tokens = 0

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            tokens = self.count_tokens(content, model)

            # 添加消息格式开销 (role 标记等)
            # Claude/GPT API 格式: 每条消息约增加 4 个 token
            format_overhead = 4
            tokens += format_overhead

            if role in ["user", "system"]:
                prompt_tokens += tokens
            elif role == "assistant":
                completion_tokens += tokens
            else:
                # 未知角色算作 prompt
                prompt_tokens += tokens

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens
        }

    def estimate_conversation_tokens(
        self,
        system_prompt: Optional[str],
        messages: List[Dict[str, str]],
        model: str = "claude"
    ) -> int:
        """
        估算完整对话的 token 使用量

        Args:
            system_prompt: 系统提示词
            messages: 消息历史
            model: 模型名称

        Returns:
            预估的总 token 数
        """
        total = 0

        # 计算 system prompt
        if system_prompt:
            total += self.count_tokens(system_prompt, model)
            total += 4  # 格式开销

        # 计算消息
        result = self.count_messages_tokens(messages, model)
        total += result["total_tokens"]

        # API 调用基础开销
        base_overhead = 3
        total += base_overhead

        return total

    def check_token_limit(
        self,
        text: str,
        model: str = "claude",
        max_tokens: int = 150000
    ) -> bool:
        """
        检查文本是否超过 token 限制

        Args:
            text: 要检查的文本
            model: 模型名称
            max_tokens: 最大 token 限制

        Returns:
            True 如果未超限,False 如果超限
        """
        tokens = self.count_tokens(text, model)
        return tokens <= max_tokens

    def truncate_to_token_limit(
        self,
        text: str,
        model: str = "claude",
        max_tokens: int = 150000
    ) -> str:
        """
        截断文本到指定 token 限制

        Args:
            text: 要截断的文本
            model: 模型名称
            max_tokens: 最大 token 限制

        Returns:
            截断后的文本
        """
        # 如果 tiktoken 可用,使用精确截断
        if self._use_tiktoken:
            try:
                encoder = self._get_encoder(model)
                tokens = encoder.encode(text)

                if len(tokens) <= max_tokens:
                    return text

                # 截断并解码
                truncated_tokens = tokens[:max_tokens]
                return encoder.decode(truncated_tokens)
            except Exception as e:
                logger.error(f"Error truncating text with tiktoken: {e}", exc_info=True)
                # 降级到字符截断
                pass

        # 降级方案: 按字符截断 (估算 1 token ≈ 4 字符)
        char_limit = max_tokens * 4
        return text[:char_limit]


# 全局单例
_token_counter: Optional[TokenCounter] = None


def get_token_counter() -> TokenCounter:
    """
    获取全局 TokenCounter 单例

    Returns:
        TokenCounter 实例
    """
    global _token_counter
    if _token_counter is None:
        _token_counter = TokenCounter()
    return _token_counter


# 便捷函数

def count_tokens(text: str, model: str = "claude") -> int:
    """
    便捷函数: 计算文本 token 数

    Args:
        text: 文本内容
        model: 模型名称

    Returns:
        token 数量
    """
    counter = get_token_counter()
    return counter.count_tokens(text, model)


def count_message_tokens(message: Dict[str, str], model: str = "claude") -> int:
    """
    便捷函数: 计算单条消息 token 数

    Args:
        message: 消息字典
        model: 模型名称

    Returns:
        token 数量
    """
    content = message.get("content", "")
    counter = get_token_counter()
    tokens = counter.count_tokens(content, model)
    return tokens + 4  # 加上格式开销
