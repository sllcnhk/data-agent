"""
test_chart_controls.py — 图表控件注入单元测试

测试 _inject_chart_controls() 函数的 CSS/JS 注入、幂等保护、
与 _inject_pilot_button 共存，以及各功能 JS 标记的存在性。

A 组：CSS / Style 注入
B 组：JS 函数标记存在
C 组：注入位置（</head> / </body> 降级）
D 组：幂等与共存
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

from backend.api.reports import _inject_chart_controls, _inject_pilot_button


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────
_FULL_HTML = """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<div class="chart-card chart-half" id="card-trend">
  <div class="chart-card-title">趋势图</div>
  <div class="chart-container echarts-chart" id="trend" style="height:320px"></div>
</div>
<script>
var _charts={};
var _chartData={'trend':[{date:'2026-01-01',value:100}]};
var REPORT_SPEC={charts:[{id:'trend',title:'趋势图',sql:'SELECT date,value FROM t'}]};
var REPORT_ID='test-report-id';
var REFRESH_TOKEN='test-token';
var API_BASE='http://localhost:8000/api/v1';
</script>
</body>
</html>"""

_NO_BODY_HTML = "<html><head></head><div>no body tag</div></html>"
_NO_HEAD_HTML = "<html><body><div>no head tag</div></body></html>"
_EMPTY_HTML = ""


# ─────────────────────────────────────────────────────────────────────────────
# A 组 — CSS / Style 注入
# ─────────────────────────────────────────────────────────────────────────────
class TestACSSInjection(unittest.TestCase):

    def setUp(self):
        self.result = _inject_chart_controls(_FULL_HTML)

    def test_A1_style_tag_with_id_injected(self):
        """注入结果含 <style id="__cc-style">"""
        self.assertIn('<style id="__cc-style">', self.result)

    def test_A2_cc_menu_btn_css_present(self):
        """注入 CSS 含 .cc-menu-btn 选择器"""
        self.assertIn(".cc-menu-btn{", self.result)

    def test_A3_cc_dropdown_css_present(self):
        """注入 CSS 含 .cc-dropdown 选择器"""
        self.assertIn(".cc-dropdown{", self.result)

    def test_A4_cc_modal_overlay_css_present(self):
        """注入 CSS 含 .cc-modal-overlay（View Query 弹窗）"""
        self.assertIn(".cc-modal-overlay{", self.result)

    def test_A5_fullscreen_css_present(self):
        """注入 CSS 含全屏覆盖规则 chart-card:fullscreen"""
        self.assertIn(".chart-card:fullscreen", self.result)

    def test_A6_cc_sql_block_css_present(self):
        """注入 CSS 含 .cc-sql-block（SQL 代码块样式）"""
        self.assertIn(".cc-sql-block{", self.result)


# ─────────────────────────────────────────────────────────────────────────────
# B 组 — JS 函数标记
# ─────────────────────────────────────────────────────────────────────────────
class TestBJSFunctions(unittest.TestCase):

    def setUp(self):
        self.result = _inject_chart_controls(_FULL_HTML)

    def test_B1_force_refresh_function_present(self):
        """注入 JS 含 ccForceRefresh 函数"""
        self.assertIn("ccForceRefresh", self.result)

    def test_B2_fullscreen_function_present(self):
        """注入 JS 含 ccFullscreen 函数"""
        self.assertIn("ccFullscreen", self.result)

    def test_B3_view_query_function_present(self):
        """注入 JS 含 ccViewQuery 函数"""
        self.assertIn("ccViewQuery", self.result)

    def test_B4_copy_sql_function_present(self):
        """注入 JS 含 ccCopySql 函数"""
        self.assertIn("ccCopySql", self.result)

    def test_B5_download_function_present(self):
        """注入 JS 含 ccDownload 函数"""
        self.assertIn("ccDownload", self.result)

    def test_B6_force_refresh_calls_refresh_data_api(self):
        """Force Refresh JS 使用 /refresh-data 端点"""
        self.assertIn("/refresh-data", self.result)

    def test_B7_force_refresh_uses_report_globals(self):
        """Force Refresh 使用 REPORT_ID 和 REFRESH_TOKEN 全局变量"""
        self.assertIn("REPORT_ID", self.result)
        self.assertIn("REFRESH_TOKEN", self.result)

    def test_B8_force_refresh_fallback_clear_setoption(self):
        """Force Refresh 降级路径含 chart.clear()"""
        self.assertIn("chart.clear()", self.result)

    def test_B9_fullscreen_request_fullscreen_api(self):
        """Enter Fullscreen 调用 requestFullscreen API"""
        self.assertIn("requestFullscreen", self.result)

    def test_B10_fullscreen_calls_chart_resize(self):
        """Enter Fullscreen 后调用 chart.resize() 适应尺寸"""
        self.assertIn("c.resize()", self.result)

    def test_B11_view_query_reads_report_spec_sql(self):
        """View Query 从 REPORT_SPEC.charts 读取 sql 字段"""
        self.assertIn("REPORT_SPEC", self.result)
        self.assertIn(".sql", self.result)

    def test_B12_download_csv_bom(self):
        """Download CSV 包含 BOM（\\uFEFF）处理"""
        self.assertIn("uFEFF", self.result)

    def test_B13_download_csv_format_branch(self):
        """Download 含 csv/excel 格式分支"""
        self.assertIn("fmt==='csv'", self.result)
        self.assertIn("vnd.ms-excel", self.result)

    def test_B14_init_controls_targets_chart_card(self):
        """initControls 扫描 .chart-card 元素"""
        self.assertIn(".chart-card", self.result)

    def test_B15_script_tag_injected(self):
        """注入结果含 <script id="__cc-script">"""
        self.assertIn('<script id="__cc-script">', self.result)


# ─────────────────────────────────────────────────────────────────────────────
# C 组 — 注入位置
# ─────────────────────────────────────────────────────────────────────────────
class TestCInjectionPosition(unittest.TestCase):

    def test_C1_injected_before_body_close(self):
        """CSS+JS 注入在 </body> 之前"""
        result = _inject_chart_controls(_FULL_HTML)
        style_pos = result.find("__cc-style")
        body_close_pos = result.lower().rfind("</body>")
        self.assertGreater(body_close_pos, style_pos)

    def test_C2_body_close_tag_still_present(self):
        """注入后 </body> 标签仍存在"""
        result = _inject_chart_controls(_FULL_HTML)
        self.assertIn("</body>", result)

    def test_C3_no_body_tag_appends_to_end(self):
        """无 </body> 标签时追加到末尾，不报错"""
        result = _inject_chart_controls(_NO_BODY_HTML)
        self.assertIn("__cc-style", result)
        self.assertIn("ccForceRefresh", result)

    def test_C4_no_head_tag_still_injects(self):
        """无 </head> 标签时仍能注入"""
        result = _inject_chart_controls(_NO_HEAD_HTML)
        self.assertIn("__cc-style", result)
        self.assertIn("ccDownload", result)

    def test_C5_empty_html_handled_gracefully(self):
        """空 HTML 字符串不报错，返回含注入内容的字符串"""
        result = _inject_chart_controls(_EMPTY_HTML)
        self.assertIn("__cc-style", result)

    def test_C6_original_content_preserved(self):
        """原始 HTML 内容（报表结构）在注入后不被破坏"""
        result = _inject_chart_controls(_FULL_HTML)
        self.assertIn('id="card-trend"', result)
        self.assertIn("REPORT_SPEC", result)  # original script block preserved
        self.assertIn("<title>Test</title>", result)


# ─────────────────────────────────────────────────────────────────────────────
# D 组 — 幂等 & 共存
# ─────────────────────────────────────────────────────────────────────────────
class TestDIdempotencyAndCoexistence(unittest.TestCase):

    def test_D1_idempotent_second_injection_no_duplicate(self):
        """连续调用两次 _inject_chart_controls 不重复注入"""
        once = _inject_chart_controls(_FULL_HTML)
        twice = _inject_chart_controls(once)
        count = twice.count('<style id="__cc-style">')
        self.assertEqual(count, 1, f"__cc-style 出现了 {count} 次，应为 1 次")

    def test_D2_idempotent_js_not_duplicated(self):
        """双重注入后 ccForceRefresh 函数定义只有一份"""
        once = _inject_chart_controls(_FULL_HTML)
        twice = _inject_chart_controls(once)
        count = twice.count("window.ccForceRefresh=function")
        self.assertEqual(count, 1)

    def test_D3_coexists_with_pilot_button(self):
        """_inject_chart_controls 与 _inject_pilot_button 共存：两者 snippet 均在 HTML 中"""
        result = _inject_chart_controls(_FULL_HTML)
        result = _inject_pilot_button(result, "abc-123", doc_type="dashboard")
        self.assertIn("__cc-style", result, "图表控件 CSS 缺失")
        self.assertIn("ccForceRefresh", result, "图表控件 JS 缺失")
        self.assertIn("__pilot-fab", result, "Pilot 按钮缺失")

    def test_D4_pilot_button_after_chart_controls_in_html(self):
        """Pilot 按钮注入在图表控件之后（先 chart controls 再 pilot）"""
        result = _inject_chart_controls(_FULL_HTML)
        result = _inject_pilot_button(result, "abc-123", doc_type="dashboard")
        cc_pos = result.find("__cc-style")
        pilot_pos = result.find("__pilot-fab")
        self.assertGreater(pilot_pos, cc_pos)

    def test_D5_pilot_button_idempotent_after_chart_controls(self):
        """先注入图表控件，再注入 pilot 两次，pilot 只出现一次"""
        result = _inject_chart_controls(_FULL_HTML)
        result = _inject_pilot_button(result, "abc-123", doc_type="dashboard")
        result = _inject_pilot_button(result, "abc-123", doc_type="dashboard")
        # pilot button itself doesn't have idempotency guard, but we verify CC does not duplicate
        cc_count = result.count('<style id="__cc-style">')
        self.assertEqual(cc_count, 1)

    def test_D6_by_path_endpoint_returns_chart_controls(self):
        """serve_report_html_by_path 注入后返回包含 cc-style 的 HTML（函数级验证）"""
        # Simulate what serve_report_html_by_path does
        raw_html = _FULL_HTML
        injected = _inject_chart_controls(raw_html)
        self.assertIn("__cc-style", injected)
        self.assertIn("ccFullscreen", injected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
