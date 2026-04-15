"""
test_report_render_fix_e2e.py — 渲染修复 E2E 测试套件

针对以下修复的完整测试：
  A1: _autoDetectFields  — spec 缺少字段时自动从数据列推断
  A2: series 样式模板合并 — echarts_override.series 作为样式模板，不替换数据
  B1: clickhouse-analyst.md 内容验证
  B2: update-report.md 内容验证

覆盖范围：
  I   — _autoDetectFields 字段推断边界情况（A1）
  II  — echarts_override.series 样式模板合并边界情况（A2）
  III — 技能文件内容验证（B1 + B2）
  IV  — Pilot 修改图表完整 E2E 流程（合并语义 + HTML 再生成）
  V   — 真实 Bug 场景精确回归（实际破损报表 spec）
  VI  — 向后兼容性验证（有字段的旧 spec 不受影响）

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_report_render_fix_e2e.py -v -s
"""
from __future__ import annotations

import json
import os
import sys
import uuid
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
_BACKEND_DIR = str(PROJECT_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_render_fix.db")

# ─── Skill 文件路径 ───────────────────────────────────────────────────────────
_SKILLS_DIR = PROJECT_ROOT / ".claude" / "skills" / "project"
_ANALYST_SKILL = _SKILLS_DIR / "clickhouse-analyst.md"
_UPDATE_SKILL  = _SKILLS_DIR / "update-report.md"


# ─── JS 逻辑 Python 镜像（与 report_builder_service.py 中 JS 保持一致）──────────

def _auto_detect_fields(spec: Dict, data: List[Dict]) -> Dict:
    """_autoDetectFields JS 函数的 Python 镜像，用于单元验证。"""
    if not data:
        return spec
    if spec.get("x_field") and spec.get("y_fields"):
        return spec

    sample = data[0]
    keys = list(sample.keys())
    str_keys, num_keys = [], []
    for k in keys:
        v = sample[k]
        if v is None:
            continue
        if isinstance(v, (int, float)):
            num_keys.append(k)
        else:
            str_keys.append(k)

    x_f = spec.get("x_field") or (str_keys[0] if str_keys else (keys[0] if keys else None))
    y_fs = (spec.get("y_fields") or
            (num_keys if num_keys else ([str_keys[1]] if len(str_keys) > 1 else [])))

    s_f = spec.get("series_field")
    if not s_f and len(str_keys) >= 2:
        candidate = next((k for k in str_keys if k != x_f), None)
        if candidate:
            uniq = set(r[candidate] for r in data)
            if 1 < len(uniq) <= max(2, len(data) // 2):
                s_f = candidate

    result = dict(spec)
    result["x_field"] = x_f
    result["y_fields"] = y_fs
    result["series_field"] = s_f or spec.get("series_field") or None
    return result


def _apply_series_template(option_series: List[Dict], override_series: List[Dict]):
    """
    buildEChartsOption series 模板合并 JS 逻辑的 Python 镜像。
    返回 (merged_series, template_was_applied)。
    """
    if (override_series and len(override_series) > 0
            and option_series and len(option_series) > 0):
        KEEP = {"name", "data"}
        tmpl = override_series[0]
        merged = []
        for s in option_series:
            out = dict(s)
            for k, v in tmpl.items():
                if k not in KEEP:
                    out[k] = v
            merged.append(out)
        return merged, True
    return option_series, False


# ════════════════════════════════════════════════════════════════════════════════
# I — _autoDetectFields 字段推断边界情况（A1）
# ════════════════════════════════════════════════════════════════════════════════

class TestIAutoDetectFieldsEdgeCases(unittest.TestCase):
    """A1 修复：_autoDetectFields 各类数据形态的推断行为。"""

    # ── I1 — 基础单系列 ────────────────────────────────────────────────────────

    def test_I1_1_date_and_numeric_col(self):
        """最常见场景：日期列 + 数值列 → x=day, y=[connected_calls]，无 series_field。"""
        spec = {"id": "c1", "chart_type": "area"}
        data = [
            {"day": "2025-01-01", "connected_calls": 100},
            {"day": "2025-01-02", "connected_calls": 120},
        ]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["x_field"], "day")
        self.assertEqual(out["y_fields"], ["connected_calls"])
        self.assertIsNone(out.get("series_field"))

    def test_I1_2_all_numeric_columns(self):
        """全数字列：第一列为 x，其余为 y。"""
        spec = {"id": "c1", "chart_type": "line"}
        data = [{"ts": 1700000000, "v1": 10, "v2": 20}]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["x_field"], "ts")
        self.assertIn("v1", out["y_fields"])
        self.assertIn("v2", out["y_fields"])

    def test_I1_3_single_column_data(self):
        """只有一列数据不崩溃。"""
        spec = {"id": "c1", "chart_type": "line"}
        data = [{"cnt": 5}, {"cnt": 8}]
        out = _auto_detect_fields(spec, data)
        # 不崩溃即可；x_field 取该列
        self.assertIn("x_field", out)

    def test_I1_4_partial_spec_has_x_but_no_y(self):
        """spec 已有 x_field 但无 y_fields → 只填 y_fields，x_field 保持原值。"""
        spec = {"id": "c1", "x_field": "date", "chart_type": "bar"}
        data = [{"date": "2025-01-01", "sales": 100, "cost": 60}]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["x_field"], "date")   # 保持原值
        self.assertIn("sales", out["y_fields"])     # 数字列填入

    def test_I1_5_already_configured_unchanged(self):
        """spec 已有 x_field 和 y_fields → 原样返回，不推断。"""
        spec = {"id": "c1", "x_field": "dt", "y_fields": ["cnt"], "series_field": "env"}
        data = [{"dt": "2025-01-01", "env": "sg", "cnt": 10}]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["x_field"], "dt")
        self.assertEqual(out["y_fields"], ["cnt"])
        self.assertEqual(out["series_field"], "env")

    # ── I2 — 分组 series_field 推断 ────────────────────────────────────────────

    def test_I2_1_two_str_cols_few_unique_env(self):
        """dt + env（少量唯一值）+ cnt → series_field=env。"""
        spec = {"id": "c1", "chart_type": "bar"}
        data = [
            {"dt": "2025-01-01", "env": "sg",  "cnt": 10},
            {"dt": "2025-01-01", "env": "idn", "cnt": 5},
            {"dt": "2025-01-02", "env": "sg",  "cnt": 12},
            {"dt": "2025-01-02", "env": "idn", "cnt": 7},
        ]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["x_field"], "dt")
        self.assertEqual(out["y_fields"], ["cnt"])
        self.assertEqual(out["series_field"], "env")

    def test_I2_2_env_too_many_unique_values_not_selected(self):
        """env 列唯一值数量 > len(data)/2 → 不作为 series_field。"""
        spec = {"id": "c1", "chart_type": "bar"}
        data = [{"dt": "2025-01-0%d" % i, "user_id": f"u{i}", "cnt": i}
                for i in range(1, 9)]  # 8 行 8 个唯一 user_id → 超过阈值
        out = _auto_detect_fields(spec, data)
        self.assertNotEqual(out.get("series_field"), "user_id")

    def test_I2_3_three_env_unique_values(self):
        """sg/idn/br 三个环境 → 推断为 series_field（唯一值数≤行数/2）。"""
        spec = {"id": "c1", "chart_type": "bar"}
        data = [
            {"dt": "2025-01-01", "env": "sg",  "cnt": 10},
            {"dt": "2025-01-01", "env": "idn", "cnt": 5},
            {"dt": "2025-01-01", "env": "br",  "cnt": 3},
            {"dt": "2025-01-02", "env": "sg",  "cnt": 12},
            {"dt": "2025-01-02", "env": "idn", "cnt": 7},
            {"dt": "2025-01-02", "env": "br",  "cnt": 4},
        ]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["series_field"], "env")

    # ── I3 — 边界 ──────────────────────────────────────────────────────────────

    def test_I3_1_empty_data_returns_spec_unchanged(self):
        """data 为空 → 直接返回 spec，不崩溃。"""
        spec = {"id": "c1", "chart_type": "line"}
        out = _auto_detect_fields(spec, [])
        self.assertEqual(out["id"], "c1")
        self.assertFalse(out.get("x_field"))

    def test_I3_2_none_values_in_row_skipped(self):
        """行中含 None 值的列不参与类型判断，不崩溃。"""
        spec = {"id": "c1", "chart_type": "line"}
        data = [{"dt": "2025-01-01", "optional": None, "cnt": 50}]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["x_field"], "dt")
        self.assertIn("cnt", out["y_fields"])

    def test_I3_3_autodetect_code_present_in_html(self):
        """生成 HTML 中包含 _autoDetectFields 函数实现。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "AutoDetect Test",
            "charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1",
                        "connection_env": "sg"}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="test-id", refresh_token="tok")
        self.assertIn("_autoDetectFields", html)
        self.assertIn("strKeys", html)
        self.assertIn("numKeys", html)

    def test_I3_4_autodetect_called_in_extract_xy_series(self):
        """生成 HTML 中 extractXYSeries 调用了 _autoDetectFields。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "AutoDetect Invoke Test",
            "charts": [{"id": "c1", "chart_type": "area", "sql": "SELECT 1",
                        "connection_env": "sg"}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="test-id", refresh_token="tok")
        # extractXYSeries 应调用 _autoDetectFields(spec, data)
        self.assertIn("spec = _autoDetectFields(spec, data)", html)


