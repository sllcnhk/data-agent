# 表：integrated_data.Fact_Daily_Call_Contacts_Offline

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | `integrated_data` |
| 表名 | `Fact_Daily_Call_Contacts_Offline` |
| 数据层级 | DWS（汇总层）|
| 业务域 | 离线导入的外呼统计数据 |
| 读取方式 | **无 FINAL**，**无 statistic_is_delete 过滤**，直接按 `s_day` 范围过滤 |

## 业务语义

**离线对账事实表**。通过线下文件（Excel/CSV）导入的外呼统计数据，与在线产生的 `Fact_Daily_Call` 互补，构成全量外呼记录。在全环境汇总分析（如新老客月度报表）中，必须与在线数据 UNION ALL 后才能得到完整视图。

---

## 与在线数据的关键区别

| 特征 | Fact_Daily_Call（在线）| Fact_Daily_Call_Contacts_Offline（离线）|
|------|------|------|
| 数据来源 | 系统实时产生 | 线下文件导入 |
| FINAL | 需要 | **不需要** |
| statistic_is_delete | 需要过滤 = 0 | **不需要过滤** |
| 统计字段 | call_num + call_code_type 行 | 各指标已预聚合为独立字段 |
| IVR_Flag | 需自己计算 | 已有 `IVR_flag` 字段 |
| Enterprise_Name | 需 JOIN Dim_Enterprise | 已有 `ent_name` 字段 |

---

## 表结构

| 字段名 | 类型 | 业务含义 | 对应在线口径 |
|--------|------|----------|------------|
| `Environment` | String | 环境标识 | `SaaS` |
| `enterprise_id` | Int64 | 企业 ID | |
| `template_code` | String | 话术模板编码 | |
| `s_day` | Date | 统计日期 | 分区字段 |
| `ent_name` | String | 企业名称（已内联）| `Dim_Enterprise.Enterprise_Name` |
| `dialogue_name` | String | 话术名称 | `Dim_Dialogue.speech_name` |
| `IVR_flag` | String | IVR 标记 | 已按规则判断好：`'IVR'` / `'NON_IVR'` |
| `unique_id` | String | 项目 Unique ID | `Dim_Dialogue.Unique_id` |
| `ent_create_user` | String | 企业创建人 | `Dim_Enterprise.Create_User` |
| `contact_phone_number` | Int64 | 触达唯一号码数 | |
| `connected_phone_number` | Int64 | 接通唯一号码数 | |
| `total_call` | Int64 | 总呼叫数 | `sum(call_num)` |
| `connected_call` | Int64 | 接通数 | `sumIf(call_num, code=1 or 16)` |
| `Abandond_Call` | Int64 | 放弃呼叫 | code=18 |
| `Voice_Mail` | Int64 | 语音信箱 | code=15 |
| `Busy` | Int64 | 占线 | code=5 |
| `Power_Off` | Int64 | 关机 | code=4 |
| `Missed_Call` | Int64 | 未接 | code=3 |
| `Line_Blind_Spot` | Int64 | 线路盲区 | code=14 |
| `DNC` | Int64 | DNC | code=17 |
| `Caller_Has_Unpaid_Bill` | Int64 | 主叫欠费 | code=13 |
| `Receiver_Has_Unpaid_Bill` | Int64 | 被叫欠费 | code=9 |
| `Invalid_Number` | Int64 | 空号 | code=10 |
| `Blacklist` | Int64 | 黑名单 | code=11 |
| `Intercepted` | Int64 | 被拦截 | code=19 |
| `Network_Outage` | Int64 | 网络故障 | code=21 |
| `Answering_Machine` | Int64 | 答录机 | code=22 |
| `out_of_service` | Int64 | 停机 | code=23 |
| `resposed_call` | Int64 | 有应答通话数 | `responsed_calls` |
| `res_score_above` | Int64 | 意图分高于阈值回复数 | |
| `res` | Int64 | 有意图回复数 | |
| `complete_call` | Int64 | 完成通话数 | `ai_hang_up_and_resposed_call_num` |
| `final_tag_call` | Int64 | 最终标签通话数 | |
| `positive_tag_call` | Int64 | 正向标签通话数 | |
| `total_duration` | Int64 | 总时长（秒）| `ring_duration + ai_duration` |
| `connected_call_total_duration` | Int64 | 接通通话总时长 | |
| `connected_talking_duration` | Int64 | 接通 AI 通话时长 | |

---

## 标准查询模式（UNION ALL 右侧）

```sql
SELECT
    Environment,
    'dialogue'                                              AS Statistic_Type,
    enterprise_id                                           AS Enterprise_Id,
    ent_name                                                AS Enterprise_Name,
    template_code                                           AS Dialogue_Code,
    dialogue_name                                           AS Dialogue_Name,
    IVR_flag                                                AS IVR_Flag,
    unique_id                                               AS Unique_ID,
    -- ... 业务字段直接引用
    formatDateTime(toStartOfMonth(dco.s_day), '%Y-%m')      AS period,
    toStartOfMonth(dco.s_day)                               AS period_start,
    contact_phone_number, connected_call, total_call, ...
    -- 无对应字段的补 0 或空串
    0                                                       AS AI_Duration,
    ' '                                                     AS Newly_Created_Unique_ID,
    ''                                                      AS Scenario_Name
FROM integrated_data.Fact_Daily_Call_Contacts_Offline dco
LEFT JOIN (...Dim_Unique_ID...) uni ON uni.Unique_ID = dco.unique_id
LEFT JOIN (...Dim_Unique_ID_Sales_Split...) unp
    ON unp.Contract_Code = uni.Contract_Code
    AND formatDateTime(toStartOfMonth(dco.s_day), '%Y-%m') = formatDateTime(unp.Month_Date, '%Y-%m')
WHERE dco.s_day >= toDate('2024-07-01')
  AND dco.s_day <  toDate('2026-04-01')
```

---

## 注意事项

1. **不需要也不应该**加 `statistic_is_delete = 0`（离线表无此字段或语义不同）
2. **不需要加 FINAL**
3. `IVR_flag` 已预处理，无需重新计算 IVR 规则
4. `AI_Duration`、`Agent_Duration` 离线数据通常补 0（无对应字段）
5. `Newly_Created_Unique_ID` 补空格 `' '`（与在线路的 `Dim_Enterprise.Unique_ID_Latest_Create` 对齐）
6. 与在线路 UNION ALL 时字段顺序和类型必须严格对齐
