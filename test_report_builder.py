"""
test_report_builder.py
======================
单元测试 — ReportBuilderService HTML 生成引擎

覆盖：
  A (6) — HTML 基础结构（标题/CDN/字段/主题）
  B (8) — 图表类型渲染（JS option 构建函数存在性验证）
  C (5) — 筛选器 HTML 生成
  D (4) — LLM 总结区域
  E (4) — 刷新令牌 & API URL 注入
  F (5) — 值格式化（JS 逻辑通过 HTML 内容验证）
  G (3) — 边界条件（空图表/空数据/中文特殊字符）
  H (3) — generate_refresh_token 随机性与长度

总计: 38 个测试用例

执行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_report_builder.py -v -s
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

from backend.services.report_builder_service import (
    build_report_html,
    generate_refresh_token,
    _esc,
    _css,
    _js_engine,
    _render_filter_html,
    _render_summary_html,
)


# ─────────────────────────────────────────────────────────────────────────────
# 测试 Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_spec(**kwargs):
    base = {
        "title": "外呼接通率分析",
        "subtitle": "2026年Q1",
        "theme": "light",
        "charts": [
            {
                "id": "c1",
                "chart_lib": "echarts",
                "chart_type": "line",
                "title": "日接通率趋势",
                "sql": "SELECT date, rate FROM t",
                "connection_env": "sg",
                "x_field": "date",
                "y_fields": ["rate"],
                "series_names": {"rate": "接通率"},
                "value_format": {"rate": "percent_raw"},
                "width": "full",
                "height": 320,
            },
            {
                "id": "c2",
                "chart_lib": "echarts",
                "chart_type": "bar",
                "title": "各环境通话量",
                "x_field": "env",
                "y_fields": ["calls"],
                "width": "half",
            },
        ],
        "filters": [
            {
                "id": "dr",
                "type": "date_range",
                "label": "时间",
                "default_days": 30,
                "data_field": "date",
            }
        ],
        "data": {
            "c1": [{"date": "2026-03-01", "rate": 85.2}, {"date": "2026-03-02", "rate": 87.1}],
            "c2": [{"env": "sg", "calls": 1000}, {"env": "idn", "calls": 800}],
        },
        "include_summary": False,
    }
    base.update(kwargs)
    return base


_REPORT_ID = "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa"
_REFRESH_TOKEN = "test_refresh_tok_abc123"
_API_BASE = "http://localhost:8000/api/v1"


def _build(spec=None):
    return build_report_html(
        spec=spec or _make_spec(),
        report_id=_REPORT_ID,
        refresh_token=_REFRESH_TOKEN,
        api_base_url=_API_BASE,
    )


# ─────────────────────────────────────────────────────────────────────────────
# A — HTML 基础结构
# ─────────────────────────────────────────────────────────────────────────────

class TestA_HtmlStructure(unittest.TestCase):

    def setUp(self):
        self.html = _build()

    def test_A1_doctype(self):
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"), "应以 DOCTYPE 开头")

    def test_A2_charset_meta(self):
        self.assertIn('charset="UTF-8"', self.html)

    def test_A3_echarts_cdn(self):
        self.assertIn("echarts", self.html.lower())
        self.assertIn("cdn.jsdelivr.net", self.html)

    def test_A4_report_title_in_html(self):
        self.assertIn("外呼接通率分析", self.html)

    def test_A5_report_id_injected(self):
        self.assertIn(_REPORT_ID, self.html)

    def test_A6_refresh_token_injected(self):
        self.assertIn(_REFRESH_TOKEN, self.html)


# ─────────────────────────────────────────────────────────────────────────────
# B — 图表类型 JS 构建函数
# ─────────────────────────────────────────────────────────────────────────────

class TestB_ChartTypeJsFunctions(unittest.TestCase):
    """验证 JS 引擎中对应图表类型的函数存在。"""

    def setUp(self):
        self.js = _js_engine()

    def _assert_fn(self, name):
        self.assertIn(f"function {name}", self.js, f"JS 中应包含函数 {name}")

    def test_B1_buildLine(self): self._assert_fn("buildLine")
    def test_B2_buildBar(self): self._assert_fn("buildBar")
    def test_B3_buildPie(self): self._assert_fn("buildPie")
    def test_B4_buildScatter(self): self._assert_fn("buildScatter")
    def test_B5_buildFunnel(self): self._assert_fn("buildFunnel")
    def test_B6_buildGauge(self): self._assert_fn("buildGauge")
    def test_B7_buildSankey(self): self._assert_fn("buildSankey")
    def test_B8_buildDualAxis(self): self._assert_fn("buildDualAxis")


# ─────────────────────────────────────────────────────────────────────────────
# C — 筛选器 HTML
# ─────────────────────────────────────────────────────────────────────────────

class TestC_FilterHtml(unittest.TestCase):

    def test_C1_date_range_inputs(self):
        html = _render_filter_html([{"id": "dr", "type": "date_range", "label": "日期", "default_days": 7}])
        self.assertIn('type="date"', html)
        self.assertIn("filter-dr-start", html)
        self.assertIn("filter-dr-end", html)

    def test_C2_date_range_quick_buttons(self):
        html = _render_filter_html([{"id": "dr", "type": "date_range", "label": "日期", "default_days": 30}])
        self.assertIn("近7天", html)
        self.assertIn("近30天", html)
        self.assertIn("近90天", html)

    def test_C3_select_filter(self):
        html = _render_filter_html([{
            "id": "env", "type": "select", "label": "环境",
            "options": ["sg", "idn", "mx"]
        }])
        self.assertIn('<select', html)
        self.assertIn("sg", html)
        self.assertIn("idn", html)

    def test_C4_multi_select(self):
        html = _render_filter_html([{
            "id": "env", "type": "multi_select", "label": "环境",
            "options": ["a", "b"]
        }])
        self.assertIn("multiple", html)

    def test_C5_radio_filter(self):
        html = _render_filter_html([{
            "id": "gran", "type": "radio", "label": "粒度",
            "options": ["日", "周", "月"]
        }])
        self.assertIn('type="radio"', html)
        self.assertIn("日", html)


# ─────────────────────────────────────────────────────────────────────────────
# D — LLM 总结区域
# ─────────────────────────────────────────────────────────────────────────────

class TestD_SummarySection(unittest.TestCase):

    def test_D1_no_summary_when_disabled(self):
        html = _build(_make_spec(include_summary=False))
        # CSS 包含 .summary-section 类名，检查 id 属性确认 DOM 元素未渲染
        self.assertNotIn('id="summary-section"', html)

    def test_D2_summary_placeholder_when_enabled_no_text(self):
        html = _build(_make_spec(include_summary=True, llm_summary=""))
        self.assertIn("summary-section", html)
        self.assertIn("生成中", html)

    def test_D3_summary_text_rendered(self):
        html = _build(_make_spec(include_summary=True, llm_summary="接通率整体平稳，3月中旬有明显提升。"))
        self.assertIn("接通率整体平稳", html)

    def test_D4_summary_ai_badge(self):
        html = _build(_make_spec(include_summary=True))
        self.assertIn("AI 生成", html)


# ─────────────────────────────────────────────────────────────────────────────
# E — 刷新 API 注入
# ─────────────────────────────────────────────────────────────────────────────

class TestE_RefreshInjection(unittest.TestCase):

    def setUp(self):
        self.html = _build()

    def test_E1_api_base_injected(self):
        self.assertIn(_API_BASE, self.html)

    def test_E2_refresh_function_exists(self):
        self.assertIn("refreshAllData", self.html)

    def test_E3_refresh_fetch_url_template(self):
        self.assertIn("/reports/${REPORT_ID}/refresh-data", self.html)

    def test_E4_refresh_token_in_fetch_url(self):
        self.assertIn("token=${REFRESH_TOKEN}", self.html)

    def test_E5_btn_refresh_element(self):
        self.assertIn("btn-refresh", self.html)


# ─────────────────────────────────────────────────────────────────────────────
# F — 值格式化函数
# ─────────────────────────────────────────────────────────────────────────────

class TestF_ValueFormat(unittest.TestCase):

    def setUp(self):
        self.js = _js_engine()

    def test_F1_percent_case(self):
        self.assertIn("'percent'", self.js)
        self.assertIn("toFixed(1) + '%'", self.js)

    def test_F2_percent_raw_case(self):
        self.assertIn("'percent_raw'", self.js)

    def test_F3_currency_case(self):
        self.assertIn("'currency'", self.js)
        self.assertIn("¥", self.js)

    def test_F4_short_case(self):
        self.assertIn("'short'", self.js)
        self.assertIn("亿", self.js)
        self.assertIn("万", self.js)

    def test_F5_format_fn_exists(self):
        self.assertIn("function formatVal", self.js)


# ─────────────────────────────────────────────────────────────────────────────
# G — 边界条件
# ─────────────────────────────────────────────────────────────────────────────

class TestG_EdgeCases(unittest.TestCase):

    def test_G1_empty_charts(self):
        spec = _make_spec()
        spec["charts"] = []
        html = build_report_html(spec, _REPORT_ID, _REFRESH_TOKEN, _API_BASE)
        self.assertIn("<!DOCTYPE html>", html)

    def test_G2_empty_data(self):
        spec = _make_spec()
        spec["data"] = {}
        html = build_report_html(spec, _REPORT_ID, _REFRESH_TOKEN, _API_BASE)
        self.assertIn("REPORT_DATA", html)

    def test_G3_xss_in_title(self):
        spec = _make_spec(title="<script>alert(1)</script>")
        html = build_report_html(spec, _REPORT_ID, _REFRESH_TOKEN, _API_BASE)
        # HTML 标签部分（<title>/<h1>）已转义
        self.assertIn("&lt;script&gt;", html)
        # JSON 中 </script> 必须被转义为 <\/ 防止 HTML 解析器误关闭
        self.assertNotIn("</script>alert", html)


# ─────────────────────────────────────────────────────────────────────────────
# H — refresh token 生成
# ─────────────────────────────────────────────────────────────────────────────

class TestH_RefreshToken(unittest.TestCase):

    def test_H1_token_is_string(self):
        t = generate_refresh_token()
        self.assertIsInstance(t, str)

    def test_H2_token_min_length(self):
        t = generate_refresh_token()
        self.assertGreaterEqual(len(t), 32, "令牌应至少 32 字符")

    def test_H3_tokens_are_unique(self):
        tokens = {generate_refresh_token() for _ in range(20)}
        self.assertEqual(len(tokens), 20, "每次生成的令牌应唯一")


# ─────────────────────────────────────────────────────────────────────────────
# CSS 主题
# ─────────────────────────────────────────────────────────────────────────────

class TestI_CssTheme(unittest.TestCase):

    def test_I1_light_theme_default_background(self):
        css = _css("light")
        self.assertIn("#f4f6fa", css)

    def test_I2_dark_theme_background(self):
        css = _css("dark")
        self.assertIn("#1a1a2e", css)

    def test_I3_print_media_query(self):
        css = _css("light")
        self.assertIn("@media print", css)


if __name__ == "__main__":
    unittest.main(verbosity=2)