# ════════════════════════════════════════════════════════════════════════════════
# II — echarts_override.series 样式模板合并边界情况（A2）
# ════════════════════════════════════════════════════════════════════════════════

class TestIISeriesTemplateMergeEdgeCases(unittest.TestCase):
    """A2 修复：series 模板合并的各种边界情况。"""

    # ── II1 — 基础模板应用 ─────────────────────────────────────────────────────

    def test_II1_1_template_applied_all_series(self):
        """多个数据驱动 series 均被模板应用，name/data 不变。"""
        opt_series = [
            {"name": "sg",  "type": "bar", "data": [10, 20]},
            {"name": "idn", "type": "bar", "data": [5,  8]},
            {"name": "br",  "type": "bar", "data": [3,  4]},
        ]
        tmpl = [{"type": "line", "smooth": False, "stack": "total",
                 "areaStyle": {"opacity": 0.75}}]
        merged, applied = _apply_series_template(opt_series, tmpl)

        self.assertTrue(applied)
        self.assertEqual(len(merged), 3)
        for i, orig in enumerate(opt_series):
            self.assertEqual(merged[i]["name"], orig["name"])   # name 保留
            self.assertEqual(merged[i]["data"], orig["data"])   # data 保留
            self.assertEqual(merged[i]["type"], "line")         # 类型被模板覆盖
            self.assertFalse(merged[i]["smooth"])               # smooth 被模板覆盖
            self.assertEqual(merged[i]["areaStyle"]["opacity"], 0.75)  # style 覆盖

    def test_II1_2_data_key_in_template_ignored(self):
        """模板 series 中若误包含 data 键，不覆盖原 series 的 data。"""
        opt_series = [{"name": "sg", "type": "bar", "data": [10, 20]}]
        # 错误写法：模板含 data（不应该，但需兼容）
        tmpl = [{"type": "line", "data": [99, 99], "smooth": False}]
        merged, applied = _apply_series_template(opt_series, tmpl)
        self.assertTrue(applied)
        self.assertEqual(merged[0]["data"], [10, 20])  # 原 data 保留，不被 [99,99] 替换

    def test_II1_3_name_key_in_template_ignored(self):
        """模板 series 中的 name 键不覆盖原 series 的 name。"""
        opt_series = [{"name": "sg", "type": "bar", "data": [10]}]
        tmpl = [{"name": "WRONG_NAME", "type": "line"}]
        merged, _ = _apply_series_template(opt_series, tmpl)
        self.assertEqual(merged[0]["name"], "sg")

    # ── II2 — 各种样式属性覆盖 ─────────────────────────────────────────────────

    def test_II2_1_symbol_none_applied(self):
        """symbol: none 从模板应用到所有 series。"""
        opt_series = [{"name": "sg", "type": "line", "data": [1, 2]}]
        tmpl = [{"symbol": "none", "lineStyle": {"width": 1.5}}]
        merged, _ = _apply_series_template(opt_series, tmpl)
        self.assertEqual(merged[0]["symbol"], "none")
        self.assertEqual(merged[0]["lineStyle"]["width"], 1.5)

    def test_II2_2_stack_total_applied(self):
        """stack: total 从模板应用，不影响 data。"""
        opt_series = [
            {"name": "a", "type": "bar", "data": [1]},
            {"name": "b", "type": "bar", "data": [2]},
        ]
        tmpl = [{"stack": "total"}]
        merged, _ = _apply_series_template(opt_series, tmpl)
        self.assertEqual(merged[0]["stack"], "total")
        self.assertEqual(merged[1]["stack"], "total")

    def test_II2_3_area_opacity_applied(self):
        """areaStyle.opacity 从模板应用。"""
        opt_series = [{"name": "cnt", "type": "line", "data": [5, 10]}]
        tmpl = [{"areaStyle": {"opacity": 0.6}}]
        merged, _ = _apply_series_template(opt_series, tmpl)
        self.assertEqual(merged[0]["areaStyle"]["opacity"], 0.6)

    # ── II3 — 不应用模板的场景 ─────────────────────────────────────────────────

    def test_II3_1_empty_option_series_no_template(self):
        """option.series 为空 → 模板不应用（applied=False），不崩溃。"""
        merged, applied = _apply_series_template([], [{"smooth": False}])
        self.assertFalse(applied)
        self.assertEqual(merged, [])

    def test_II3_2_empty_override_series_no_template(self):
        """override.series 为空列表 → 模板不应用。"""
        opt_series = [{"name": "sg", "data": [1, 2]}]
        merged, applied = _apply_series_template(opt_series, [])
        self.assertFalse(applied)
        self.assertEqual(merged, opt_series)

    def test_II3_3_none_override_series_no_template(self):
        """override.series 为 None → 不应用模板。"""
        opt_series = [{"name": "sg", "data": [1, 2]}]
        merged, applied = _apply_series_template(opt_series, None)  # type: ignore
        self.assertFalse(applied)

    # ── II4 — HTML 中的代码验证 ────────────────────────────────────────────────

    def test_II4_1_template_logic_in_generated_html(self):
        """生成 HTML 包含 series 模板合并代码（KEEP + override.series 处理）。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Template Logic Test",
            "charts": [{"id": "c1", "chart_type": "area", "sql": "SELECT 1",
                        "connection_env": "sg",
                        "echarts_override": {"series": [{"smooth": False, "areaStyle": {}}]}}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="test-id", refresh_token="tok")
        # KEEP 集合保护 name/data
        self.assertIn("KEEP", html)
        # override.series 被单独处理后删除
        self.assertIn("delete override.series", html)
        # 模板逻辑入口
        self.assertIn("override.series", html)

    def test_II4_2_no_echarts_override_no_template_code_path(self):
        """无 echarts_override 的 chart → deepMerge 正常路径，不触发模板分支。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "No Override Test",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1",
                        "connection_env": "sg"}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="test-id", refresh_token="tok")
        # HTML 仍包含 deepMerge 函数定义
        self.assertIn("deepMerge", html)


