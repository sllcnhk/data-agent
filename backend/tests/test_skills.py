"""
Skills单元测试

测试所有技能的正常功能
"""
import pytest
import asyncio
import json
import pandas as pd
from unittest.mock import Mock, patch, MagicMock

from backend.skills import (
    database_query_skill,
    database_list_tables_skill,
    database_describe_table_skill,
    database_connection_test_skill,
    data_analysis_skill,
    trend_analysis_skill,
    outlier_detection_skill,
    sql_generation_skill,
    sql_optimization_skill,
    chart_generation_skill,
    chart_type_recommendation_skill,
    etl_design_skill,
    data_validation_skill,
    data_cleaning_skill,
    create_skill_input
)


class TestDatabaseQuerySkill:
    """测试数据库查询技能"""

    @pytest.mark.asyncio
    async def test_query_execution(self):
        """测试查询执行"""
        with patch('backend.skills.database_query.get_clickhouse_server') as mock_server:
            mock_instance = Mock()
            mock_instance.call_tool.return_value = {
                "success": True,
                "rows": [{"id": 1, "name": "test"}],
                "columns": ["id", "name"],
                "row_count": 1
            }
            mock_server.return_value = mock_instance

            input_data = create_skill_input(
                parameters={
                    "database_type": "clickhouse",
                    "environment": "idn",
                    "query": "SELECT * FROM test",
                    "max_rows": 10,
                    "export_format": "json"
                }
            )

            result = await database_query_skill(input_data)

            assert result.success is True
            assert result.data["query"] == "SELECT * FROM test"
            assert result.data["row_count"] == 1

    @pytest.mark.asyncio
    async def test_query_without_sql(self):
        """测试空查询"""
        input_data = create_skill_input(
            parameters={
                "query": ""
            }
        )

        result = await database_query_skill(input_data)

        assert result.success is False
        assert "不能为空" in result.error

    @pytest.mark.asyncio
    async def test_invalid_database_type(self):
        """测试不支持的数据库类型"""
        input_data = create_skill_input(
            parameters={
                "database_type": "invalid_db",
                "query": "SELECT 1"
            }
        )

        result = await database_query_skill(input_data)

        assert result.success is False
        assert "不支持的数据库类型" in result.error


class TestDataAnalysisSkill:
    """测试数据分析技能"""

    @pytest.mark.asyncio
    async def test_summary_analysis(self):
        """测试摘要分析"""
        test_data = [
            {"name": "Alice", "age": 25, "score": 85},
            {"name": "Bob", "age": 30, "score": 90},
            {"name": "Charlie", "age": 35, "score": 80}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "analysis_type": "summary"
            }
        )

        result = await data_analysis_skill(input_data)

        assert result.success is True
        assert result.data["analysis_type"] == "summary"
        assert result.data["data_shape"] == (3, 3)
        assert len(result.data["result"]["columns"]) == 3

    @pytest.mark.asyncio
    async def test_statistics_analysis(self):
        """测试统计分析"""
        test_data = [
            {"value": 10},
            {"value": 20},
            {"value": 30}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "analysis_type": "statistics"
            }
        )

        result = await data_analysis_skill(input_data)

        assert result.success is True
        assert "statistics" in result.data["result"]
        assert "value" in result.data["result"]["statistics"]

    @pytest.mark.asyncio
    async def test_correlation_analysis(self):
        """测试相关性分析"""
        test_data = [
            {"x": 1, "y": 2},
            {"x": 2, "y": 4},
            {"x": 3, "y": 6}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "analysis_type": "correlation"
            }
        )

        result = await data_analysis_skill(input_data)

        assert result.success is True
        assert "correlations" in result.data["result"]
        assert len(result.data["result"]["correlations"]) > 0

    @pytest.mark.asyncio
    async def test_grouping_analysis(self):
        """测试分组分析"""
        test_data = [
            {"category": "A", "value": 10},
            {"category": "A", "value": 20},
            {"category": "B", "value": 15}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "analysis_type": "grouping",
                "group_by": "category"
            }
        )

        result = await data_analysis_skill(input_data)

        assert result.success is True
        assert result.data["result"]["group_by"] == "category"
        assert len(result.data["result"]["groups"]) == 2

    @pytest.mark.asyncio
    async def test_invalid_analysis_type(self):
        """测试不支持的分析类型"""
        input_data = create_skill_input(
            parameters={
                "data": [{"value": 1}],
                "analysis_type": "invalid_type"
            }
        )

        result = await data_analysis_skill(input_data)

        assert result.success is False
        assert "不支持的分析类型" in result.error


