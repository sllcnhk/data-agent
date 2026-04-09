# 表：integrated_data.Fact_Bill_DS

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | integrated_data |
| 表名 | Fact_Bill_DS |
| 数据量 | ~1.86亿行 |
| 磁盘大小 | 3.48 GiB |
| 数据层级 | DWS（汇总层） |
| 业务域 | 账单计费 |
| 更新粒度 | 按天（每日汇总） |

## 业务语义

**按日账单事实表（DS = Daily Summary）**，是 `Bill_Monthly` 的明细版本，精细到每天的账单数据。记录每个企业每天按计费类型产生的使用量和金额。

**核心用途**：
- 企业每日账单查询
- 月度账单汇总（聚合为 Bill_Monthly）
- 计费异常检测（日环比）
- 使用量趋势分析

## 字段详情

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| **Environment** | String | 部署环境 | 见 Dim_Enterprise.Environment 枚举 |
| **Record_Date** | Date | 账单日期 | 分区键 |
| **Charge_Type_ID** | Int32 | 计费类型ID | 见 Bill_Monthly 枚举 |
| **Charge_Type** | String | 计费类型名称 | |
| **Charge_Subtype_ID** | Int32 | 计费子类型ID | |
| **Charge_Subtype** | String | 计费子类型名称 | |
| **Enterprise_ID** | Int64 | 企业ID | 关联 Dim_Enterprise |
| **Enterprise_Name** | String | 企业名称 | 冗余字段 |
| **Bill_Number** | Decimal(24,4) | 当日用量 | 通话次数/条数 |
| **Unit_Price** | Decimal(24,6) | 单价 | |
| **Bill_Amount** | Decimal(32,6) | 当日账单金额 | = Bill_Number × Unit_Price |
| **Currency** | String | 结算货币 | |
| **Original_Currency** | String | 原始货币 | |
| **Exchange_Rate** | Decimal(32,10) | 汇率 | |
| **Statistic_Is_Delete** | Int16 | 统计删除标记 | 0=有效 |

## 关联关系

```
integrated_data.Fact_Bill_DS
    ├── Enterprise_ID ──→ integrated_data.Dim_Enterprise.Enterprise_ID
    ├── Record_Date ────→ integrated_data.Fact_Daily_Call.call_date (按日关联通话数据)
    └── 聚合 ───────────→ integrated_data.Bill_Monthly (按月汇总)
```

## 注意事项

1. **数据量大**（1.86亿行），查询时必须带 `Record_Date` 时间范围过滤
2. `Fact_Bill_DS` 是 `Bill_Monthly` 的明细，`Bill_Monthly = sum(Fact_Bill_DS) GROUP BY Year_Month`
3. 过滤有效数据：`Statistic_Is_Delete = 0`

## 典型查询

```sql
-- 某企业最近30天每日账单
SELECT 
    Record_Date,
    Charge_Type,
    sum(Bill_Amount) AS daily_amount,
    sum(Bill_Number) AS daily_usage
FROM integrated_data.Fact_Bill_DS
WHERE Enterprise_ID = ?
    AND Record_Date >= today() - 30
    AND Statistic_Is_Delete = 0
GROUP BY Record_Date, Charge_Type
ORDER BY Record_Date DESC;
```
