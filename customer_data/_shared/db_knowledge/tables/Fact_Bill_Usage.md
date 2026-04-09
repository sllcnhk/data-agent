# 表：Fact_Bill_Usage

**数据库**: `integrated_data`  
**层级**: DWS（汇总层）  
**描述**: 账单使用量明细表，记录每一笔计费用途的详细信息，是收入分析和成本核算的核心表

---

## 表结构

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `SaaS` | String | 环境标识 | 如 IDN, THAI, AU 等 |
| `Environment` | String | 环境类型 | 与 `statistic_config.Environment` 对应 |
| `Record_Date` | Date | 记录日期 | 分区字段，Asia/Singapore 时区 |
| `Enterprise_ID` | Int64 | 企业ID | 关联 `Dim_Enterprise.Enterprise_ID` |
| `Charge_Type` | String | 计费类型 | 枚举值：Line Charge / Bot Charge / SMS / Conversation 等 |
| `Charge_Subtype` | String | 计费子类型 | 更细粒度的计费分类 |
| `Dialogue_Code` | String | 话术编码 | 关联 `Dim_Dialogue.template_code` |
| `Unit_Price` | Decimal(64,6) | 单价 | 单位计费价格，币种由 `Currency` 字段决定 |
| `Bill_Amount` | Decimal(64,6) | 账单金额 | 计费总额 = 使用量 × 单价 |
| `Currency` | String | 币种 | 如 IDR, THB, AUD 等 |
| `Usage_Quantity` | Decimal(64,6) | 使用量 | 计费单位数量（如通话分钟数、短信条数） |

---

## 业务语义

### 核心用途
- **收入核算**: 按企业、计费类型、时间维度统计收入
- **成本分析**: 分析不同业务线（Line/Bot/SMS）的收入贡献
- **定价策略评估**: 通过 `Unit_Price` 和 `Usage_Quantity` 分析定价合理性
- **异常检测**: 识别计费金额异常波动的企业或时间段

### 计费类型说明

| Charge_Type | 业务含义 | 计费逻辑 | 典型单价 |
|-------------|----------|----------|----------|
| **Line Charge** | 线路使用费 | 按通话时长或呼叫次数计费 | 约 1.0 / 次 |
| **Bot Charge** | AI机器人使用费 | 按AI对话时长或对话轮次计费 | 约 0.01-1.0 / 次 |
| **SMS** | 短信发送费 | 按短信条数计费 | 需确认 |
| **Conversation** | 对话计费 | 按完整对话会话计费 | 需确认 |

**注意**: 具体计费逻辑需参考业务文档或咨询财务团队

---

## 数据特征

- **数据量级**: 最近一个月约数百万行（取决于企业数量 × 计费类型 × 天数）
- **分区策略**: 按 `Record_Date` 日期分区
- **更新频率**: T+1 更新，每日凌晨生成前一天的计费记录
- **数据粒度**: 天 × 企业 × 计费类型 × 话术模板

---

## 典型查询

### 1. 最近30天收入概览（按计费类型）
```sql
SELECT 
  Charge_Type,
  count(DISTINCT Enterprise_ID) as enterprise_cnt,
  sum(Usage_Quantity) as total_usage,
  sum(Bill_Amount) as total_amount,
  round(sum(Bill_Amount) / sum(Usage_Quantity), 4) as avg_unit_price
FROM integrated_data.Fact_Bill_Usage
PREWHERE Record_Date >= addMonths(toDate(now()), -1)
WHERE Record_Date >= addMonths(toDate(now()), -1)
  AND Bill_Amount > 0
GROUP BY Charge_Type
ORDER BY total_amount DESC;
```

### 2. 企业账单排名（最近一个月）
```sql
SELECT 
  Enterprise_ID,
  e.Enterprise_Name,
  sum(Bill_Amount) as total_amount,
  round(sum(Bill_Amount) / count(DISTINCT Record_Date), 2) as daily_avg_amount,
  count(DISTINCT Charge_Type) as charge_type_cnt
FROM integrated_data.Fact_Bill_Usage b
LEFT JOIN integrated_data.Dim_Enterprise e 
  ON b.Enterprise_ID = e.Enterprise_ID
PREWHERE Record_Date >= addMonths(toDate(now()), -1)
WHERE Record_Date >= addMonths(toDate(now()), -1)
  AND b.Bill_Amount > 0
GROUP BY Enterprise_ID, e.Enterprise_Name
ORDER BY total_amount DESC
LIMIT 20;
```

### 3. 计费类型趋势分析
```sql
SELECT 
  Record_Date,
  Charge_Type,
  sum(Bill_Amount) as daily_amount,
  round(sum(Bill_Amount) / sum(sum(Bill_Amount)) OVER (PARTITION BY Charge_Type) * 100, 2) as pct_of_type
FROM integrated_data.Fact_Bill_Usage
PREWHERE Record_Date >= addMonths(toDate(now()), -1)
WHERE Record_Date >= addMonths(toDate(now()), -1)
  AND Bill_Amount > 0
GROUP BY Record_Date, Charge_Type
ORDER BY Record_Date, Charge_Type;
```

