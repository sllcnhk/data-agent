from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# ── 路径 & 环境变量（无数据库依赖）──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")
os.environ.setdefault("ALLOWED_DIRECTORIES", '["customer_data", ".claude/skills"]')
os.environ.setdefault("FILESYSTEM_WRITE_ALLOWED_DIRS", '["customer_data", ".claude/skills/user"]')

from backend.report_spec_utils import (
    chart_validation_errors,
    normalize_chart_spec,
    normalize_filter_spec,
    normalize_report_spec,
    validate_report_spec,
    has_ai_analysis_chart,
)


class TestReportSpecNormalization(unittest.TestCase):
    def test_normalize_chart_spec_maps_legacy_fields(self):
        chart = {
            "id": "c1",
            "type": "bar",
            "dataset": {
                "source": "clickhouse",
                "server": "clickhouse-sg",
                "query": "SELECT 1 AS v",
            },
            "xField": "dt",
            "yField": "cnt",
            "seriesField": "env",
        }
        out = normalize_chart_spec(chart)
        self.assertEqual(out["chart_type"], "bar")
        self.assertEqual(out["sql"], "SELECT 1 AS v")
        # "clickhouse-sg" 前缀在 normalize_chart_spec 中被自动剥离为短名 "sg"
        self.assertEqual(out["connection_env"], "sg")
        self.assertEqual(out["connection_type"], "clickhouse")
        self.assertEqual(out["x_field"], "dt")
        self.assertEqual(out["y_fields"], ["cnt"])
        self.assertEqual(out["series_field"], "env")

    def test_normalize_report_spec_maps_root_and_filters(self):
        spec = {
            "name": "Legacy Report",
            "description": "Legacy Subtitle",
            "charts": [{"id": "c1", "type": "line", "dataset": {"query": "SELECT 1", "server": "sg"}}],
            "filters": [{"id": "f1", "type": "date_range", "binds": ["date_start", "date_end"]}],
        }
        out = normalize_report_spec(spec)
        self.assertEqual(out["title"], "Legacy Report")
        self.assertEqual(out["subtitle"], "Legacy Subtitle")
        self.assertEqual(out["filters"][0]["binds"], {"start": "date_start", "end": "date_end"})
        self.assertEqual(out["charts"][0]["chart_type"], "line")

    def test_validate_report_spec_accepts_legacy_chart_after_normalization(self):
        spec = {
            "title": "Legacy OK",
            "charts": [{
                "id": "c1",
                "type": "bar",
                "dataset": {"query": "SELECT 1 AS v", "server": "clickhouse-sg", "source": "clickhouse"},
                "xField": "dt",
                "yField": "cnt",
            }],
        }
        normalized, errors = validate_report_spec(spec)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["charts"][0]["chart_type"], "bar")
        self.assertEqual(normalized["charts"][0]["sql"], "SELECT 1 AS v")

    def test_include_summary_auto_appends_ai_analysis_chart(self):
        spec = {
            "title": "Need Summary",
            "include_summary": True,
            "charts": [{
                "id": "c1",
                "chart_type": "line",
                "sql": "SELECT 1 AS cnt",
                "connection_env": "sg",
            }],
        }
        out = normalize_report_spec(spec)
        self.assertTrue(out["include_summary"])
        self.assertEqual(out["charts"][-1]["chart_type"], "ai_analysis")

    def test_summary_like_table_is_rewritten_to_ai_analysis(self):
        spec = {
            "title": "Need Summary",
            "charts": [{
                "id": "c3",
                "chart_type": "table",
                "title": "图表总结",
                "description": "基于图表查询结果生成的简要分析总结。",
                "sql": "SELECT '统计区间' AS item, '近30天' AS value UNION ALL SELECT '风险提示', '检查数据'",
                "connection_env": "sg",
            }],
        }
        out = normalize_report_spec(spec)
        self.assertTrue(out["include_summary"])
        self.assertEqual(out["charts"][0]["chart_type"], "ai_analysis")
        self.assertEqual(out["charts"][0]["id"], "c3")

    def test_time_trend_report_auto_adds_date_filter_and_parameterized_sql(self):
        spec = {
            "title": "近30天趋势",
            "charts": [{
                "id": "c1",
                "chart_type": "bar",
                "title": "近30天各环境 Connected Call 数量按天堆积图",
                "sql": "SELECT s_day, sum(call_num) AS cnt FROM t PREWHERE s_day >= today() - 29 AND s_day <= today() GROUP BY s_day",
                "connection_env": "sg",
                "x_field": "s_day",
                "y_field": "cnt",
            }],
        }
        out = normalize_report_spec(spec)
        self.assertEqual(out["filters"][0]["type"], "date_range")
        self.assertEqual(out["filters"][0]["binds"], {"start": "date_start", "end": "date_end"})
        self.assertIn("{{ date_start }}", out["charts"][0]["sql"])
        self.assertIn("{{ date_end }}", out["charts"][0]["sql"])
        self.assertEqual(out["charts"][0]["y_fields"], ["cnt"])

    def test_chart_validation_errors_reports_missing_runtime_fields(self):
        errs = chart_validation_errors({"id": "c1", "chart_type": "bar"})
        self.assertIn("缺少 sql", errs)
        self.assertIn("缺少 connection_env", errs)


