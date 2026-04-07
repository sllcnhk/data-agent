/**
 * SQL → Excel 数据导出页面
 *
 * 布局分三区：
 *   区域 1  查询输入区（SQL + 连接选择器 + 查询按钮）
 *   区域 2  预览结果区（列表展示 + 右上角导出按钮）
 *   区域 3  历史任务列表（分页 + 进度 + 操作）
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Pagination,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExportOutlined,
  LoadingOutlined,
  ReloadOutlined,
  SearchOutlined,
  StopOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import {
  dataExportApi,
  Connection,
  ExportJob,
  ExportJobListResult,
  QueryPreviewResult,
  ColumnMeta,
} from '@/services/dataExportApi';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;
const { Option } = Select;

// 活跃任务状态（需要轮询）
const ACTIVE_STATUSES = new Set(['pending', 'running', 'cancelling']);

// 状态标签配置
const STATUS_TAG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  pending:    { color: 'default',   icon: <SyncOutlined spin />,       label: '等待中' },
  running:    { color: 'processing', icon: <LoadingOutlined spin />,   label: '导出中' },
  completed:  { color: 'success',   icon: <CheckCircleOutlined />,     label: '已完成' },
  failed:     { color: 'error',     icon: <CloseCircleOutlined />,     label: '失败' },
  cancelling: { color: 'warning',   icon: <SyncOutlined spin />,       label: '取消中' },
  cancelled:  { color: 'default',   icon: <StopOutlined />,            label: '已取消' },
};

function StatusTag({ status }: { status: string }) {
  const cfg = STATUS_TAG[status] ?? { color: 'default', icon: null, label: status };
  return (
    <Tag color={cfg.color} icon={cfg.icon}>
      {cfg.label}
    </Tag>
  );
}

function formatBytes(bytes: number | null): string {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ─────────────────────────────────────────────────────────────────────────────

const DataExport: React.FC = () => {
  // ── 连接 & 查询输入 ─────────────────────────────────────────────────────────
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selectedEnv, setSelectedEnv] = useState<string>('');
  const [sql, setSql] = useState<string>('');
  const [loadingConns, setLoadingConns] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  // ── 预览结果 ────────────────────────────────────────────────────────────────
  const [preview, setPreview] = useState<QueryPreviewResult | null>(null);
  const [previewError, setPreviewError] = useState<string>('');

  // ── 导出 Modal ──────────────────────────────────────────────────────────────
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [exportForm] = Form.useForm();
  const [exporting, setExporting] = useState(false);

  // ── 任务历史 ────────────────────────────────────────────────────────────────
  const [jobList, setJobList] = useState<ExportJobListResult | null>(null);
  const [listPage, setListPage] = useState(1);
  const [listPageSize] = useState(10);
  const [listLoading, setListLoading] = useState(false);
  const [downloadingIds, setDownloadingIds] = useState<Set<string>>(new Set());
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── 加载连接列表 ─────────────────────────────────────────────────────────────
  const loadConnections = useCallback(async () => {
    setLoadingConns(true);
    try {
      const conns = await dataExportApi.getConnections();
      setConnections(conns);
      if (conns.length > 0 && !selectedEnv) {
        setSelectedEnv(conns[0].env);
      }
    } catch (e: any) {
      message.error(`加载连接失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setLoadingConns(false);
    }
  }, [selectedEnv]);

  // ── 加载历史任务列表 ─────────────────────────────────────────────────────────
  const loadJobList = useCallback(
    async (page = listPage, silent = false) => {
      if (!silent) setListLoading(true);
      try {
        const result = await dataExportApi.listJobs(page, listPageSize);
        setJobList(result);
      } catch (e: any) {
        if (!silent) message.error(`加载任务列表失败: ${e?.response?.data?.detail ?? e.message}`);
      } finally {
        if (!silent) setListLoading(false);
      }
    },
    [listPage, listPageSize],
  );

  // ── 首次加载 ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    loadConnections();
    loadJobList(1);
  }, []);

  // ── 轮询活跃任务 ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const hasActive = jobList?.items.some((j) => ACTIVE_STATUSES.has(j.status));
    if (hasActive) {
      pollTimerRef.current = setInterval(() => loadJobList(listPage, true), 2000);
    } else {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    }
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [jobList, listPage]);

  // ── SQL 预览 ─────────────────────────────────────────────────────────────────
  const handlePreview = async () => {
    if (!sql.trim()) {
      message.warning('请输入 SQL 语句');
      return;
    }
    if (!selectedEnv) {
      message.warning('请选择数据库连接');
      return;
    }
    setPreviewing(true);
    setPreviewError('');
    setPreview(null);
    try {
      const result = await dataExportApi.previewQuery(sql, selectedEnv);
      setPreview(result);
    } catch (e: any) {
      const msg = e?.response?.data?.detail ?? e.message ?? '查询失败';
      setPreviewError(msg);
    } finally {
      setPreviewing(false);
    }
  };

  // ── 提交导出 ─────────────────────────────────────────────────────────────────
  const handleExport = async () => {
    const values = await exportForm.validateFields();
    setExporting(true);
    try {
      const result = await dataExportApi.executeExport({
        query_sql: sql,
        connection_env: selectedEnv,
        job_name: values.job_name || '',
        batch_size: values.batch_size || 50000,
      });
      message.success(`导出任务已提交，文件名: ${result.output_filename}`);
      setExportModalOpen(false);
      exportForm.resetFields();
      await loadJobList(1);
      setListPage(1);
    } catch (e: any) {
      message.error(`提交失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setExporting(false);
    }
  };

  // ── 取消任务 ─────────────────────────────────────────────────────────────────
  const handleCancel = async (jobId: string) => {
    try {
      const r = await dataExportApi.cancelJob(jobId);
      message.success(r.status === 'cancelled' ? '任务已取消' : '取消请求已发送');
      await loadJobList(listPage, true);
    } catch (e: any) {
      message.error(`取消失败: ${e?.response?.data?.detail ?? e.message}`);
    }
  };

  // ── 删除任务 ─────────────────────────────────────────────────────────────────
  const handleDelete = async (jobId: string) => {
    try {
      await dataExportApi.deleteJob(jobId);
      message.success('任务已删除');
      await loadJobList(listPage);
    } catch (e: any) {
      message.error(`删除失败: ${e?.response?.data?.detail ?? e.message}`);
    }
  };

  // ── 下载文件 ─────────────────────────────────────────────────────────────────
  // 使用 axios blob 下载，确保 Authorization Bearer token 随请求发送。
  // 原生 <a href> 导航不经过 axios 拦截器，会触发 401 → 浏览器报"无法从网站上提取文件"。
  const handleDownload = async (job: ExportJob) => {
    setDownloadingIds(prev => new Set(prev).add(job.job_id));
    try {
      const blob = await dataExportApi.downloadFile(job.job_id);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = job.output_filename ?? `export_${job.job_id}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    } catch (e: any) {
      let detail = e?.message ?? '未知错误';
      // responseType='blob' 时，错误响应 data 是 Blob，需要解析才能拿到 detail
      if (e?.response?.data instanceof Blob) {
        try {
          const text = await (e.response.data as Blob).text();
          detail = JSON.parse(text)?.detail ?? text;
        } catch {
          // ignore
        }
      } else if (e?.response?.data?.detail) {
        detail = e.response.data.detail;
      }
      message.error(`下载失败: ${detail}`);
    } finally {
      setDownloadingIds(prev => {
        const next = new Set(prev);
        next.delete(job.job_id);
        return next;
      });
    }
  };

  // ── 预览表格列 ───────────────────────────────────────────────────────────────
  const previewColumns = preview
    ? preview.columns.map((col: ColumnMeta, idx: number) => ({
        title: (
          <Tooltip title={col.type}>
            <span>{col.name}</span>
          </Tooltip>
        ),
        dataIndex: idx,
        key: col.name,
        ellipsis: true,
        width: 140,
        render: (v: any) => (v === null || v === undefined ? <Text type="secondary">NULL</Text> : String(v)),
      }))
    : [];

  const previewDataSource = preview
    ? preview.rows.map((row, i) => ({ key: i, ...row }))
    : [];

  // ── 历史任务表格列 ────────────────────────────────────────────────────────────
  const jobColumns = [
    {
      title: '任务名称',
      dataIndex: 'job_name',
      key: 'job_name',
      width: 140,
      ellipsis: true,
      render: (v: string | null, r: ExportJob) => v || r.output_filename || '-',
    },
    {
      title: '连接',
      dataIndex: 'connection_env',
      key: 'connection_env',
      width: 100,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => <StatusTag status={s} />,
    },
    {
      title: '进度',
      key: 'progress',
      width: 160,
      render: (_: any, r: ExportJob) => {
        if (r.status === 'completed') {
          return (
            <Text type="success">
              {r.exported_rows?.toLocaleString()} 行 / {r.total_sheets} Sheet
            </Text>
          );
        }
        if (r.status === 'failed') {
          return (
            <Tooltip title={r.error_message}>
              <Text type="danger">失败</Text>
            </Tooltip>
          );
        }
        if (r.status === 'cancelled' || r.status === 'cancelling') {
          return <Text type="secondary">{r.exported_rows?.toLocaleString() ?? 0} 行（已取消）</Text>;
        }
        const pct =
          r.total_batches && r.total_batches > 0
            ? Math.round((r.done_batches / r.total_batches) * 100)
            : null;
        return (
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Progress
              percent={pct ?? 0}
              status={r.status === 'running' ? 'active' : 'normal'}
              size="small"
              format={() => (pct !== null ? `${pct}%` : '...')}
            />
            <Text type="secondary" style={{ fontSize: 11 }}>
              {r.exported_rows?.toLocaleString() ?? 0} 行
              {r.current_sheet ? ` · ${r.current_sheet}` : ''}
            </Text>
          </Space>
        );
      },
    },
    {
      title: '文件大小',
      dataIndex: 'file_size',
      key: 'file_size',
      width: 90,
      render: (v: number | null) => formatBytes(v),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string | null) =>
        v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      fixed: 'right' as const,
      render: (_: any, r: ExportJob) => (
        <Space size="small">
          {r.status === 'completed' && (
            <Tooltip title={downloadingIds.has(r.job_id) ? '下载中…' : '下载'}>
              <Button
                type="link"
                size="small"
                icon={<DownloadOutlined />}
                loading={downloadingIds.has(r.job_id)}
                onClick={() => handleDownload(r)}
              />
            </Tooltip>
          )}
          {(r.status === 'pending' || r.status === 'running') && (
            <Popconfirm
              title="确认取消该导出任务？"
              onConfirm={() => handleCancel(r.job_id)}
              okText="取消任务"
              cancelText="保留"
            >
              <Tooltip title="取消">
                <Button type="link" size="small" danger icon={<StopOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          {!ACTIVE_STATUSES.has(r.status) && (
            <Popconfirm
              title="确认删除该任务记录？同时删除导出文件。"
              onConfirm={() => handleDelete(r.job_id)}
              okText="删除"
              cancelText="取消"
            >
              <Tooltip title="删除">
                <Button type="link" size="small" danger icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  // ── 渲染 ─────────────────────────────────────────────────────────────────────
  return (
    <div style={{ padding: '0 4px' }}>
      <Title level={4} style={{ marginBottom: 16 }}>
        数据导出
      </Title>

      {/* ── 区域 1：查询输入 ─────────────────────────────────────────────────── */}
      <Card style={{ marginBottom: 16 }}>
        <Row gutter={12} align="middle" style={{ marginBottom: 12 }}>
          <Col flex="none">
            <Text strong>数据库连接：</Text>
          </Col>
          <Col flex="240px">
            <Select
              style={{ width: '100%' }}
              loading={loadingConns}
              placeholder="选择连接"
              value={selectedEnv || undefined}
              onChange={setSelectedEnv}
              notFoundContent={loadingConns ? <Spin size="small" /> : '暂无连接'}
            >
              {connections.map((c) => (
                <Option key={c.env} value={c.env}>
                  {c.display_name}
                </Option>
              ))}
            </Select>
          </Col>
          <Col flex="none">
            <Button icon={<ReloadOutlined />} onClick={loadConnections} loading={loadingConns}>
              刷新连接
            </Button>
          </Col>
        </Row>

        <TextArea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          placeholder="输入 SELECT SQL 语句，例如：SELECT * FROM db.table LIMIT 1000"
          autoSize={{ minRows: 4, maxRows: 12 }}
          style={{ fontFamily: 'monospace', marginBottom: 12 }}
          onKeyDown={(e) => {
            // Ctrl/Cmd + Enter 触发查询
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              e.preventDefault();
              handlePreview();
            }
          }}
        />

        <Row justify="end">
          <Space>
            <Button
              type="primary"
              icon={<SearchOutlined />}
              loading={previewing}
              onClick={handlePreview}
              disabled={!sql.trim() || !selectedEnv}
            >
              查询预览
            </Button>
          </Space>
        </Row>

        {previewError && (
          <Alert
            type="error"
            message="查询错误"
            description={previewError}
            showIcon
            closable
            style={{ marginTop: 12 }}
            onClose={() => setPreviewError('')}
          />
        )}
      </Card>

      {/* ── 区域 2：预览结果 ─────────────────────────────────────────────────── */}
      {preview && (
        <Card
          style={{ marginBottom: 16 }}
          title={
            <Space>
              <Text strong>查询预览</Text>
              <Text type="secondary">
                显示前 {preview.row_count} 行 · {preview.columns.length} 列
              </Text>
            </Space>
          }
          extra={
            <Button
              type="primary"
              icon={<ExportOutlined />}
              onClick={() => {
                exportForm.setFieldsValue({ job_name: '', batch_size: 50000 });
                setExportModalOpen(true);
              }}
            >
              导出
            </Button>
          }
        >
          <Table
            dataSource={previewDataSource}
            columns={previewColumns}
            pagination={false}
            scroll={{ x: 'max-content', y: 360 }}
            size="small"
            bordered
          />
        </Card>
      )}

      {/* ── 区域 3：历史任务 ─────────────────────────────────────────────────── */}
      <Card
        title={<Text strong>导出历史</Text>}
        extra={
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={() => loadJobList(listPage)}
            loading={listLoading}
          >
            刷新
          </Button>
        }
      >
        <Table
          dataSource={jobList?.items ?? []}
          columns={jobColumns}
          rowKey="job_id"
          loading={listLoading}
          pagination={false}
          scroll={{ x: 900 }}
          size="small"
          locale={{ emptyText: '暂无导出记录' }}
        />
        {jobList && jobList.total > listPageSize && (
          <div style={{ marginTop: 12, textAlign: 'right' }}>
            <Pagination
              current={listPage}
              pageSize={listPageSize}
              total={jobList.total}
              showTotal={(t) => `共 ${t} 条`}
              onChange={(p) => {
                setListPage(p);
                loadJobList(p);
              }}
              size="small"
            />
          </div>
        )}
      </Card>

      {/* ── 导出配置 Modal ───────────────────────────────────────────────────── */}
      <Modal
        title="导出配置"
        open={exportModalOpen}
        onCancel={() => setExportModalOpen(false)}
        onOk={handleExport}
        confirmLoading={exporting}
        okText="开始导出"
        cancelText="取消"
        width={480}
      >
        <Form form={exportForm} layout="vertical">
          <Form.Item
            name="job_name"
            label="文件名（可选）"
            extra="留空则自动生成，最终文件名为 {名称}_{时间戳}.xlsx"
          >
            <Input placeholder="如：用户行为分析_2024Q1" maxLength={50} />
          </Form.Item>
          <Form.Item
            name="batch_size"
            label="批次大小（高级）"
            extra="每批从数据库读取的行数，影响内存与速度，默认 50,000 行"
            initialValue={50000}
          >
            <InputNumber min={1000} max={200000} step={1000} style={{ width: '100%' }} />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message={`SQL → Excel 导出说明`}
            description={
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                <li>每超过 100 万行自动插入新 Sheet，每个 Sheet 均含标题行</li>
                <li>Int64 / UInt64 等大整数列自动转为字符串，避免科学计数法</li>
                <li>导出过程可随时取消，已完成批次不可回退</li>
                <li>导出完成后可在历史列表下载，文件长期保存在服务器</li>
              </ul>
            }
          />
        </Form>
      </Modal>
    </div>
  );
};

export default DataExport;
