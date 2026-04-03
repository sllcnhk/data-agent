---
name: clickhouse-analyst-mx
version: "4.0"
description: ClickHouse MX外呼业务数据分析，含呼叫记录/日账单对比/有呼叫无账单企业检测，适用墨西哥环境
triggers:
  - mx
  - 墨西哥
  - mexico
  - mx环境
  - mx_azure
  - creditnow
  - xinfei
category: analytics
priority: high
always_inject: false
---

# ClickHouse Analyst Skill — MX 外呼业务分析规程

> **版本**: v4.0 | **更新时间**: 2026-03-19
> **适用环境**: ClickHouse MX（墨西哥）及其他区域环境
> **知识库路径**: `{CURRENT_USER}/db_knowledge/`

---

## 数据库知识库

数据库元数据位于 `{CURRENT_USER}/db_knowledge/`：

```
{CURRENT_USER}/db_knowledge/
├── _index.md                          ← L1: 全局索引（首先读取）
├── relationships.md                   ← L2: 完整 ERD + 业务链路图
├── tables/                            ← L2: 各表详细文档
│   ├── dim_call_task.md                   外呼任务表 ⭐
│   ├── dim_call_task_customer.md          任务线索明细（重拨父子）
│   ├── realtime_dwd_crm_call_record.md    呼叫记录宽表 ⭐
│   └── realtime_ods_cost_data_report_day_bill_record.md  日账单表 ⭐
└── metrics/                           ← L3: 核心指标口径
    ├── cost_per_call.md
    ├── connect_rate.md
    └── monthly_bill.md
```

**按需加载原则**：
- 首先读取 `{CURRENT_USER}/db_knowledge/_index.md` 了解整体结构
- 分析具体表时，读取 `{CURRENT_USER}/db_knowledge/tables/<表名>.md`
- 跨表分析时，读取 `{CURRENT_USER}/db_knowledge/relationships.md`
- 计算指标时，读取 `{CURRENT_USER}/db_knowledge/metrics/<指标>.md`
- **不要一次性加载所有文件**

---

## 核心业务理解

### 外呼业务两种模式

| 特征 | 策略外呼 (Automation) | 手动外呼 (Manual) |
|------|----------------------|-----------------|
| 识别方式 | `automatic_task_id > 0` | `automatic_task_id IS NULL 或 = 0` |
| 任务表标识 | `dim_call_task.is_automatic_task = 1` | `is_automatic_task = 0 或 NULL` |
| 实际占比 | ~15.5%（近 7 天验证）| ~84.5% |
| 线索流转 | Plan 驱动，按 Action 节点流转，产生子线索 | 无流转，重拨产生子线索 |

### 账单业务关键理解（重要更新⭐）

**账单表结构**：`crm.realtime_ods_cost_data_report_day_bill_record`

#### bus_type 字段枚举（核心）

| bus_type | 业务含义 | 是否外呼相关 | 说明 |
|----------|---------|-------------|------|
| **1** | **Line Charge** | ✅ **是** | 外呼通话费，AI 外呼产生的通话费用 ⭐ |
| **2** | SMS | ❌ 否 | 短信通知费，属于通知类业务 |
| **3** | WhatsApp | ❌ 否 | WhatsApp 消息费，属于通知类业务 |
| **4** | Email | ❌ 否 | 邮件通知费，属于通知类业务 |
| **5** | **BotCharge** | ✅ **是** | 机器人调用费，AI 外呼机器人产生的费用 ⭐ |

**关键结论**：
- **外呼相关账单类型 = `bus_type IN (1, 5)`**
- `bus_type=1`（Line Charge）：传统外呼通话费
- `bus_type=5`（BotCharge）：AI 机器人外呼费
- `bus_type IN (2,3,4)`：通知类业务，**不计入外呼账单**

#### 常见错误案例

```sql
-- ❌ 错误：只过滤 bus_type=1，遗漏 BotCharge
WHERE bus_type = 1

-- ✅ 正确：包含所有外呼相关账单类型
WHERE bus_type IN (1, 5)
```

**实际业务场景**：
- 企业使用 AI 外呼机器人 → 可能产生 `bus_type=5` 账单
- 企业使用传统外呼 → 产生 `bus_type=1` 账单
- 企业同时使用外呼 + 短信通知 → 可能同时有 `bus_type=1` 和 `bus_type=2` 账单

#### 账单日期字段

| 字段 | 类型 | 含义 | 用途 |
|------|------|------|------|
| `record_time` | Date | 账单对应日期 | 与呼叫记录的 `toDate(call_start_time)` 关联 |

#### 典型问题模式

1. **有呼叫无账单**：企业有 `call_type=1` 呼叫记录，但无 `bus_type IN (1,5)` 账单
2. **账单类型错配**：企业有 `bus_type=2`（SMS）账单，但无 `bus_type=1`（Line Charge）账单
3. **日期不匹配**：企业有账单，但账单日期与呼叫日期不一致

---

## 查询规范（强制执行）

### 1. 时间过滤（必须用 PREWHERE）

