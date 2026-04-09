# 表：integrated_data.Bill_Monthly

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | integrated_data |
| 表名 | Bill_Monthly |
| 数据层级 | DWS（汇总层） |
| 业务域 | 账单计费 |
| 更新方式 | 月度ETL处理 |

## 业务语义

**月度账单汇总表**，是账单分析的核心表。按企业、计费类型、月份维度汇总账单金额，并预计算了环比/同比对比数据。

**核心用途**：
- 企业月度账单查询
- GMV（总收入）统计
- 按产品线/计费类型分析收入
- 同比环比趋势分析

## 字段详情

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| **Environment** | String | 部署环境 | 见 Dim_Enterprise.Environment 枚举 |
| **Year_Month** | String | 账单月份 | 格式: 'YYYY-MM' |
| **Start_Date** | Date | 账单开始日期 | |
| **End_Date** | Date | 账单结束日期 | |
| **Charge_Type_ID** | Int32 | 计费类型ID | 见枚举值说明 |
| **Charge_Type** | String | 计费类型名称 | 见枚举值说明 |
| **Charge_Subtype_ID** | Int32 | 计费子类型ID | 1001=接通通话, 1002=语音信箱 |
| **Charge_Subtype** | String | 计费子类型名称 | |
| **Enterprise_ID** | Int64 | 企业ID | 关联 Dim_Enterprise |
| **Enterprise_Name** | String | 企业名称 | 冗余字段 |
| **Unique_ID** | String | 唯一标识 | 销售侧标识 |
| **Customer_ID** | String | 客户ID | 销售侧客户编号 |
| **Product_Line** | String | 产品线 | 如 AI Voice, SMS 等 |
| **Use_Case** | String | 使用场景 | |
| **Client_Name** | String | 客户名称 | 销售侧客户名 |
| **Project_Country** | String | 项目所在国家 | |
| **Bill_Number** | Decimal(24,4) | 账单数量（用量） | 通话次数/条数等 |
| **Unit_Price** | Decimal(24,6) | 单价 | 每次通话/每条短信的价格 |
| **Bill_Amount** | Decimal(32,6) | 账单金额（本月） | = Bill_Number × Unit_Price |
| **Last_Month_Same_Period_BillAmount** | Decimal(32,6) | 上月同期金额 | 环比基准 |
| **Last_3_Months_Same_Period_BillAmount** | Decimal(32,6) | 近3月同期均值 | |
| **Last_6_Months_Same_Period_BillAmount** | Decimal(32,6) | 近6月同期均值 | |
| **Last_Month_Same_Period_Decrease_BillAmount** | Decimal(32,6) | 环比降幅 | 负值=增长, 正值=下降 |
| **Last_3_Months_Same_Period_Decrease_BillAmount** | Decimal(32,6) | 近3月同期降幅 | |
| **Last_6_Months_Same_Period_Decrease_BillAmount** | Decimal(32,6) | 近6月同期降幅 | |
| **Currency** | String | 结算货币 | 统一货币（USD） |
| **Original_Currency** | String | 原始货币 | 本地货币 |
| **Exchange_Rate** | Decimal(32,10) | 汇率 | 原始货币→结算货币 |
| **Payment_Type** | String | 支付类型 | prepaid=预付费, postpaid=后付费 |
| **Statistic_Is_Delete** | Int16 | 统计删除标记 | 0=有效, 1=删除 |

## 枚举值说明

### Charge_Type_ID（计费类型）
| ID | 类型名称 | 含义 |
|----|---------|------|
| 1 | Line Charge | 线路费（通话费） |
| 2 | SMS | 短信费 |
| 3 | WhatsApp | WhatsApp消息费 |
| 4 | Email | 邮件费 |
| 5 | BotCharge | 机器人使用费 |

### Charge_Subtype_ID（计费子类型）
| ID | 类型名称 | 含义 |
|----|---------|------|
| 1001 | ConnectedCall | 接通通话计费 |
| 1002 | Voicemail | 语音信箱计费 |

## 关联关系

```
integrated_data.Bill_Monthly
    ├── Enterprise_ID ──→ integrated_data.Dim_Enterprise.Enterprise_ID
    ├── Year_Month ─────→ integrated_data.Fact_Bill_DS (按日明细)
    └── Charge_Type_ID ─→ integrated_data.Fact_Bill_Usage.Charge_Type_ID
```

## 注意事项

1. **GMV计算**：`sum(Bill_Amount)` 即为总账单金额（GMV）
2. 过滤有效数据：`Statistic_Is_Delete = 0`
3. 多货币处理：`Bill_Amount` 已按 `Exchange_Rate` 换算为统一货币
4. 同比分析：使用 `Last_Month_Same_Period_BillAmount` 字段，无需自行 JOIN
5. 负值的 `Decrease` 字段表示增长（减少量为负 = 实际增加）

## 典型查询

```sql
-- 统计某月各环境GMV
SELECT 
    Environment,
    Year_Month,
    sum(Bill_Amount) AS GMV,
    sum(Bill_Number) AS total_usage
FROM integrated_data.Bill_Monthly
WHERE Year_Month = '2026-02'
    AND Statistic_Is_Delete = 0
GROUP BY Environment, Year_Month
ORDER BY GMV DESC;

-- 统计各计费类型收入占比
SELECT 
    Charge_Type,
    sum(Bill_Amount) AS revenue,
    sum(Bill_Amount) / sum(sum(Bill_Amount)) OVER () AS ratio
FROM integrated_data.Bill_Monthly
WHERE Year_Month = '2026-02'
    AND Statistic_Is_Delete = 0
GROUP BY Charge_Type
ORDER BY revenue DESC;

-- 环比增长分析
SELECT 
    Enterprise_Name,
    Year_Month,
    sum(Bill_Amount) AS current_month,
    sum(Last_Month_Same_Period_BillAmount) AS last_month,
    (sum(Bill_Amount) - sum(Last_Month_Same_Period_BillAmount)) / 
        nullIf(sum(Last_Month_Same_Period_BillAmount), 0) AS mom_growth
FROM integrated_data.Bill_Monthly
WHERE Year_Month = '2026-02' AND Statistic_Is_Delete = 0
GROUP BY Enterprise_Name, Year_Month
ORDER BY current_month DESC
LIMIT 20;
```