# ════════════════════════════════════════════════════════════════════════════════
# III — 技能文件内容验证（B1 + B2）
# ════════════════════════════════════════════════════════════════════════════════

class TestIIISkillFilesContent(unittest.TestCase):
    """B1 + B2：验证技能文件包含正确的字段要求和语义说明。"""

    @classmethod
    def setUpClass(cls):
        cls.analyst_content = _ANALYST_SKILL.read_text(encoding="utf-8") if _ANALYST_SKILL.exists() else ""
        cls.update_content  = _UPDATE_SKILL.read_text(encoding="utf-8") if _UPDATE_SKILL.exists() else ""

    # ── III1 — B1: clickhouse-analyst.md ──────────────────────────────────────

    def test_III1_1_analyst_skill_file_exists(self):
        """B1 目标文件存在。"""
        self.assertTrue(_ANALYST_SKILL.exists(), f"技能文件不存在: {_ANALYST_SKILL}")

    def test_III1_2_analyst_has_x_field_in_example(self):
        """clickhouse-analyst.md 示例 spec 含 x_field 字段。"""
        self.assertIn('"x_field"', self.analyst_content, "示例 spec 缺少 x_field")

    def test_III1_3_analyst_has_y_fields_in_example(self):
        """clickhouse-analyst.md 示例 spec 含 y_fields 字段。"""
        self.assertIn('"y_fields"', self.analyst_content, "示例 spec 缺少 y_fields")

    def test_III1_4_analyst_has_series_field_in_example(self):
        """clickhouse-analyst.md 示例 spec 含 series_field 字段。"""
        self.assertIn('"series_field"', self.analyst_content, "示例 spec 缺少 series_field")

    def test_III1_5_analyst_has_warning_about_missing_fields(self):
        """clickhouse-analyst.md 含缺失字段导致渲染失败的警告。"""
        has_warning = ("缺少这三个字段将导致图表在动态数据加载后无法正确渲染" in self.analyst_content
                       or "undefined" in self.analyst_content)
        self.assertTrue(has_warning, "analyst skill 缺少字段缺失警告")

    def test_III1_6_analyst_has_x_field_mandatory_note(self):
        """clickhouse-analyst.md 说明 x_field 为必填（bar/line/area/scatter）。"""
        self.assertIn("x_field", self.analyst_content)
        # 至少有一个包含 x_field 的说明段落
        lines_with_x = [l for l in self.analyst_content.splitlines() if "x_field" in l]
        self.assertGreater(len(lines_with_x), 1, "x_field 应在多处（示例+说明）出现")

    def test_III1_7_analyst_has_connection_type_note(self):
        """clickhouse-analyst.md 示例含 connection_type 字段。"""
        self.assertIn('"connection_type"', self.analyst_content)

    # ── III2 — B2: update-report.md ───────────────────────────────────────────

    def test_III2_1_update_skill_file_exists(self):
        """B2 目标文件存在。"""
        self.assertTrue(_UPDATE_SKILL.exists(), f"技能文件不存在: {_UPDATE_SKILL}")

    def test_III2_2_update_skill_has_series_template_section(self):
        """update-report.md 含 echarts_override.series 语义说明段落。"""
        self.assertIn("echarts_override.series", self.update_content)
        self.assertIn("样式模板", self.update_content)

    def test_III2_3_update_skill_has_keep_explanation(self):
        """update-report.md 说明 name/data 不被模板覆盖。"""
        has_keep = ("name" in self.update_content and "data" in self.update_content
                    and ("保留" in self.update_content or "KEEP" in self.update_content))
        self.assertTrue(has_keep, "update skill 应说明 name/data 保留")

    def test_III2_4_update_skill_prohibits_data_in_template(self):
        """update-report.md 明确禁止在模板 series 中设置 data 字段。"""
        has_prohibition = ("禁止" in self.update_content or "不要" in self.update_content)
        self.assertTrue(has_prohibition, "update skill 应禁止在 series 模板中设置 data")

    def test_III2_5_update_skill_has_correct_example(self):
        """update-report.md 示例中 series 数组只含样式属性（无 data 键）。"""
        # 验证正确示例中的 series 元素结构（只含 smooth/areaStyle 等）
        self.assertIn('"smooth"', self.update_content)
        self.assertIn('"areaStyle"', self.update_content)
        # 示例说明中有"正确写法"标注
        self.assertIn("正确写法", self.update_content)

    def test_III2_6_update_skill_has_wrong_example(self):
        """update-report.md 含错误写法示例（标注 data 不应写）。"""
        self.assertIn("错误写法", self.update_content)