```sql
-- ✅ 正确
PREWHERE call_start_time >= addDays(now(), -30)

-- ❌ 禁止（全表扫描）
WHERE call_start_time >= addDays(now(), -30)
```

### 2. 软删除过滤（必须）

```sql
WHERE is_delete = 0
```

### 3. 禁止 SELECT *

`realtime_dwd_crm_call_record` 有 78 个字段，只选需要的列。

### 4. 统计线索数必须区分父子

```sql
-- 策略线索：统计实际线索数（只数父线索）
WHERE parent_id = '0'

-- 任务线索：统计实际线索数（只数父线索）
WHERE parent_call_task_customer_id IS NULL
```

### 5. 外呼呼叫类型过滤

```sql
-- call_type=1 代表 AI 外呼
WHERE call_type = 1
```

### 6. 外呼账单类型过滤（重要更新⭐）

```sql
-- ✅ 正确：包含 Line Charge 和 BotCharge
WHERE bus_type IN (1, 5)

-- ❌ 错误：遗漏 BotCharge
WHERE bus_type = 1
```

### 7. 接通率统一口径

```sql
-- 接通 = call_code_type IN (1, 16)
-- Answering Machine (AM) = call_code_type = 22
countIf(call_code_type IN (1, 16)) / count() AS connect_rate
```

> ⚠️ **重要**：`call_code_type` 为整数类型，直接使用整数，**不加引号**。
> - Connected（接通）：`call_code_type IN (1, 16)`
> - Answering Machine（AM）：`call_code_type = 22`

### 8. ClickHouse LEFT JOIN NULL 判断陷阱（重要⚠️）

**问题描述**：
ClickHouse 中 LEFT JOIN 未匹配时，不同数据类型返回不同的"默认值"而非 NULL：
- `Int64`/`UInt64` 类型 → 返回 `0`
- `Date`/`DateTime` 类型 → 返回 `1970-01-01` / `1970-01-01 00:00:00`
- `String` 类型 → 返回空字符串 `''`

**错误示例**：
```sql
-- ❌ 错误：IS NULL 在 ClickHouse 中对数值/日期类型无效
SELECT * FROM a LEFT JOIN b ON a.id = b.id WHERE b.id IS NULL
```

**正确写法**：
```sql
-- ✅ 正确：根据字段类型判断默认值
SELECT * 
FROM a 
LEFT JOIN b ON a.id = b.id 
WHERE b.id = 0  -- Int64/UInt64 类型
   OR b.some_date = toDate('1970-01-01')  -- Date 类型
   OR b.some_str = ''  -- String 类型
```

**实际应用场景**（有呼叫无账单检测）：
```sql
SELECT 
    c.enterprise_id,
    c.call_date,
    c.call_count,
    b.bill_date
FROM calls c
LEFT JOIN bills b 
  ON c.enterprise_id = b.enterprise_id 
  AND c.call_date = b.record_time
  AND b.bus_type IN (1, 5)  -- 外呼账单类型
WHERE b.enterprise_id = 0  -- Int64 类型判断未匹配
```

---

## 核心查询模板

### 模板 1：有呼叫无账单检测（日期级）

```sql
-- 检测指定期间内"有外呼呼叫但无外呼账单"的企业 - 日期组合
WITH calls AS (
    SELECT 
        enterprise_id,
        toDate(call_start_time) AS call_date,
        count() AS call_count
    FROM crm.realtime_dwd_crm_call_record
    PREWHERE call_start_time >= '{start_date}' AND call_start_time < '{end_date}'
    WHERE is_delete = 0
      AND call_type = 1  -- AI 外呼
    GROUP BY enterprise_id, toDate(call_start_time)
),
bills AS (
    SELECT 
        enterprise_id,
        record_time AS bill_date
    FROM crm.realtime_ods_cost_data_report_day_bill_record
    PREWHERE record_time >= '{start_date}' AND record_time < '{end_date}'
    WHERE bus_type IN (1, 5)  -- Line Charge + BotCharge
    GROUP BY enterprise_id, record_time
),
issues AS (
    SELECT 
        c.enterprise_id,
        c.call_date,
        c.call_count
    FROM calls c
    LEFT JOIN bills b 
        ON c.enterprise_id = b.enterprise_id AND c.call_date = b.bill_date
    WHERE b.enterprise_id = 0  -- ClickHouse Int64 NULL 判断
)
SELECT 
    enterprise_id,
    count() AS issue_days,
    sum(call_count) AS total_calls,
    min(call_date) AS first_issue_date,
    max(call_date) AS last_issue_date
FROM issues
GROUP BY enterprise_id
ORDER BY total_calls DESC;
```

### 模板 2：新出现问题企业筛选（排除历史问题）

