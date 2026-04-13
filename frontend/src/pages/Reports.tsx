/**
 * Reports — 报告管理列表页 /reports
 *
 * 功能：
 *  - 展示所有已生成的 HTML 报告（分页）
 *  - 操作：预览、下载 HTML、导出 PDF/PPTX、删除
 *  - superadmin 可查看所有用户的报告
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Table,
  Tag,
  Space,
  Popconfirm,
  message as antMessage,
  Tooltip,
  Typography,
  Row,
  Col,
  Statistic,
  Card,
  Empty,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  EyeOutlined,
  DownloadOutlined,
  DeleteOutlined,
  FilePdfOutlined,
  FileOutlined,
  ReloadOutlined,
  BarChartOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/useAuthStore';
import ReportPreviewModal from '../components/chat/ReportPreviewModal';

const { Title } = Typography;

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1') as string;

interface ReportItem {
  id: string;
  name: string;
  description?: string;
  report_file_path?: string;
  download_url?: string;
  theme: string;
  summary_status?: string;
  llm_summary?: string;
  username?: string;
  created_at: string;
  updated_at: string;
  view_count: number;
}

const Reports: React.FC = () => {
  const { accessToken, user } = useAuthStore();
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [previewReport, setPreviewReport] = useState<ReportItem | null>(null);
  const [exporting, setExporting] = useState<Record<string, boolean>>({});

  const authHeaders = useCallback(() => ({
    'Content-Type': 'application/json',
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
  }), [accessToken]);

  const fetchReports = useCallback(async (p = 1) => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/reports?page=${p}&page_size=20`, {
        headers: authHeaders(),
      });
      const json = await res.json();
      if (json.success) {
        setReports(json.data.items || []);
        setTotal(json.data.total || 0);
      }
    } catch (e) {
      antMessage.error('获取报告列表失败');
    } finally {
      setLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => { fetchReports(page); }, [page]);

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
    setExporting(prev => ({ ...prev, [key]: true }));
    try {
      const res = await fetch(`${API_BASE}/reports/${report.id}/export`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ format: fmt }),
      });
      const json = await res.json();
      if (!json.success) throw new Error(json.detail || '导出失败');
      const jobId = json.data?.job_id;
      antMessage.info(`${fmt.toUpperCase()} 导出中，请稍候…`);

      // 轮询
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
            setExporting(prev => ({ ...prev, [key]: false }));
            const dlUrl = j.data?.download_url;
            if (dlUrl) {
              antMessage.success({
                content: (
                  <span>
                    导出完成！
                    <Button type="link" size="small"
                      onClick={() => window.open(window.location.origin + dlUrl, '_blank')}>
                      下载
                    </Button>
                  </span>
                ),
                duration: 8,
              });
            }
          } else if (st === 'failed') {
            clearInterval(poll);
            setExporting(prev => ({ ...prev, [key]: false }));
            antMessage.error(`导出失败: ${j.data?.error || '未知错误'}`);
          }
        } catch { /* ignore */ }
      }, 2000);
    } catch (e: any) {
      setExporting(prev => ({ ...prev, [key]: false }));
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
    return <Tag color={cfg.color}>{cfg.text}</Tag>;
  };

  const columns: ColumnsType<ReportItem> = [
    {
      title: '报告名称',
      dataIndex: 'name',
      render: (name, record) => (
        <div>
          <div style={{ fontWeight: 500 }}>{name}</div>
          {record.description && (
            <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>
              {record.description}
            </div>
          )}
          {summaryStatusTag(record.summary_status)}
        </div>
      ),
    },
    {
      title: '主题',
      dataIndex: 'theme',
      width: 80,
      render: t => <Tag>{t || 'light'}</Tag>,
    },
    {
      title: '创建者',
      dataIndex: 'username',
      width: 100,
      render: u => u || '-',
    },
    {
      title: '浏览',
      dataIndex: 'view_count',
      width: 70,
      render: n => n || 0,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: t => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 260,
      render: (_, record) => (
        <Space size={4} wrap>
          {record.report_file_path && (
            <Tooltip title="预览报告">
              <Button
                size="small"
                type="primary"
                icon={<EyeOutlined />}
                onClick={() => setPreviewReport(record)}
              >
                预览
              </Button>
            </Tooltip>
          )}
          {record.download_url && (
            <Tooltip title="下载 HTML">
              <Button
                size="small"
                icon={<DownloadOutlined />}
                onClick={() => window.open(window.location.origin + record.download_url, '_blank')}
              >
                HTML
              </Button>
            </Tooltip>
          )}
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
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0 }}>
          <BarChartOutlined style={{ marginRight: 8, color: '#1677ff' }} />
          图表报告
        </Title>
        <Button icon={<ReloadOutlined />} onClick={() => fetchReports(page)}>
          刷新
        </Button>
      </div>

      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col span={6}>
          <Card>
            <Statistic title="报告总数" value={total} />
          </Card>
        </Col>
      </Row>

      <Table
        columns={columns}
        dataSource={reports}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          onChange: setPage,
          showSizeChanger: false,
        }}
        locale={{ emptyText: <Empty description="暂无报告，在对话中说「生成图表」来创建第一个报告" /> }}
        size="middle"
      />

      {previewReport && (
        <ReportPreviewModal
          open={!!previewReport}
          onClose={() => setPreviewReport(null)}
          reportId={previewReport.id}
          filePath={previewReport.report_file_path || ''}
          fileName={previewReport.name + '.html'}
        />
      )}
    </div>
  );
};

export default Reports;
