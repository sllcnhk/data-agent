# 表：integrated_data.Dim_Enterprise

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | integrated_data |
| 表名 | Dim_Enterprise |
| 数据层级 | DIM（维度表） |
| 业务域 | 企业管理（核心维度） |
| 更新方式 | 定期同步 |

## 业务语义

**企业维度表**，是整个数据仓库的核心维度表。记录所有在系统中注册的企业信息，包括企业基本属性、所属部署环境、企业来源系统等。

**核心用途**：
- 所有业务分析的维度关联（通过 Enterprise_ID）
- 企业筛选与分组
- 环境（部署区域）划分

## 字段详情

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| **Environment** | String | 部署环境/区域 | 见枚举值说明 |
| **Enterprise_ID** | Int64 | 企业唯一ID | 主键，雪花ID |
| **Enterprise_Name** | String | 企业名称 | |
| **Ai_Number** | Int16 | AI并发数 | 企业购买的AI并发通道数 |
| **Create_Time** | DateTime | 企业创建时间 | |
| **Create_User** | String | 创建人 | |
| **Country** | String | 企业所在国家 | |
| **statistic_is_delete** | Int16 | 统计删除标记 | 0=有效, 1=已删除 |
| **Ent_Source** | String | 企业来源系统 | 见枚举值说明 |
| **Customer_ID** | String | 外部客户ID | 销售CRM中的客户编号 |
| **Unique_ID_Latest_Create** | String | 最新创建的唯一标识 | 格式: CustomerID-x-x |
| **Name_Test_Flag** | Int16 | 测试企业标记 | 0=正式企业, 1=测试企业 |

## 枚举值说明

### Environment（部署环境）
| 值 | 含义 |
|----|------|
| `SG` | 新加坡（Singapore，主环境） |
| `SG-Azure` | 新加坡 Azure 云 |
| `AU` | 澳大利亚（Australia） |
| `MY` | 马来西亚（Malaysia） |
| `THAI` | 泰国（Thailand） |
| `IDN` | 印度尼西亚（Indonesia） |
| `IDN-Sampoerna` | 印尼 Sampoerna 专属环境 |
| `MX` | 墨西哥（Mexico） |
| `BR` | 巴西（Brazil） |

### Ent_Source（企业来源系统）
| 值 | 含义 |
|----|------|
| `crm` | 来自 CRM 系统注册 |
| `engage` | 来自 Engage 系统注册 |
| `chatbot` | 来自 Chatbot 系统注册 |

### Name_Test_Flag（测试标记）
| 值 | 含义 |
|----|------|
| `0` | 正式企业（生产数据） |
| `1` | 测试企业（应在统计中排除） |

## 关联关系

```
integrated_data.Dim_Enterprise
    └── Enterprise_ID ←── integrated_data.Bill_Monthly.Enterprise_ID
    └── Enterprise_ID ←── integrated_data.Fact_Bill_DS.Enterprise_ID
    └── Enterprise_ID ←── integrated_data.Fact_Daily_Call.enterprise_id
    └── Enterprise_ID ←── om_statistics.consolidated_cdr.EnterpriseId
    └── Enterprise_ID ←── crm.realtime_ods_crm_call_record_intention.ent_id
    └── Enterprise_ID ←── data_statistics.call_task_entbot_num.enterprise_id
    └── Enterprise_ID ←── data_statistics.connected_call_record.enterprise_id
```

## 注意事项

1. **过滤测试数据**：正式分析时必须加 `Name_Test_Flag = 0` 过滤测试企业
2. **过滤删除企业**：加 `statistic_is_delete = 0` 过滤已删除企业
3. `Ai_Number` 代表企业购买的 AI 并发通道数，是企业规模的重要指标
4. 不同 `Environment` 对应不同的数据库实例，`SG` 是主环境
5. `Customer_ID` 是销售侧 CRM 的客户编号，用于与销售数据关联

## 典型查询

```sql
-- 查询所有正式企业（排除测试）
SELECT 
    Environment,
    Enterprise_ID,
    Enterprise_Name,
    Ai_Number,
    Country
FROM integrated_data.Dim_Enterprise
WHERE Name_Test_Flag = 0 
    AND statistic_is_delete = 0
ORDER BY Create_Time DESC;

-- 按环境统计企业数量
SELECT 
    Environment,
    count() AS ent_count,
    sum(Ai_Number) AS total_ai_channels
FROM integrated_data.Dim_Enterprise
WHERE Name_Test_Flag = 0 AND statistic_is_delete = 0
GROUP BY Environment
ORDER BY ent_count DESC;
```
