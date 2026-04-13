/**
 * DataCenterDashboards — 数据管理中心·报表清单
 *
 * 展示 doc_type=dashboard 的报表，以卡片网格形式呈现。
 * 支持预览、下载、AI 助手、删除操作。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Empty,
  Input,
  message as antMessage,
  Popconfirm,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/useAuthStore';
import ReportPreviewModal from '../components/chat/ReportPreviewModal';
import DataCenterCopilot from '../components/DataCenterCopilot';
import { useEffect } from 'react';

const { Title, Text } = Typography;

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1') as string;

export interface ReportItem {
  id: string;
  name: string;
  description?: string;
  theme: string;
  username?: string;
  view_count: number;
  created_at: string;
  updated_at: string;
  report_file_path?: string;
  refresh_token?: string;
  html_url?: string;
  download_url?: string;
  doc_type?: string;
  summary_status?: string;
  llm_summary?: string;
}

const DataCenterDashboards: React.FC = () => {
  const { accessToken } = useAuthStore();
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page] = useState(1);
  const [loading, setLoading] = useState(false);
  const [previewReport, setPreviewReport] = useState<ReportItem | null>(null);
  const [copilotReport, setCopilotReport] = useState<ReportItem | null>(null);
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
          `${API_BASE}/reports?doc_type=dashboard&page=${p}&page_size=20`,
          { headers: authHeaders() },
        );
        const json = await res.json();
        if (json.success) {
          setReports(json.data.items || []);
          setTotal(json.data.total || 0);
        } else {
          antMessage.error(json.detail || '获取报表列表失败');
        }
      } catch {
        antMessage.error('获取报表列表失败');
      } finally {
        setLoading(false);
      }
    },
    [authHeaders],
  );

  useEffect(() => {
    fetchReports(page);
  }, [page]);

  // 处理来自独立 HTML 标签页的 autoPilot 参数（B2 注入的按钮打开新标签后落地）
  useEffect(() => {
    if (reports.length === 0) return;
    const params = new URLSearchParams(window.location.search);
    const autoPilotId = params.get('autoPilot');
    if (!autoPilotId) return;
    const target = reports.find((r) => r.id === autoPilotId);
    if (target) {
      setCopilotReport(target);
      // 清理 URL 中的参数，避免刷新时再次触发
      const url = new URL(window.location.href);
      url.searchParams.delete('autoPilot');
      window.history.replaceState({}, '', url.toString());
    }
  }, [reports]);

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/reports/${id}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      const json = await res.json();
      if (json.success) {
        antMessage.success('报表已删除');
        fetchReports(page);
      } else {
        antMessage.error(json.detail || '删除失败');
      }
    } catch {
      antMessage.error('删除失败');
    }
  };

  const handleDownload = (report: ReportItem) => {
    const url = report.download_url || report.html_url;
    if (url) {
      window.open(url.startsWith('http') ? url : window.location.origin + url, '_blank');
    } else {
      antMessage.warning('暂无下载链接');
    }
  };

  // 客户端搜索过滤
  const filteredReports = reports.filter((r) =>
    r.name.toLowerCase().includes(searchText.toLowerCase()),
  );

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
          📊 报表清单
        </Title>
        <Space wrap>
          <Input
            placeholder="搜索报表名称..."
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
            <Statistic title="报表总数" value={total} />
          </Card>
        </Col>
        <Col xs={24} sm={8} md={6}>
          <Card size="small">
            <Statistic title="已过滤" value={filteredReports.length} />
          </Card>
        </Col>
      </Row>

      {/* ── 报表卡片网格 ─────────────────────────────────────────────────── */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" tip="加载中..." />
        </div>
      ) : filteredReports.length === 0 ? (
        <Empty description="暂无报表，在对话中说「生成图表」来创建第一个报表" />
      ) : (
        <Row gutter={[16, 16]}>
          {filteredReports.map((report) => (
            <Col key={report.id} xs={24} sm={12} lg={8}>
              <Card
                hoverable
                title={
                  <Tooltip title={report.name}>
                    <span
                      style={{
                        display: 'block',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        maxWidth: 200,
                      }}
                    >
                      {report.name}
                    </span>
                  </Tooltip>
                }
                size="small"
                extra={
                  <Tag color="blue">{report.theme || 'light'}</Tag>
                }
                actions={[
                  <Tooltip key="preview" title="预览报表">
                    <Button
                      type="primary"
                      size="small"
                      icon={<EyeOutlined />}
                      onClick={() => setPreviewReport(report)}
                      disabled={!report.report_file_path && !report.refresh_token}
                    >
                      预览
                    </Button>
                  </Tooltip>,
                  <Tooltip key="download" title="下载 HTML">
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={() => handleDownload(report)}
                    >
                      下载
                    </Button>
                  </Tooltip>,
                  <Tooltip key="copilot" title="AI 助手">
                    <Button
                      size="small"
                      icon={<RobotOutlined />}
                      style={{ color: '#52c41a', borderColor: '#52c41a' }}
                      onClick={() => setCopilotReport(report)}
                    >
                      AI 助手
                    </Button>
                  </Tooltip>,
                  <Popconfirm
                    key="delete"
                    title="确定删除该报表？"
                    description="报表文件将同时删除，无法恢复"
                    onConfirm={() => handleDelete(report.id)}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                  >
                    <Tooltip title="删除">
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Tooltip>
                  </Popconfirm>,
                ]}
              >
                <div style={{ minHeight: 60 }}>
                  {report.description && (
                    <Text
                      type="secondary"
                      style={{ fontSize: 12, display: 'block', marginBottom: 8 }}
                      ellipsis={{ tooltip: report.description }}
                    >
                      {report.description}
                    </Text>
                  )}
                  <Space size={4} wrap style={{ fontSize: 12 }}>
                    {report.username && (
                      <Text type="secondary">👤 {report.username}</Text>
                    )}
                    <Text type="secondary">
                      🕐 {report.created_at ? new Date(report.created_at).toLocaleString('zh-CN') : '-'}
                    </Text>
                    <Text type="secondary">👁 {report.view_count ?? 0} 次</Text>
                  </Space>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {/* ── 报表预览 Modal ───────────────────────────────────────────────── */}
      {previewReport && (
        <ReportPreviewModal
          open={!!previewReport}
          onClose={() => setPreviewReport(null)}
          reportId={previewReport.id}
          refreshToken={previewReport.refresh_token}
          filePath={previewReport.report_file_path}
          fileName={previewReport.name + '.html'}
          pilotContext={{
            contextType: 'dashboard',
            contextId: previewReport.id,
            contextName: previewReport.name,
            contextSpec: previewReport,
            onSpecUpdated: () => fetchReports(page),
          }}
        />
      )}

      {/* ── AI 助手 Co-pilot ─────────────────────────────────────────────── */}
      <DataCenterCopilot
        open={!!copilotReport}
        onClose={() => setCopilotReport(null)}
        contextType="dashboard"
        contextId={copilotReport?.id ?? ''}
        contextName={copilotReport?.name ?? ''}
        contextSpec={copilotReport ?? null}
        onSpecUpdated={() => fetchReports(page)}
      />
    </div>
  );
};

export default DataCenterDashboards;
