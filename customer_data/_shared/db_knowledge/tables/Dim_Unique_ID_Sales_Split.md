# 表：integrated_data.Dim_Unique_ID_Sales_Split

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | `integrated_data` |
| 表名 | `Dim_Unique_ID_Sales_Split` |
| 数据层级 | DIM（维度表）|
| 业务域 | 销售归因分摊 |
| 读取方式 | **必须加 `FINAL`** |

## 业务语义

**销售团队分摊表**。当一个合同（`Contract_Code`）的销售归属需要在多个团队间分摊时，该表记录各月份的分摊比例和金额。是 `Dim_Unique_ID.Sales_Team` 为空时的**回退数据源**。

---

## 表结构

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `Contract_Code` | String | 合同编号 | 关联 `Dim_Unique_ID.Contract_Code` |
| `Month` | Int | 月份（数字）| 如 `7` |
| `Year` | Int | 年份 | 如 `2024` |
| `Month_Date` | Date | 月份日期（月初）| 用于与业务数据月份对齐 |
| `Sales_Team` | String | 销售团队名称 | |
| `Share_Rate` | Float | 分摊比例 | 0~1，各团队合计为 1 |
| `Sales` | String | 销售人员 | |

---

## 聚合模式（必须先聚合再 JOIN）

```sql
SELECT
    usp1.Contract_Code,
    usp1.Month,
    usp1.Year,
    usp1.Month_Date,
    count(1)                                                        AS bs,
    count(distinct Sales_Team)                                      AS ts,   -- 关键：去重团队数
    arrayStringConcat(groupArray(
        concat(usp1.Sales_Team, ': ', toString(usp1.Share_Rate), ': ', usp1.Sales)
    ), ', ')                                                        AS contact_sale,
    max(Sales_Team)                                                 AS S_Sales_Team   -- ts=1时的唯一团队
FROM integrated_data.Dim_Unique_ID_Sales_Split usp1 FINAL
GROUP BY usp1.Contract_Code, usp1.Month, usp1.Year, usp1.Month_Date
```

**关键字段**：
- `ts`（`count(distinct Sales_Team)`）：去重团队数
  - `ts = 1` → 该合同当月只有一个销售团队 → 可安全用 `S_Sales_Team` 回退
  - `ts > 1` → 多团队分摊，回退逻辑暂不处理（保持原值）
- `S_Sales_Team`（`max(Sales_Team)`）：`ts=1` 时即唯一团队名，`ts>1` 时为 max 值（不可用）

---

## JOIN 条件

```sql
LEFT JOIN (
    -- 上方聚合子查询
) unp
    ON unp.Contract_Code = uni.Contract_Code
    AND formatDateTime(toStartOfMonth(事实表.s_day), '%Y-%m') = formatDateTime(unp.Month_Date, '%Y-%m')
```

> 月份对齐使用 `formatDateTime(..., '%Y-%m')` 字符串比较，不要直接用 Date 类型比较。

---

## 使用场景

只在 `Dim_Unique_ID.Sales_Team` 为空时才需要 JOIN 本表：

```sql
-- 完整回退逻辑
case
    when (uni.Sales_Team is null or uni.Sales_Team = '') and unp.ts = 1 then unp.S_Sales_Team
    when (uni.Sales_Team is null or uni.Sales_Team = '') and SaaS ilike '%IDN-Sampoerna%' then 'Indonesia'
    else uni.Sales_Team
end
```

---

## 关联关系

| 关联表 | 关联字段 | 说明 |
|--------|----------|------|
| `Dim_Unique_ID` | `Contract_Code` + 月份 | 补充 Sales_Team |
