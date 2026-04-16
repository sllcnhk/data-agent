"""
报告 AI 数据分析服务

从图表实时查询结果出发，调用 LLM 进行趋势分析、异常检测、洞察提炼、建议输出。

LLM 调用优先级：Claude（默认）→ OpenAI → Gemini → Qianwen → Doubao
每个 provider 失败后自动 fallback 到下一个，全部不可用时静默返回空列表。
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# LLM 调用优先级顺序（按业务偏好排列）
_PROVIDER_ORDER = ["claude", "openai", "gemini", "qianwen", "doubao"]

# 每个图表最多发给 LLM 的数据行数（控制 prompt token 量）
_MAX_ROWS_PER_CHART = 50

# 支持的分析维度元数据（前端渲染图标/颜色也从这里读）
SECTION_META: Dict[str, Dict[str, str]] = {
    "trend":      {"icon": "📈", "color": "#1890ff", "label": "趋势分析"},
    "anomaly":    {"icon": "⚠️",  "color": "#fa8c16", "label": "异常检测"},
    "insight":    {"icon": "💡", "color": "#52c41a", "label": "业务洞察"},
    "conclusion": {"icon": "✅", "color": "#722ed1", "label": "总结建议"},
}


# ─────────────────────────────────────────────────────────────────────────────
# LLM Adapter 获取
# ─────────────────────────────────────────────────────────────────────────────

async def _get_adapter() -> Tuple[Optional[Any], Optional[str]]:
    """
    按优先级尝试创建 LLM adapter。

    优先读取 settings 里已配置的默认 provider（若存在），否则按 _PROVIDER_ORDER 顺序。

    Returns:
        (adapter, provider_name)，无可用 provider 时返回 (None, None)
    """
    from backend.core.model_adapters.factory import ModelAdapterFactory
    from backend.config.settings import settings

    order = list(_PROVIDER_ORDER)

    # 若 settings 有明确的默认 provider，排到第一位
    for attr in ("default_llm_provider", "llm_provider", "model_provider"):
        default_prov = getattr(settings, attr, None)
        if default_prov and isinstance(default_prov, str) and default_prov in order:
            order.remove(default_prov)
            order.insert(0, default_prov)
            break

    for provider in order:
        try:
            adapter = ModelAdapterFactory.create_from_settings(provider)
            if adapter is not None:
                logger.debug("[ReportAnalysis] 使用 LLM provider: %s", provider)
                return adapter, provider
        except Exception as exc:
            logger.debug("[ReportAnalysis] provider=%s 不可用: %s", provider, exc)

    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Prompt 构建
# ─────────────────────────────────────────────────────────────────────────────

def _build_data_summary(
    charts_data: Dict[str, Any],
    chart_specs: List[Dict],
) -> str:
    """将图表查询结果格式化为 LLM 可读的数据摘要段落。"""
    spec_map = {c.get("id"): c for c in (chart_specs or [])}
    parts: List[str] = []

    for cid, rows in charts_data.items():
        spec = spec_map.get(cid, {})
        chart_title = spec.get("title", cid)
        chart_type = spec.get("chart_type", "")
        x_field = spec.get("x_field", "")
        y_fields = spec.get("y_fields", [])

        if not isinstance(rows, list):
            continue

        total_rows = len(rows)
        sample_rows = rows[:_MAX_ROWS_PER_CHART]

        entry = f"### [{cid}] {chart_title}（图表类型：{chart_type}）\n"
        if x_field:
            y_str = "、".join(y_fields) if y_fields else "（未知）"
            entry += f"- 维度：X={x_field}，Y={y_str}\n"
        entry += f"- 共 {total_rows} 行数据（展示前 {len(sample_rows)} 行）：\n"
        entry += (
            "```json\n"
            + json.dumps(sample_rows, ensure_ascii=False, default=str, indent=None)
            + "\n```\n"
        )
        parts.append(entry)

    return "\n".join(parts) if parts else "（暂无可用数据）"


def _build_prompt(
    charts_data: Dict[str, Any],
    report_title: str,
    analysis_focus: List[str],
    chart_specs: List[Dict],
) -> str:
    """
    构建发送给 LLM 的完整分析 Prompt。

    包含：角色定位、报告名称、图表数据摘要（真实查询结果）、
    分析维度要求（按 analysis_focus 动态组装）、严格 JSON 输出格式要求。
    """
    data_summary = _build_data_summary(charts_data, chart_specs)

    # 各分析维度的详细指引（供 LLM 理解任务边界）
    focus_descs: Dict[str, str] = {
        "trend": (
            "📈 **趋势分析**：识别数据在时间轴上的上升/下降/波动趋势，"
            "找出高峰期和低谷期，量化变化幅度（如「本周较上周下降 12%」），"
            "判断趋势是否持续或出现转折点。"
        ),
        "anomaly": (
            "⚠️ **异常检测**：找出明显偏离正常范围的数据点（突然断崖下跌/暴涨、"
            "单日值与前后差异悬殊），指明具体日期/时段，推测可能原因"
            "（节假日、系统故障、数据口径变化等）。若数据走势平稳，请明确说明。"
        ),
        "insight": (
            "💡 **业务洞察**：结合外呼 SaaS 业务背景，解读数据背后的业务含义，"
            "例如接通率变化对用户体验的影响、不同时段/地区的外呼效率对比、"
            "高/低峰期的资源配置建议。"
        ),
        "conclusion": (
            "✅ **总结建议**：基于以上分析，给出 1-3 条简洁、可执行的业务建议，"
            "或明确指出需要重点关注的指标和时间节点，帮助决策者快速判断。"
        ),
    }

    requested_descs = [focus_descs[f] for f in analysis_focus if f in focus_descs]
    if not requested_descs:
        requested_descs = list(focus_descs.values())

    focus_text = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(requested_descs))

    # 构造 JSON 格式示例（仅前两项）
    valid_types = [f for f in analysis_focus if f in focus_descs] or list(focus_descs.keys())
    example_items = [
        {"type": t, "title": SECTION_META[t]["label"], "content": "（此处填写100-250字分析内容）"}
        for t in valid_types[:2]
    ]
    types_example = json.dumps(example_items, ensure_ascii=False, indent=2)

    prompt = f"""你是一名资深数据分析师，专注于 SaaS 外呼平台业务数据解读（接通率、通话时长、计费、多区域环境对比等）。

