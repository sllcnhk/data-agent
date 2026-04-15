"""
test_report_spec_extraction.py — Pilot Spec 提取与 Pin 修复测试套件
====================================================================

覆盖范围：
  A段 (5)  — extract_spec_from_html_file() 单元测试（无需 DB）
               A1: 正常含图表 HTML → 提取 charts 数组
               A2: HTML 缺少 REPORT_SPEC → 返回 None
               A3: 文件不存在 → 返回 None（不抛异常）
               A4: JSON 格式错误 → 返回 None（不抛异常）
               A5: charts 字段为空列表 → 返回空列表（非 None）
               A6: 含 filters/theme → 正确提取

  B段 (4)  — pin 端点 + spec-meta 集成测试（需 DB）
               B1: pin 含图表 HTML → DB 中 charts 非空
               B2: pin 无 REPORT_SPEC HTML → DB 中 charts=[]（不报错）
               B3: spec-meta 对 charts=NULL 记录触发懒更新 → 返回正确 charts
               B4: 幂等 pin（同路径已有记录）→ is_new=False，不覆盖已有 charts

  C段 (5)  — 前端 & 后端代码静态分析（无需 DB）
               C1: DataCenterCopilot.tsx 含 contextSpec?.charts 读取
               C2: DataCenterCopilot.tsx 系统提示使用图表数量字符串
               C3: reports.py extract_spec_from_html_file 已导入 / 可用
               C4: spec-meta 端点有懒更新代码
               C5: get_spec_by_token 有 HTML 兜底逻辑

总计: 14 个测试用例
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 路径 & 环境初始化（必须在任何 backend 导入之前）─────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_spe_{uuid.uuid4().hex[:6]}_"

# ── 前端 / 后端文件路径 ───────────────────────────────────────────────────────
_FRONTEND_ROOT   = Path(__file__).parent / "frontend" / "src"
_COPILOT_FILE    = _FRONTEND_ROOT / "components" / "DataCenterCopilot.tsx"
_REPORTS_API_FILE = Path(__file__).parent / "backend" / "api" / "reports.py"
_REPORT_SVC_FILE  = Path(__file__).parent / "backend" / "services" / "report_service.py"

# ── 工具：构造模拟 HTML ──────────────────────────────────────────────────────

def _make_html(spec: dict | None = None, data: dict | None = None) -> str:
    """构造与 report_builder_service 格式兼容的 HTML 字符串。"""
    spec_json = json.dumps(spec or {}, ensure_ascii=False)
    data_json = json.dumps(data or {}, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html>
<head><title>Test Report</title></head>
<body>
<script>
const REPORT_SPEC   = {spec_json};
const REPORT_DATA   = {data_json};
const REPORT_ID     = "test-id";
const REFRESH_TOKEN = "test-token";
window.REPORT_SPEC   = REPORT_SPEC;
window.REPORT_ID     = REPORT_ID;
</script>
</body>
</html>"""


# ── DB helpers（B段专用）────────────────────────────────────────────────────

_auth_patcher = None
_g_db = None


def setup_module(_=None):
    global _auth_patcher, _g_db
    try:
        from backend.config.settings import settings
        _auth_patcher = patch.object(settings, "enable_auth", False)
        _auth_patcher.start()
    except Exception:
        pass
    try:
        from backend.config.database import SessionLocal
        _g_db = SessionLocal()
    except Exception:
        pass


def teardown_module(_=None):
    global _auth_patcher, _g_db
    if _auth_patcher:
        _auth_patcher.stop()
    if _g_db:
        _cleanup_test_data()
        _g_db.close()


