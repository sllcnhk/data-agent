# 指标：GMV（Gross Merchandise Value，总账单金额）

## 指标定义

**GMV** 在本系统中指企业每月产生的**总账单金额**，即平台收取的通话费、短信费、机器人费等所有计费项目的总和。

> 注意：与电商GMV（总成交额）不同，本系统GMV = 平台对企业的总收费额

---

## 计算口径

### 主要计算表：`integrated_data.Bill_Monthly`

```sql
-- 月度GMV（按环境）
SELECT 
    Environment,
    Year_Month,
    sum(Bill_Amount) AS GMV,
    Currency
FROM integrated_data.Bill_Monthly
WHERE Statistic_Is_Delete = 0
GROUP BY Environment, Year_Month, Currency
ORDER BY Year_Month DESC, GMV DESC;

-- 月度GMV（全局汇总）
SELECT 
    Year_Month,
    sum(Bill_Amount) AS total_GMV
FROM integrated_data.Bill_Monthly
WHERE Statistic_Is_Delete = 0
GROUP BY Year_Month
ORDER BY Year_Month DESC;
```

### 日度GMV（更细粒度）：`integrated_data.Fact_Bill_DS`

```sql
-- 日度GMV
SELECT 
    Record_Date,
    sum(Bill_Amount) AS daily_GMV
FROM integrated_data.Fact_Bill_DS
WHERE Statistic_Is_Delete = 0
    AND Record_Date >= today() - 30
GROUP BY Record_Date
ORDER BY Record_Date DESC;
```

---

## 维度拆分

| 维度 | 字段 | 说明 |
|------|------|------|
| 时间 | `Year_Month` / `Record_Date` | 月度或日度 |
| 环境 | `Environment` | SG/AU/MY/THAI等 |
| 企业 | `Enterprise_ID` + `Enterprise_Name` | 单企业GMV |
| 产品线 | `Product_Line` | AI Voice/SMS等 |
| 计费类型 | `Charge_Type` | Line Charge/SMS/BotCharge等 |
| 支付类型 | `Payment_Type` | prepaid/postpaid |
| 国家 | `Project_Country` | 项目所在国 |

---

## 同比/环比

`Bill_Monthly` 已预计算同比/环比字段：

```sql
-- 环比增长率
SELECT 
    Enterprise_Name,
    Year_Month,
    sum(Bill_Amount) AS current_GMV,
    sum(Last_Month_Same_Period_BillAmount) AS last_month_GMV,
    (sum(Bill_Amount) - sum(Last_Month_Same_Period_BillAmount)) / 
        nullIf(sum(Last_Month_Same_Period_BillAmount), 0) AS mom_growth_rate
FROM integrated_data.Bill_Monthly
WHERE Statistic_Is_Delete = 0
    AND Year_Month = '2026-02'
GROUP BY Enterprise_Name, Year_Month
ORDER BY current_GMV DESC;
```

---

## 注意事项

1. **货币统一**：`Bill_Amount` 已按 `Exchange_Rate` 换算为统一货币（`Currency` 字段）
2. **过滤删除**：必须加 `Statistic_Is_Delete = 0`
3. **测试企业排除**：JOIN `Dim_Enterprise` 后加 `Name_Test_Flag = 0`
4. **多计费类型**：同一企业同月可能有多条记录（不同 Charge_Type），汇总时需 `sum(Bill_Amount)`
5. **负值处理**：`Decrease` 系列字段为负值表示增长，正值表示下降（注意方向）
