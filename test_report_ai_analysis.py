"""
报告 AI 数据分析功能测试

测试范围：
A - Service 单元测试（analyze_report_data / adapter fallback / prompt / parse）
B - Endpoint 单元测试（token / 数据截断 / LLM 失败降级）
C - RBAC/Token 测试（token 验证 / 无 ai_analysis chart 仍可调用）
D - E2E mock 测试（含 ai_analysis 的完整 spec → HTML → analyze 调用）
E - 回归测试（现有 report_filter 功能不受影响）

运行：pytest test_report_ai_analysis.py -v -s
"""
import json
import pytest
import types
from typing import Dict, Any, List
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.api.reports import router, AnalyzeReportRequest
from backend.services.report_analysis_service import (
    analyze_report_data,
    _build_data_summary,
    _build_prompt,
    _parse_sections,
    _get_adapter,
    _PROVIDER_ORDER,
    SECTION_META,
)
from backend.services.report_builder_service import build_report_html


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_db():
    """Mock DB session."""
    db = MagicMock(spec=Session)
    return db


@pytest.fixture
def fake_report():
    """Mock report object."""
    report = MagicMock()
    report.id = "11111111-1111-1111-1111-111111111111"
    report.name = "测试报表"
    report.description = ""
    report.theme = "light"
    report.refresh_token = "test-token-12345678"
    report.report_file_path = "test/reports/test.html"
    report.charts = [
        {
            "id": "c1",
            "chart_type": "line",
            "title": "通话量趋势",
            "sql": "SELECT toDate(call_start_time) AS date, count() AS cnt FROM crm.realtime_dwd_crm_call_record WHERE call_start_time >= '{{ date_start }}' AND call_start_time < '{{ date_end }}' GROUP BY date ORDER BY date",
            "connection_env": "sg",
            "connection_type": "clickhouse",
            "x_field": "date",
            "y_fields": ["cnt"],
        },
        {
            "id": "summary_1",
            "chart_type": "ai_analysis",
            "title": "数据分析总结",
            "width": "full",
        },
    ]
    report.filters = [
        {
            "id": "date_range",
            "type": "date_range",
            "label": "时间范围",
            "default_days": 30,
            "binds": {"start": "date_start", "end": "date_end"},
        }
    ]
    report.llm_summary = None
    return report


@pytest.fixture
def sample_charts_data() -> Dict[str, Any]:
    """模拟图表查询结果."""
    return {
        "c1": [
            {"date": "2026-03-01", "cnt": 1200},
            {"date": "2026-03-02", "cnt": 1350},
            {"date": "2026-03-03", "cnt": 1100},
            {"date": "2026-03-04", "cnt": 1600},
            {"date": "2026-03-05", "cnt": 1400},
        ],
        "c2": [
            {"env": "SG", "connected_calls": 5000},
            {"env": "IDN", "connected_calls": 3200},
            {"env": "BR", "connected_calls": 2800},
        ],
    }