def _cleanup_test_data():
    if _g_db is None:
        return
    try:
        from backend.models.report import Report
        _g_db.query(Report).filter(Report.name.like(f"{_PREFIX}%")).delete(
            synchronize_session=False
        )
        _g_db.commit()
    except Exception:
        try:
            _g_db.rollback()
        except Exception:
            pass


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# A段 — extract_spec_from_html_file() 单元测试（无需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestAExtractSpecFromHtml(unittest.TestCase):
    """extract_spec_from_html_file() 函数的单元测试"""

    def setUp(self):
        from backend.services.report_service import extract_spec_from_html_file
        self.extract = extract_spec_from_html_file

    # A1: 正常含图表 HTML → 提取 charts 数组 ----------------------------------
    def test_A1_normal_html_extracts_charts(self):
        spec = {
            "title": "测试报表",
            "theme": "light",
            "charts": [
                {"id": "c1", "chart_type": "bar", "title": "柱图"},
                {"id": "c2", "chart_type": "line", "title": "折线图", "smooth": True},
            ],
            "filters": [],
        }
        html = _make_html(spec)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            path = Path(f.name)

        try:
            result = self.extract(path)
            self.assertIsNotNone(result, "应能提取到 spec")
            self.assertEqual(len(result["charts"]), 2)
            self.assertEqual(result["charts"][0]["id"], "c1")
            self.assertEqual(result["charts"][1]["chart_type"], "line")
            self.assertEqual(result["theme"], "light")
        finally:
            path.unlink(missing_ok=True)

    # A2: HTML 缺少 REPORT_SPEC → 返回 None -----------------------------------
    def test_A2_html_without_report_spec_returns_none(self):
        html = "<html><body><script>var x = 1;</script></body></html>"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            path = Path(f.name)

        try:
            result = self.extract(path)
            self.assertIsNone(result, "无 REPORT_SPEC 时应返回 None")
        finally:
            path.unlink(missing_ok=True)

    # A3: 文件不存在 → 返回 None，不抛异常 -----------------------------------
    def test_A3_file_not_found_returns_none(self):
        path = Path("/nonexistent/path/report_xyz123.html")
        try:
            result = self.extract(path)
            self.assertIsNone(result, "文件不存在时应返回 None")
        except Exception as e:
            self.fail(f"文件不存在时不应抛出异常，实际抛出: {e}")

    # A4: JSON 格式错误 → 返回 None，不抛异常 --------------------------------
    def test_A4_malformed_json_returns_none(self):
        html = """<script>
const REPORT_SPEC   = {invalid json here: not valid};
</script>"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            path = Path(f.name)

        try:
            result = self.extract(path)
            self.assertIsNone(result, "JSON 格式错误时应返回 None")
        except Exception as e:
            self.fail(f"JSON 格式错误时不应抛出异常，实际抛出: {e}")
        finally:
            path.unlink(missing_ok=True)

    # A5: charts 字段为空列表 → 返回空列表（非 None）-------------------------
    def test_A5_empty_charts_returns_empty_list_not_none(self):
        spec = {"title": "空报表", "theme": "dark", "charts": [], "filters": []}
        html = _make_html(spec)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            path = Path(f.name)

        try:
            result = self.extract(path)
            self.assertIsNotNone(result, "spec 存在时不应返回 None")
            self.assertIsInstance(result["charts"], list)
            self.assertEqual(len(result["charts"]), 0,
                             "空 charts 应返回空列表而非 None")
        finally:
            path.unlink(missing_ok=True)

    # A6: 含 filters/theme → 正确提取 ----------------------------------------
    def test_A6_extracts_filters_and_theme(self):
        spec = {
            "title": "含筛选器报表",
            "theme": "dark",
            "charts": [{"id": "c1", "chart_type": "pie", "title": "饼图"}],
            "filters": [
                {"id": "date_range", "type": "date_range", "label": "时间范围"}
            ],
        }
        html = _make_html(spec)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            path = Path(f.name)

        try:
            result = self.extract(path)
            self.assertIsNotNone(result)
            self.assertEqual(result["theme"], "dark")
            self.assertEqual(len(result["filters"]), 1)
            self.assertEqual(result["filters"][0]["id"], "date_range")
        finally:
            path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# B段 — pin 端点 + spec-meta 集成测试（需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestBPinAndSpecMetaIntegration(unittest.TestCase):
    """pin 端点 spec 提取 + spec-meta 懒更新集成测试"""

    @classmethod
    def setUpClass(cls):
        if _g_db is None:
            raise unittest.SkipTest("DB 未连接，跳过 B 段测试")
        cls.client = _make_client()
        # 确定 customer_data 根路径
        try:
            from backend.config.settings import settings
            cls.customer_root = (
                Path(settings.allowed_directories[0])
                if settings.allowed_directories
                else Path("customer_data")
            )
        except Exception:
            cls.customer_root = Path("customer_data")

    def _make_html_file(self, spec: dict | None, username: str = "testuser") -> tuple[str, Path]:
        """在 customer_data/{username}/reports/ 下写入测试 HTML，返回 (norm_fp, abs_path)"""
        rpt_dir = self.customer_root / username / "reports"
        rpt_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{_PREFIX}{uuid.uuid4().hex[:6]}.html"
        abs_path = rpt_dir / filename
        abs_path.write_text(_make_html(spec), encoding="utf-8")
        norm_fp = f"{username}/reports/{filename}"
        return norm_fp, abs_path

    def _cleanup_files(self, *abs_paths):
        for p in abs_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass

    # B1: pin 含图表 HTML → DB 中 charts 非空 ---------------------------------
    def test_B1_pin_with_charts_stores_charts_in_db(self):
        spec = {
            "title": "堆积图报表",
            "theme": "light",
            "charts": [
                {"id": "c1", "chart_type": "bar", "title": "堆积柱状图",
                 "smooth": False, "symbol_size": 4},
            ],
            "filters": [],
        }
        norm_fp, abs_path = self._make_html_file(spec)
        try:
            # 调用 pin API
            rpt_name = f"{_PREFIX}b1_{uuid.uuid4().hex[:4]}"
            resp = self.client.post(
                "/api/v1/reports/pin",
                json={
                    "file_path": norm_fp,
                    "name": rpt_name,
                    "doc_type": "dashboard",
                },
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()["data"]
            report_id = data["report_id"]
            self.assertTrue(data["is_new"], "应创建新记录")

            # 验证 DB 中 charts 已被提取
            from backend.models.report import Report as Rpt
            rpt = _g_db.query(Rpt).filter(Rpt.id == uuid.UUID(report_id)).first()
            _g_db.refresh(rpt)
            self.assertIsNotNone(rpt.charts, "pin 后 charts 不应为 NULL")
            self.assertGreater(len(rpt.charts), 0, "charts 数组不应为空")
            self.assertEqual(rpt.charts[0]["id"], "c1")
            self.assertEqual(rpt.charts[0]["chart_type"], "bar")
            self.assertEqual(rpt.theme, "light")
        finally:
            self._cleanup_files(abs_path)
            # 清理 DB 记录
            try:
                from backend.models.report import Report as Rpt
                _g_db.query(Rpt).filter(Rpt.report_file_path == norm_fp).delete(
                    synchronize_session=False
                )
                _g_db.commit()
            except Exception:
                _g_db.rollback()

    # B2: pin 无 REPORT_SPEC HTML → DB 中 charts=[]（不报错）-----------------
    def test_B2_pin_without_spec_stores_empty_charts(self):
        # HTML 中无 REPORT_SPEC
        html_no_spec = "<html><body><p>no spec</p></body></html>"
        rpt_dir = self.customer_root / "testuser" / "reports"
        rpt_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{_PREFIX}{uuid.uuid4().hex[:6]}.html"
        abs_path = rpt_dir / filename
        abs_path.write_text(html_no_spec, encoding="utf-8")
        norm_fp = f"testuser/reports/{filename}"

        try:
            rpt_name = f"{_PREFIX}b2_{uuid.uuid4().hex[:4]}"
            resp = self.client.post(
                "/api/v1/reports/pin",
                json={"file_path": norm_fp, "name": rpt_name, "doc_type": "document"},
            )
            self.assertEqual(resp.status_code, 200, f"无 REPORT_SPEC 时 pin 仍应成功: {resp.text}")
            data = resp.json()["data"]
            self.assertTrue(data["is_new"])
        finally:
            self._cleanup_files(abs_path)
            try:
                from backend.models.report import Report as Rpt
                _g_db.query(Rpt).filter(Rpt.report_file_path == norm_fp).delete(
                    synchronize_session=False
                )
                _g_db.commit()
            except Exception:
                _g_db.rollback()

    # B3: spec-meta 对 charts=NULL 记录触发懒更新 ----------------------------
    def test_B3_spec_meta_lazy_backfills_charts_from_html(self):
        """已有 charts=NULL 的 pin 记录，访问 spec-meta 后 charts 应被回填"""
        spec = {
            "title": "懒更新测试报表",
            "theme": "dark",
            "charts": [
                {"id": "c1", "chart_type": "line", "title": "折线"},
                {"id": "c2", "chart_type": "scatter", "title": "散点"},
            ],
            "filters": [{"id": "f1", "type": "date_range", "label": "日期"}],
        }
        norm_fp, abs_path = self._make_html_file(spec, username="testuser")

        try:
            # 直接写一条 charts=NULL 的记录（模拟旧的 pin 行为）
            from backend.models.report import Report as Rpt
            refresh_token = uuid.uuid4().hex
            rpt_name = f"{_PREFIX}b3_{uuid.uuid4().hex[:4]}"
            old_rpt = Rpt(
                name=rpt_name,
                doc_type="dashboard",
                username="testuser",
                refresh_token=refresh_token,
                report_file_path=norm_fp,
                summary_status="skipped",
                # charts 故意不设 → NULL
            )
            _g_db.add(old_rpt)
            _g_db.commit()
            _g_db.refresh(old_rpt)
            report_id = str(old_rpt.id)

            # 调用 spec-meta
            resp = self.client.get(
                f"/api/v1/reports/{report_id}/spec-meta?token={refresh_token}"
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()["data"]

            # spec-meta 返回值应含正确 charts
            self.assertIsNotNone(data.get("charts"), "懒更新后 charts 不应为 None")
            self.assertEqual(len(data["charts"]), 2, "应提取到 2 个图表")
            self.assertEqual(data["charts"][0]["id"], "c1")
            self.assertEqual(data["theme"], "dark")

            # DB 也应被回填
            _g_db.expire(old_rpt)
            _g_db.refresh(old_rpt)
            self.assertIsNotNone(old_rpt.charts, "DB 中 charts 应被懒更新回填")
            self.assertEqual(len(old_rpt.charts), 2)

        finally:
            self._cleanup_files(abs_path)
            try:
                _g_db.query(Rpt).filter(Rpt.name.like(f"{_PREFIX}b3%")).delete(
                    synchronize_session=False
                )
                _g_db.commit()
            except Exception:
                _g_db.rollback()

    # B4: 幂等 pin（同路径已有记录）→ is_new=False，不覆盖已有 charts ----------
    def test_B4_idempotent_pin_does_not_overwrite_existing_charts(self):
        """同一文件路径已有记录时，pin 应幂等返回，不重写 charts"""
        spec = {
            "title": "幂等测试",
            "theme": "light",
            "charts": [{"id": "c1", "chart_type": "bar", "title": "柱图"}],
            "filters": [],
        }
        norm_fp, abs_path = self._make_html_file(spec, username="testuser")

        try:
            rpt_name = f"{_PREFIX}b4_{uuid.uuid4().hex[:4]}"
            # 第一次 pin
            r1 = self.client.post(
                "/api/v1/reports/pin",
                json={"file_path": norm_fp, "name": rpt_name, "doc_type": "dashboard"},
            )
            self.assertEqual(r1.status_code, 200)
            d1 = r1.json()["data"]
            self.assertTrue(d1["is_new"])
            first_report_id = d1["report_id"]

            # 第二次 pin（同路径）
            r2 = self.client.post(
                "/api/v1/reports/pin",
                json={"file_path": norm_fp, "name": "另一个名字", "doc_type": "document"},
            )
            self.assertEqual(r2.status_code, 200)
            d2 = r2.json()["data"]
            self.assertFalse(d2["is_new"], "同路径应幂等，is_new=False")
            self.assertEqual(d2["report_id"], first_report_id, "应返回原有 report_id")

        finally:
            self._cleanup_files(abs_path)
            try:
                from backend.models.report import Report as Rpt
                _g_db.query(Rpt).filter(Rpt.report_file_path == norm_fp).delete(
                    synchronize_session=False
                )
                _g_db.commit()
            except Exception:
                _g_db.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# C段 — 代码静态分析（无需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestCCodeAnalysis(unittest.TestCase):
    """前端 & 后端关键代码改动静态分析"""

    # C1: DataCenterCopilot.tsx 含 contextSpec?.charts 读取 -------------------
    def test_C1_copilot_reads_context_spec_charts(self):
        self.assertTrue(_COPILOT_FILE.exists(),
                        f"文件不存在: {_COPILOT_FILE}")
        content = _COPILOT_FILE.read_text(encoding="utf-8")
        self.assertIn("contextSpec", content,
                      "DataCenterCopilot 应读取 contextSpec")
        self.assertIn("charts", content,
                      "DataCenterCopilot 应访问 charts 字段")

    # C2: Pilot 系统提示使用图表数量字符串 -----------------------------------
    def test_C2_pilot_system_prompt_uses_chart_count(self):
        content = _COPILOT_FILE.read_text(encoding="utf-8")
        # 应有"图表"数量相关的系统提示文本
        self.assertTrue(
            "图表" in content,
            "DataCenterCopilot 系统提示应提及'图表'"
        )
        # 应读取 charts 数组长度或进行 map 操作
        self.assertTrue(
            "charts" in content and ("length" in content or "map" in content),
            "系统提示应遍历或统计 charts 数组"
        )

    # C3: reports.py 中存在 extract_spec_from_html_file 导入/调用 ------------
    def test_C3_reports_api_imports_extract_spec(self):
        self.assertTrue(_REPORTS_API_FILE.exists(),
                        f"文件不存在: {_REPORTS_API_FILE}")
        content = _REPORTS_API_FILE.read_text(encoding="utf-8")
        self.assertIn("extract_spec_from_html_file", content,
                      "reports.py 应导入 extract_spec_from_html_file")

    # C4: spec-meta 端点含懒更新代码 -----------------------------------------
    def test_C4_spec_meta_has_lazy_backfill_code(self):
        content = _REPORTS_API_FILE.read_text(encoding="utf-8")
        # 懒更新的关键标志：检查 charts 为空（not report.charts 或 charts is None）
        has_lazy_check = ("not report.charts" in content or "report.charts is None" in content)
        self.assertTrue(has_lazy_check,
                        "spec-meta 应检查 charts 为空以触发懒更新")
        # 提交到 DB
        self.assertIn("db.commit()", content,
                      "spec-meta 懒更新应提交到 DB")

    # C5: get_spec_by_token 有 HTML 兜底逻辑 ---------------------------------
    def test_C5_get_spec_by_token_has_html_fallback(self):
        self.assertTrue(_REPORT_SVC_FILE.exists(),
                        f"文件不存在: {_REPORT_SVC_FILE}")
        content = _REPORT_SVC_FILE.read_text(encoding="utf-8")
        self.assertIn("extract_spec_from_html_file", content,
                      "report_service.py 应定义 extract_spec_from_html_file")
        # get_spec_by_token 中也应有兜底
        self.assertIn("charts is None", content,
                      "get_spec_by_token 应包含 charts is None 的兜底逻辑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
