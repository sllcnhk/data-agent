# 表：integrated_data.Dim_Unique_ID

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | `integrated_data` |
| 表名 | `Dim_Unique_ID` |
| 数据层级 | DIM（维度表）|
| 业务域 | 项目/合同属性维度 |
| 读取方式 | **必须加 `FINAL`**；**过滤** `Unique_ID <> ''` |

## 业务语义

**项目维度表**，以 `Unique_ID` 为核心键，记录每个外呼项目的业务属性：客户名称、销售归属、行业、是否新老客等。是跨环境业务分析中**最重要的业务属性来源**。

---

## 表结构

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `Unique_ID` | String | 项目唯一标识 | **核心主键**；`Unique_ID <> ''` 必须过滤 |
| `Customer_ID` | String | 客户 ID | 关联外部 CRM |
| `Product_Line` | String | 产品线 | |
| `Use_Case` | String | 使用场景/用例 | |
| `Client_Name` | String | 客户名称 | 可能为空，IDN-Sampoerna 有特殊兜底逻辑 |
| `Country` | String | 项目所在国家 | |
| `Contract_Code` | String | 合同编号 | 关联 `Dim_Unique_ID_Sales_Split` |
| `Sales_Owner` | String | 销售负责人 | |
| `Sales_Team` | String | 销售团队 | **可为空**，空时需回退到 Split 表 |
| `Industry` | String | 行业 | |
| `Business_Unit` | String | 业务单元 | |
| `Existing_Customer` | String | 新老客标记 | 值 `'Existing'`=老客，其余=新客（含 NULL）|
| `MA` | String | MA 标识 | |

---

## 新老客判断规则（重要）

```sql
-- 原始字段只有 'Existing' 和其他值两种情况
-- 业务上需要二分为 New / Existing
case
    when Existing_Customer not in ('Existing') then 'New'
    else Existing_Customer
end AS Existing_Customer_Modified
```

> `NOT IN ('Existing')` 涵盖了 NULL、空串、其他历史值，全部归为 `'New'`。
> 不要写 `= 'New'`，因为字段里未必有 'New' 这个值。

---

## Client_Name 特殊兜底（IDN-Sampoerna）

```sql
case
    when (uni.Client_Name is null or uni.Client_Name = '')
         and bb.SaaS ilike '%IDN-Sampoerna%' then 'Sampoerna'
    else uni.Client_Name
end as Client_Name
```

---

## Sales_Team 回退链

```sql
-- 优先级：① Split 表（唯一团队） > ② IDN-Sampoerna 特例 > ③ 维度表原值
case
    when (uni.Sales_Team is null or uni.Sales_Team = '')
         and unp.ts = 1                               then unp.S_Sales_Team
    when (uni.Sales_Team is null or uni.Sales_Team = '')
         and bb.SaaS ilike '%IDN-Sampoerna%'          then 'Indonesia'
    else uni.Sales_Team
end
```

> 需左连接 `Dim_Unique_ID_Sales_Split` ON `Contract_Code` + 月份，再使用 `ts` 字段判断。

---

## 标准查询模式

```sql
SELECT
    Unique_ID, Customer_ID, Product_Line, Use_Case,
    Client_Name, Country, Contract_Code, Sales_Owner,
    Sales_Team, Industry, Business_Unit, Existing_Customer, MA
FROM integrated_data.Dim_Unique_ID uni1 FINAL
WHERE Unique_ID <> ''
```

---

## 关联关系

| 关联表 | 关联字段 | 说明 |
|--------|----------|------|
| `Dim_Dialogue` | `Dim_Unique_ID.Unique_ID = Dim_Dialogue.Unique_id` | 通过话术找到项目属性 |
| `Dim_Unique_ID_Sales_Split` | `Contract_Code` + 月份 | 销售归因补充 |
| `Dim_Enterprise` | （间接，通过话术或企业 ID）| 企业信息 |

---

## 注意事项

1. `Unique_ID <> ''` 必须过滤，否则会包含大量无效行
2. `Existing_Customer` 不要假设只有 'New'/'Existing' 两个值，历史数据可能有其他值
3. `Sales_Team` 和 `Client_Name` 均可为空，分析前确认回退逻辑