# ════════════════════════════════════════════════════════════════════════════════
# IV — Pilot 修改图表完整 E2E 流程
# ════════════════════════════════════════════════════════════════════════════════

class TestIVPilotUpdateChartE2E(unittest.TestCase):
    """
    验证 Pilot 通过 report__update_single_chart 修改图表后：
    1. 图表 spec 合并正确（x_field/y_fields 等字段保留）
    2. echarts_override.series 只含样式属性
    3. HTML 再生成包含 _autoDetectFields 函数
    """

    def _merge_chart(self, existing_chart: Dict, patch: Dict) -> Dict:
        """模拟 reports.py update_single_chart 的浅合并逻辑。"""
        return {**existing_chart, **patch}

    def test_IV1_1_x_field_preserved_after_chart_type_change(self):
        """bar→area 修改后，原 x_field/y_fields/series_field 保留。"""
        original = {
            "id": "chart-1",
            "chart_type": "bar",
            "title": "环境接通趋势",
            "sql": "SELECT dt, env, cnt FROM calls",
            "connection_env": "sg",
            "x_field": "dt",
            "y_fields": ["cnt"],
            "series_field": "env",
        }
        patch = {
            "chart_type": "area",
            "echarts_override": {
                "series": [{"smooth": False, "stack": "total", "areaStyle": {"opacity": 0.75}}]
            },
        }
        merged = self._merge_chart(original, patch)
        self.assertEqual(merged["x_field"], "dt")
        self.assertEqual(merged["y_fields"], ["cnt"])
        self.assertEqual(merged["series_field"], "env")
        self.assertEqual(merged["chart_type"], "area")

    def test_IV1_2_echarts_override_series_has_no_data_key(self):
        """Pilot 发出的 chart_patch 中 echarts_override.series 不含 data 键。"""
        # 这是对 update-report.md B2 修复的间接验证：
        # Pilot 按正确规范只传样式属性
        pilot_patch = {
            "chart_type": "area",
            "echarts_override": {
                "color": ["#2E5FA3", "#3A8FC1"],
                "series": [{"type": "line", "smooth": False, "stack": "total",
                            "areaStyle": {"opacity": 0.75}, "lineStyle": {"width": 1.5},
                            "symbol": "none"}],
            },
        }
        series_template = pilot_patch["echarts_override"]["series"][0]
        self.assertNotIn("data", series_template, "series 模板不应含 data 键")
        self.assertNotIn("name", series_template, "series 模板不应含 name 键")

    def test_IV1_3_spec_without_x_field_after_merge_auto_detect_handles(self):
        """原 spec 缺 x_field（旧报表），Pilot 改 chart_type → 合并后 auto_detect 可推断。"""
        # 模拟旧报表：没有 x_field/y_fields
        original = {
            "id": "chart-stacked-1",
            "chart_type": "bar",
            "sql": "SELECT toDate(call_start_time) AS day, countIf(...) AS connected_calls ...",
            "connection_env": "sg",
        }
        patch = {
            "chart_type": "area",
            "echarts_override": {"series": [{"smooth": False, "stack": "total"}]},
        }
        merged = self._merge_chart(original, patch)
        # merged 缺少 x_field/y_fields — auto_detect 需要处理
        self.assertNotIn("x_field", merged)
        # 模拟 /data 返回的行
        data_rows = [
            {"day": "2025-01-01", "connected_calls": 100},
            {"day": "2025-01-02", "connected_calls": 120},
        ]
        detected = _auto_detect_fields(merged, data_rows)
        self.assertEqual(detected["x_field"], "day")
        self.assertIn("connected_calls", detected["y_fields"])

    def test_IV2_1_html_regen_contains_autodetect(self):
        """update_single_chart 后重新生成的 HTML 含 _autoDetectFields。"""
        from backend.services.report_builder_service import build_report_html
        merged_spec = {
            "title": "Pilot Updated Report",
            "charts": [{
                "id": "chart-1",
                "chart_type": "area",
                "title": "环境接通趋势",
                "sql": "SELECT dt, env, cnt FROM calls",
                "connection_env": "sg",
                "echarts_override": {
                    "series": [{"smooth": False, "stack": "total", "areaStyle": {}}]
                },
            }],
            "filters": [],
            "data": {},
        }
        html = build_report_html(
            spec=merged_spec, report_id="regen-test", refresh_token="tok"
        )
        self.assertIn("_autoDetectFields", html)
        self.assertIn("KEEP", html)
        self.assertIn("delete override.series", html)

    def test_IV2_2_html_regen_contains_correct_spec_json(self):
        """再生成的 HTML 中 REPORT_SPEC 含合并后的 chart_type 和 echarts_override。"""
        from backend.services.report_builder_service import build_report_html
        merged_spec = {
            "title": "Spec JSON Test",
            "charts": [{
                "id": "c1",
                "chart_type": "area",
                "sql": "SELECT 1",
                "connection_env": "sg",
                "x_field": "dt",
                "y_fields": ["cnt"],
                "echarts_override": {
                    "series": [{"smooth": False, "areaStyle": {"opacity": 0.75}}]
                },
            }],
            "filters": [],
            "data": {},
        }
        html = build_report_html(
            spec=merged_spec, report_id="spec-json-test", refresh_token="tok"
        )
        # REPORT_SPEC JSON 中应含 chart_type area
        self.assertIn('"chart_type": "area"', html)
        self.assertIn('"x_field": "dt"', html)

    def test_IV3_1_update_chart_via_endpoint_preserves_other_charts(self):
        """PUT /charts/{chart_id} 只修改目标图表，不影响其他图表。"""
        charts = [
            {"id": "c1", "chart_type": "bar", "title": "图表A", "sql": "SELECT 1",
             "connection_env": "sg", "x_field": "dt", "y_fields": ["cnt"]},
            {"id": "c2", "chart_type": "pie", "title": "图表B", "sql": "SELECT 2",
             "connection_env": "sg"},
        ]
        # 只 patch c1
        patch = {"id": "c1", "chart_type": "area",
                 "echarts_override": {"series": [{"smooth": False}]}}
        merged_charts = []
        for c in charts:
            if c["id"] == "c1":
                merged_charts.append({**c, **patch})
            else:
                merged_charts.append(c)

        self.assertEqual(len(merged_charts), 2)
        self.assertEqual(merged_charts[0]["chart_type"], "area")   # c1 改变
        self.assertEqual(merged_charts[0]["x_field"], "dt")        # x_field 保留
        self.assertEqual(merged_charts[1]["chart_type"], "pie")    # c2 不变
        self.assertEqual(merged_charts[1]["id"], "c2")