## 报告名称
《{report_title}》

## 图表数据（来自数据库的实时查询结果）
{data_summary}

## 分析任务
请针对以上数据，完成以下维度的分析：

{focus_text}

## 输出格式要求（严格遵守）

1. 直接输出 JSON 数组，**不要**添加 markdown 代码块（```json）、前言或后记
2. 每个分析维度输出一个 JSON 对象，格式如下：
{types_example}
3. `type` 取值范围：{", ".join(valid_types)}
4. `title`：简洁中文标题（2-8字），可自定义，比"趋势分析"更具体更好
5. `content`：100-250 字中文分析
   - 必须引用具体数字（如某日数值、变化幅度）
   - 逻辑清晰，分析有依据
   - 不要泛泛而谈，不要重复数据表格
6. 请求的每个 type **都必须输出**，不可遗漏；如某类无明显发现，请明确说明（如"数据走势平稳，未见明显异常"）

**仅输出 JSON 数组，不添加任何其他内容。**"""

    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# LLM 调用（含 fallback）
# ─────────────────────────────────────────────────────────────────────────────

async def _call_adapter(adapter: Any, prompt: str, max_tokens: int) -> str:
    """
    统一调用 LLM adapter，按接口优先级尝试：
    1. chat_plain(messages, system_prompt, **kwargs)  — ClaudeAdapter 原生接口，最优先
    2. chat_with_messages(messages, **kwargs)          — 部分 adapter 接口
    3. chat(UnifiedConversation, **kwargs)             — UnifiedConversation 接口兜底
    """
    # ── 优先：ClaudeAdapter.chat_plain ──────────────────────────────────────
    if hasattr(adapter, "chat_plain"):
        resp = await adapter.chat_plain(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="",
            max_tokens=max_tokens,
        )
        # chat_plain 返回 {stop_reason, content: [{type, text}, ...]}
        for block in (resp.get("content") or []):
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text") or ""
                if text:
                    return text
        return ""

    # ── 次优：chat_with_messages ─────────────────────────────────────────────
    if hasattr(adapter, "chat_with_messages"):
        resp = await adapter.chat_with_messages(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return resp.get("content", "") or ""

    # ── 兜底：UnifiedConversation 接口（ClaudeAdapter.chat）───────────────────
    if hasattr(adapter, "chat"):
        from backend.core.conversation_format import (
            UnifiedConversation,
            UnifiedMessage,
            MessageRole,
        )
        conv = UnifiedConversation(messages=[
            UnifiedMessage(role=MessageRole.USER, content=prompt)
        ])
        result = await adapter.chat(conv, max_tokens=max_tokens)
        return result.content or ""

    return ""


async def _call_llm_with_fallback(prompt: str, max_tokens: int) -> str:
    """
    调用 LLM，失败时按优先级自动 fallback 到下一个 provider。

    Returns:
        LLM 响应文本；所有 provider 均失败时返回空字符串。
    """
    from backend.core.model_adapters.factory import ModelAdapterFactory

    attempted: set = set()

    primary_adapter, primary_provider = await _get_adapter()
    if primary_provider:
        attempted.add(primary_provider)

    # 尝试主 adapter
    if primary_adapter is not None:
        try:
            text = await _call_adapter(primary_adapter, prompt, max_tokens)
            if text.strip():
                return text
            logger.warning("[ReportAnalysis] 主 LLM (%s) 返回空内容", primary_provider)
        except Exception as exc:
            logger.warning(
                "[ReportAnalysis] 主 LLM (%s) 调用失败: %s，尝试 fallback",
                primary_provider, exc,
            )

    # Fallback：遍历剩余 provider
    for provider in _PROVIDER_ORDER:
        if provider in attempted:
            continue
        try:
            adapter = ModelAdapterFactory.create_from_settings(provider)
            if adapter is None:
                continue
            attempted.add(provider)
            logger.info("[ReportAnalysis] fallback to provider: %s", provider)
            text = await _call_adapter(adapter, prompt, max_tokens)
            if text.strip():
                return text
        except Exception as exc:
            logger.debug("[ReportAnalysis] fallback provider=%s 失败: %s", provider, exc)

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 结果解析
# ─────────────────────────────────────────────────────────────────────────────

def _parse_sections(raw_text: str) -> List[Dict[str, str]]:
    """
    将 LLM 返回的文本解析为结构化 sections 列表。

    策略（按优先级）：
    1. 直接 json.loads
    2. 正则提取 [...] 块再解析
    3. fallback：整段文字包裹为单个 insight 节
    """
    if not raw_text or not raw_text.strip():
        return []

    text = raw_text.strip()

    # 策略 1：整体直接解析
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return [s for s in obj if isinstance(s, dict) and "content" in s]
    except json.JSONDecodeError:
        pass

    # 策略 2：提取最外层 [...] 块
    match = re.search(r'\[[\s\S]*\]', text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, list):
                return [s for s in obj if isinstance(s, dict) and "content" in s]
        except json.JSONDecodeError:
            pass

    # 策略 3：fallback — 整段文字作为单个 insight
    logger.warning("[ReportAnalysis] LLM 返回非标准 JSON，已 fallback 为纯文本 insight")
    return [{"type": "insight", "title": "数据分析", "content": text[:800]}]


# ─────────────────────────────────────────────────────────────────────────────
# 公开入口
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_report_data(
    charts_data: Dict[str, Any],
    report_title: str,
    analysis_focus: Optional[List[str]] = None,
    chart_specs: Optional[List[Dict]] = None,
    max_tokens: int = 1500,
) -> List[Dict[str, str]]:
    """
    报告数据 AI 分析入口。

    Args:
        charts_data:    图表查询结果，格式 {chart_id: [行数据...]}
        report_title:   报告标题（注入 prompt 作为上下文，帮助 LLM 理解业务场景）
        analysis_focus: 分析维度列表，可选：
                        "trend"（趋势）/ "anomaly"（异常）/
                        "insight"（洞察）/ "conclusion"（总结建议）
                        默认为全部四项
        chart_specs:    图表配置列表（含 title/chart_type/x_field/y_fields），
                        用于在 prompt 中生成更准确的数据上下文
        max_tokens:     LLM 最大输出 token 数（建议 1200-2000）

    Returns:
        [{type, title, content}] 分析结果列表；
        无可用 LLM 或分析失败时静默返回 []（不抛异常，保证主流程不中断）
    """
    if not charts_data:
        logger.info("[ReportAnalysis] 图表数据为空，跳过分析")
        return []

    focus = analysis_focus or ["trend", "anomaly", "insight", "conclusion"]
    specs = chart_specs or []

    try:
        prompt = _build_prompt(charts_data, report_title, focus, specs)
        raw_text = await _call_llm_with_fallback(prompt, max_tokens)
    except Exception as exc:
        logger.error("[ReportAnalysis] 构建 prompt 或调用 LLM 异常: %s", exc)
        return []

    if not raw_text:
        logger.warning("[ReportAnalysis] 所有 LLM provider 均无响应，返回空结果")
        return []

    return _parse_sections(raw_text)


from typing import AsyncIterator as _AsyncIterator


async def stream_analyze_report_data(
    charts_data: Dict[str, Any],
    report_title: str,
    analysis_focus: Optional[List[str]] = None,
    chart_specs: Optional[List[Dict]] = None,
    max_tokens: int = 1500,
) -> _AsyncIterator[Dict[str, Any]]:
    """
    流式 AI 分析入口：将 LLM 响应以 SSE 事件形式逐块 yield。

    Yields:
        {"type": "chunk",  "text": str}           — LLM 文本片段（流式）
        {"type": "done",   "sections": list}       — 分析完成，携带解析后的 sections
        {"type": "error",  "message": str}         — 发生错误
        {"type": "empty"}                          — 无可用数据或 LLM 不可用
    """
    if not charts_data:
        logger.info("[ReportAnalysis/stream] 图表数据为空，跳过分析")
        yield {"type": "empty"}
        return

    focus = analysis_focus or ["trend", "anomaly", "insight", "conclusion"]
    specs = chart_specs or []

    try:
        prompt = _build_prompt(charts_data, report_title, focus, specs)
    except Exception as exc:
        logger.error("[ReportAnalysis/stream] 构建 prompt 异常: %s", exc)
        yield {"type": "error", "message": "Prompt 构建失败"}
        return

    # 获取主适配器
    adapter, provider = await _get_adapter()
    if adapter is None:
        logger.warning("[ReportAnalysis/stream] 无可用 LLM provider")
        yield {"type": "empty"}
        return

    logger.debug("[ReportAnalysis/stream] 使用 provider: %s", provider)

    # 流式或一次性获取文本
    accumulated = ""
    try:
        if hasattr(adapter, "stream_plain_text"):
            async for chunk in adapter.stream_plain_text(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="",
                max_tokens=max_tokens,
            ):
                accumulated += chunk
                yield {"type": "chunk", "text": chunk}
        else:
            # 非流式兜底：获取全文后按词逐步 yield
            import asyncio as _asyncio
            raw = await _call_adapter(adapter, prompt, max_tokens)
            accumulated = raw
            for word in raw.split(" "):
                if word:
                    yield {"type": "chunk", "text": word + " "}
                    await _asyncio.sleep(0.025)
    except Exception as exc:
        logger.warning("[ReportAnalysis/stream] LLM 调用异常: %s", exc)
        # 若已有部分文本仍可解析，继续；否则 fallback 到下一个 provider
        if not accumulated:
            # 尝试 fallback
            for prov in _PROVIDER_ORDER:
                if prov == provider:
                    continue
                try:
                    from backend.core.model_adapters.factory import ModelAdapterFactory
                    fb_adapter = ModelAdapterFactory.create_from_settings(prov)
                    if fb_adapter is None:
                        continue
                    raw = await _call_adapter(fb_adapter, prompt, max_tokens)
                    accumulated = raw
                    if raw:
                        import asyncio as _asyncio2
                        for word in raw.split(" "):
                            if word:
                                yield {"type": "chunk", "text": word + " "}
                                await _asyncio2.sleep(0.025)
                        break
                except Exception:
                    continue

    if not accumulated:
        yield {"type": "empty"}
        return

    sections = _parse_sections(accumulated)
    yield {"type": "done", "sections": sections}