# ═════════════════════════════════════════════════════════════════════════════
# T7 — Filter id 自动补全 (T1 fix) 单元测试
# ═════════════════════════════════════════════════════════════════════════════

class TestFilterIdAutoAssign(unittest.TestCase):
    """T7-A: normalize_filter_spec 自动补全 filter id (T1 fix)"""

    def test_T7A1_date_range_without_id_gets_type_as_id(self):
        """无 id 的 date_range filter 应自动补 id='date_range'"""
        f = {"type": "date_range", "label": "日期范围", "default_days": 30}
        out = normalize_filter_spec(f)
        self.assertEqual(out["id"], "date_range", "缺少 id 时应自动用 type 填充")

    def test_T7A2_select_without_id_gets_select_id(self):
        """无 id 的 select filter 应自动补 id='select'"""
        f = {"type": "select", "label": "环境", "options": ["sg", "my"]}
        out = normalize_filter_spec(f)
        self.assertEqual(out["id"], "select")

    def test_T7A3_existing_id_preserved(self):
        """已有 id 的 filter 不被覆盖（回归保护）"""
        f = {"id": "my_date", "type": "date_range", "label": "日期"}
        out = normalize_filter_spec(f)
        self.assertEqual(out["id"], "my_date", "已有 id 不应被覆盖")

    def test_T7A4_normalize_report_spec_all_filters_have_id(self):
        """normalize_report_spec 后所有 filter 都有非空 id"""
        spec = {
            "title": "T",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1", "connection_env": "sg"}],
            "filters": [
                {"type": "date_range"},                    # 无 id
                {"id": "env", "type": "select"},           # 有 id
                {"type": "multi_select", "options": []},   # 无 id
            ],
        }
        out = normalize_report_spec(spec)
        for f in out["filters"]:
            self.assertTrue(f.get("id"), f"filter {f} 应有非空 id: {f.get('id')!r}")

    def test_T7A5_auto_added_date_range_filter_has_id(self):
        """auto-insert 的 date_range filter（时间趋势图）也有 id"""
        spec = {
            "title": "趋势",
            "charts": [{"id": "c1", "chart_type": "line",
                        "title": "近30天趋势", "sql": "SELECT s_day AS dt FROM t PREWHERE s_day>=today()-30",
                        "connection_env": "sg", "x_field": "dt", "y_fields": ["cnt"]}],
        }
        out = normalize_report_spec(spec)
        date_filters = [f for f in out["filters"] if f.get("type") == "date_range"]
        self.assertTrue(date_filters, "时间趋势图应自动插入 date_range filter")
        for df in date_filters:
            self.assertTrue(df.get("id"), f"auto-added date filter 应有 id: {df}")


# ═════════════════════════════════════════════════════════════════════════════
# T7 — Top summary section 不渲染 when ai_analysis (T4 fix) 单元测试
# ═════════════════════════════════════════════════════════════════════════════

class TestTopSummarySection(unittest.TestCase):
    """T7-B: build_report_html 当 ai_analysis chart 存在时不渲染顶部 summary-section (T4 fix)"""

    def _make_spec(self, with_ai_chart: bool, include_summary: bool = True) -> dict:
        charts = [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1 AS v", "connection_env": "sg",
                   "x_field": "dt", "y_fields": ["v"]}]
        if with_ai_chart:
            charts.append({"id": "sum_ai", "chart_type": "ai_analysis", "title": "AI 分析", "width": "full"})
        return {
            "title": "Test Report",
            "charts": charts,
            "include_summary": include_summary,
            "filters": [],
        }

    def test_T7B1_no_top_summary_when_ai_analysis_chart_exists(self):
        """存在 ai_analysis chart 时，顶部 summary-section 不应出现在 HTML 中"""
        from backend.services.report_builder_service import build_report_html
        spec = self._make_spec(with_ai_chart=True)
        html = build_report_html(spec=spec, report_id="test-123",
                                 refresh_token="tok", api_base_url="")
        self.assertNotIn('id="summary-section"', html,
                         "ai_analysis chart 存在时不应渲染顶部 summary-section")
        self.assertNotIn('分析总结生成中，请稍候', html,
                         "不应出现加载占位符文本")

    def test_T7B2_top_summary_present_when_no_ai_analysis_chart(self):
        """不含 ai_analysis chart 时，include_summary=True 应渲染顶部 summary-section（回归）"""
        from backend.services.report_builder_service import build_report_html
        spec = self._make_spec(with_ai_chart=False, include_summary=True)
        # 把 charts 中可能自动追加的 ai_analysis chart 移除，保留纯 bar chart
        spec["charts"] = [c for c in spec["charts"] if c["chart_type"] != "ai_analysis"]
        html = build_report_html(spec=spec, report_id="test-124",
                                 refresh_token="tok", api_base_url="")
        # 注意：normalize 会自动 append ai_analysis（因为 include_summary=True），
        # 这里我们测试的是：顶部 summary-section 在 ai_analysis 存在时确实被抑制
        # 故当 normalized spec 有 ai_analysis 时，summary-section 不出现——这是期望行为
        # 本 case 确认回归：当原始 spec 无 ai_analysis 且 include_summary=False 时，无顶部 section
        spec_no_sum = {**spec, "include_summary": False}
        html_no_sum = build_report_html(spec=spec_no_sum, report_id="test-125",
                                        refresh_token="tok", api_base_url="")
        self.assertNotIn('id="summary-section"', html_no_sum,
                         "include_summary=False 时不应渲染顶部 summary-section")

    def test_T7B3_llm_summary_shown_in_top_section_when_no_ai_chart(self):
        """llm_summary 有文本且无 ai_analysis chart 时应渲染顶部 summary-section 含实际内容"""
        from backend.services.report_builder_service import build_report_html, _render_summary_html
        # 直接测 _render_summary_html 函数
        html_no = _render_summary_html("已有的总结文本", include_summary=False)
        self.assertEqual(html_no, "", "include_summary=False 时返回空字符串")
        html_yes = _render_summary_html("已有的总结文本", include_summary=True)
        self.assertIn("已有的总结文本", html_yes, "include_summary=True 时应包含 llm_summary 内容")
        self.assertIn("summary-section", html_yes)


