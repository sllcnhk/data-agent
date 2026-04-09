# 指标：月度账单金额（Monthly Bill Amount）

## 指标定义

**月度账单金额** = 某企业在某月产生的所有计费项目的总金额（已换算为统一货币）。

---

## 计算口径

### 主要计算表：`integrated_data.Bill_Monthly`

```sql
-- 某企业某月账单明细
SELECT 
    e.Enterprise_Name,
    b.Year_Month,
    b.Charge_Type,
    b.Charge_Subtype,
    b.Bill_Number AS usage_quantity,
    b.Unit_Price,
    b.Bill_Amount,
    b.Currency,
    b.Payment_Type
FROM integrated_data.Bill_Monthly b
JOIN integrated_data.Dim_Enterprise e 
    ON b.Enterprise_ID = e.Enterprise_ID
WHERE b.Enterprise_ID = ?
    AND b.Year_Month = '2026-02'
    AND b.Statistic_Is_Delete = 0
ORDER BY b.Bill_Amount DESC;

-- 企业月度账单汇总（所有计费类型合计）
SELECT 
    Enterprise_ID,
    Enterprise_Name,
    Year_Month,
    sum(Bill_Amount) AS total_bill,
    Currency
FROM integrated_data.Bill_Monthly
WHERE Year_Month = '2026-02'
    AND Statistic_Is_Delete = 0
GROUP BY Enterprise_ID, Enterprise_Name, Year_Month, Currency
ORDER BY total_bill DESC
LIMIT 20;
```

---

## 账单组成结构

```
月度账单总金额
├── Line Charge（线路费）
│   ├── ConnectedCall（接通通话费）= 接通次数 × 单价/分钟 × 通话时长
│   └── Voicemail（语音信箱费）= 语音信箱次数 × 单价
├── BotCharge（机器人费）= 并发数 × 时间 × 单价
├── SMS（短信费）= 短信条数 × 单价
├── WhatsApp（WhatsApp消息费）
└── Email（邮件费）
```

---

## 同比/环比分析

```sql
-- 环比增长率（利用预计算字段）
SELECT 
    Enterprise_Name,
    Year_Month,
    sum(Bill_Amount) AS current_bill,
    sum(Last_Month_Same_Period_BillAmount) AS last_month_bill,
    round(
        (sum(Bill_Amount) - sum(Last_Month_Same_Period_BillAmount)) / 
        nullIf(sum(Last_Month_Same_Period_BillAmount), 0) * 100, 2
    ) AS mom_growth_pct
FROM integrated_data.Bill_Monthly
WHERE Statistic_Is_Delete = 0
    AND Year_Month >= '2026-01'
GROUP BY Enterprise_Name, Year_Month
HAVING current_bill > 0
ORDER BY Year_Month DESC, current_bill DESC;
```

---

## 注意事项

1. **货币统一**：`Bill_Amount` 已换算为统一货币，`Original_Currency` 为原始货币
2. **过滤条件**：`Statistic_Is_Delete = 0` 必须加
3. **测试企业**：JOIN `Dim_Enterprise` 并加 `Name_Test_Flag = 0` 排除测试数据
4. **多行合计**：同企业同月有多行（不同 Charge_Type），用 `sum()` 汇总
5. **负值含义**：`Last_Month_Same_Period_Decrease_BillAmount` 为负时表示账单增长