class TestTrendAnalysisSkill:
    """测试趋势分析技能"""

    @pytest.mark.asyncio
    async def test_trend_calculation(self):
        """测试趋势计算"""
        test_data = [
            {"date": "2024-01-01", "value": 100},
            {"date": "2024-01-02", "value": 110},
            {"date": "2024-01-03", "value": 120}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "time_column": "date",
                "value_column": "value"
            }
        )

        result = await trend_analysis_skill(input_data)

        assert result.success is True
        assert result.data["overall_trend"] == "increasing"
        assert result.data["data_points"] == 3

    @pytest.mark.asyncio
    async def test_decreasing_trend(self):
        """测试下降趋势"""
        test_data = [
            {"date": "2024-01-01", "value": 120},
            {"date": "2024-01-02", "value": 110},
            {"date": "2024-01-03", "value": 100}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "time_column": "date",
                "value_column": "value"
            }
        )

        result = await trend_analysis_skill(input_data)

        assert result.success is True
        assert result.data["overall_trend"] == "decreasing"

    @pytest.mark.asyncio
    async def test_missing_parameters(self):
        """测试缺少参数"""
        input_data = create_skill_input(
            parameters={
                "data": [{"date": "2024-01-01", "value": 100}]
            }
        )

        result = await trend_analysis_skill(input_data)

        assert result.success is False
        assert "需要提供" in result.error


class TestOutlierDetectionSkill:
    """测试异常值检测技能"""

    @pytest.mark.asyncio
    async def test_zscore_detection(self):
        """测试Z-Score方法"""
        test_data = [
            {"value": 10},
            {"value": 12},
            {"value": 11},
            {"value": 100}  # 异常值
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "method": "zscore",
                "threshold": 2.0
            }
        )

        result = await outlier_detection_skill(input_data)

        assert result.success is True
        assert result.data["method"] == "zscore"
        assert "value" in result.data["outliers"]

    @pytest.mark.asyncio
    async def test_iqr_detection(self):
        """测试IQR方法"""
        test_data = [
            {"value": 10},
            {"value": 12},
            {"value": 11},
            {"value": 100}  # 异常值
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "method": "iqr"
            }
        )

        result = await outlier_detection_skill(input_data)

        assert result.success is True
        assert result.data["method"] == "iqr"

    @pytest.mark.asyncio
    async def test_invalid_method(self):
        """测试不支持的方法"""
        input_data = create_skill_input(
            parameters={
                "data": [{"value": 1}],
                "method": "invalid_method"
            }
        )

        result = await outlier_detection_skill(input_data)

        assert result.success is False
        assert "不支持的检测方法" in result.error


class TestSQLGenerationSkill:
    """测试SQL生成技能"""

    @pytest.mark.asyncio
    async def test_select_generation(self):
        """测试SELECT查询生成"""
        input_data = create_skill_input(
            parameters={
                "description": "查询所有用户",
                "table_schema": {
                    "table_name": "users",
                    "columns": [
                        {"name": "id", "type": "int"},
                        {"name": "name", "type": "string"}
                    ]
                }
            }
        )

        result = await sql_generation_skill(input_data)

        assert result.success is True
        assert "SELECT" in result.data["sql"]
        assert "users" in result.data["sql"]

    @pytest.mark.asyncio
    async def test_count_generation(self):
        """测试COUNT查询生成"""
        input_data = create_skill_input(
            parameters={
                "description": "统计用户数量",
                "table_schema": {
                    "table_name": "users",
                    "columns": [
                        {"name": "id", "type": "int"}
                    ]
                }
            }
        )

        result = await sql_generation_skill(input_data)

        assert result.success is True
        assert "COUNT" in result.data["sql"]

    @pytest.mark.asyncio
    async def test_sum_generation(self):
        """测试SUM查询生成"""
        input_data = create_skill_input(
            parameters={
                "description": "求销售额总和",
                "table_schema": {
                    "table_name": "orders",
                    "columns": [
                        {"name": "amount", "type": "decimal"}
                    ]
                }
            }
        )

        result = await sql_generation_skill(input_data)

        assert result.success is True
        assert "SUM" in result.data["sql"]

    @pytest.mark.asyncio
    async def test_sql_validation(self):
        """测试SQL验证"""
        input_data = create_skill_input(
            parameters={
                "description": "查询数据",
                "table_schema": {
                    "table_name": "test",
                    "columns": []
                }
            }
        )

        result = await sql_generation_skill(input_data)

        assert result.success is True
        assert "validation" in result.data
        assert result.data["validation"]["valid"] is True

    @pytest.mark.asyncio
    async def test_empty_description(self):
        """测试空描述"""
        input_data = create_skill_input(
            parameters={
                "description": ""
            }
        )

        result = await sql_generation_skill(input_data)

        assert result.success is False
        assert "不能为空" in result.error


