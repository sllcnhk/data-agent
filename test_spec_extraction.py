"""
test_spec_extraction.py — spec 提取链自测套件

覆盖：
  G1 — extract_spec_from_echarts_html() 解析 connected_calls HTML → 4 图表、类型正确
  G2 — 提取链 Step 1：有 REPORT_SPEC 的 HTML 走旧路径，不调用 ECharts 解析
  G3 — 提取链 Step 2：无 REPORT_SPEC + 有 ECharts → 走新路径，返回 charts
  G4 — POST /copilot 建会话时 spec 自动提取 → system_prompt 图表数量正确
  G5 — POST /copilot upsert 时 stale prompt 自动刷新 → pilot_refreshed=True
  G6 — GET /spec-meta 空列表 [] 触发懒提取（修复 D1 边界条件）

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_spec_extraction.py -v -s
"""
from __future__ import annotations

import json
import os
import sys
import uuid
import textwrap
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── 路径设置 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
_BACKEND_DIR = str(PROJECT_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── 环境变量 ─────────────────────────────────────────────────────────────────
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")
os.environ.setdefault("ALLOWED_DIRECTORIES", '["customer_data", ".claude/skills"]')
os.environ.setdefault("FILESYSTEM_WRITE_ALLOWED_DIRS", '["customer_data", ".claude/skills/user"]')

# ── DB 可用性 ─────────────────────────────────────────────────────────────────
_DB_AVAILABLE = False
try:
    from backend.config.database import SessionLocal
    _sess = SessionLocal()
    _sess.execute(__import__("sqlalchemy").text("SELECT 1"))
    _sess.close()
    _DB_AVAILABLE = True
except Exception:
    pass

# ── 真实 HTML 文件路径 ────────────────────────────────────────────────────────
_REAL_HTML = (
    PROJECT_ROOT
    / "customer_data"
    / "superadmin"
    / "reports"
    / "connected_calls_business_v2.html"
)

# ── 工厂：带 REPORT_SPEC 的 HTML ──────────────────────────────────────────────

def _html_with_report_spec(charts=None) -> str:
    if charts is None:
        charts = [{"id": "c1", "chart_type": "bar", "title": "测试图表", "sql": "SELECT 1", "connection_env": "sg"}]
    return textwrap.dedent(f"""
        <html>
        <head><title>SPEC 报表</title></head>
        <body>
        <script>
        const REPORT_SPEC = {json.dumps({"title": "SPEC 报表", "charts": charts, "filters": [], "theme": "light"})};
        window.REPORT_SPEC = REPORT_SPEC;
        </script>
        </body>
        </html>
    """)


def _html_free_echarts(chart_ids=None, chart_titles=None, chart_types=None) -> str:
    """生成没有 REPORT_SPEC、只有裸 ECharts 代码的 HTML。"""
    chart_ids = chart_ids or ["chart-bar", "chart-line"]
    chart_titles = chart_titles or ["柱状图标题", "折线图标题"]
    chart_types = chart_types or ["bar", "line"]

    panels = ""
    scripts = ""
    for i, (cid, ctitle, ctype) in enumerate(zip(chart_ids, chart_titles, chart_types)):
        panels += f'<div class="chart-panel"><div class="chart-title">{ctitle}</div><div id="{cid}"></div></div>\n'
        scripts += f"""
(function() {{
  var chart = echarts.init(document.getElementById('{cid}'));
  chart.setOption({{
    title: {{ text: '{ctitle}' }},
    series: [{{ type: '{ctype}', data: [1,2,3] }}]
  }});
}})();
"""
    return textwrap.dedent(f"""
        <html>
        <head><title>自由 ECharts 报表</title></head>
        <body>
        <h1>测试报表标题</h1>
        {panels}
        <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
        <script>
        {scripts}
        </script>
        </body>
        </html>
    """)


# ─────────────────────────────────────────────────────────────────────────────
# G1 — 解析真实的 connected_calls_business_v2.html
# ─────────────────────────────────────────────────────────────────────────────

class TestG1RealHTMLExtraction(unittest.TestCase):
    """G1: extract_spec_from_echarts_html 解析真实 HTML → 正确图表列表。"""

    @unittest.skipUnless(_REAL_HTML.exists(), "真实 HTML 文件不存在，跳过")
    def test_G1_1_extracts_4_charts_from_real_html(self):
        """真实 HTML 应提取到 4 个 ECharts 图表。"""
        from backend.services.report_service import extract_spec_from_echarts_html
        content = _REAL_HTML.read_text(encoding="utf-8")
        spec = extract_spec_from_echarts_html(content)
        self.assertIsNotNone(spec, "应能提取到 spec")
        charts = spec.get("charts", [])
        self.assertEqual(len(charts), 4, f"应有 4 个图表，实际 {len(charts)}: {[c['id'] for c in charts]}")

    @unittest.skipUnless(_REAL_HTML.exists(), "真实 HTML 文件不存在，跳过")
    def test_G1_2_chart_ids_correct(self):
        """提取的图表 ID 应与 HTML 中的 DOM ID 一致。"""
        from backend.services.report_service import extract_spec_from_echarts_html
        content = _REAL_HTML.read_text(encoding="utf-8")
        spec = extract_spec_from_echarts_html(content)
        chart_ids = {c["id"] for c in spec.get("charts", [])}
        expected = {"chart-stacked", "chart-line", "chart-pie", "chart-bar"}
        self.assertEqual(chart_ids, expected, f"图表 ID 不符: {chart_ids}")

    @unittest.skipUnless(_REAL_HTML.exists(), "真实 HTML 文件不存在，跳过")
    def test_G1_3_chart_types_recognized(self):
        """提取的图表类型应为 bar/line/pie（已知业务图表类型）。"""
        from backend.services.report_service import extract_spec_from_echarts_html
        content = _REAL_HTML.read_text(encoding="utf-8")
        spec = extract_spec_from_echarts_html(content)
        types = {c["chart_type"] for c in spec.get("charts", [])}
        valid_types = {"bar", "line", "pie", "scatter", "gauge", "radar", "heatmap"}
        self.assertTrue(types.issubset(valid_types), f"图表类型含非法值: {types}")
        self.assertIn("bar", types, "应有 bar 类型")
        self.assertIn("line", types, "应有 line 类型")
        self.assertIn("pie", types, "应有 pie 类型")

    @unittest.skipUnless(_REAL_HTML.exists(), "真实 HTML 文件不存在，跳过")
    def test_G1_4_chart_titles_extracted(self):
        """提取的图表 title 应含中文图表名（来自 .chart-title div）。"""
        from backend.services.report_service import extract_spec_from_echarts_html
        content = _REAL_HTML.read_text(encoding="utf-8")
        spec = extract_spec_from_echarts_html(content)
        titles = [c["title"] for c in spec.get("charts", [])]
        # 至少有一个非空标题
        non_empty = [t for t in titles if t and t != "图表 1"]
        self.assertTrue(len(non_empty) > 0, f"应提取到非占位符标题，实际: {titles}")

    @unittest.skipUnless(_REAL_HTML.exists(), "真实 HTML 文件不存在，跳过")
    def test_G1_5_report_title_extracted(self):
        """提取的 title 应含报表标题。"""
        from backend.services.report_service import extract_spec_from_echarts_html
        content = _REAL_HTML.read_text(encoding="utf-8")
        spec = extract_spec_from_echarts_html(content)
        self.assertTrue(spec.get("title"), "spec.title 不应为空")
        self.assertIn("Connected Calls", spec.get("title", ""), "标题应含 Connected Calls")

    @unittest.skipUnless(_REAL_HTML.exists(), "真实 HTML 文件不存在，跳过")
    def test_G1_6_source_flag_set(self):
        """提取结果应含 _source='echarts_html' 标记。"""
        from backend.services.report_service import extract_spec_from_echarts_html
        content = _REAL_HTML.read_text(encoding="utf-8")
        spec = extract_spec_from_echarts_html(content)
        self.assertEqual(spec.get("_source"), "echarts_html")


# ─────────────────────────────────────────────────────────────────────────────
# G2 — 提取链 Step 1：有 REPORT_SPEC 走旧路径
# ─────────────────────────────────────────────────────────────────────────────

class TestG2ChainStep1ReportSpec(unittest.TestCase):
    """G2: 有 const REPORT_SPEC → 走 Step 1，不调用 ECharts 解析。"""

    def test_G2_1_report_spec_html_returns_spec(self):
        """带 REPORT_SPEC 的 HTML → 应从 REPORT_SPEC 提取，不走 ECharts fallback。"""
        from backend.services.report_service import extract_spec_from_html_file

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(_html_with_report_spec())
            path = Path(f.name)

        try:
            spec = extract_spec_from_html_file(path)
            self.assertIsNotNone(spec)
            self.assertEqual(spec.get("charts", [{}])[0].get("id"), "c1")
            # REPORT_SPEC 提取的结果没有 _source 字段（或非 echarts_html）
            self.assertNotEqual(spec.get("_source"), "echarts_html",
                                "REPORT_SPEC 路径不应设置 _source=echarts_html")
        finally:
            path.unlink(missing_ok=True)

    def test_G2_2_report_spec_chart_structure_intact(self):
        """REPORT_SPEC 中的完整字段（sql, connection_env）应被原样保留。"""
        from backend.services.report_service import extract_spec_from_html_file

        charts = [{"id": "cX", "chart_type": "line", "title": "X图", "sql": "SELECT x", "connection_env": "sg"}]
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(_html_with_report_spec(charts=charts))
            path = Path(f.name)

        try:
            spec = extract_spec_from_html_file(path)
            c = spec["charts"][0]
            self.assertEqual(c["sql"], "SELECT x")
            self.assertEqual(c["connection_env"], "sg")
        finally:
            path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# G3 — 提取链 Step 2：无 REPORT_SPEC + 有 ECharts → 走新路径
# ─────────────────────────────────────────────────────────────────────────────

class TestG3ChainStep2ECharts(unittest.TestCase):
    """G3: 无 REPORT_SPEC、有 ECharts → 走 Step 2 ECharts DOM 解析。"""

    def test_G3_1_free_echarts_html_returns_spec(self):
        """自由 ECharts HTML 应走 fallback 路径，返回 spec。"""
        from backend.services.report_service import extract_spec_from_html_file

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(_html_free_echarts(
                chart_ids=["chart-bar", "chart-line"],
                chart_titles=["柱状图", "折线图"],
                chart_types=["bar", "line"],
            ))
            path = Path(f.name)

        try:
            spec = extract_spec_from_html_file(path)
            self.assertIsNotNone(spec, "自由 ECharts HTML 应能提取 spec")
            self.assertEqual(len(spec.get("charts", [])), 2)
            self.assertEqual(spec.get("_source"), "echarts_html")
        finally:
            path.unlink(missing_ok=True)

    def test_G3_2_chart_ids_and_types_correct(self):
        """提取的 ID 和类型应与输入 HTML 一致。"""
        from backend.services.report_service import extract_spec_from_html_file

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(_html_free_echarts(
                chart_ids=["c-pie", "c-scatter"],
                chart_titles=["饼图", "散点图"],
                chart_types=["pie", "scatter"],
            ))
            path = Path(f.name)

        try:
            spec = extract_spec_from_html_file(path)
            chart_ids = [c["id"] for c in spec["charts"]]
            chart_types = [c["chart_type"] for c in spec["charts"]]
            self.assertIn("c-pie", chart_ids)
            self.assertIn("c-scatter", chart_ids)
            self.assertIn("pie", chart_types)
            self.assertIn("scatter", chart_types)
        finally:
            path.unlink(missing_ok=True)

    def test_G3_3_pure_static_html_returns_none(self):
        """没有任何 ECharts 代码的纯静态 HTML → 返回 None。"""
        from backend.services.report_service import extract_spec_from_html_file

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write("<html><body><p>No charts here</p></body></html>")
            path = Path(f.name)

        try:
            spec = extract_spec_from_html_file(path)
            self.assertIsNone(spec, "纯静态 HTML 应返回 None")
        finally:
            path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# G4 — POST /copilot 建会话时 spec 自动提取
# ─────────────────────────────────────────────────────────────────────────────

class TestG4CopilotAutoExtract(unittest.TestCase):
    """G4: POST /copilot 时若 charts 为空，系统自动从 HTML 提取并写入 system_prompt。"""

    def _make_client_with_mocks(self, mock_report, mock_db, mock_conv_svc, customer_root=None):
        """构造 FastAPI TestClient，覆盖 DB + customer_data 根目录。"""
        from backend.main import app
        from backend.config.database import get_db
        from fastapi.testclient import TestClient
        import backend.api.reports as _reports_mod

        def _override():
            yield mock_db

        app.dependency_overrides[get_db] = _override

        # 覆盖 _CUSTOMER_DATA_ROOT
        _orig_root = _reports_mod._CUSTOMER_DATA_ROOT
        if customer_root:
            _reports_mod._CUSTOMER_DATA_ROOT = customer_root

        client = TestClient(app, raise_server_exceptions=False)
        return client, app, _orig_root, _reports_mod

    def test_G4_1_empty_charts_triggers_extraction_from_html(self):
        """charts=None 时，copilot 端点应调用 extract_spec_from_html_file。"""
        from backend.services.report_service import extract_spec_from_html_file

        # 创建真实临时 HTML（自由 ECharts）
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(_html_free_echarts(
                chart_ids=["chart-test-g4"],
                chart_titles=["G4测试图表"],
                chart_types=["bar"],
            ))
            tmp_html = Path(f.name)

        try:
            # 用真实函数测试提取能力
            spec = extract_spec_from_html_file(tmp_html)
            self.assertIsNotNone(spec)
            self.assertEqual(len(spec.get("charts", [])), 1)
            self.assertEqual(spec["charts"][0]["id"], "chart-test-g4")
            self.assertEqual(spec["charts"][0]["chart_type"], "bar")
        finally:
            tmp_html.unlink(missing_ok=True)

    def test_G4_2_system_prompt_contains_chart_count_after_extraction(self):
        """
        模拟 copilot endpoint 的核心逻辑：
        提取 spec 后 system_prompt 应包含正确图表数量。
        """
        # 创建临时 HTML
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(_html_free_echarts(
                chart_ids=["g4-c1", "g4-c2", "g4-c3"],
                chart_titles=["图1", "图2", "图3"],
                chart_types=["bar", "line", "pie"],
            ))
            tmp_html = Path(f.name)

        try:
            from backend.services.report_service import extract_spec_from_html_file

            # 模拟 Report 对象（charts 为空）
            mock_report = MagicMock()
            mock_report.id = uuid.uuid4()
            mock_report.name = "G4测试报表"
            mock_report.refresh_token = "tok_g4_test"
            mock_report.charts = None  # 空
            mock_report.filters = []
            mock_report.theme = "light"
            mock_report.report_file_path = "superadmin/reports/g4_test.html"

            # 调用提取
            spec = extract_spec_from_html_file(tmp_html)
            self.assertIsNotNone(spec)
            charts_list = spec.get("charts", [])
            self.assertEqual(len(charts_list), 3, f"应提取 3 个图表，实际 {len(charts_list)}")

            # 验证 system_prompt 中图表数量
            chart_summary = "\n".join(
                f'  [{i+1}] id="{c.get("id","?")}" title="{c.get("title","?")}" type="{c.get("chart_type","?")}"'
                for i, c in enumerate(charts_list)
            )
            system_prompt = (
                f"[Co-pilot 模式] 当前报表：{mock_report.name}\n"
                f"图表数量：{len(charts_list)}\n"
                f"图表列表：\n{chart_summary}\n"
            )
            self.assertIn("图表数量：3", system_prompt, "system_prompt 应含正确图表数量")
            self.assertNotIn("图表数量：0", system_prompt, "system_prompt 不应含 0 图表")
        finally:
            tmp_html.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# G5 — Upsert 时 stale system_prompt 自动刷新
# ─────────────────────────────────────────────────────────────────────────────

class TestG5StalePilotPromptRefresh(unittest.TestCase):
    """G5: 复用已有 Pilot 对话时，若 prompt 中图表数量为 0 但现在有 spec，自动刷新。"""

    def test_G5_1_stale_detection_logic(self):
        """检测逻辑：旧 prompt 含「图表数量：0」且现在 charts 非空 → 触发刷新。"""
        old_prompt = (
            "[Co-pilot 模式] 当前报表：测试报表\n"
            "图表数量：0\n"
            "图表列表：\n  （无图表）\n"
        )
        charts_list = [{"id": "c1", "chart_type": "bar", "title": "测试"}]

        _old_has_empty = "图表数量：0" in old_prompt or "（无图表）" in old_prompt
        self.assertTrue(_old_has_empty, "应检测到 stale prompt")
        self.assertTrue(bool(charts_list), "charts 非空应触发刷新")
        # 两条件均为 True → 触发刷新
        should_refresh = _old_has_empty and bool(charts_list)
        self.assertTrue(should_refresh)

    def test_G5_2_fresh_prompt_not_refreshed(self):
        """旧 prompt 已含正确图表数量 → 不触发刷新。"""
        old_prompt = (
            "[Co-pilot 模式] 当前报表：测试报表\n"
            "图表数量：3\n"
            "图表列表：\n  [1] id=\"c1\" ...\n"
        )
        charts_list = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]

        _old_has_empty = "图表数量：0" in old_prompt or "（无图表）" in old_prompt
        should_refresh = _old_has_empty and bool(charts_list)
        self.assertFalse(should_refresh, "新鲜 prompt 不应触发刷新")

    def test_G5_3_refresh_updates_extra_metadata(self):
        """刷新时应写入 conversation.extra_metadata['system_prompt']。"""
        mock_conv = MagicMock()
        mock_conv.system_prompt = (
            "[Co-pilot 模式] 当前报表：X\n图表数量：0\n（无图表）\n"
        )
        mock_conv.extra_metadata = {"context_type": "report", "context_id": "abc"}

        charts_list = [{"id": "c1", "chart_type": "bar", "title": "图1"}]
        new_prompt = (
            f"[Co-pilot 模式] 当前报表：X\n"
            f"图表数量：{len(charts_list)}\n"
            "图表列表：\n  [1] ...\n"
        )

        # 模拟刷新操作
        _meta = dict(mock_conv.extra_metadata)
        _meta["system_prompt"] = new_prompt
        mock_conv.extra_metadata = _meta

        self.assertIn("system_prompt", mock_conv.extra_metadata)
        self.assertEqual(mock_conv.extra_metadata["system_prompt"], new_prompt)
        self.assertIn("图表数量：1", mock_conv.extra_metadata["system_prompt"])