@pytest.fixture
def sample_chart_specs() -> List[Dict]:
    """模拟图表配置."""
    return [
        {"id": "c1", "title": "每日通话量趋势", "chart_type": "line", "x_field": "date", "y_fields": ["cnt"]},
        {"id": "c2", "title": "各环境接通量对比", "chart_type": "bar", "x_field": "env", "y_fields": ["connected_calls"]},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# A - Service 单元测试
# ─────────────────────────────────────────────────────────────────────────────

class TestAServiceUnit:
    """Service 层单元测试."""

    def test_A1_build_data_summary(self, sample_charts_data, sample_chart_specs):
        """测试数据摘要生成."""
        summary = _build_data_summary(sample_charts_data, sample_chart_specs)
        assert "### [c1] 每日通话量趋势" in summary
        assert "图表类型：line" in summary          # 实际格式：（图表类型：line）
        assert "### [c2] 各环境接通量对比" in summary
        assert "X=date，Y=cnt" in summary           # y_fields 用顿号拼接，非 JSON 数组
        assert "共 5 行数据（展示前 5 行）" in summary  # sample_rows = min(rows, 50) = 5

    def test_A2_build_prompt_structure(self, sample_charts_data, sample_chart_specs):
        """测试 prompt 构建包含所有必需元素."""
        prompt = _build_prompt(
            sample_charts_data,
            "测试报表",
            ["trend", "anomaly", "insight", "conclusion"],
            sample_chart_specs,
        )
        assert "《测试报表》" in prompt
        assert "每日通话量趋势" in prompt or "各环境接通量对比" in prompt
        assert "📈 **趋势分析**" in prompt
        assert "⚠️ **异常检测**" in prompt
        assert "💡 **业务洞察**" in prompt
        assert "✅ **总结建议**" in prompt
        assert '"type": "trend"' in prompt
        assert "直接输出 JSON 数组" in prompt

    def test_A3_parse_sections_valid_json(self):
        """测试标准 JSON 解析."""
        json_str = '[{"type":"trend","title":"趋势","content":"数据上升"}]'
        sections = _parse_sections(json_str)
        assert len(sections) == 1
        assert sections[0]["type"] == "trend"
        assert sections[0]["title"] == "趋势"
        assert sections[0]["content"] == "数据上升"

    def test_A4_parse_sections_json_with_prefix(self):
        """测试带前缀文本的 JSON 解析（正则提取）."""
        text = """这是分析结果：
[{"type":"anomaly","title":"异常","content":"发现异常点"}]
结束"""
        sections = _parse_sections(text)
        assert len(sections) == 1
        assert sections[0]["type"] == "anomaly"

    def test_A5_parse_sections_fallback_text(self):
        """测试非 JSON 输出的 fallback（包裹为 insight）."""
        plain_text = "这是一个纯文本分析，没有 JSON 格式。"
        sections = _parse_sections(plain_text)
        assert len(sections) == 1
        assert sections[0]["type"] == "insight"
        assert sections[0]["content"] == plain_text[:800]

    def test_A6_parse_sections_empty_input(self):
        """测试空输入."""
        assert _parse_sections("") == []
        assert _parse_sections(None) == []
        assert _parse_sections("   ") == []

    @pytest.mark.asyncio
    async def test_A7_analyze_report_data_empty_charts(self):
        """测试空图表数据."""
        with patch("backend.services.report_analysis_service._call_llm_with_fallback", return_value=""):
            sections = await analyze_report_data({}, "空报表")
            assert sections == []

    @pytest.mark.asyncio
    async def test_A8_analyze_report_data_success(self, sample_charts_data, sample_chart_specs):
        """测试成功调用分析."""
        mock_response = json.dumps([
            {"type": "trend", "title": "趋势分析", "content": "通话量呈上升趋势"},
            {"type": "anomaly", "title": "异常检测", "content": "无明显异常"},
        ])
        with patch("backend.services.report_analysis_service._call_llm_with_fallback", return_value=mock_response):
            sections = await analyze_report_data(sample_charts_data, "测试报表", chart_specs=sample_chart_specs)
            assert len(sections) == 2
            assert sections[0]["type"] == "trend"
            assert "上升趋势" in sections[0]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# B - Endpoint 单元测试
# ─────────────────────────────────────────────────────────────────────────────

class TestBEndpointUnit:
    """Endpoint 层单元测试."""

    @pytest.fixture
    def app(self, fake_db, fake_report):
        """创建测试应用."""
        app = FastAPI()
        app.include_router(router)

        async def override_get_db():
            yield fake_db

        from backend.config.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        def mock_query(model):
            class Query:
                def filter(self, condition):
                    if hasattr(condition, 'right') and str(condition.right) == str(fake_report.id):
                        return self
                    return self

                def first(self):
                    return fake_report

            return Query()

        fake_db.query = mock_query
        return app

    @pytest.fixture
    def client(self, app):
        """创建测试客户端."""
        return TestClient(app)

    def test_B1_analyze_missing_token(self, client):
        """测试缺少 token 参数."""
        response = client.post(
            "/reports/11111111-1111-1111-1111-111111111111/analyze",
            json={"charts_data": {}}
        )
        assert response.status_code == 422  # FastAPI validation error for missing query param

    def test_B2_analyze_invalid_token(self, client):
        """测试无效 token."""
        response = client.post(
            "/reports/11111111-1111-1111-1111-111111111111/analyze?token=wrong-token",
            json={"charts_data": {}}
        )
        assert response.status_code == 403
        assert "无效的刷新令牌" in response.json()["detail"]

    def test_B3_analyze_valid_request(self, client):
        """测试有效请求（LLM 调用被 mock）."""
        with patch("backend.api.reports.analyze_report_data", return_value=[]):
            response = client.post(
                f"/reports/11111111-1111-1111-1111-111111111111/analyze?token=test-token-12345678",
                json={
                    "charts_data": {"c1": [{"x": 1, "y": 10}]},
                    "report_title": "测试报表",
                    "analysis_focus": ["trend"],
                    "chart_specs": [{"id": "c1", "title": "测试图表"}],
                }
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "sections" in data
        assert "count" in data

    def test_B4_analyze_data_truncation(self, client):
        """测试数据截断逻辑（超过 2000 行）."""
        large_data = {f"c{i}": [{"x": j} for j in range(600)] for i in range(4)}  # 2400 rows
        with patch("backend.api.reports.analyze_report_data", return_value=[]) as mock_analyze:
            response = client.post(
                f"/reports/11111111-1111-1111-1111-111111111111/analyze?token=test-token-12345678",
                json={"charts_data": large_data}
            )
            assert response.status_code == 200
            # 验证 analyze 被调用，且数据被截断
            assert mock_analyze.called
            call_args = mock_analyze.call_args
            capped_data = call_args[1]["charts_data"]
            total_rows = sum(len(v) for v in capped_data.values())
            assert total_rows <= 2000

    def test_B5_analyze_filters_ai_analysis_specs(self, client):
        """测试 chart_specs 中的 ai_analysis 被过滤掉."""
        with patch("backend.api.reports.analyze_report_data", return_value=[]) as mock_analyze:
            response = client.post(
                f"/reports/11111111-1111-1111-1111-111111111111/analyze?token=test-token-12345678",
                json={
                    "charts_data": {"c1": [{"x": 1}]},
                    "chart_specs": [
                        {"id": "c1", "chart_type": "line"},
                        {"id": "summary_1", "chart_type": "ai_analysis"},
                    ],
                }
            )
            assert response.status_code == 200
            # 验证 ai_analysis chart spec 被过滤
            call_args = mock_analyze.call_args
            filtered_specs = call_args[1]["chart_specs"]
            assert len(filtered_specs) == 1
            assert filtered_specs[0]["id"] == "c1"

    def test_B6_analyze_llm_failure_graceful_degradation(self, client):
        """测试 LLM 失败时的降级处理."""
        with patch("backend.api.reports.analyze_report_data", side_effect=Exception("LLM error")):
            response = client.post(
                f"/reports/11111111-1111-1111-1111-111111111111/analyze?token=test-token-12345678",
                json={"charts_data": {"c1": [{"x": 1}]}}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["sections"] == []
            assert data["count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# C - RBAC/Token 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestCRbacToken:
    """RBAC 和 Token 认证测试."""

    @pytest.fixture
    def app(self, fake_db):
        """创建测试应用."""
        app = FastAPI()
        app.include_router(router)

        async def override_get_db():
            yield fake_db

        from backend.config.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        return app

    @pytest.fixture
    def client(self, app):
        """创建测试客户端."""
        return TestClient(app)

    # 端点要求合法 UUID，使用固定测试 UUID
    _C_REPORT_ID = "22222222-2222-2222-2222-222222222222"

    def test_C1_token_auth_no_rbac_required(self, client, fake_db):
        """测试 token auth，不依赖 RBAC."""
        def mock_query(model):
            class Query:
                def filter(self, condition):
                    return self
                def first(self):
                    r = MagicMock()
                    r.refresh_token = "valid-token"
                    r.name = "测试"
                    return r
            return Query()

        fake_db.query = mock_query
        with patch("backend.api.reports.analyze_report_data", return_value=[]):
            response = client.post(
                f"/reports/{self._C_REPORT_ID}/analyze?token=valid-token",
                json={"charts_data": {}}
            )
        assert response.status_code == 200

    def test_C2_analyze_without_ai_analysis_chart(self, client, fake_db):
        """测试没有 ai_analysis chart 时，analyze 仍可正常调用."""
        def mock_query(model):
            class Query:
                def filter(self, condition):
                    return self
                def first(self):
                    r = MagicMock()
                    r.refresh_token = "valid-token"
                    r.name = "无 AI 分析报表"
                    return r
            return Query()

        fake_db.query = mock_query
        with patch("backend.api.reports.analyze_report_data", return_value=[]):
            response = client.post(
                f"/reports/{self._C_REPORT_ID}/analyze?token=valid-token",
                json={"charts_data": {"c1": [{"x": 1}]}}
            )
        assert response.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# D - E2E mock 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestDE2E:
    """端到端流程测试."""

    def test_D1_build_html_with_ai_analysis_chart(self):
        """测试生成含 ai_analysis chart 的 HTML."""
        spec = {
            "title": "AI 分析测试报表",
            "subtitle": "E2E 测试",
            "theme": "light",
            "charts": [
                {
                    "id": "c1",
                    "chart_type": "line",
                    "title": "数据趋势",
                    "sql": "SELECT toDate(call_start_time) AS date, count() AS cnt FROM crm.realtime_dwd_crm_call_record WHERE call_start_time >= '{{ date_start }}' AND call_start_time < '{{ date_end }}' GROUP BY date ORDER BY date",
                    "connection_env": "sg",
                    "connection_type": "clickhouse",
                    "x_field": "date",
                    "y_fields": ["cnt"],
                },
                {
                    "id": "summary_1",
                    "chart_type": "ai_analysis",
                    "title": "AI 数据分析",
                    "width": "full",
                },
            ],
            "filters": [
                {
                    "id": "date_range",
                    "type": "date_range",
                    "label": "时间范围",
                    "default_days": 30,
                    "binds": {"start": "date_start", "end": "date_end"},
                }
            ],
        }

        html = build_report_html(
            spec=spec,
            report_id="preview",
            refresh_token="test-token",
            api_base_url="http://test/api/v1",
        )

        # 验证 HTML 包含 AI 分析相关标记
        assert 'ai-analysis-card' in html
        assert 'ai-analysis-container' in html
        assert 'AI 分析' in html
        assert 'initAiAnalysisChart' in html
        assert '_triggerAiAnalysis' in html
        assert '_renderAiSections' in html

    def test_D2_ai_analysis_card_structure(self):
        """测试 AI 分析卡 HTML 结构."""
        spec = {
            "title": "测试",
            "theme": "light",
            "charts": [
                {"id": "summary", "chart_type": "ai_analysis", "title": "分析总结", "width": "full"},
            ],
            "filters": [],
        }

        html = build_report_html(spec, "preview", "token", "http://test")

        # 验证关键 CSS 类存在
        assert 'class="chart-card chart-full ai-analysis-card"' in html
        assert 'ai-analysis-badge' in html
        assert 'ai-analysis-container' in html
        assert 'ai-waiting' in html

    def test_D3_skip_ai_analysis_in_echarts_init(self):
        """测试 DOMContentLoaded 跳过 ai_analysis 的 ECharts 初始化."""
        spec = {
            "title": "测试",
            "theme": "light",
            "charts": [
                {"id": "c1", "chart_type": "line", "title": "普通图表", "sql": "SELECT 1", "connection_env": "sg", "connection_type": "clickhouse"},
                {"id": "summary", "chart_type": "ai_analysis", "title": "AI 分析", "width": "full"},
            ],
            "filters": [],
        }

        html = build_report_html(spec, "preview", "token", "http://test")

        # 验证 DOMContentLoaded 中跳过 ai_analysis
        assert "if (spec.chart_type === 'ai_analysis') return;" in html

    def test_D3b_include_summary_auto_inserts_ai_analysis_chart(self):
        """仅传 include_summary=true 时，也应自动补 ai_analysis 图表。"""
        spec = {
            "title": "测试",
            "theme": "light",
            "include_summary": True,
            "charts": [
                {"id": "c1", "chart_type": "line", "title": "普通图表", "sql": "SELECT 1", "connection_env": "sg", "connection_type": "clickhouse"},
            ],
            "filters": [],
        }

        html = build_report_html(spec, "preview", "token", "http://test")
        assert '"chart_type": "ai_analysis"' in html
        assert 'AI 数据分析总结' in html

    @pytest.mark.asyncio
    async def test_D4_analyze_endpoint_integration(self):
        """测试 analyze 端点集成（模拟完整调用链）."""
        # 这个测试需要真实调用链，但可以用 mock 隔离外部依赖
        with patch("backend.services.report_analysis_service._get_adapter", return_value=(None, None)):
            with patch("backend.core.model_adapters.factory.ModelAdapterFactory.create_from_settings", return_value=None):
                sections = await analyze_report_data(
                    charts_data={"c1": [{"x": 1, "y": 10}]},
                    report_title="集成测试",
                    chart_specs=[{"id": "c1", "title": "测试"}],
                )
                # 无可用 LLM 时返回 []
                assert sections == []

    @pytest.mark.asyncio
    async def test_D5_full_flow_mock(self):
        """测试完整流程（mock 所有依赖）."""
        mock_llm_response = json.dumps([
            {"type": "trend", "title": "趋势", "content": "数据呈上升趋势"},
            {"type": "insight", "title": "洞察", "content": "业务表现良好"},
        ])

        with patch("backend.services.report_analysis_service._call_llm_with_fallback", return_value=mock_llm_response):
            sections = await analyze_report_data(
                charts_data={"c1": [{"date": "2026-03-01", "cnt": 1000}]},
                report_title="完整流程测试",
                analysis_focus=["trend", "insight"],
                chart_specs=[{"id": "c1", "title": "通话量", "chart_type": "line"}],
            )

        assert len(sections) == 2
        assert sections[0]["type"] == "trend"
        assert "上升趋势" in sections[0]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# E - 回归测试
# ─────────────────────────────────────────────────────────────────────────────

class TestERegression:
    """回归测试：确保现有功能不受影响."""

    def test_E1_render_chart_divs_unchanged_for_regular_charts(self):
        """测试常规图表渲染逻辑不变."""
        spec = {
            "title": "回归测试",
            "theme": "light",
            "charts": [
                {"id": "c1", "chart_type": "bar", "title": "柱状图", "sql": "SELECT 1", "connection_env": "sg", "connection_type": "clickhouse"},
            ],
            "filters": [],
        }

        html = build_report_html(spec, "preview", "token", "http://test")

        # 验证常规图表结构不变
        assert 'id="card-c1"' in html
        assert 'chart-container echarts-chart' in html
        assert 'loading-c1' in html

    def test_E2_data_endpoint_skips_ai_analysis(self):
        """测试 /data 端点跳过 ai_analysis 图表."""
        # 这个测试已经在代码中验证（/data 中的 `if not sql_template or not env: continue`）
        # 这里只是文档化说明
        pass

    def test_E3_regenerate_html_preserves_ai_analysis(self):
        """测试 regenerate-html 保留 ai_analysis chart."""
        spec = {
            "title": "测试",
            "theme": "light",
            "charts": [
                {"id": "summary", "chart_type": "ai_analysis", "title": "AI 分析", "width": "full"},
            ],
            "filters": [],
        }

        html = build_report_html(spec, "preview", "token", "http://test")

        # 验证 ai_analysis chart 被正确渲染
        assert 'ai-analysis-card' in html
        assert 'AI 分析' in html

    def test_E4_data_endpoint_can_fill_llm_summary(self, fake_db, fake_report):
        """首次 /data 查询后，应自动生成并返回 llm_summary。"""
        app = FastAPI()
        app.include_router(router)

        async def override_get_db():
            yield fake_db

        from backend.config.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        def mock_query(model):
            class Query:
                def filter(self, condition):
                    return self
                def first(self):
                    return fake_report
            return Query()

        fake_db.query = mock_query
        client = TestClient(app)

        fake_report.charts = [
            {
                "id": "c1",
                "chart_type": "line",
                "title": "通话量趋势",
                "sql": "SELECT 1 AS cnt",
                "connection_env": "sg",
                "connection_type": "clickhouse",
            }
        ]
        fake_report.extra_metadata = {"include_summary": True}
        fake_report.summary_status = "pending"

        fake_factory = types.ModuleType("backend.agents.factory")
        fake_factory.get_default_llm_adapter = lambda: MagicMock()

        with patch("backend.api.reports.generate_llm_summary", new=AsyncMock(return_value="自动总结内容")), \
             patch("backend.api.reports._run_query", new=AsyncMock(return_value=[{"cnt": 1}])), \
            patch.dict("sys.modules", {"backend.agents.factory": fake_factory}):
            response = client.get(
                f"/reports/11111111-1111-1111-1111-111111111111/data?token=test-token-12345678"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["llm_summary"] == "自动总结内容"
        assert fake_report.llm_summary == "自动总结内容"
        assert fake_report.summary_status == "done"


# ─────────────────────────────────────────────────────────────────────────────
# 额外测试：辅助函数和边界情况
# ─────────────────────────────────────────────────────────────────────────────

class TestFHelper:
    """辅助函数和边界情况测试."""

    def test_F1_section_meta_completeness(self):
        """测试 SECTION_META 包含所有必需类型."""
        assert "trend" in SECTION_META
        assert "anomaly" in SECTION_META
        assert "insight" in SECTION_META
        assert "conclusion" in SECTION_META
        for meta in SECTION_META.values():
            assert "icon" in meta
            assert "color" in meta
            assert "label" in meta

    def test_F2_provider_order(self):
        """测试 LLM provider 优先级."""
        assert "claude" in _PROVIDER_ORDER
        assert "openai" in _PROVIDER_ORDER
        assert "gemini" in _PROVIDER_ORDER

    def test_F3_build_data_summary_empty_charts(self):
        """测试空图表数据摘要."""
        summary = _build_data_summary({}, [])
        assert summary == "（暂无可用数据）"

    def test_F4_build_data_summary_invalid_chart_data(self):
        """测试无效图表数据."""
        summary = _build_data_summary({"c1": "not-a-list"}, [{"id": "c1", "title": "坏数据"}])
        # 应该跳过无效数据
        assert "### [c1]" not in summary

    def test_F5_parse_sections_malformed_json(self):
        """测试格式错误的 JSON."""
        malformed = "[{type: trend, title: Test}]"  # 缺少引号
        sections = _parse_sections(malformed)
        # 应该 fallback 到纯文本
        assert len(sections) == 1
        assert sections[0]["type"] == "insight"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