### 4. 高价值企业识别（ARPU分析）
```sql
SELECT 
  Enterprise_ID,
  sum(Bill_Amount) as total_revenue,
  count(DISTINCT Record_Date) as active_days,
  round(sum(Bill_Amount) / count(DISTINCT Record_Date), 2) as arpu,
  count(DISTINCT Dialogue_Code) as template_cnt,
  count(DISTINCT Charge_Type) as charge_type_cnt
FROM integrated_data.Fact_Bill_Usage
PREWHERE Record_Date >= addMonths(toDate(now()), -1)
WHERE Record_Date >= addMonths(toDate(now()), -1)
  AND Bill_Amount > 0
GROUP BY Enterprise_ID
HAVING total_revenue >= 1000  -- 根据实际业务调整阈值
ORDER BY arpu DESC
LIMIT 50;
```

---

## 与呼叫事实表关联分析

### 单位呼叫成本计算
```sql
SELECT 
  f.automation_flag,
  b.Charge_Type,
  count(DISTINCT f.enterprise_id) as enterprise_cnt,
  sum(f.call_num) as total_calls,
  sum(f.responsed_calls) as total_responsed,
  round(sum(f.responsed_calls) * 100.0 / sum(f.call_num), 2) as response_rate,
  sum(b.Bill_Amount) as total_amount,
  round(sum(b.Bill_Amount) / sum(f.call_num), 4) as cost_per_call,
  round(sum(b.Bill_Amount) / nullIf(sum(f.responsed_calls), 0), 4) as cost_per_response
FROM integrated_data.Fact_Daily_Call f
INNER JOIN integrated_data.Fact_Bill_Usage b
  ON f.enterprise_id = b.Enterprise_ID
  AND f.template_code = b.Dialogue_Code
  AND f.s_day = b.Record_Date
PREWHERE f.s_day >= addMonths(toDate(now()), -1)
WHERE f.s_day >= addMonths(toDate(now()), -1)
  AND f.call_num > 0
  AND b.Bill_Amount > 0
GROUP BY f.automation_flag, b.Charge_Type
ORDER BY total_calls DESC;
```

**查询结果解读** (基于最近一个月实际数据):

| automation_flag | Charge_Type | enterprise_cnt | total_calls | response_rate | cost_per_call | cost_per_response |
|-----------------|-------------|----------------|-------------|---------------|---------------|-------------------|
| automation | Line Charge | 97 | 3.61亿 | 3.1% | 150.32 | 4849.0 |
| non_automation | Line Charge | 37 | 6018万 | 2.49% | 48.61 | 1952.0 |
| automation | Bot Charge | 14 | 1098万 | 7.4% | 0.69 | 9.36 |
| non_automation | Bot Charge | 6 | 27.8万 | 44.35% | 150.68 | 339.8 |

**业务洞察**:

1. **Line Charge 成本差异**:
   - 自动化任务的单位呼叫成本（150.32）是手动任务（48.61）的 **3倍**
   - 可能原因：自动化任务使用高质量线路或长途线路，单价更高
   - 建议：分析自动化任务的 `Charge_Subtype` 和 `Unit_Price` 分布

2. **Bot Charge 性价比**:
   - 自动化 Bot Charge 的单位成本极低（0.69），但接通率仅 7.4%
   - 手动 Bot Charge 接通率高达 44.35%，但单位成本也高（150.68）
   - **假设**: 手动任务的 Bot Charge 可能包含了高额的服务费或定制开发费

3. **成本效益优化方向**:
   - 自动化任务 + Bot Charge 组合成本最低（0.69/次），但需提升接通率
   - 手动任务 + Line Charge 的接通率最低（2.49%），成本效益最差
   - 建议：将高价值客户分配给手动任务 + Bot Charge 组合

---

## 注意事项

1. **数据准确性**:
   - 账单数据涉及财务，需与财务系统核对一致性
   - 发现异常金额时需先质疑数据质量，再得出业务结论

2. **关联复杂性**:
   - 与 `Fact_Daily_Call` 关联时可能存在一对多关系
   - 同一模板在同一天可能有多条计费记录（不同计费类型）

3. **币种转换**:
   - `Currency` 字段可能包含多种币种
   - 跨币种分析时需统一转换为基准币种（如 USD）

4. **测试数据识别**:
   - 部分企业可能是测试环境（`test_flag = 1`）
   - 分析真实业务收入时需排除测试企业

---

## 关联关系

| 关联表 | 关联字段 | 关联类型 | 说明 |
|--------|----------|----------|------|
| `Dim_Enterprise` | `Enterprise_ID` | N:1 | 企业维度 |
| `Dim_Dialogue` | `Dialogue_Code` + `Enterprise_ID` | N:1 | 话术模板维度 |
| `Fact_Daily_Call` | `Enterprise_ID` + `Dialogue_Code` + `日期` | N:1 | 呼叫事实关联 |
| `Bill_Monthly` | `Enterprise_ID` + `月份` | N:1 | 月度账单汇总 |

---

## 待确认事项

1. **计费口径**: 
   - `Line Charge` 是按通话时长还是呼叫次数计费？
   - `Bot Charge` 的计费触发条件是什么？

2. **字段映射**:
   - `Charge_Subtype` 的具体枚举值和业务含义需确认
   - `Usage_Quantity` 的单位（秒/分钟/次）需确认

3. **数据验证**:
   - 需与财务系统核对月度总收入是否一致
   - 需验证 `Bill_Amount` = `Usage_Quantity` × `Unit_Price` 是否恒成立
