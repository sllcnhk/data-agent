/**
 * DataCenterSchedules — 数据管理中心·固定推送任务
 *
 * 展示定时推送任务列表，支持新建、编辑、立即执行、暂停/启用、历史查看、删除操作。
 * 每个任务可配置多个通知渠道（email / wecom / feishu / webhook）。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  message as antMessage,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Card,
  Divider,
  Spin,
  Empty,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CalendarOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  MailOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/useAuthStore';
import DataCenterCopilot from '../components/DataCenterCopilot';

const { Title, Text } = Typography;
const { Option } = Select;

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1') as string;

// ── 类型定义 ────────────────────────────────────────────────────────────────

interface NotifyChannel {
  type: 'email' | 'wecom' | 'feishu' | 'webhook';
  to?: string;          // email 收件人
  webhook_url?: string; // wecom / feishu / webhook URL
}

export interface ScheduleItem {
  id: string;
  name: string;
  description?: string;
  cron_expr: string;
  timezone: string;
  is_active: boolean;
  last_run_at?: string;
  next_run_at?: string;
  run_count: number;
  fail_count: number;
  notify_channels: NotifyChannel[];
  doc_type: string;
  created_at: string;
  include_summary?: boolean;
}

interface RunLogItem {
  id: string;
  run_at: string;
  status: 'success' | 'failed' | 'running';
  duration_sec?: number;
  error_msg?: string;
}

// ── 常用 cron 预设 ──────────────────────────────────────────────────────────

const CRON_PRESETS = [
  { label: '每天 08:00', value: '0 8 * * *' },
  { label: '每天 18:00', value: '0 18 * * *' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每月 1 日 08:00', value: '0 8 1 * *' },
  { label: '每小时', value: '0 * * * *' },
];

// ── 渠道图标映射 ────────────────────────────────────────────────────────────

const channelIcon = (type: string) => {
  switch (type) {
    case 'email':   return <MailOutlined style={{ color: '#1677ff' }} />;
    case 'wecom':   return <span style={{ fontSize: 12, color: '#07c160' }}>企</span>;
    case 'feishu':  return <span style={{ fontSize: 12, color: '#3370ff' }}>飞</span>;
    case 'webhook': return <span style={{ fontSize: 12, color: '#fa8c16' }}>🔗</span>;
    default:        return null;
  }
};

// ── 主组件 ──────────────────────────────────────────────────────────────────

const DataCenterSchedules: React.FC = () => {
  const { accessToken } = useAuthStore();
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // 新建 / 编辑 Drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<ScheduleItem | null>(null);
  const [form] = Form.useForm();
  const [channels, setChannels] = useState<NotifyChannel[]>([]);
  const [saving, setSaving] = useState(false);

  // 历史记录 Drawer
  const [historyDrawerId, setHistoryDrawerId] = useState<string | null>(null);
  const [runLogs, setRunLogs] = useState<RunLogItem[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  // AI 助手 Drawer
  const [copilotSchedule, setCopilotSchedule] = useState<ScheduleItem | null>(null);

  const authHeaders = useCallback(
    () => ({
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    }),
    [accessToken],
  );

  // ── 拉取任务列表 ───────────────────────────────────────────────────────────

  const fetchSchedules = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/scheduled-reports?page=1&page_size=50`,
        { headers: authHeaders() },
      );
      const json = await res.json();
      if (json.success) {
        setSchedules(json.data.items || []);
        setTotal(json.data.total || 0);
      } else {
        antMessage.error(json.detail || '获取推送任务失败');
      }
    } catch {
      antMessage.error('获取推送任务失败');
    } finally {
      setLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    fetchSchedules();
  }, []);

  // ── 历史记录 ────────────────────────────────────────────────────────────────

  const fetchHistory = useCallback(
    async (id: string) => {
      setLogsLoading(true);
      try {
        const res = await fetch(
          `${API_BASE}/scheduled-reports/${id}/history`,
          { headers: authHeaders() },
        );
        const json = await res.json();
        if (json.success) {
          setRunLogs(json.data || []);
        } else {
          antMessage.error(json.detail || '获取历史记录失败');
        }
      } catch {
        antMessage.error('获取历史记录失败');
      } finally {
        setLogsLoading(false);
      }
    },
    [authHeaders],
  );

  const openHistory = (id: string) => {
    setHistoryDrawerId(id);
    fetchHistory(id);
  };

  // ── 立即执行 ────────────────────────────────────────────────────────────────

  const handleRunNow = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/scheduled-reports/${id}/run-now`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const json = await res.json();
      if (json.success) {
        antMessage.success('任务已触发，请稍后查看历史记录');
      } else {
        antMessage.error(json.detail || '触发失败');
      }
    } catch {
      antMessage.error('触发失败');
    }
  };

  // ── 启用 / 暂停切换 ─────────────────────────────────────────────────────────

  const handleToggle = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/scheduled-reports/${id}/toggle`, {
        method: 'PUT',
        headers: authHeaders(),
      });
      const json = await res.json();
      if (json.success) {
        antMessage.success('状态已更新');
        fetchSchedules();
      } else {
        antMessage.error(json.detail || '操作失败');
      }
    } catch {
      antMessage.error('操作失败');
    }
  };

  // ── 删除 ────────────────────────────────────────────────────────────────────

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/scheduled-reports/${id}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      const json = await res.json();
      if (json.success) {
        antMessage.success('任务已删除');
        fetchSchedules();
      } else {
        antMessage.error(json.detail || '删除失败');
      }
    } catch {
      antMessage.error('删除失败');
    }
  };

  // ── 新建 / 编辑 Drawer ──────────────────────────────────────────────────────

  const openCreateDrawer = () => {
    setEditingSchedule(null);
    setChannels([]);
    form.resetFields();
    form.setFieldsValue({ timezone: 'Asia/Shanghai', doc_type: 'dashboard', include_summary: false });
    setDrawerOpen(true);
  };

  const openEditDrawer = (item: ScheduleItem) => {
    setEditingSchedule(item);
    setChannels(item.notify_channels || []);
    form.setFieldsValue({
      name: item.name,
      description: item.description,
      doc_type: item.doc_type,
      cron_expr: item.cron_expr,
      timezone: item.timezone,
      include_summary: item.include_summary ?? false,
    });
    setDrawerOpen(true);
  };

  const handleSave = async () => {
    let values: any;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    const body = { ...values, notify_channels: channels };
    try {
      const url = editingSchedule
        ? `${API_BASE}/scheduled-reports/${editingSchedule.id}`
        : `${API_BASE}/scheduled-reports`;
      const method = editingSchedule ? 'PUT' : 'POST';
      const res = await fetch(url, {
        method,
        headers: authHeaders(),
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (json.success) {
        antMessage.success(editingSchedule ? '任务已更新' : '任务已创建');
        setDrawerOpen(false);
        fetchSchedules();
      } else {
        antMessage.error(json.detail || '保存失败');
      }
    } catch {
      antMessage.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  // ── 渠道管理 ────────────────────────────────────────────────────────────────

  const addChannel = () => {
    setChannels((prev) => [...prev, { type: 'email', to: '' }]);
  };

  const updateChannel = (idx: number, patch: Partial<NotifyChannel>) => {
    setChannels((prev) =>
      prev.map((ch, i) => (i === idx ? { ...ch, ...patch } : ch)),
    );
  };

  const removeChannel = (idx: number) => {
    setChannels((prev) => prev.filter((_, i) => i !== idx));
  };

  // ── 历史记录表格列 ──────────────────────────────────────────────────────────

  const logColumns: ColumnsType<RunLogItem> = [
    {
      title: '执行时间',
      dataIndex: 'run_at',
      render: (t: string) => (t ? new Date(t).toLocaleString('zh-CN') : '-'),
      width: 160,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 80,
      render: (s: string) => {
        const map: Record<string, string> = {
          success: 'success',
          failed: 'error',
          running: 'processing',
        };
        const labelMap: Record<string, string> = {
          success: '成功',
          failed: '失败',
          running: '执行中',
        };
        return <Tag color={map[s] || 'default'}>{labelMap[s] || s}</Tag>;
      },
    },
    {
      title: '耗时(秒)',
      dataIndex: 'duration_sec',
      width: 90,
      render: (v?: number) => (v != null ? v.toFixed(1) : '-'),
    },
    {
      title: '错误信息',
      dataIndex: 'error_msg',
      render: (msg?: string) =>
        msg ? (
          <Tooltip title={msg}>
            <Text type="danger" ellipsis style={{ maxWidth: 200 }}>
              {msg}
            </Text>
          </Tooltip>
        ) : (
          '-'
        ),
    },
  ];

  // ── 渲染 ─────────────────────────────────────────────────────────────────────

  return (
    <div style={{ padding: 24 }}>
      {/* ── 顶部工具栏 ────────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          📅 固定推送任务
          <Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginLeft: 8 }}>
            共 {total} 个
          </Text>
        </Title>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={fetchSchedules}
            loading={loading}
          >
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreateDrawer}
          >
            新建任务
          </Button>
        </Space>
      </div>

      {/* ── 任务卡片列表 ─────────────────────────────────────────────────── */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" tip="加载中..." />
        </div>
      ) : schedules.length === 0 ? (
        <Empty
          description="暂无推送任务，点击「新建任务」创建第一个"
          style={{ padding: 60 }}
        />
      ) : (
        <Row gutter={[16, 16]}>
          {schedules.map((s) => (
            <Col key={s.id} xs={24} xl={12}>
              <Card
                size="small"
                hoverable
                title={
                  <Space size={8}>
                    <Badge
                      status={s.is_active ? 'success' : 'default'}
                      text={
                        <Text strong style={{ fontSize: 14 }}>
                          {s.name}
                        </Text>
                      }
                    />
                    <Tag color="blue">{s.doc_type}</Tag>
                  </Space>
                }
                extra={
                  <Space size={4}>
                    <Tooltip title="查看历史">
                      <Button
                        size="small"
                        icon={<HistoryOutlined />}
                        onClick={() => openHistory(s.id)}
                      />
                    </Tooltip>
                    <Popconfirm
                      title="立即执行该任务？"
                      description="将在后台触发一次推送"
                      onConfirm={() => handleRunNow(s.id)}
                      okText="执行"
                      cancelText="取消"
                    >
                      <Tooltip title="立即执行">
                        <Button
                          size="small"
                          icon={<ThunderboltOutlined />}
                          type="dashed"
                        />
                      </Tooltip>
                    </Popconfirm>
                    <Tooltip title="编辑">
                      <Button
                        size="small"
                        icon={<EditOutlined />}
                        onClick={() => openEditDrawer(s)}
                      />
                    </Tooltip>
                    <Tooltip title={s.is_active ? '暂停' : '启用'}>
                      <Button
                        size="small"
                        icon={s.is_active ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                        onClick={() => handleToggle(s.id)}
                        type={s.is_active ? 'default' : 'primary'}
                      />
                    </Tooltip>
                    <Tooltip title="AI 助手">
                      <Button
                        size="small"
                        icon={<RobotOutlined />}
                        style={{ color: '#52c41a', borderColor: '#52c41a' }}
                        onClick={() => setCopilotSchedule(s)}
                      />
                    </Tooltip>
                    <Popconfirm
                      title="确定删除该任务？"
                      onConfirm={() => handleDelete(s.id)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                    >
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                }
              >
                <Descriptions column={2} size="small" labelStyle={{ color: '#888' }}>
                  <Descriptions.Item label="Cron">
                    <Text code>{s.cron_expr}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="时区">{s.timezone}</Descriptions.Item>
                  <Descriptions.Item label="下次执行">
                    {s.next_run_at
                      ? new Date(s.next_run_at).toLocaleString('zh-CN')
                      : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="最后执行">
                    {s.last_run_at
                      ? new Date(s.last_run_at).toLocaleString('zh-CN')
                      : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="成功 / 失败">
                    <Text type="success">{s.run_count}</Text>
                    {' / '}
                    <Text type="danger">{s.fail_count}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="渠道">
                    <Space size={4}>
                      {(s.notify_channels || []).map((ch, i) => (
                        <Tooltip key={i} title={`${ch.type}: ${ch.to || ch.webhook_url || ''}`}>
                          {channelIcon(ch.type)}
                        </Tooltip>
                      ))}
                      {(!s.notify_channels || s.notify_channels.length === 0) && '-'}
                    </Space>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {/* ── 新建 / 编辑 Drawer ───────────────────────────────────────────── */}
      <Drawer
        title={editingSchedule ? '编辑推送任务' : '新建推送任务'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={480}
        destroyOnClose
        extra={
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={saving} onClick={handleSave}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label="任务名称"
            name="name"
            rules={[{ required: true, message: '请输入任务名称' }]}
          >
            <Input placeholder="例：每日销售报告推送" />
          </Form.Item>

          <Form.Item label="描述" name="description">
            <Input.TextArea rows={2} placeholder="任务说明（可选）" />
          </Form.Item>

          <Form.Item
            label="报告类型"
            name="doc_type"
            rules={[{ required: true, message: '请选择报告类型' }]}
          >
            <Select placeholder="选择类型">
              <Option value="dashboard">报表 (Dashboard)</Option>
              <Option value="document">报告 (Document)</Option>
            </Select>
          </Form.Item>

          <Form.Item
            label={
              <Space>
                Cron 表达式
                <Select
                  size="small"
                  placeholder="常用预设"
                  style={{ width: 160 }}
                  onChange={(v) => form.setFieldValue('cron_expr', v)}
                  options={CRON_PRESETS.map((p) => ({ label: p.label, value: p.value }))}
                />
              </Space>
            }
            name="cron_expr"
            rules={[{ required: true, message: '请输入 Cron 表达式' }]}
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                格式：分 时 日 月 星期（如 <Text code>0 8 * * 1</Text> = 每周一 08:00）
              </Text>
            }
          >
            <Input placeholder="0 8 * * *" />
          </Form.Item>

          <Form.Item
            label="时区"
            name="timezone"
            rules={[{ required: true, message: '请输入时区' }]}
          >
            <Select showSearch placeholder="选择时区">
              <Option value="Asia/Shanghai">Asia/Shanghai（北京时间）</Option>
              <Option value="Asia/Tokyo">Asia/Tokyo</Option>
              <Option value="UTC">UTC</Option>
              <Option value="America/New_York">America/New_York</Option>
              <Option value="Europe/London">Europe/London</Option>
            </Select>
          </Form.Item>

          <Form.Item label="附带 AI 摘要" name="include_summary" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Divider orientation="left" orientationMargin={0}>
            <Space>
              <CalendarOutlined />
              通知渠道
              <Button size="small" icon={<PlusOutlined />} onClick={addChannel}>
                添加
              </Button>
            </Space>
          </Divider>

          {channels.length === 0 && (
            <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
              暂无通知渠道，点击「添加」配置推送目标
            </Text>
          )}

          {channels.map((ch, idx) => (
            <Card
              key={idx}
              size="small"
              style={{ marginBottom: 8 }}
              extra={
                <Button
                  size="small"
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => removeChannel(idx)}
                />
              }
            >
              <Row gutter={8}>
                <Col span={8}>
                  <Select
                    value={ch.type}
                    onChange={(v) => updateChannel(idx, { type: v, to: undefined, webhook_url: undefined })}
                    style={{ width: '100%' }}
                    size="small"
                  >
                    <Option value="email">邮件</Option>
                    <Option value="wecom">企业微信</Option>
                    <Option value="feishu">飞书</Option>
                    <Option value="webhook">Webhook</Option>
                  </Select>
                </Col>
                <Col span={16}>
                  {ch.type === 'email' ? (
                    <Input
                      size="small"
                      placeholder="收件人邮箱（多个用逗号分隔）"
                      value={ch.to}
                      onChange={(e) => updateChannel(idx, { to: e.target.value })}
                    />
                  ) : (
                    <Input
                      size="small"
                      placeholder="Webhook URL"
                      value={ch.webhook_url}
                      onChange={(e) => updateChannel(idx, { webhook_url: e.target.value })}
                    />
                  )}
                </Col>
              </Row>
            </Card>
          ))}
        </Form>
      </Drawer>

      {/* ── 历史记录 Drawer ──────────────────────────────────────────────── */}
      <Drawer
        title="执行历史"
        open={!!historyDrawerId}
        onClose={() => setHistoryDrawerId(null)}
        width={600}
        destroyOnClose
        styles={{ body: { position: 'relative', paddingBottom: 72 } }}
        extra={
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => historyDrawerId && fetchHistory(historyDrawerId)}
          >
            刷新
          </Button>
        }
      >
        {logsLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin tip="加载历史记录..." />
          </div>
        ) : (
          <Table
            columns={logColumns}
            dataSource={runLogs}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 20, showSizeChanger: false }}
            locale={{ emptyText: <Empty description="暂无执行记录" /> }}
          />
        )}

        {/* ── Pilot FAB（历史抽屉右下角） ──────────────────────────────── */}
        <Tooltip title="AI 助手 Pilot" placement="left">
          <button
            style={{
              position: 'absolute',
              bottom: 20,
              right: 20,
              width: 44,
              height: 44,
              borderRadius: '50%',
              background: '#52c41a',
              border: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 4px 12px rgba(82,196,26,0.4)',
              zIndex: 10,
              color: '#fff',
              fontSize: 18,
            }}
            onClick={() => {
              const s = schedules.find((sc) => sc.id === historyDrawerId);
              if (s) setCopilotSchedule(s);
            }}
          >
            <RobotOutlined style={{ fontSize: 18 }} />
          </button>
        </Tooltip>
      </Drawer>

      {/* ── AI 助手 Drawer ──────────────────────────────────────────────── */}
      {/* ── AI 助手 Co-pilot ─────────────────────────────────────────────── */}
      <DataCenterCopilot
        open={!!copilotSchedule}
        onClose={() => setCopilotSchedule(null)}
        contextType="schedule"
        contextId={copilotSchedule?.id ?? ''}
        contextName={copilotSchedule?.name ?? ''}
        contextSpec={copilotSchedule ?? null}
        onSpecUpdated={fetchSchedules}
      />
    </div>
  );
};

export default DataCenterSchedules;