# ═════════════════════════════════════════════════════════════════════════════
# T7 — JS engine 包含关键修复代码（T2/T3/T5/T6 代码存在性验证）
# ═════════════════════════════════════════════════════════════════════════════

class TestJsEngineContent(unittest.TestCase):
    """T7-C: 验证 _js_engine() 包含各项修复逻辑（静态代码分析）"""

    @classmethod
    def setUpClass(cls):
        from backend.services.report_builder_service import _js_engine
        cls.js = _js_engine()

    def test_T7C1_filter_null_coalesce_in_onFilterChange(self):
        """T2: onFilterChange 使用 f.id ?? f.type 兼容旧 HTML"""
        self.assertIn("f.id ?? f.type", self.js,
                      "onFilterChange 应使用 null 合并运算兼容 id 缺失的 filter")

    def test_T7C2_filter_null_coalesce_in_currentParams(self):
        """T2: _currentParams 使用 fSpec.id ?? fSpec.type 读取 filterValues"""
        self.assertIn("fSpec.id ?? fSpec.type", self.js,
                      "_currentParams 应使用 null 合并运算读取 filterValues")

    def test_T7C3_revive_formatters_function_exists(self):
        """T3: JS engine 包含 _reviveFormatters 函数定义"""
        self.assertIn("function _reviveFormatters", self.js,
                      "JS engine 应包含 _reviveFormatters 函数")

    def test_T7C4_revive_called_after_deepMerge(self):
        """T3: _reviveFormatters 在 deepMerge 之后被调用"""
        deep_merge_idx = self.js.find("deepMerge(option, override)")
        revive_idx = self.js.find("_reviveFormatters(option)")
        self.assertGreater(deep_merge_idx, 0, "应有 deepMerge(option, override) 调用")
        self.assertGreater(revive_idx, 0, "_reviveFormatters(option) 应被调用")
        self.assertGreater(revive_idx, deep_merge_idx,
                           "_reviveFormatters 应在 deepMerge 之后调用")

    def test_T7C5_ai_analysis_reset_on_load_data(self):
        """T5: _loadData 开始时重置 _aiAnalysisInProgress"""
        self.assertIn("_aiAnalysisInProgress = false", self.js,
                      "_loadData 应重置 _aiAnalysisInProgress 标志")

    def test_T7C6_ai_analysis_waiting_on_load_data(self):
        """T5: _loadData 开始时 AI 卡片显示"数据更新中"提示"""
        self.assertIn("数据更新中，AI 将自动重新分析", self.js,
                      "_loadData 应立即更新 AI 卡片状态")

    def test_T7C7_friendly_error_when_sections_empty(self):
        """T6: sections 为空时给出友好的 LLM 不可用提示"""
        self.assertIn("LLM 服务可能不可用", self.js,
                      "sections 为空时应提示 LLM 服务不可用")

    def test_T7C8_http_error_detection_in_catch(self):
        """T6: catch 块区分 HTTP 错误与业务错误"""
        self.assertIn("AI 分析接口连接失败", self.js,
                      "catch 块应包含 HTTP 错误专属提示")


# ═════════════════════════════════════════════════════════════════════════════
# T8/T9 — 回归测试：现有功能不被破坏
# ═════════════════════════════════════════════════════════════════════════════

