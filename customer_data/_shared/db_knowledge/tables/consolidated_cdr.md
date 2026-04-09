# 表：om_statistics.consolidated_cdr

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | om_statistics |
| 表名 | consolidated_cdr |
| 数据量 | ~7.9亿行 |
| 磁盘大小 | 85.95 GiB |
| 数据层级 | DWS（汇总层） |
| 业务域 | 运营监控 / 话务分析 |
| 时区 | Asia/Singapore |

## 业务语义

**综合话单记录表（CDR: Call Detail Record）**，是运营监控的核心原始数据表。记录每一次通话的完整技术参数，包含主被叫信息、网络层IP、通话状态码、通话时长等。

**核心用途**：
- 话务量监控与统计
- 通话质量分析（SIP状态码分布）
- 网络层故障定位（IP追踪）
- 接通率计算

## 字段详情

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| **CallId** | String | 通话唯一标识 | 主键 |
| **CallStartTime** | DateTime('Asia/Singapore') | 通话开始时间 | 分区键 |
| **CallEndTime** | DateTime('Asia/Singapore') | 通话结束时间 | |
| **Duration** | UInt32 | 通话时长（秒） | |
| **BillDuration** | UInt32 | 计费时长（秒） | 与 Duration 可能不同 |
| **CallerNumber** | String | 主叫号码 | 脱敏存储 |
| **CalleeNumber** | String | 被叫号码 | 脱敏存储 |
| **CallerIp** | String | 主叫侧IP | SIP信令IP |
| **CalleeIp** | String | 被叫侧IP | SIP信令IP |
| **CallerRtpIp** | String | 主叫RTP媒体IP | 语音流IP |
| **CalleeRtpIp** | String | 被叫RTP媒体IP | 语音流IP |
| **GatewayName** | LowCardinality(String) | 网关名称 | 出口网关标识 |
| **Region** | LowCardinality(String) | 区域标识 | 如 SG, AU, MY 等 |
| **EnterpriseName** | LowCardinality(String) | 企业名称 | |
| **EnterpriseId** | Int64 | 企业ID | 关联 Dim_Enterprise |
| **TemplateCode** | LowCardinality(String) | 模板编码 | 话术模板标识 |
| **CallCode** | LowCardinality(String) | 通话结果码 | 见枚举值说明 |
| **SipCode** | UInt16 | SIP响应码 | 标准SIP协议状态码 |
| **Direction** | LowCardinality(String) | 通话方向 | outbound=外呼, inbound=呼入 |
| **CallType** | Int8 | 通话类型 | 1=AI外呼, 2=人工外呼 等 |

## 枚举值说明

### CallCode（通话结果码）
| 值 | 含义 |
|----|------|
| `Answered` | 已接通 |
| `NoAnswer` | 无应答 |
| `Voicemail` | 语音信箱 |
| `AnswerMachine` | 应答机（自动语音） |
| `CallBusy` | 忙线 |
| `PowerOff` | 关机 |
| `OutOfService` | 停机/无服务 |
| `InvalidNumber` | 无效号码 |
| `NoRoute` | 无路由 |
| `Code403` | SIP 403 Forbidden |
| `Code404` | SIP 404 Not Found |
| `Code480` | SIP 480 Temporarily Unavailable |
| `Code486` | SIP 486 Busy Here |
| `Code503` | SIP 503 Service Unavailable |
| `Code603` | SIP 603 Decline |

### SipCode（标准SIP响应码）
| 范围 | 含义 |
|------|------|
| 200 | 成功（接通） |
| 403 | 禁止访问 |
| 404 | 号码不存在 |
| 408 | 请求超时 |
| 480 | 暂时不可用 |
| 484 | 地址不完整 |
| 486 | 忙线 |
| 488 | 不可接受 |
| 500 | 服务器内部错误 |
| 503 | 服务不可用 |
| 600 | 全线忙 |
| 603 | 拒绝 |

## 关联关系

```
om_statistics.consolidated_cdr
    ├── EnterpriseId ──→ integrated_data.Dim_Enterprise.Enterprise_ID
    ├── TemplateCode ──→ data_statistics.connected_call_record.template_code
    ├── CallId ────────→ crm.realtime_dwd_crm_call_record_dialog_detail (通过 call_record_id 间接关联)
    └── Region ────────→ om_statistics.cdr_statistics_hourly.Region
```

## 注意事项

1. **数据量极大**（7.9亿行），查询必须带 `CallStartTime` 时间范围过滤
2. 时区为 Asia/Singapore，跨时区比较时注意转换
3. `GatewayName`、`Region`、`EnterpriseName` 使用 `LowCardinality` 优化，适合 GROUP BY
4. 接通率计算：`countIf(CallCode = 'Answered') / count()`
5. 该表是 `cdr_statistics_hourly` 的原始数据来源

## 典型查询

```sql
-- 统计某企业某天的接通率
SELECT 
    toDate(CallStartTime) AS call_date,
    count() AS total_calls,
    countIf(CallCode = 'Answered') AS answered_calls,
    countIf(CallCode = 'Answered') / count() AS connect_rate
FROM om_statistics.consolidated_cdr
WHERE EnterpriseId = ?
    AND CallStartTime >= '2026-03-01'
    AND CallStartTime < '2026-03-12'
GROUP BY call_date
ORDER BY call_date;

-- 统计各失败原因分布
SELECT 
    CallCode,
    count() AS cnt,
    count() / sum(count()) OVER () AS ratio
FROM om_statistics.consolidated_cdr
WHERE CallStartTime >= today() - 7
    AND CallCode != 'Answered'
GROUP BY CallCode
ORDER BY cnt DESC;
```
