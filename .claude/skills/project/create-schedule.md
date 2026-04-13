---
name: create-schedule
description: 通过自然语言创建固定推送任务（定时报告订阅）
triggers:
  - 每天发送
  - 每周发送
  - 定时发送
  - 每月发送
  - 定时推送
  - 周期性报告
  - 自动发送
  - 订阅报告
  - 设置定时
  - 每天早上
  - 每周一
  - 固定推送
  - 定期生成
  - 每天给我
  - 每周给我
always_inject: false
---

# 定时报告创建助手 (create-schedule)

## 你的职责

将用户的自然语言描述转化为结构化的 ScheduledReport 配置，调用 `POST /api/v1/scheduled-reports` 创建定时任务。

## 解析规则

### cron 表达式解析

| 用户说 | cron_expr | 说明 |
|---|---|---|
| 每天早上9点 | `0 9 * * *` | 每天 |
| 每天8点半 | `30 8 * * *` | 每天 |
| 每周一早上9点 | `0 9 * * 1` | 周一 |
| 每周五下午6点 | `0 18 * * 5` | 周五 |
| 工作日每天8点 | `0 8 * * 1-5` | 周一到周五 |
| 每月1号9点 | `0 9 1 * *` | 每月1号 |
| 每两小时 | `0 */2 * * *` | 每2小时 |

### 渠道解析

| 用户说 | channel type |
|---|---|
| 发邮件给 a@b.com | `{"type":"email","to":["a@b.com"]}` |
| 发到企微群 | `{"type":"wecom","webhook_url":"<用户提供>"}` |
| 发到飞书 | `{"type":"feishu","webhook_url":"<用户提供>"}` |

## 操作流程

1. **信息收集** — 若用户描述不完整，逐一询问缺失信息：
   - 报告名称是什么？
   - 执行频率（每天/每周/...）和具体时间？
   - 通知渠道（邮件地址/企微 Webhook/飞书 Webhook）？
   - 报告类型（图表报表 dashboard / 文字报告 document）？

2. **构造 report_spec** — 从对话上下文或用户提供的 SQL/图表信息构造 spec：
   ```json
   {
     "title": "报告名称",
     "charts": [{"id":"c1","chart_lib":"echarts","chart_type":"bar","sql":"...","connection_env":"sg","x_field":"date","y_fields":["value"],"width":"full"}],
     "filters": [],
     "theme": "light"
   }
   ```

3. **确认后创建**：
   ```
   POST /api/v1/scheduled-reports
   {
     "name": "每日销售报告",
     "cron_expr": "0 9 * * *",
     "timezone": "Asia/Shanghai",
     "doc_type": "dashboard",
     "report_spec": {...},
     "notify_channels": [{"type":"email","to":["a@corp.com"]}]
   }
   ```

4. **返回确认** — 显示「已创建定时任务，首次执行时间：xxx」

## 注意事项

- 如果用户没有现成的 SQL，引导他先在对话中生成一个图表，再设置定时任务
- 创建前务必向用户确认执行时间和渠道，避免误操作
- timezone 默认 Asia/Shanghai
