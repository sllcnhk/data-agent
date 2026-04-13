# 表：integrated_data.Dim_Dialogue

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | `integrated_data` |
| 表名 | `Dim_Dialogue` |
| 数据层级 | DIM（维度表）|
| 业务域 | 话术/模板维度 |
| 读取方式 | **必须加 `FINAL`** |

## 业务语义

**话术（对话模板）维度表**。记录各环境企业配置的 Talkbot 话术信息，是 IVR 判断和话术分析的核心维度。

核心用途：
- 通过 `switch` 字段和 `speech_name` 命名规则判断是否为 IVR
- 通过 `Unique_id` 关联 `Dim_Unique_ID` 获取项目属性
- 通过 `template_code` 关联 `Fact_Daily_Call`、`Fact_Call_Unique_Phone` 等事实表

---

## 表结构

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `SaaS` | String | 环境标识 | 如 IDN, THAI, SG 等 |
| `enterprise_id` | Int64 | 企业 ID | |
| `template_code` | String | 话术模板编码 | **核心关联键**，关联所有事实表 |
| `speech_name` | String | 话术名称 | IVR 判断的文本依据 |
| `switch` | String | IVR 开关 | `'0'` = IVR 模式；其他或空 = 非 IVR |
| `Unique_id` | String | 项目 Unique ID | 关联 `Dim_Unique_ID.Unique_ID` |
| `scenario_name` | String | 场景名称 | 话术所属场景 |
| `statistic_is_delete` | Int8 | 软删除 | `0`=有效；**必须过滤** |

---

## IVR 判断完整逻辑

```sql
case
    when speech.switch = '0' then 'IVR'
    when match(LOWER(speech.speech_name), '(?i)[^a-zA-Z]ivr[^a-zA-Z]') = 1
      OR match(LOWER(speech.speech_name), '(?i)^ivr[^a-zA-Z]') = 1
      OR match(LOWER(speech.speech_name), '(?i)[^a-zA-Z]ivr$') = 1
      OR LOWER(speech.speech_name) = 'ivr'                          then 'IVR'
    else 'NON_IVR'
end as IVR_Flag
```

**判断优先级**：
1. `switch = '0'` → 优先，不看名称
2. 名称中包含 `ivr`（大小写不敏感，词边界匹配）→ IVR
3. 其余 → NON_IVR

**四条 regex 覆盖场景**：
- `[^a-zA-Z]ivr[^a-zA-Z]`：中间出现，如 `call_ivr_flow`
- `^ivr[^a-zA-Z]`：开头，如 `ivr-main`
- `[^a-zA-Z]ivr$`：结尾，如 `call_ivr`
- `= 'ivr'`：完整名称就是 ivr

---

## 标准查询模式

```sql
SELECT
    speech.SaaS,
    speech.enterprise_id,
    speech.template_code,
    speech.speech_name,
    speech.Unique_id,
    speech.scenario_name,
    case
        when speech.switch = '0' then 'IVR'
        when match(LOWER(speech.speech_name), '(?i)[^a-zA-Z]ivr[^a-zA-Z]') = 1
          OR match(LOWER(speech.speech_name), '(?i)^ivr[^a-zA-Z]') = 1
          OR match(LOWER(speech.speech_name), '(?i)[^a-zA-Z]ivr$') = 1
          OR LOWER(speech.speech_name) = 'ivr' then 'IVR'
        else 'NON_IVR'
    end as IVR_Flag
FROM integrated_data.Dim_Dialogue speech FINAL
WHERE speech.statistic_is_delete = 0
```

---

## 关联关系

| 关联表 | 关联字段 | 说明 |
|--------|----------|------|
| `Fact_Daily_Call` | `SaaS + enterprise_id + template_code` | 事实关联 |
| `Fact_Call_Unique_Phone` | `SaaS + enterprise_id + type_value(=template_code)` | 注意字段名不同 |
| `Dim_Unique_ID` | `Unique_id` = `Unique_ID` | 获取项目属性 |