```sql
-- 找出期间 2 新出现、期间 1 无问题的企业
WITH period1_issues AS (
    -- 期间 1（如 2.20-3.04）有问题的企业列表
    SELECT DISTINCT enterprise_id
    FROM (
        -- [模板 1 的查询逻辑，期间 1 的日期范围]
    )
),
period2_issues AS (
    -- 期间 2（如 3.06-3.16）有问题的企业列表
    SELECT 
        enterprise_id,
        count() AS issue_days,
        sum(call_count) AS total_calls
    FROM (
        -- [模板 1 的查询逻辑，期间 2 的日期范围]
    )
    GROUP BY enterprise_id
)
SELECT 
    p2.enterprise_id,
    p2.issue_days,
    p2.total_calls,
    'NEW' AS issue_type
FROM period2_issues p2
LEFT JOIN period1_issues p1 ON p2.enterprise_id = p1.enterprise_id
WHERE p1.enterprise_id = 0  -- 排除期间 1 已有问题的企业
ORDER BY p2.total_calls DESC;
```

### 模板 3：企业呼叫 + 账单详情查询

```sql
-- 查询指定企业的呼叫和账单对比详情
SELECT 
    d.call_date,
    d.call_count,
    b.bill_count,
    b.bus_types,
    if(b.bill_count > 0, '✅', '❌') AS has_bill
FROM (
    SELECT 
        toDate(call_start_time) AS call_date,
        count() AS call_count
    FROM crm.realtime_dwd_crm_call_record
    PREWHERE call_start_time >= '2026-03-06' AND call_start_time < '2026-03-17'
    WHERE is_delete = 0
      AND call_type = 1
      AND enterprise_id = {enterprise_id}
    GROUP BY toDate(call_start_time)
) d
LEFT JOIN (
    SELECT 
        record_time AS bill_date,
        count() AS bill_count,
        groupArrayDistinct(bus_type) AS bus_types
    FROM crm.realtime_ods_cost_data_report_day_bill_record
    PREWHERE record_time >= '2026-03-06' AND record_time < '2026-03-17'
    WHERE enterprise_id = {enterprise_id}
    GROUP BY record_time
) b ON d.call_date = b.bill_date
ORDER BY d.call_date;
```

### 模板 4：关联企业名称查询

```sql
-- 查询问题企业列表并关联企业名称
SELECT 
    ri.enterprise_id,
    de.ent_name AS enterprise_name,
    de.ent_code,
    ri.issue_days,
    ri.total_calls,
    ri.first_issue_date,
    ri.last_issue_date
FROM (
    -- [模板 1 或模板 2 的查询结果]
) ri
LEFT JOIN crm.dim_enterprise de ON ri.enterprise_id = de.ent_id
ORDER BY ri.total_calls DESC;
```

---

## Quick Reference

| 需求 | 关键字段/条件 |
|------|-------------|
| 判断 AI 外呼呼叫 | `call_type = 1` |
| 判断外呼账单 | `bus_type IN (1, 5)` |
| Line Charge（外呼通话费） | `bus_type = 1` |
| BotCharge（机器人外呼费） | `bus_type = 5` |
| 判断接通（Connected） | `call_code_type IN (1, 16)` |
| 判断答录机（AM） | `call_code_type = 22` |
| 账单日期关联 | `record_time = toDate(call_start_time)` |
| ClickHouse LEFT JOIN 未匹配（Int64） | `field = 0` |
| ClickHouse LEFT JOIN 未匹配（Date） | `field = toDate('1970-01-01')` |
| 策略外呼 | `automatic_task_id > 0` |
| 策略父线索 | `parent_id = '0'`（字符串）|
| 任务父线索 | `parent_call_task_customer_id IS NULL` |

---

## 数据质量注意事项

1. **账单类型多样性**：企业可能同时有多种 `bus_type` 账单，需明确区分外呼相关（1,5）和通知类（2,3,4）
2. **账单日期延迟**：账单生成可能存在 T+1 延迟，查询时需排除最近 2 天数据
3. **ClickHouse NULL 陷阱**：LEFT JOIN 后数值类型返回 0 而非 NULL，必须用 `field = 0` 判断
4. **企业 ID 相似性**：注意相似但不相同的企业 ID（如 422223060752834733 vs 422223060752834563），必须精确匹配
5. **bus_type=5 增长趋势**：随着 AI 机器人外呼普及，`bus_type=5` 账单占比可能持续上升

---

## 典型案例分析

### 案例 1：Creditnow_CG（611823180855297252）

**问题**：3.11-3.16 期间有大量呼叫（3.16 单日 39,090 次），但只有 SMS 账单（bus_type=2）和少量 BotCharge（bus_type=5），**无 Line Charge 账单（bus_type=1）**

**根因假设**：
- AI 外呼任务被错误归类为机器人调用（bus_type=5）而非通话费（bus_type=1）
- 企业计费配置中 `call_type=1` 未正确关联 `bus_type=1`

**行动**：核查企业计费配置和任务类型映射

### 案例 2：Xinfei_MX_CG_AI（628543671079625921）

**问题**：3.13-3.14 两天有呼叫但无账单，其他日期正常

**根因假设**：
- 计费任务在特定日期执行失败
- 数据同步延迟

**行动**：检查 3.13-3.14 期间计费批处理日志

---

**版本历史**：
- v4.0 (2026-03-19): 更新账单类型枚举（bus_type 1=Line Charge, 5=BotCharge），添加 LEFT JOIN NULL 判断陷阱说明
- v3.2 (2026-03-21): 初始版本