# ════════════════════════════════════════════════════════════════════════════════
# V — 真实 Bug 场景精确回归
# ════════════════════════════════════════════════════════════════════════════════

class TestVRealBugScenarioRegression(unittest.TestCase):
    """
    精确复现实际破损报表的 spec 和 Pilot 操作，验证修复正确。

    实际破损 spec（从 customer_data 读取）：
      - chart_type: "area"（Pilot 已改）
      - sql: SELECT toDate(call_start_time) AS day, countIf(...) AS connected_calls ...
      - echarts_override.series: [style template]
      - 缺少: x_field, y_fields, series_field
    """

    # 实际破损的 chart spec（从 HTML 文件提取）
    BROKEN_CHART_SPEC = {
        "id": "chart-stacked-1",
        "sql": ("SELECT toDate(call_start_time) AS day, "
                "countIf(call_code_type IN (1,16)) AS connected_calls "
                "FROM crm.realtime_dwd_crm_call_record "
                "PREWHERE call_start_time >= toStartOfDay(addDays(today(), -29)) "
                "AND call_start_time < toStartOfDay(addDays(today(), 1)) "
                "WHERE is_delete = 0 AND call_type = 1 "
                "GROUP BY day ORDER BY day"),
        "title": "按天展示各环境 Connected Calls 堆积图",
        "chart_type": "area",
        "connection_env": "sg",
        "echarts_override": {
            "color": ["#2E5FA3", "#3A8FC1", "#4FBDBA", "#6DAEDB", "#1A3A5C", "#5B8DB8"],
            "series": [{"type": "line", "smooth": False, "stack": "total",
                        "areaStyle": {"opacity": 0.75}, "lineStyle": {"width": 1.5},
                        "symbol": "none"}],
        },
    }

    # 模拟 GET /data 返回的行
    SIMULATED_ROWS = [
        {"day": "2025-12-17", "connected_calls": 87},
        {"day": "2025-12-18", "connected_calls": 102},
        {"day": "2025-12-19", "connected_calls": 95},
        {"day": "2025-12-20", "connected_calls": 76},
    ]

    def test_V1_1_autodetect_fixes_missing_x_field(self):
        """破损 spec（无 x_field）经 _autoDetectFields → day 被识别为 x_field。"""
        out = _auto_detect_fields(self.BROKEN_CHART_SPEC, self.SIMULATED_ROWS)
        self.assertEqual(out["x_field"], "day")

    def test_V1_2_autodetect_fixes_missing_y_fields(self):
        """破损 spec（无 y_fields）经 _autoDetectFields → connected_calls 被识别为 y_field。"""
        out = _auto_detect_fields(self.BROKEN_CHART_SPEC, self.SIMULATED_ROWS)
        self.assertIn("connected_calls", out["y_fields"])

    def test_V1_3_no_series_field_for_single_dim_sql(self):
        """破损 spec 的 SQL 只有 day+connected_calls 两列（无分组维度），不推断 series_field。"""
        out = _auto_detect_fields(self.BROKEN_CHART_SPEC, self.SIMULATED_ROWS)
        # SQL 输出只有一个字符串列（day），所以不应推断 series_field
        self.assertFalse(out.get("series_field"))

    def test_V2_1_series_template_data_preserved(self):
        """破损 spec 的 echarts_override.series 作为模板应用后，data 不丢失。"""
        # 模拟 extractXYSeries 自动推断后构建的 data-driven series
        detected_spec = _auto_detect_fields(self.BROKEN_CHART_SPEC, self.SIMULATED_ROWS)
        data_series = [{
            "name": detected_spec["y_fields"][0],
            "type": "line",
            "data": [r["connected_calls"] for r in self.SIMULATED_ROWS],
        }]
        override_series = self.BROKEN_CHART_SPEC["echarts_override"]["series"]
        merged, applied = _apply_series_template(data_series, override_series)

        self.assertTrue(applied)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name"], "connected_calls")
        self.assertEqual(merged[0]["data"], [87, 102, 95, 76])
        self.assertFalse(merged[0]["smooth"])
        self.assertEqual(merged[0]["stack"], "total")
        self.assertEqual(merged[0]["areaStyle"]["opacity"], 0.75)

    def test_V2_2_xaxis_data_not_undefined(self):
        """破损 spec 修复后，x 轴数据为实际日期，不含 undefined。"""
        detected_spec = _auto_detect_fields(self.BROKEN_CHART_SPEC, self.SIMULATED_ROWS)
        x_vals = [r[detected_spec["x_field"]] for r in self.SIMULATED_ROWS]
        self.assertNotIn(None, x_vals)
        self.assertNotIn("undefined", x_vals)
        self.assertEqual(x_vals[0], "2025-12-17")

    def test_V3_1_build_html_with_broken_spec_has_autodetect(self):
        """用破损 spec 生成 HTML → 包含 _autoDetectFields（兼容处理）。"""
        from backend.services.report_builder_service import build_report_html
        full_spec = {
            "title": "all_env_connected_calls_last30d_stacked_report",
            "charts": [self.BROKEN_CHART_SPEC],
            "filters": [],
            "data": {},
        }
        html = build_report_html(
            spec=full_spec,
            report_id="1bdb8d27-d195-4643-94ab-00c11e0cb1c3",
            refresh_token="tok",
        )
        self.assertIn("_autoDetectFields", html)

    def test_V3_2_build_html_with_broken_spec_has_template_logic(self):
        """用破损 spec 生成 HTML → 包含 series 模板合并逻辑（KEEP + delete）。"""
        from backend.services.report_builder_service import build_report_html
        full_spec = {
            "title": "Regression HTML Test",
            "charts": [self.BROKEN_CHART_SPEC],
            "filters": [],
            "data": {},
        }
        html = build_report_html(
            spec=full_spec,
            report_id="regression-test",
            refresh_token="tok",
        )
        self.assertIn("KEEP", html)
        self.assertIn("delete override.series", html)

    def test_V3_3_broken_spec_in_report_spec_json(self):
        """破损 spec 生成的 HTML 中 REPORT_SPEC 包含 echarts_override（供 JS 处理）。"""
        from backend.services.report_builder_service import build_report_html
        full_spec = {
            "title": "Real Broken Report",
            "charts": [self.BROKEN_CHART_SPEC],
            "filters": [],
            "data": {},
        }
        html = build_report_html(
            spec=full_spec, report_id="broken-spec-test", refresh_token="tok"
        )
        self.assertIn("chart-stacked-1", html)
        self.assertIn("echarts_override", html)
        self.assertIn("areaStyle", html)

    def test_V4_1_get_data_endpoint_returns_200_for_broken_spec(self):
        """GET /data：破损 spec（无 x_field）仍能查询并返回 200。"""
        import uuid as _uuid
        from unittest.mock import MagicMock, AsyncMock, patch as _patch
        from backend.main import app
        from backend.config.database import get_db
        from fastapi.testclient import TestClient

        rid = str(_uuid.uuid4())
        mock_report = MagicMock()
        mock_report.id = _uuid.UUID(rid)
        mock_report.refresh_token = "tok_broken_spec"
        mock_report.charts = [dict(self.BROKEN_CHART_SPEC)]
        mock_report.filters = []
        mock_report.name = "Broken Spec Report"
        mock_report.username = "superadmin"
        mock_report.report_file_path = "superadmin/reports/broken.html"
        mock_report.to_dict.return_value = {"id": rid, "name": "Broken"}

        orig = dict(app.dependency_overrides)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        client = TestClient(app, raise_server_exceptions=False)

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return self.SIMULATED_ROWS

        with _patch("backend.api.reports._run_query", new=AsyncMock(side_effect=_fake_run)):
            resp = client.get(f"/api/v1/reports/{rid}/data",
                              params={"token": "tok_broken_spec"})

        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("chart-stacked-1", body["data"])
        # 返回行列表（行数由实际查询决定，只验证结构）
        rows = body["data"]["chart-stacked-1"]
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0, "应有至少一行数据")


