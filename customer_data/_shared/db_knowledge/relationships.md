# SG ClickHouse 外呼业务 — 跨表关系图（ERD 文字版）

> **版本**: v3.0 | **更新时间**: 2025-07
> **数据库**: crm（主库）+ integrated_data（计费）+ data_statistics（统计）

---

## 一、外呼业务两种模式总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         外呼业务两种模式                              │
├──────────────────────────┬──────────────────────────────────────────┤
│      策略外呼模式          │              手动外呼模式                 │
│  (Automation/Strategy)   │           (Direct Call Task)             │
├──────────────────────────┼──────────────────────────────────────────┤
│ 使用策略功能               │ 不使用策略功能                             │
│ is_automatic_task = 1    │ is_automatic_task = 0 或 NULL             │
│ automatic_task_id > 0    │ automatic_task_id = 0 或 NULL             │
│ 约占 15.5% 呼叫量          │ 约占 84.5% 呼叫量                         │
└──────────────────────────┴──────────────────────────────────────────┘
```

---

## 二、策略外呼完整链路（核心）

### 2.1 业务流程说明

```
① 策略定义阶段（一次性配置）
   企业创建策略（dim_automatic_task）
   → 在策略中定义多个 Action 节点（dim_automatic_action）
     每个节点有类型（外呼/SMS/WhatsApp等）和流转条件

② 批次导入阶段（每次导入线索）
   导入线索 → 创建 Plan（dim_automatic_plan）
   → 为每条线索创建父线索记录（dim_automatic_task_customer, parent_id=NULL）

③ 流转执行阶段（Plan 驱动）
   Plan 按策略定义的 Action 节点和流转条件，驱动线索逐批流转：
   → 线索流转到某 Action 节点
   → 生成子线索记录（dim_automatic_task_customer, parent_id=上一线索ID）
   → 在本表生成关联记录（dim_automatic_plan_action_task）
   → 创建具体执行任务（dim_call_task, type=1时）

④ 任务执行阶段
   外呼任务（dim_call_task）执行呼叫
   → 线索进入任务（dim_call_task_customer）
   → 产生呼叫记录（realtime_dwd_crm_call_record）
   → 生成对话明细（realtime_ods_call_record_dialog_detail）
   → 生成意图标签（realtime_ods_crm_call_record_intention）
```

### 2.2 ERD 关系图

```
crm.dim_automatic_task
  │  id (PK)
  │  name, type, category, enterprise_id
  │
  ├──────────────────────────────────────────────────────┐
  │                                                      │
  ▼ task_id → id                                         │
crm.dim_automatic_plan                                   │
  │  id (PK)                                             │
  │  task_id, name, status, contact_batch_id             │
  │                                                      │
  ├──────────────────────────────┐                       │
  │                              │                       │
  ▼ plan_id → id                 │                       │
crm.dim_automatic_task_customer  │                       │
     id (PK)                     │                       │
     task_id, plan_id            │                       │
     customer_id                 │                       │
     parent_id (NULL=父线索)      │                       │
     ↑ 自关联（父子线索）          │                       │
                                 │                       │
  ┌──────────────────────────────┘                       │
  │                                                      │
  ▼                                                      ▼ automatic_task_id → id
crm.dim_automatic_action ◄────── crm.dim_automatic_plan_action_task
     action_id (PK)  action_id ──►     id (PK)
     plan_id (关联plan)               plan_id
     type (节点类型,18种)              action_id
     name, template_code              task_id ──────────────────────┐
                                      type (1=外呼/2=SMS/3=WA...)    │
                                      automatic_task_id              │
                                                                     │
  ┌──────────────────────────────────────────────────────────────────┘
  │
  ▼ task_id → call_task_id（100%匹配，已验证）
crm.dim_call_task
  │  call_task_id (PK)
  │  name, template_code
  │  is_automatic_task (0=手动, 1=策略)
  │  call_task_type (AI/PERSIST/GROUP/TTS_AI)
  │  status (1未启动/2进行中/3已停止/4已完成)
  │  is_reboot, reboot_num, reboot_period (重拨配置)
  │  enterprise_id
  │
  ├──────────────────────────────────────────────────────┐
  │                                                      │
  ▼ call_task_id → call_task_id                          │
crm.dim_call_task_customer                               │
     call_task_customer_id (PK)                          │
     call_task_id                                        │
     customer_id                                         │
     parent_call_task_customer_id (NULL=父线索)           │
     ↑ 自关联（重拨父子线索）                              │
     status, bus_redial_status                           │
     │                                                   │
     │ call_task_customer_id → call_task_customer_id     │ call_task_id → call_task_id
     └──────────────────────────────────────────────────►│
                                                         ▼
                                          crm.realtime_dwd_crm_call_record
                                               call_record_id (PK)
                                               enterprise_id
                                               call_task_id
                                               call_task_customer_id
                                               customer_id
                                               ── 策略字段（15.5%填充）──
                                               automatic_task_id → dim_automatic_task.id
                                               plan_id           → dim_automatic_plan.id
                                               action_id         → dim_automatic_action.action_id
                                               ── 呼叫结果字段 ──
                                               call_start_time (过滤主字段)
                                               call_duration (>0=接通)
                                               call_type (1=AI外呼,3=人工,12=WA...)
                                               intent_code, intent_name
                                               template_code (关联计费)
                                               │
                                               ├── call_record_id
                                               │       ▼
                                               │   crm.realtime_ods_call_record_dialog_detail
                                               │       (对话明细，每轮对话一行)
                                               │
                                               └── call_record_id
                                                       ▼
                                                   crm.realtime_ods_crm_call_record_intention
                                                       (最终意图标签)