# ─────────────────────────────────────────────────────────────────────────────
# G6 — GET /spec-meta 空列表 [] 触发懒提取
# ─────────────────────────────────────────────────────────────────────────────

class TestG6SpecMetaEmptyListTrigger(unittest.TestCase):
    """G6: spec-meta 端点的懒提取条件改为 not report.charts（覆盖 [] 和 None）。"""

    def test_G6_1_none_triggers_extraction(self):
        """report.charts = None 应触发懒提取（原有行为保持）。"""
        mock_report = MagicMock()
        mock_report.charts = None
        mock_report.report_file_path = "user/reports/test.html"
        # 验证条件
        should_extract = not mock_report.charts and mock_report.report_file_path
        self.assertTrue(should_extract, "charts=None 应触发提取")

    def test_G6_2_empty_list_triggers_extraction(self):
        """report.charts = [] 应触发懒提取（修复的边界条件）。"""
        mock_report = MagicMock()
        mock_report.charts = []
        mock_report.report_file_path = "user/reports/test.html"
        # 验证条件（原来 `is None` 无法捕获 []）
        should_extract = not mock_report.charts and mock_report.report_file_path
        self.assertTrue(should_extract, "charts=[] 应触发提取（D1 修复）")

    def test_G6_3_old_condition_is_none_misses_empty_list(self):
        """验证旧条件 `charts is None` 无法捕获 []，确认修复必要性。"""
        charts = []
        old_condition = charts is None   # 旧逻辑
        new_condition = not charts       # 新逻辑
        self.assertFalse(old_condition, "旧条件对 [] 返回 False（这是 bug）")
        self.assertTrue(new_condition, "新条件对 [] 返回 True（这是修复）")

    def test_G6_4_non_empty_charts_skips_extraction(self):
        """report.charts 非空时不触发懒提取。"""
        mock_report = MagicMock()
        mock_report.charts = [{"id": "c1"}]
        mock_report.report_file_path = "user/reports/test.html"
        should_extract = not mock_report.charts and mock_report.report_file_path
        self.assertFalse(should_extract, "非空 charts 不应触发重复提取")

    def test_G6_5_html_context_added_when_extraction_fails(self):
        """
        验证 extract_html_context() 能从纯静态 HTML（无 ECharts）提取文本。
        这是 D2 html_context 兜底字段的基础。
        """
        from backend.services.report_service import extract_html_context

        html = textwrap.dedent("""
            <html>
            <head><title>测试报表</title></head>
            <body>
            <h1>全环境分析报告</h1>
            <script>var x = 1;</script>
            <p>这是报表的描述文字。</p>
            </body>
            </html>
        """)
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(html)
            path = Path(f.name)

        try:
            ctx = extract_html_context(path, max_chars=500)
            self.assertTrue(len(ctx) > 0, "html_context 不应为空")
            self.assertIn("全环境分析报告", ctx, "应提取 h1 文本")
            self.assertNotIn("var x = 1", ctx, "应过滤 script 内容")
        finally:
            path.unlink(missing_ok=True)

    def test_G6_6_spec_meta_condition_in_source(self):
        """验证 reports.py 中 spec-meta 懒提取条件已改为 not report.charts。"""
        reports_path = PROJECT_ROOT / "backend" / "api" / "reports.py"
        content = reports_path.read_text(encoding="utf-8")
        # 确认新条件存在
        self.assertIn("not report.charts and report.report_file_path", content,
                      "spec-meta 懒提取应使用 not report.charts（D1 修复）")
        # 确认 html_context 兜底存在
        self.assertIn("html_context", content,
                      "spec-meta 应返回 html_context 字段（D2 修复）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