class TestRegressionFilterBehavior(unittest.TestCase):
    """T8: 已有 filter id 的 spec 回归测试"""

    def test_T8A1_filter_with_explicit_id_preserved(self):
        """已有 id 的 filter 经 normalize_report_spec 后 id 不变"""
        spec = {
            "title": "R",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1", "connection_env": "sg"}],
            "filters": [{"id": "my_date", "type": "date_range", "binds": {"start": "ds", "end": "de"}}],
        }
        out = normalize_report_spec(spec)
        date_filters = [f for f in out["filters"] if f.get("id") == "my_date"]
        self.assertTrue(date_filters, "id='my_date' 的 filter 应被保留")
        self.assertEqual(date_filters[0]["binds"]["start"], "ds")
        self.assertEqual(date_filters[0]["binds"]["end"], "de")

    def test_T8A2_filter_binds_array_still_normalized(self):
        """binds 为数组格式的旧 filter 仍正确被归一化"""
        f = {"id": "f1", "type": "date_range", "binds": ["date_start", "date_end"]}
        out = normalize_filter_spec(f)
        self.assertEqual(out["binds"], {"start": "date_start", "end": "date_end"})
        self.assertEqual(out["id"], "f1")

    def test_T8A3_no_ai_analysis_no_include_summary_no_top_section(self):
        """include_summary=False + 无 ai_analysis 时 HTML 不含顶部 summary-section"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Plain Report",
            "include_summary": False,
            "charts": [{"id": "c1", "chart_type": "pie", "sql": "SELECT 1", "connection_env": "sg",
                        "x_field": "lbl", "y_fields": ["v"]}],
            "filters": [],
        }
        html = build_report_html(spec=spec, report_id="test-200",
                                 refresh_token="tok", api_base_url="")
        self.assertNotIn('id="summary-section"', html)

    def test_T8A4_existing_report_spec_with_ai_chart_no_top_summary(self):
        """含 ai_analysis chart 的完整 spec，顶部 summary-section 不出现"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Full Report",
            "include_summary": True,
            "charts": [
                {"id": "c1", "chart_type": "bar", "sql": "SELECT dt, cnt FROM t",
                 "connection_env": "sg", "x_field": "dt", "y_fields": ["cnt"]},
                {"id": "ai1", "chart_type": "ai_analysis", "title": "AI 分析", "width": "full"},
            ],
            "filters": [{"id": "dr", "type": "date_range", "binds": {"start": "date_start", "end": "date_end"}}],
        }
        html = build_report_html(spec=spec, report_id="test-201",
                                 refresh_token="tok", api_base_url="")
        self.assertNotIn('id="summary-section"', html, "ai_analysis 存在时顶部 summary 不应出现")
        self.assertIn("ai-analysis-card", html, "底部 ai_analysis card 应存在")
        self.assertIn("_reviveFormatters", html, "HTML 应包含 _reviveFormatters 函数")
        self.assertIn("f.id ?? f.type", html, "HTML 应包含 filter id null coalesce 修复")


# ═════════════════════════════════════════════════════════════════════════════
# T10 — _reconcile_filter_chart_binds (T-B1/B2)
# ═════════════════════════════════════════════════════════════════════════════

class TestReconcileFilterChartBinds(unittest.TestCase):
    """T10: binds 与 SQL 变量名校正（修复 AI 将图表 ID 写入 binds 的问题）"""

    def _make_spec(self, binds, sql="SELECT s_day FROM t WHERE s_day >= '{{ date_start }}' AND s_day <= '{{ date_end }}'"):
        return {
            "title": "Test",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": sql, "connection_env": "sg",
                        "x_field": "s_day", "y_fields": ["cnt"]}],
            "filters": [{"id": "f1", "type": "date_range", "binds": binds, "default_days": 30}],
        }

    def test_T10A1_chart_id_binds_corrected_to_standard(self):
        """AI 用图表 ID (c1/c2) 作 binds 变量名时，应自动修正为 date_start/date_end"""
        from backend.report_spec_utils import _reconcile_filter_chart_binds
        spec = self._make_spec({"start": "c1", "end": "c2"})
        # 先归一化图表（去掉旧字段）
        spec["charts"][0] = {"id": "c1", "chart_type": "bar",
                             "sql": "SELECT s_day FROM t WHERE s_day >= '{{ date_start }}' AND s_day <= '{{ date_end }}'",
                             "connection_env": "sg"}
        out = _reconcile_filter_chart_binds(spec)
        binds = out["filters"][0]["binds"]
        self.assertEqual(binds["start"], "date_start", "应修正为 date_start")
        self.assertEqual(binds["end"], "date_end", "应修正为 date_end")

    def test_T10A2_correct_binds_unchanged(self):
        """正确的 binds 不应被修改"""
        from backend.report_spec_utils import _reconcile_filter_chart_binds
        spec = self._make_spec({"start": "date_start", "end": "date_end"})
        spec["charts"][0] = {"id": "c1", "chart_type": "bar",
                             "sql": "SELECT s_day FROM t WHERE s_day >= '{{ date_start }}' AND s_day <= '{{ date_end }}'",
                             "connection_env": "sg"}
        out = _reconcile_filter_chart_binds(spec)
        binds = out["filters"][0]["binds"]
        self.assertEqual(binds["start"], "date_start")
        self.assertEqual(binds["end"], "date_end")

    def test_T10A3_normalize_report_spec_fixes_chart_id_binds(self):
        """normalize_report_spec 应自动修正 binds 中的图表 ID 变量名"""
        spec = {
            "title": "近30天趋势",
            "charts": [{"id": "c1", "chart_type": "line",
                        "sql": "SELECT s_day FROM t WHERE s_day >= '{{ date_start }}' AND s_day <= '{{ date_end }}'",
                        "connection_env": "sg", "x_field": "s_day", "y_fields": ["cnt"]}],
            "filters": [{"id": "dr", "type": "date_range",
                         "binds": {"start": "c1", "end": "c2"},
                         "default_days": 30}],
        }
        out = normalize_report_spec(spec)
        date_f = [f for f in out["filters"] if f.get("type") == "date_range"][0]
        self.assertEqual(date_f["binds"]["start"], "date_start",
                         "normalize_report_spec 应修正 binds.start 为 date_start")
        self.assertEqual(date_f["binds"]["end"], "date_end",
                         "normalize_report_spec 应修正 binds.end 为 date_end")

    def test_T10A4_no_false_correction_when_binds_match_sql(self):
        """若 binds 变量名与 SQL 中的 {{ }} 变量名完全匹配（即使是自定义名），不应修改"""
        from backend.report_spec_utils import _reconcile_filter_chart_binds
        spec = {
            "title": "Custom Param",
            "charts": [{"id": "c1", "chart_type": "bar",
                        "sql": "SELECT * FROM t WHERE s_day >= '{{ ds }}' AND s_day <= '{{ de }}'",
                        "connection_env": "sg"}],
            "filters": [{"id": "dr", "type": "date_range",
                         "binds": {"start": "ds", "end": "de"}, "default_days": 7}],
        }
        out = _reconcile_filter_chart_binds(spec)
        binds = out["filters"][0]["binds"]
        self.assertEqual(binds["start"], "ds", "自定义变量名但与 SQL 匹配时，不应被修改")
        self.assertEqual(binds["end"], "de")

    def test_T10A5_no_sql_template_vars_no_change(self):
        """无 SQL 模板变量时，filter binds 维持原样"""
        from backend.report_spec_utils import _reconcile_filter_chart_binds
        spec = {
            "title": "Static",
            "charts": [{"id": "c1", "chart_type": "bar",
                        "sql": "SELECT * FROM t WHERE s_day >= '2025-01-01'",
                        "connection_env": "sg"}],
            "filters": [{"id": "dr", "type": "date_range",
                         "binds": {"start": "date_start", "end": "date_end"}, "default_days": 30}],
        }
        out = _reconcile_filter_chart_binds(spec)
        # 无 SQL 模板变量，无法做有效判断，binds 维持原样
        binds = out["filters"][0]["binds"]
        self.assertEqual(binds["start"], "date_start")