```

---

## 三、手动外呼链路（简化版）

```
企业用户直接创建任务
    ↓
crm.dim_call_task（is_automatic_task=0）
    ↓ call_task_id
crm.dim_call_task_customer（线索明细，含重拨父子线索）
    ↓ call_task_id + call_task_customer_id
crm.realtime_dwd_crm_call_record（automatic_task_id=NULL/0）
    ↓ call_record_id
crm.realtime_ods_call_record_dialog_detail（对话明细）
crm.realtime_ods_crm_call_record_intention（意图标签）
```

---

## 四、计费业务链路

```
crm.realtime_dwd_crm_call_record
    template_code + enterprise_id + call_date
            ↓
integrated_data.Fact_Bill_Usage
    (Environment, Record_Date, Charge_Type_ID, Charge_Type, template_code)
            ↓
integrated_data.Bill_Monthly
    (Year_Month, Enterprise_ID, Charge_Type, Amount)
            ↓
integrated_data.Fact_Bill_DS
    (按天账单事实表)
```

---

## 五、关键关联键速查表

| 关联场景 | 左表.字段 | 右表.字段 | 验证状态 |
|---------|---------|---------|---------|
| 策略→批次 | `dim_automatic_task.id` | `dim_automatic_plan.task_id` | ✅ |
| 批次→节点任务 | `dim_automatic_plan.id` | `dim_automatic_plan_action_task.plan_id` | ✅ |
| 节点定义→节点任务 | `dim_automatic_action.action_id` | `dim_automatic_plan_action_task.action_id` | ✅ |
| **节点任务→外呼任务** | `dim_automatic_plan_action_task.task_id` | `dim_call_task.call_task_id` | ✅ **100%匹配** |
| 外呼任务→线索明细 | `dim_call_task.call_task_id` | `dim_call_task_customer.call_task_id` | ✅ |
| 线索明细→呼叫记录 | `dim_call_task_customer.call_task_customer_id` | `realtime_dwd_crm_call_record.call_task_customer_id` | ✅ |
| 呼叫记录→对话明细 | `realtime_dwd_crm_call_record.call_record_id` | `realtime_ods_call_record_dialog_detail.call_record_id` | ✅ |
| 呼叫记录→意图标签 | `realtime_dwd_crm_call_record.call_record_id` | `realtime_ods_crm_call_record_intention.call_record_id` | ✅ |
| 呼叫记录→策略（宽表直连） | `realtime_dwd_crm_call_record.automatic_task_id` | `dim_automatic_task.id` | ✅ 15.5%填充 |

---

## 六、父子线索机制对比

| 维度 | 策略外呼（dim_automatic_task_customer）| 手动外呼（dim_call_task_customer）|
|------|--------------------------------------|----------------------------------|
| 父线索标识 | `parent_id = 0`（字符串"0"）| `parent_call_task_customer_id IS NULL` |
| 子线索产生时机 | 线索流转到下一个 Action 节点时 | 任务重拨配置触发时 |
| 子线索含义 | 同一线索在不同 Action 节点的记录 | 同一线索的重拨轮次记录 |
| 关联字段 | `parent_id → id`（自关联）| `parent_call_task_customer_id → call_task_customer_id` |

---

## 七、数据分层架构

```
┌─────────────────────────────────────────────────────┐
│  ODS 层（原始数据同步）                                │
│  crm.realtime_ods_*                                  │
│  - realtime_ods_call_record_dialog_detail            │
│  - realtime_ods_crm_call_record_intention            │
├─────────────────────────────────────────────────────┤
│  DWD 层（加工宽表）                                    │
│  crm.realtime_dwd_* + crm.dim_*                      │
│  - realtime_dwd_crm_call_record（核心事实宽表）        │
│  - dim_automatic_task/plan/action/task_customer      │
│  - dim_call_task/dim_call_task_customer              │
├─────────────────────────────────────────────────────┤
│  DWS/汇总层                                           │
│  data_statistics.*                                   │
│  - call_task_entbot_num（按企业/小时汇总）             │
│  - connected_call_record（接通记录汇总）               │
├─────────────────────────────────────────────────────┤
│  集成数据层（跨系统）                                   │
│  integrated_data.*                                   │
│  - Fact_Daily_Call / Fact_Bill_DS / Bill_Monthly     │
│  - Dim_Enterprise / Dim_Automatic_Task               │
└─────────────────────────────────────────────────────┘
```

---

## 八、常见分析场景的查询路径

### 场景1：分析某策略的整体呼叫效果
```
dim_automatic_task（策略名称）
  → realtime_dwd_crm_call_record（直接用 automatic_task_id 关联，无需多表 JOIN）
```

### 场景2：分析某策略各 Action 节点的效果
```
dim_automatic_task → dim_automatic_plan_action_task → dim_call_task
  → realtime_dwd_crm_call_record（通过 call_task_id 关联）
```

### 场景3：追踪某线索的完整流转路径
```
dim_automatic_task_customer（父线索）
  → dim_automatic_task_customer（子线索，parent_id 自关联）
  → dim_automatic_plan_action_task（action 节点）
  → realtime_dwd_crm_call_record（呼叫记录）
```

### 场景4：计算某企业的呼叫成本
```
realtime_dwd_crm_call_record（template_code + enterprise_id）
  → integrated_data.Fact_Bill_Usage（计费明细）
  → integrated_data.Bill_Monthly（月度账单）
```

### 场景5：分析通话内容与意图
```
realtime_dwd_crm_call_record（call_record_id）
  → realtime_ods_call_record_dialog_detail（对话文本）
  → realtime_ods_crm_call_record_intention（最终意图）
```