class TestSQLOptimizationSkill:
    """测试SQL优化技能"""

    @pytest.mark.asyncio
    async def test_add_limit(self):
        """测试添加LIMIT"""
        input_data = create_skill_input(
            parameters={
                "sql": "SELECT * FROM users",
                "database_type": "clickhouse"
            }
        )

        result = await sql_optimization_skill(input_data)

        assert result.success is True
        assert "LIMIT" in result.data["optimized_sql"]

    @pytest.mark.asyncio
    async def test_optimization_suggestions(self):
        """测试优化建议"""
        input_data = create_skill_input(
            parameters={
                "sql": "SELECT * FROM users",
                "database_type": "mysql"
            }
        )

        result = await sql_optimization_skill(input_data)

        assert result.success is True
        assert len(result.data["suggestions"]) > 0

    @pytest.mark.asyncio
    async def test_empty_sql(self):
        """测试空SQL"""
        input_data = create_skill_input(
            parameters={
                "sql": ""
            }
        )

        result = await sql_optimization_skill(input_data)

        assert result.success is False
        assert "不能为空" in result.error


class TestChartGenerationSkill:
    """测试图表生成技能"""

    @pytest.mark.asyncio
    async def test_bar_chart_generation(self):
        """测试柱状图生成"""
        test_data = [
            {"category": "A", "value": 10},
            {"category": "B", "value": 20},
            {"category": "C", "value": 15}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "chart_type": "bar",
                "title": "测试图表",
                "library": "echarts"
            }
        )

        result = await chart_generation_skill(input_data)

        assert result.success is True
        assert result.data["chart_type"] == "bar"
        assert result.data["library"] == "echarts"
        assert "config" in result.data

    @pytest.mark.asyncio
    async def test_auto_chart_type_selection(self):
        """测试自动图表类型选择"""
        test_data = [
            {"x": 1, "y": 2},
            {"x": 2, "y": 4},
            {"x": 3, "y": 6}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "chart_type": "auto",
                "library": "echarts"
            }
        )

        result = await chart_generation_skill(input_data)

        assert result.success is True
        assert result.data["chart_type"] in ["scatter", "line"]

    @pytest.mark.asyncio
    async def test_line_chart_generation(self):
        """测试折线图生成"""
        test_data = [
            {"date": "2024-01-01", "value": 10},
            {"date": "2024-01-02", "value": 20}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "chart_type": "line",
                "x_column": "date",
                "y_column": "value",
                "library": "echarts"
            }
        )

        result = await chart_generation_skill(input_data)

        assert result.success is True
        assert result.data["chart_type"] == "line"

    @pytest.mark.asyncio
    async def test_pie_chart_generation(self):
        """测试饼图生成"""
        test_data = [
            {"category": "A", "count": 10},
            {"category": "B", "count": 20}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "chart_type": "pie",
                "x_column": "category",
                "library": "echarts"
            }
        )

        result = await chart_generation_skill(input_data)

        assert result.success is True
        assert result.data["chart_type"] == "pie"

    @pytest.mark.asyncio
    async def test_empty_data(self):
        """测试空数据"""
        input_data = create_skill_input(
            parameters={
                "data": [],
                "chart_type": "bar"
            }
        )

        result = await chart_generation_skill(input_data)

        assert result.success is False
        assert "数据不能为空" in result.error


class TestChartTypeRecommendationSkill:
    """测试图表类型推荐技能"""

    @pytest.mark.asyncio
    async def test_recommendation_for_numeric_data(self):
        """测试数值数据推荐"""
        test_data = [
            {"x": 1, "y": 2},
            {"x": 2, "y": 4}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data
            }
        )

        result = await chart_type_recommendation_skill(input_data)

        assert result.success is True
        assert "recommendations" in result.data
        assert len(result.data["recommendations"]) > 0

    @pytest.mark.asyncio
    async def test_recommendation_for_categorical_data(self):
        """测试分类数据推荐"""
        test_data = [
            {"category": "A", "value": 10},
            {"category": "B", "value": 20}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data
            }
        )

        result = await chart_type_recommendation_skill(input_data)

        assert result.success is True
        # 应该推荐柱状图和饼图
        chart_types = [r["chart_type"] for r in result.data["recommendations"]]
        assert "bar" in chart_types