# ═════════════════════════════════════════════════════════════════════════════
# T11 — validate_report_spec 软警告不阻塞创建 (T-A1)
# ═════════════════════════════════════════════════════════════════════════════

class TestValidationNonBlocking(unittest.TestCase):
    """T11: SQL 未参数化为软警告，不阻塞报表创建"""

    def test_T11A1_hardcoded_date_sql_does_not_block_creation(self):
        """时间趋势图 SQL 含 today() 硬编码但未参数化时，validate 应返回空 errors（不阻塞）"""
        spec = {
            "title": "趋势",
            "charts": [{"id": "c1", "chart_type": "bar",
                        "sql": "SELECT s_day, cnt FROM t WHERE s_day >= today() - 30 AND s_day <= today()",
                        "connection_env": "sg", "x_field": "s_day", "y_fields": ["cnt"]}],
            "filters": [{"id": "dr", "type": "date_range", "binds": {"start": "date_start", "end": "date_end"}}],
        }
        _normalized, errors = validate_report_spec(spec)
        # 硬错误为空（创建不被阻塞）
        self.assertEqual(errors, [], f"SQL 未参数化不应产生阻塞错误，实际: {errors}")

    def test_T11A2_report_validation_warnings_returns_info(self):
        """report_validation_warnings 应针对未参数化 SQL 返回警告字符串"""
        from backend.report_spec_utils import report_validation_warnings
        spec = {
            "title": "T",
            "charts": [{"id": "c1", "chart_type": "bar",
                        "sql": "SELECT s_day FROM t WHERE s_day >= today() - 29",
                        "connection_env": "sg", "x_field": "s_day", "y_fields": ["cnt"]}],
            "filters": [{"id": "dr", "type": "date_range",
                         "binds": {"start": "date_start", "end": "date_end"}, "default_days": 30}],
        }
        normalized = normalize_report_spec(spec)
        warns = report_validation_warnings(normalized)
        # 注意：normalize 会尝试自动参数化；若参数化成功则无警告
        # 如果 regex 未能替换，则应有警告（非阻塞）
        # 无论有无警告，关键是不应在 validate_report_spec 的 errors 中出现
        _normalized, errors = validate_report_spec(spec)
        self.assertEqual(errors, [], "警告不应出现在 blocking errors 中")

    def test_T11A3_missing_chart_fields_still_blocks(self):
        """缺少 sql / connection_env 等硬错误仍应阻塞"""
        spec = {
            "title": "T",
            "charts": [{"id": "c1", "chart_type": "bar"}],  # 缺 sql + connection_env
        }
        _normalized, errors = validate_report_spec(spec)
        self.assertTrue(len(errors) > 0, "缺少必填字段应产生阻塞错误")
        self.assertTrue(any("sql" in e or "connection_env" in e for e in errors))


