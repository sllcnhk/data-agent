# SG ClickHouse 数据库知识库索引

> **版本**: v3.0 | **更新时间**: 2025-07
> **适用环境**: SG（新加坡）ClickHouse
> **维护说明**: 本文件为 L1 常驻摘要，供 Agent 快速定位；详细信息按需加载对应 L2/L3 文件

---

## 一、数据库总览

| 数据库 | 业务定位 | 核心表数 | 数据量级 |
|--------|---------|---------|---------|
| `crm` | 外呼 CRM 主库（ODS+DWD层） | ~128 张 | ~1.05 TiB |
| `integrated_data` | 跨系统集成层（计费/企业维度）| ~20 张 | ~4 GiB |
| `data_statistics` | 统计汇总层（DWS）| ~15 张 | ~700 MiB |
| `om_statistics` | 运营监控层（CDR话单）| ~10 张 | ~86 GiB |

---

## 二、外呼业务核心表清单（crm 库）

### 2.1 策略外呼维度表

| 表名 | 业务含义 | 主键 | 详细文档 |
|------|---------|------|---------|
| `dim_automatic_task` | 策略定义（Automation/Strategy）| `id` | [→ tables/dim_automatic_task.md](tables/dim_automatic_task.md) |
| `dim_automatic_action` | 策略 Action 节点定义（18种类型）| `action_id` | [→ tables/dim_automatic_action.md](tables/dim_automatic_action.md) |
| `dim_automatic_plan` | 线索批次（每次导入线索创建）| `id` | [→ tables/dim_automatic_plan.md](tables/dim_automatic_plan.md) |
| `dim_automatic_task_customer` | 策略线索明细（父子线索流转）| `id` | [→ tables/dim_automatic_task_customer.md](tables/dim_automatic_task_customer.md) |
| `dim_automatic_plan_action_task` | **核心桥梁表**：Plan×Action→Task | `id` | [→ tables/dim_automatic_plan_action_task.md](tables/dim_automatic_plan_action_task.md) |

### 2.2 外呼任务维度表

| 表名 | 业务含义 | 主键 | 详细文档 |
|------|---------|------|---------|
| `dim_call_task` | 外呼任务（策略生成 or 手动创建）| `call_task_id` | [→ tables/dim_call_task.md](tables/dim_call_task.md) |
| `dim_call_task_customer` | 任务线索明细（含重拨父子线索）| `call_task_customer_id` | [→ tables/dim_call_task_customer.md](tables/dim_call_task_customer.md) |

### 2.3 呼叫记录事实表（最重要）

| 表名 | 业务含义 | 主键 | 详细文档 |
|------|---------|------|---------|
| `realtime_dwd_crm_call_record` | **呼叫明细宽表**（78字段，核心事实表）| `call_record_id` | [→ tables/realtime_dwd_crm_call_record.md](tables/realtime_dwd_crm_call_record.md) |
| `realtime_ods_call_record_dialog_detail` | 对话明细（每轮对话一行）| - | 待文档化 |
| `realtime_ods_crm_call_record_intention` | 呼叫最终意图标签 | - | 待文档化 |

---

## 三、外呼业务两种模式

### 模式一：策略外呼（Automation）

**识别方式**: `realtime_dwd_crm_call_record.automatic_task_id > 0`（约占 15.5%）

**完整链路**:
```
dim_automatic_task（策略定义）
  └─ dim_automatic_action（Action 节点，18种类型）
  └─ dim_automatic_plan（线索批次）
       └─ dim_automatic_task_customer（线索明细，父子流转）
       └─ dim_automatic_plan_action_task（Plan×Action→Task，核心桥梁）
            └─ dim_call_task（外呼任务，is_automatic_task=1）
                 └─ dim_call_task_customer（任务线索，含重拨）
                      └─ realtime_dwd_crm_call_record（呼叫记录）
```

**关键理解**：
- Action 是策略的节点定义（静态配置）
- Plan 驱动线索按 Action 节点和流转条件逐批流转
- 线索每流转到一个 Action 节点，生成一条子线索 + 一个具体 Task

### 模式二：手动外呼（Direct Task）

**识别方式**: `realtime_dwd_crm_call_record.automatic_task_id IS NULL 或 = 0`（约占 84.5%）

**完整链路**:
```
dim_call_task（手动创建，is_automatic_task=0）
  └─ dim_call_task_customer（任务线索，含重拨父子）
       └─ realtime_dwd_crm_call_record（呼叫记录）
```

---

## 四、关键关联键速查