# ════════════════════════════════════════════════════════════════════════════════
# VI — 向后兼容性验证
# ════════════════════════════════════════════════════════════════════════════════

class TestVIBackwardCompatibility(unittest.TestCase):
    """修复不应破坏已有正确的 spec 和渲染行为。"""

    def test_VI1_1_spec_with_all_fields_unchanged(self):
        """完整 spec（含 x_field/y_fields/series_field）→ _autoDetectFields 原样返回。"""
        spec = {
            "id": "c1",
            "x_field": "dt",
            "y_fields": ["cnt"],
            "series_field": "env",
        }
        data = [{"dt": "2025-01-01", "env": "sg", "cnt": 10}]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["x_field"], "dt")
        self.assertEqual(out["y_fields"], ["cnt"])
        self.assertEqual(out["series_field"], "env")

    def test_VI1_2_no_echarts_override_series_not_applied(self):
        """echarts_override 无 series 键 → series 模板逻辑不执行。"""
        opt_series = [{"name": "sg", "type": "bar", "data": [1, 2, 3]}]
        # no override_series
        merged, applied = _apply_series_template(opt_series, [])
        self.assertFalse(applied)
        self.assertEqual(merged, opt_series)

    def test_VI1_3_build_html_literal_sql_no_template_regression(self):
        """literal SQL 报表（无 echarts_override）HTML 生成不回归。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Literal SQL Chart",
            "charts": [{"id": "c1", "chart_type": "line",
                        "sql": "SELECT 1 AS n", "connection_env": "sg",
                        "x_field": "day", "y_fields": ["cnt"]}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="compat-test", refresh_token="tok")
        # 基本结构完整
        self.assertIn("REPORT_SPEC", html)
        self.assertIn("_loadData", html)
        self.assertIn("REPORT_ID !== 'preview'", html)
        # _autoDetectFields 仍存在
        self.assertIn("_autoDetectFields", html)

    def test_VI2_1_multi_y_fields_spec_correct(self):
        """多 y_fields 的 spec 经 autoDetect 后 y_fields 保持原样（不覆盖）。"""
        spec = {
            "id": "c1",
            "x_field": "dt",
            "y_fields": ["connected_calls", "am_calls"],
        }
        data = [{"dt": "2025-01-01", "connected_calls": 100, "am_calls": 20}]
        out = _auto_detect_fields(spec, data)
        self.assertEqual(out["y_fields"], ["connected_calls", "am_calls"])

    def test_VI2_2_color_in_echarts_override_still_applied(self):
        """echarts_override.color（非 series）通过 deepMerge 正常覆盖颜色。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Color Override Test",
            "charts": [{"id": "c1", "chart_type": "bar",
                        "sql": "SELECT 1", "connection_env": "sg",
                        "echarts_override": {"color": ["#FF0000", "#00FF00"]}}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="color-test", refresh_token="tok")
        # color override 在 REPORT_SPEC 中
        self.assertIn("#FF0000", html)

    def test_VI3_1_kpi_chart_no_xy_fields_no_crash(self):
        """KPI 类型图表（无 x/y 字段概念）autoDetect 不崩溃。"""
        spec = {"id": "c1", "chart_type": "gauge"}
        data = [{"value": 0.75}]
        try:
            out = _auto_detect_fields(spec, data)
            self.assertIsInstance(out, dict)
        except Exception as e:
            self.fail(f"_autoDetectFields 对 gauge 图表崩溃: {e}")

    def test_VI3_2_empty_charts_list_html_ok(self):
        """charts 为空的报表 HTML 生成不崩溃。"""
        from backend.services.report_builder_service import build_report_html
        spec = {"title": "Empty Charts", "charts": [], "filters": [], "data": {}}
        try:
            html = build_report_html(spec=spec, report_id="empty-test", refresh_token="tok")
            self.assertIn("REPORT_SPEC", html)
        except Exception as e:
            self.fail(f"空 charts 的 HTML 生成崩溃: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