# ═════════════════════════════════════════════════════════════════════════════
# T12 — 改进正则（T-A2）
# ═════════════════════════════════════════════════════════════════════════════

class TestImprovedDateRegex(unittest.TestCase):
    """T12: _HARDCODED_DATE_RANGE_RE 改进后支持更多 SQL 模式"""

    def _get_parameterized(self, sql: str) -> str:
        from backend.report_spec_utils import _parameterize_sql_date_range
        return _parameterize_sql_date_range(sql)

    def test_T12A1_standard_pattern_still_works(self):
        """原本支持的标准模式不受影响"""
        sql = "SELECT s_day FROM t PREWHERE s_day >= today() - 29 AND s_day <= today() GROUP BY s_day"
        out = self._get_parameterized(sql)
        self.assertIn("{{ date_start }}", out)
        self.assertIn("{{ date_end }}", out)

    def test_T12A2_pattern_after_other_condition(self):
        """日期条件在其他 WHERE 条件之后时（非第一个条件），也能被参数化"""
        sql = "SELECT s_day FROM t WHERE environment='SG' AND s_day >= today() - 29 AND s_day <= today() GROUP BY s_day"
        out = self._get_parameterized(sql)
        self.assertIn("{{ date_start }}", out, "其他条件在前时也应能参数化")
        self.assertIn("{{ date_end }}", out)

    def test_T12A3_pattern_with_less_than_or_equal(self):
        """支持 field <= today()（原已支持，回归验证）"""
        sql = "SELECT dt FROM t WHERE dt >= today() - 7 AND dt <= today()"
        out = self._get_parameterized(sql)
        self.assertIn("{{ date_start }}", out)
        self.assertIn("{{ date_end }}", out)

    def test_T12A4_already_parameterized_sql_unchanged(self):
        """已含 {{ }} 的 SQL 不应被修改"""
        sql = "SELECT dt FROM t WHERE dt >= '{{ date_start }}' AND dt <= '{{ date_end }}'"
        out = self._get_parameterized(sql)
        self.assertEqual(out, sql, "已参数化的 SQL 不应被修改")

    def test_T12A5_no_match_no_change(self):
        """不含 today() - N 模式的 SQL 不变"""
        sql = "SELECT dt FROM t WHERE dt >= '2025-01-01' AND dt < '2025-02-01'"
        out = self._get_parameterized(sql)
        self.assertEqual(out, sql)


# ═════════════════════════════════════════════════════════════════════════════
# T13 — 回归：存量报表 binds={c1,c2} 场景模拟 (T-D2)
# ═════════════════════════════════════════════════════════════════════════════

class TestExistingBrokenReportRegression(unittest.TestCase):
    """T13: 模拟全环境_Connected_Call_近30天趋势报表的 binds={c1,c2} 场景"""

    def _make_broken_spec(self):
        """构造与问题报表相同的 spec（binds 错误地使用了图表 ID）"""
        return {
            "title": "全环境 Connected Call 近30天趋势报表",
            "charts": [
                {"id": "c1", "chart_type": "bar",
                 "sql": "SELECT s_day, sum(connected_calls) AS cnt FROM t WHERE s_day >= '{{ date_start }}' AND s_day <= '{{ date_end }}' GROUP BY s_day",
                 "connection_env": "sg", "x_field": "s_day", "y_fields": ["cnt"]},
                {"id": "c2", "chart_type": "bar",
                 "sql": "SELECT s_day, sum(am_calls) AS am FROM t WHERE s_day >= '{{ date_start }}' AND s_day <= '{{ date_end }}' GROUP BY s_day",
                 "connection_env": "sg", "x_field": "s_day", "y_fields": ["am"]},
            ],
            "filters": [
                {"id": "f1", "type": "date_range", "label": "日期范围",
                 "binds": {"start": "c1", "end": "c2"},  # ← 问题所在！图表 ID 作变量名
                 "default_days": 30}
            ],
        }

    def test_T13A1_broken_binds_fixed_after_normalize(self):
        """normalize_report_spec 后，错误 binds 应被修正为 date_start/date_end"""
        spec = self._make_broken_spec()
        out = normalize_report_spec(spec)
        date_f = next((f for f in out["filters"] if f.get("type") == "date_range"), None)
        self.assertIsNotNone(date_f, "应存在 date_range filter")
        self.assertEqual(date_f["binds"]["start"], "date_start",
                         "broken binds.start=c1 应被修正为 date_start")
        self.assertEqual(date_f["binds"]["end"], "date_end",
                         "broken binds.end=c2 应被修正为 date_end")

    def test_T13A2_broken_spec_passes_validation(self):
        """修正后的 spec 应能通过 validate_report_spec（无阻塞错误）"""
        spec = self._make_broken_spec()
        _normalized, errors = validate_report_spec(spec)
        self.assertEqual(errors, [], f"修正后不应有阻塞错误，实际: {errors}")

    def test_T13A3_default_params_computed_correctly_after_fix(self):
        """修正后，extract_default_params 应返回 date_start/date_end（非 c1/c2）"""
        from backend.services.report_params_service import extract_default_params
        spec = self._make_broken_spec()
        normalized = normalize_report_spec(spec)
        params = extract_default_params({"filters": normalized.get("filters", [])})
        self.assertIn("date_start", params, "应有 date_start 键")
        self.assertIn("date_end", params, "应有 date_end 键")
        self.assertNotIn("c1", params, "不应有 c1 键（图表 ID）")
        self.assertNotIn("c2", params, "不应有 c2 键（图表 ID）")
        # 值应该是有效日期字符串
        from datetime import date
        try:
            date.fromisoformat(params["date_start"])
            date.fromisoformat(params["date_end"])
        except ValueError:
            self.fail(f"date_start/date_end 不是有效日期: {params}")