| 关联场景 | 关联键 | 匹配率 |
|---------|--------|--------|
| 策略节点任务 → 外呼任务 | `dim_automatic_plan_action_task.task_id = dim_call_task.call_task_id` | **100%** ✅ |
| 外呼任务 → 呼叫记录 | `dim_call_task.call_task_id = realtime_dwd_crm_call_record.call_task_id` | ~100% ✅ |
| 呼叫记录 → 对话明细 | `realtime_dwd_crm_call_record.call_record_id = dialog_detail.call_record_id` | ✅ |
| 策略直连呼叫记录 | `dim_automatic_task.id = realtime_dwd_crm_call_record.automatic_task_id` | 15.5%填充 |

---

## 五、dim_automatic_action.type 枚举（18种节点类型）

| type | 节点名称 | 是否生成Task |
|------|---------|------------|
| 0 | Entry（入口）| 否 |
| 1 | Talkbot 外呼 | ✅ 生成 dim_call_task |
| 2 | SMS | ✅ 生成 SMS 任务 |
| 3 | WhatsApp | ✅ 生成 WA 任务 |
| 4 | Email | ✅ 生成 Email 任务 |
| 5 | Time Delay（时间延迟）| 否 |
| 6 | Condition Split（条件分支）| 否 |
| 7 | Service Call（API 调用）| 否 |
| 8 | A-B Test | 否 |
| 9 | Exit Node（退出）| 否 |
| 10 | Wait for Event（等待事件）| 否 |
| 11 | Add Tag（打标签）| 否 |
| 12 | WhatsApp Inbound | 否 |
| 14 | Assign（分配）| 否 |
| 15 | Add to Strategy（加入另一策略）| 否 |
| 16 | Viber | ✅ 生成 Viber 任务 |
| 17 | Update Variable（更新变量）| 否 |
| 18 | Add to Blacklist（加黑名单）| 否 |

---

## 六、核心指标口径速查

| 指标 | 计算口径 | 字段 |
|------|---------|------|
| **接通率** | `countIf(call_duration > 0) / count()` | `call_duration` |
| **平均通话时长** | `avg(if(call_duration > 0, call_duration, NULL))` | `call_duration`（秒）|
| **策略外呼占比** | `countIf(automatic_task_id > 0) / count()` | `automatic_task_id` |
| **父线索数（策略）** | `countIf(parent_id = '0')` | `dim_automatic_task_customer.parent_id` |
| **父线索数（手动）** | `countIf(parent_call_task_customer_id IS NULL)` | `dim_call_task_customer` |

---

## 七、查询规范（必须遵守）

1. **PREWHERE 过滤时间**: `PREWHERE call_start_time >= addDays(now(), -N)`
2. **过滤软删除**: `WHERE is_delete = 0`
3. **禁止 SELECT \***: 只选需要的字段（realtime_dwd_crm_call_record 有 78 个字段）
4. **策略外呼过滤 type=1**: `dim_automatic_plan_action_task.type = 1`（外呼任务）
5. **父线索区分方式不同**:
   - 策略线索：`parent_id = '0'`（字符串）
   - 任务线索：`parent_call_task_customer_id IS NULL`
6. **历史数据兼容**: `is_automatic_task` 字段历史数据可能为 NULL，手动任务用 `(is_automatic_task = 0 OR is_automatic_task IS NULL)`

---

## 八、文件目录结构

```
customer_data/db_knowledge/
├── _index.md                          ← 本文件（L1 常驻摘要）
├── relationships.md                   ← L2 完整 ERD + 业务链路图
├── tables/                            ← L2 各表详细文档
│   ├── dim_automatic_task.md          ← 策略定义表
│   ├── dim_automatic_action.md        ← Action 节点定义表
│   ├── dim_automatic_plan.md          ← 线索批次表
│   ├── dim_automatic_task_customer.md ← 策略线索明细（父子流转）
│   ├── dim_automatic_plan_action_task.md ← 核心桥梁表 ⭐
│   ├── dim_call_task.md               ← 外呼任务表 ⭐
│   ├── dim_call_task_customer.md      ← 任务线索明细（重拨）
│   └── realtime_dwd_crm_call_record.md ← 呼叫记录宽表 ⭐
└── metrics/                           ← L3 指标口径文档
    ├── cost_per_call.md
    ├── connect_rate.md
    ├── gmv.md
    └── monthly_bill.md
```

---

## 九、加载指引（给 Agent）

- **首次理解业务**: 先读本文件 → 再读 `relationships.md`
- **分析具体表**: 读 `tables/<表名>.md`
- **跨表分析**: 读 `relationships.md` 确认关联键
- **计算指标**: 读 `metrics/<指标>.md`
- **不要一次性加载所有文件**，按需加载即可