class TestETLDesignSkill:
    """测试ETL设计技能"""

    @pytest.mark.asyncio
    async def test_pipeline_design(self):
        """测试管道设计"""
        input_data = create_skill_input(
            parameters={
                "source_type": "database",
                "source_config": {
                    "database_type": "clickhouse",
                    "query": "SELECT * FROM events",
                    "batch_size": 10000
                },
                "transformation_rules": [
                    {"type": "clean", "description": "清理数据"}
                ],
                "target_config": {
                    "type": "database",
                    "table": "events_processed",
                    "mode": "append"
                },
                "pipeline_type": "incremental"
            }
        )

        result = await etl_design_skill(input_data)

        assert result.success is True
        assert "pipeline" in result.data
        assert "extract" in result.data["pipeline"]
        assert "transform" in result.data["pipeline"]
        assert "load" in result.data["pipeline"]

    @pytest.mark.asyncio
    async def test_pipeline_without_transformations(self):
        """测试无转换规则的管道"""
        input_data = create_skill_input(
            parameters={
                "source_type": "database",
                "source_config": {
                    "database_type": "mysql",
                    "query": "SELECT * FROM users"
                },
                "target_config": {
                    "type": "database",
                    "table": "users_copy"
                }
            }
        )

        result = await etl_design_skill(input_data)

        assert result.success is True
        # 应该添加默认转换
        assert len(result.data["pipeline"]["transform"]) > 0

    @pytest.mark.asyncio
    async def test_missing_source_config(self):
        """测试缺少源配置"""
        input_data = create_skill_input(
            parameters={
                "source_type": "database"
            }
        )

        result = await etl_design_skill(input_data)

        assert result.success is False
        assert "需要提供" in result.error


class TestDataValidationSkill:
    """测试数据验证技能"""

    @pytest.mark.asyncio
    async def test_null_check(self):
        """测试空值检查"""
        test_data = [
            {"name": "Alice", "age": 25},
            {"name": None, "age": 30},
            {"name": "Charlie", "age": None}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "rules": [
                    {"type": "null_check", "columns": ["name", "age"], "level": "error"}
                ]
            }
        )

        result = await data_validation_skill(input_data)

        assert result.success is True
        assert result.data["validation_passed"] is False
        assert result.data["failed_checks"] > 0

    @pytest.mark.asyncio
    async def test_duplicate_check(self):
        """测试重复检查"""
        test_data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 1, "name": "Alice"}  # 重复
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "rules": [
                    {"type": "duplicate_check", "columns": ["id", "name"]}
                ]
            }
        )

        result = await data_validation_skill(input_data)

        assert result.success is True
        assert result.data["validation_passed"] is False

    @pytest.mark.asyncio
    async def test_data_type_check(self):
        """测试数据类型检查"""
        test_data = [
            {"value": 10},
            {"value": "20"},  # 字符串，应该转换
            {"value": 30}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "rules": [
                    {"type": "data_type_check", "config": {"value": "numeric"}}
                ]
            }
        )

        result = await data_validation_skill(input_data)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_validation_passed(self):
        """测试验证通过"""
        test_data = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data
            }
        )

        result = await data_validation_skill(input_data)

        assert result.success is True
        assert result.data["passed_checks"] > 0


class TestDataCleaningSkill:
    """测试数据清洗技能"""

    @pytest.mark.asyncio
    async def test_remove_duplicates(self):
        """测试移除重复数据"""
        test_data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 1, "name": "Alice"}  # 重复
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "operations": [
                    {"type": "remove_duplicates"}
                ]
            }
        )

        result = await data_cleaning_skill(input_data)

        assert result.success is True
        assert result.data["rows_removed"] == 1
        assert result.data["cleaned_shape"][0] == 2

    @pytest.mark.asyncio
    async def test_handle_nulls(self):
        """测试处理空值"""
        test_data = [
            {"name": "Alice", "age": 25},
            {"name": None, "age": 30},
            {"name": "Charlie", "age": None}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "operations": [
                    {"type": "handle_nulls", "method": "drop"}
                ]
            }
        )

        result = await data_cleaning_skill(input_data)

        assert result.success is True
        assert result.data["rows_removed"] == 2

    @pytest.mark.asyncio
    async def test_trim_strings(self):
        """测试清理字符串空格"""
        test_data = [
            {"name": " Alice "},
            {"name": "Bob"},
            {"name": "  Charlie  "}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "operations": [
                    {"type": "trim_strings", "columns": ["name"]}
                ]
            }
        )

        result = await data_cleaning_skill(input_data)

        assert result.success is True
        assert "清理字符串空格" in result.data["operations_applied"][0]

    @pytest.mark.asyncio
    async def test_convert_types(self):
        """测试类型转换"""
        test_data = [
            {"value": "10"},
            {"value": "20"}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "operations": [
                    {"type": "convert_types", "types": {"value": "int"}}
                ]
            }
        )

        result = await data_cleaning_skill(input_data)

        assert result.success is True
        assert "转换 value 为 int" in result.data["operations_applied"][0]

    @pytest.mark.asyncio
    async def test_fill_nulls_with_mean(self):
        """测试用均值填充空值"""
        test_data = [
            {"value": 10},
            {"value": None},
            {"value": 30}
        ]

        input_data = create_skill_input(
            parameters={
                "data": test_data,
                "operations": [
                    {"type": "handle_nulls", "method": "fill", "value": "mean"}
                ]
            }
        )

        result = await data_cleaning_skill(input_data)

        assert result.success is True
        assert "填充空值" in result.data["operations_applied"][0]


# 运行示例

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