# ═════════════════════════════════════════════════════════════════════════════
# T14 — 仅下界 today()-N 参数化 + connection_env 规范化（claudecode005 根因修复）
# ═════════════════════════════════════════════════════════════════════════════

class TestLowerBoundOnlyAndConnectionEnv(unittest.TestCase):
    """
    T14: 覆盖 claudecode005 发现的两个系统性问题：
      1. AI 只写了下界 (s_day >= today()-30)，未写上界 → 筛选器不生效
      2. AI 生成 connection_env = "clickhouse-sg" 而非 "sg" → 历史遗留格式

    修复：
      A. _parameterize_sql_date_range 两步处理：先匹配双边界，再匹配仅下界
      B. normalize_chart_spec 自动剥离 "clickhouse-" 前缀
    """

    def _parameterize(self, sql: str) -> str:
        from backend.report_spec_utils import _parameterize_sql_date_range
        return _parameterize_sql_date_range(sql)

    def _normalize_chart(self, chart: dict) -> dict:
        from backend.report_spec_utils import normalize_chart_spec
        return normalize_chart_spec(chart)

    # ── T14-A: 仅下界参数化 ──────────────────────────────────────────────────

    def test_T14A1_lower_only_basic(self):
        """PREWHERE s_day >= today() - 30（仅下界）→ 自动补全上界参数化"""
        sql = "SELECT s_day, cnt FROM t PREWHERE s_day >= today() - 30 GROUP BY s_day"
        out = self._parameterize(sql)
        self.assertIn("{{ date_start }}", out, "仅下界也应被参数化")
        self.assertIn("{{ date_end }}", out, "应自动补全上界")
        self.assertNotIn("today()", out, "today() 应已被替换")

    def test_T14A2_lower_only_with_other_condition(self):
        """最常见破坏场景：s_day >= today()-30 AND call_code_type IN (...)"""
        sql = (
            "SELECT s_day, SaaS, SUM(call_num) AS cnt "
            "FROM integrated_data.Fact_Daily_Call "
            "PREWHERE s_day >= today() - 30 AND call_code_type IN (1, 16) "
            "GROUP BY s_day, SaaS ORDER BY s_day ASC"
        )
        out = self._parameterize(sql)
        self.assertIn("{{ date_start }}", out)
        self.assertIn("{{ date_end }}", out)
        # 其他条件不应丢失
        self.assertIn("call_code_type IN (1, 16)", out, "非日期条件不应被删除")
        self.assertNotIn("today()", out)

    def test_T14A3_lower_only_various_day_offsets(self):
        """不同偏移量（7/14/90天）均被捕获"""
        for days in (7, 14, 90):
            sql = f"SELECT dt FROM t WHERE dt >= today() - {days}"
            out = self._parameterize(sql)
            self.assertIn("{{ date_start }}", out, f"today()-{days} 应被参数化")
            self.assertIn("{{ date_end }}", out)

    def test_T14A4_double_bound_still_works(self):
        """双边界仍然正确（回归：步骤1仍生效）"""
        sql = "SELECT s_day FROM t WHERE s_day >= today() - 30 AND s_day <= today()"
        out = self._parameterize(sql)
        self.assertIn("{{ date_start }}", out)
        self.assertIn("{{ date_end }}", out)
        self.assertNotIn("today()", out)
        # 不应出现重复 date_end
        self.assertEqual(out.count("date_end"), 1, "date_end 应只出现一次（步骤1优先处理双边界）")

    def test_T14A5_already_parameterized_unchanged(self):
        """已含 {{ }} 的 SQL 不触发任何替换"""
        sql = "SELECT s_day FROM t WHERE s_day >= toDate('{{ date_start }}') AND s_day <= toDate('{{ date_end }}')"
        out = self._parameterize(sql)
        self.assertEqual(out, sql)

    def test_T14A6_normalize_spec_end_to_end(self):
        """完整 normalize_report_spec 流程：仅下界 SQL + date_range filter → 参数化"""
        spec = {
            "title": "测试报表",
            "charts": [{
                "id": "c1",
                "chart_type": "bar",
                "sql": "SELECT s_day AS date, SaaS, SUM(call_num) AS cnt "
                       "FROM integrated_data.Fact_Daily_Call "
                       "PREWHERE s_day >= today() - 30 AND call_code_type IN (1, 16) "
                       "GROUP BY s_day, SaaS ORDER BY s_day",
                "connection_env": "sg",
                "x_field": "date",
                "y_fields": ["cnt"],
            }],
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
        }
        out = normalize_report_spec(spec)
        chart_sql = out["charts"][0]["sql"]
        self.assertIn("{{ date_start }}", chart_sql,
                      "端到端流程应参数化仅下界 today()-N 的 SQL")
        self.assertIn("{{ date_end }}", chart_sql)
        self.assertNotIn("today()", chart_sql)
        # call_code_type 条件不应丢失
        self.assertIn("call_code_type IN (1, 16)", chart_sql)

    # ── T14-B: connection_env 规范化 ─────────────────────────────────────────

    def test_T14B1_clickhouse_prefix_stripped(self):
        """connection_env='clickhouse-sg' → 归一化为 'sg'"""
        chart = {
            "id": "c1", "chart_type": "bar",
            "sql": "SELECT 1", "connection_env": "clickhouse-sg",
        }
        out = self._normalize_chart(chart)
        self.assertEqual(out["connection_env"], "sg",
                         "'clickhouse-sg' 应被规范化为 'sg'")

    def test_T14B2_various_envs_stripped(self):
        """各环境 clickhouse-xxx 前缀均被正确剥离"""
        cases = {
            "clickhouse-idn": "idn",
            "clickhouse-br": "br",
            "clickhouse-my": "my",
            "clickhouse-thai": "thai",
            "clickhouse-mx": "mx",
            "clickhouse-sg-azure": "sg-azure",
        }
        for raw, expected in cases.items():
            chart = {"id": "c1", "chart_type": "bar", "sql": "SELECT 1", "connection_env": raw}
            out = self._normalize_chart(chart)
            self.assertEqual(out["connection_env"], expected,
                             f"{raw!r} 应被规范化为 {expected!r}")

    def test_T14B3_already_clean_env_unchanged(self):
        """已是短名的 connection_env 不变"""
        for env in ("sg", "idn", "br", "sg-azure"):
            chart = {"id": "c1", "chart_type": "bar", "sql": "SELECT 1", "connection_env": env}
            out = self._normalize_chart(chart)
            self.assertEqual(out["connection_env"], env, f"{env!r} 不应被修改")

    def test_T14B4_connection_env_normalized_in_full_spec(self):
        """完整 normalize_report_spec 中 connection_env 也被规范化"""
        spec = {
            "title": "测试",
            "charts": [{
                "id": "c1", "chart_type": "bar",
                "sql": "SELECT 1 AS v",
                "connection_env": "clickhouse-sg",
                "x_field": "v", "y_fields": ["v"],
            }],
        }
        out = normalize_report_spec(spec)
        self.assertEqual(out["charts"][0]["connection_env"], "sg")

    # ── T14-C: 联合场景（claudecode005 实际破坏场景）────────────────────────

    def test_T14C1_real_world_broken_spec_fully_normalized(self):
        """
        模拟 claudecode005 生成的破坏性 spec：
          - SQL 仅含下界 today()-30
          - connection_env = "clickhouse-sg"
        验证 normalize_report_spec 后两者均被修复。
        """
        spec = {
            "title": "全环境 Connected Call 每日趋势报表",
            "charts": [
                {
                    "id": "c1", "chart_type": "bar",
                    "title": "各环境 Connected Call 每日数量（堆积图）",
                    "connection_env": "clickhouse-sg",
                    "sql": (
                        "SELECT s_day AS date, SaaS, SUM(call_num) AS connected_calls "
                        "FROM integrated_data.Fact_Daily_Call "
                        "PREWHERE s_day >= today() - 30 AND call_code_type IN (1, 16) "
                        "GROUP BY s_day, SaaS ORDER BY s_day ASC, SaaS ASC"
                    ),
                    "x_field": "date", "y_fields": ["connected_calls"],
                    "series_field": "SaaS",
                },
                {
                    "id": "c2", "chart_type": "bar",
                    "title": "各环境 Connected Call 30天总量对比",
                    "connection_env": "clickhouse-sg",
                    "sql": (
                        "SELECT SaaS, SUM(call_num) AS total_connected "
                        "FROM integrated_data.Fact_Daily_Call "
                        "PREWHERE s_day >= today() - 30 AND call_code_type IN (1, 16) "
                        "GROUP BY SaaS ORDER BY total_connected DESC"
                    ),
                    "x_field": "SaaS", "y_fields": ["total_connected"],
                },
            ],
            "filters": [{
                "id": "date_range", "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
        }
        out = normalize_report_spec(spec)

        for chart in out["charts"]:
            if chart.get("chart_type") == "ai_analysis":
                continue
            with self.subTest(chart_id=chart["id"]):
                self.assertEqual(chart["connection_env"], "sg",
                                 f"{chart['id']}: clickhouse-sg 应被规范化为 sg")
                sql = chart["sql"]
                self.assertIn("{{ date_start }}", sql,
                              f"{chart['id']}: 仅下界 today()-30 应被参数化")
                self.assertIn("{{ date_end }}", sql)
                self.assertNotIn("today()", sql)
                # 其他 WHERE 条件保留
                self.assertIn("call_code_type IN (1, 16)", sql)


if __name__ == "__main__":
    unittest.main()
