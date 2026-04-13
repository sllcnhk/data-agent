/**
 * DataCenterDocuments — 数据管理中心·报告清单
 *
 * 展示 doc_type=document 的报告，以 Ant Design Table 呈现。
 * 支持预览、导出 PDF/PPTX、AI 助手、删除操作。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Empty,
  message as antMessage,
  Popconfirm,
  Row,
  Col,
  Card,
  Statistic,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  Input,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DeleteOutlined,
  EyeOutlined,
  FilePdfOutlined,
  FileOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/useAuthStore';
import ReportPreviewModal from '../components/chat/ReportPreviewModal';
import DataCenterCopilot from '../components/DataCenterCopilot';
import type { ReportItem } from './DataCenterDashboards';

const { Title, Text } = Typography;

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1') as string;

const DataCenterDocuments: React.FC = () => {
  const { accessToken } = useAuthStore();
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [previewReport, setPreviewReport] = useState<ReportItem | null>(null);
  const [copilotReport, setCopilotReport] = useState<ReportItem | null>(null);
  const [exporting, setExporting] = useState<Record<string, boolean>>({});
  const [searchText, setSearchText] = useState('');

  const authHeaders = useCallback(
    () => ({
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    }),
    [accessToken],
  );

  const fetchReports = useCallback(
    async (p = 1) => {
      setLoading(true);
      try {
        const res = await fetch(
          `${API_BASE}/reports?doc_type=document&page=${p}&page_size=20`,
          { headers: authHeaders() },
        );
        const json = await res.json();
        if (json.success) {
          setReports(json.data.items || []);
          setTotal(json.data.total || 0);
        } else {
          antMessage.error(json.detail || '获取报告列表失败');
        }
      } catch {
        antMessage.error('获取报告列表失败');
      } finally {
        setLoading(false);
      }
    },
    [authHeaders],
  );

  useEffect(() => {
    fetchReports(page);
  }, [page]);

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/reports/${id}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      const json = await res.json();
      if (json.success) {
        antMessage.success('报告已删除');
        fetchReports(page);
      } else {
        antMessage.error(json.detail || '删除失败');
      }
    } catch {
      antMessage.error('删除失败');
    }
  };

  const handleExport = async (report: ReportItem, fmt: 'pdf' | 'pptx') => {
    const key = `${report.id}-${fmt}`;
    setExporting((prev) => ({ ...prev, [key]: true }));
    try {
      const res = await fetch(`${API_BASE}/reports/${report.id}/export`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ format: fmt }),
      });
      const json = await res.json();
      if (!json.success) throw new Error(json.detail || '导出请求失败');
      const jobId = json.data?.job_id;
      antMessage.info(`${fmt.toUpperCase()} 导出中，请稍候…`);

      // 轮询导出状态
      const poll = setInterval(async () => {
        try {
          const r = await fetch(
            `${API_BASE}/reports/${report.id}/export-status?job_id=${jobId}`,
            { headers: authHeaders() },
          );
          const j = await r.json();
          const st = j.data?.status;
          if (st === 'done') {
            clearInterval(poll);
            setExporting((prev) => ({ ...prev, [key]: false }));
            const dlUrl = j.data?.download_url;
            if (dlUrl) {
              antMessage.success({
                content: (
                  <span>
                    导出完成！{' '}
                    <Button
                      type="link"
                      size="small"
                      onClick={() =>
                        window.open(
                          dlUrl.startsWith('http')
                            ? dlUrl
                            : window.location.origin + dlUrl,
                          '_blank',
                        )
                      }
                    >
                      下载
                    </Button>
                  </span>
                ),
                duration: 8,
              });
            }
          } else if (st === 'failed') {
            clearInterval(poll);
            setExporting((prev) => ({ ...prev, [key]: false }));
            antMessage.error(`导出失败: ${j.data?.error || '未知错误'}`);
          }
        } catch {
          /* ignore poll errors */
        }
      }, 2000);
    } catch (e: any) {
      setExporting((prev) => ({ ...prev, [key]: false }));
      antMessage.error(`导出请求失败: ${e.message}`);
    }
  };

  const summaryStatusTag = (status?: string) => {
    if (!status || status === 'skipped') return null;
    const map: Record<string, { color: string; text: string }> = {
      pending: { color: 'default', text: '等待总结' },
      generating: { color: 'processing', text: 'AI 生成中' },
      done: { color: 'success', text: '总结已生成' },
      failed: { color: 'error', text: '总结失败' },
    };
    const cfg = map[status] || { color: 'default', text: status };
    return (
      <Tag color={cfg.color} style={{ marginLeft: 4 }}>
        {cfg.text}
      </Tag>
    );
  };

  // 客户端过滤
  const filteredReports = reports.filter((r) =>
    r.name.toLowerCase().includes(searchText.toLowerCase()),
  );

  const columns: ColumnsType<ReportItem> = [
    {
      title: '名称 / 摘要',
      dataIndex: 'name',
      render: (name: string, record: ReportItem) => (
        <div>
          <div style={{ fontWeight: 500, display: 'flex', alignItems: 'center', gap: 4 }}>
            {name}
            {summaryStatusTag(record.summary_status)}
          </div>
          {record.summary_status === 'done' && record.llm_summary && (
            <div
              style={{
                fontSize: 12,
                color: '#888',
                marginTop: 4,
                lineHeight: 1.5,
              }}
            >
              {record.llm_summary.slice(0, 60)}
              {record.llm_summary.length > 60 ? '…' : ''}
            </div>
          )}
          {record.description && !record.llm_summary && (
            <div style={{ fontSize: 12, color: '#aaa', marginTop: 2 }}>
              {record.description}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '主题',
      dataIndex: 'theme',
      width: 80,
      render: (t: string) => <Tag>{t || 'light'}</Tag>,
    },
    {
      title: '创建者',
      dataIndex: 'username',
      width: 100,
      render: (u: string) => u || '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (t: string) => (t ? new Date(t).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_: any, record: ReportItem) => (
        <Space size={4} wrap>
          <Tooltip title="预览报告">
            <Button
              size="small"
              type="primary"
              icon={<EyeOutlined />}
              onClick={() => setPreviewReport(record)}
              disabled={!record.report_file_path && !record.refresh_token}
            >
              预览
            </Button>
          </Tooltip>
          <Tooltip title="导出 PDF">
            <Button
              size="small"
              icon={<FilePdfOutlined />}
              loading={exporting[`${record.id}-pdf`]}
              onClick={() => handleExport(record, 'pdf')}
            >
              PDF
            </Button>
          </Tooltip>
          <Tooltip title="导出 PPTX">
            <Button
              size="small"
              icon={<FileOutlined />}
              loading={exporting[`${record.id}-pptx`]}
              onClick={() => handleExport(record, 'pptx')}
            >
              PPTX
            </Button>
          </Tooltip>
          <Tooltip title="AI 助手">
            <Button
              size="small"
              icon={<RobotOutlined />}
              style={{ color: '#52c41a', borderColor: '#52c41a' }}
              onClick={() => setCopilotReport(record)}
            >
              AI
            </Button>
          </Tooltip>
          <Popconfirm
            title="确定删除该报告？"
            description="报告文件将同时删除，无法恢复"
            onConfirm={() => handleDelete(record.id)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      {/* ── 顶部工具栏 ──────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          📄 报告清单
        </Title>
        <Space wrap>
          <Input
            placeholder="搜索报告名称..."
            prefix={<SearchOutlined />}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            allowClear
            style={{ width: 220 }}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => fetchReports(page)}
            loading={loading}
          >
            刷新
          </Button>
        </Space>
      </div>

      {/* ── 统计卡片 ─────────────────────────────────────────────────────── */}
      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col xs={24} sm={8} md={6}>
          <Card size="small">
            <Statistic title="报告总数" value={total} />
          </Card>
        </Col>
      </Row>

      {/* ── 报告表格 ─────────────────────────────────────────────────────── */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" tip="加载中..." />
        </div>
      ) : (
        <Table
          columns={columns}
          dataSource={filteredReports}
          rowKey="id"
          loading={false}
          pagination={{
            current: page,
            pageSize: 20,
            total,
            onChange: setPage,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 条`,
          }}
          locale={{ emptyText: <Empty description="暂无报告，在对话中说「生成报告」来创建第一个报告" /> }}
          size="middle"
        />
      )}

      {/* ── 报告预览 Modal ───────────────────────────────────────────────── */}
      {previewReport && (
        <ReportPreviewModal
          open={!!previewReport}
          onClose={() => setPreviewReport(null)}
          reportId={previewReport.id}
          refreshToken={previewReport.refresh_token}
          filePath={previewReport.report_file_path}
          fileName={previewReport.name + '.html'}
        />
      )}

      {/* ── AI 助手 Co-pilot ─────────────────────────────────────────────── */}
      <DataCenterCopilot
        open={!!copilotReport}
        onClose={() => setCopilotReport(null)}
        contextType="document"
        contextId={copilotReport?.id ?? ''}
        contextName={copilotReport?.name ?? ''}
        contextSpec={copilotReport ?? null}
        onSpecUpdated={() => fetchReports(page)}
      />
    </div>
  );
};

export default DataCenterDocuments;
