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
  DatePicker,
  Divider,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Pagination,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { Dayjs } from 'dayjs';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  ExportOutlined,
  FileExcelOutlined,
  LoadingOutlined,
  ReloadOutlined,
  SearchOutlined,
  StopOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import {
  dataExportApi,
  ChunkConfig,
  Connection,
  ExportFileEntry,
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
  pending:        { color: 'default',   icon: <SyncOutlined spin />,            label: '等待中' },
  running:        { color: 'processing', icon: <LoadingOutlined spin />,        label: '导出中' },
  completed:      { color: 'success',   icon: <CheckCircleOutlined />,          label: '已完成' },
  failed:         { color: 'error',     icon: <CloseCircleOutlined />,          label: '失败' },
  // v2.14.7: 分块模式部分成功 — 按 completed 等同对待(允许下载已成功块)
  partial_failed: { color: 'orange',    icon: <ExclamationCircleOutlined />,    label: '部分完成' },
  cancelling:     { color: 'warning',   icon: <SyncOutlined spin />,            label: '取消中' },
  cancelled:      { color: 'default',   icon: <StopOutlined />,                 label: '已取消' },
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

const FILE_STATUS_TAG: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '等待' },
  running: { color: 'processing', label: '导出中' },
  completed: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
  cancelled: { color: 'default', label: '已取消' },
};

// 任务展开行：分块文件清单
const ChunkFileList: React.FC<{
  job: ExportJob;
  downloadingIds: Set<string>;
  onDownload: (file: ExportFileEntry) => void;
}> = ({ job, downloadingIds, onDownload }) => {
  const files = job.output_files ?? [];
  const cols = [
    { title: '#', dataIndex: 'index', key: 'index', width: 50 },
    {
      title: '日期/时间范围',
      key: 'range',
      width: 240,
      render: (_: any, f: ExportFileEntry) => {
        // v2.14: sub-day 自动细分时 date_start/date_end 可能是 ISO datetime
        // ('YYYY-MM-DDTHH:MM:SS')。展示时把 T 替换为空格更易读。
        const fmt = (s: string) => s.includes('T') ? s.replace('T', ' ') : s;
        return `${fmt(f.date_start)} ~ ${fmt(f.date_end)}`;
      },
    },
    { title: '文件名', dataIndex: 'filename', key: 'filename', ellipsis: true },
    {
      title: '行数',
      dataIndex: 'rows',
      key: 'rows',
      width: 100,
      render: (v: number) => (v ?? 0).toLocaleString(),
    },
    { title: 'Sheet 数', dataIndex: 'sheets', key: 'sheets', width: 80 },
    {
      title: '大小',
      dataIndex: 'file_size',
      key: 'file_size',
      width: 90,
      render: (v: number | null) => formatBytes(v),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: string) => {
        const cfg = FILE_STATUS_TAG[s] ?? { color: 'default', label: s };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      render: (_: any, f: ExportFileEntry) => {
        if (f.status !== 'completed') return null;
        const dlKey = `${job.job_id}#${f.index}`;
        return (
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            loading={downloadingIds.has(dlKey)}
            onClick={() => onDownload(f)}
          >
            下载
          </Button>
        );
      },
    },
  ];
  return (
    <Table
      size="small"
      pagination={false}
      rowKey="index"
      dataSource={files}
      columns={cols as any}
    />
  );
};

// ─────────────────────────────────────────────────────────────────────────────

// 检测 SQL 中是否含日期占位符（与后端 data_export_chunker 一致）
const sqlHasDatePlaceholders = (sql: string): boolean =>
  sql.includes('{{date_start}}') && sql.includes('{{date_end}}');

const sqlHasTsPlaceholders = (sql: string): boolean =>
  sql.includes('{{ts_start}}') && sql.includes('{{ts_end}}');

const sqlHasAnyDatePlaceholders = (sql: string): boolean =>
  sqlHasDatePlaceholders(sql) || sqlHasTsPlaceholders(sql);

const sqlHasPartialPlaceholders = (sql: string): boolean => {
  const hasDateStart = sql.includes('{{date_start}}');
  const hasDateEnd = sql.includes('{{date_end}}');
  const hasTsStart = sql.includes('{{ts_start}}');
  const hasTsEnd = sql.includes('{{ts_end}}');
  return (hasDateStart !== hasDateEnd) || (hasTsStart !== hasTsEnd);
};

const placeholderPairText = (sql: string): string => {
  const pairs: string[] = [];
  if (sqlHasDatePlaceholders(sql)) pairs.push('{{date_start}} / {{date_end}}');
  if (sqlHasTsPlaceholders(sql)) pairs.push('{{ts_start}} / {{ts_end}}');
  return pairs.join('，');
};

const DataExport: React.FC = () => {
  // ── 连接 & 查询输入 ─────────────────────────────────────────────────────────
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selectedEnv, setSelectedEnv] = useState<string>('');
  const [sql, setSql] = useState<string>('');
  const [loadingConns, setLoadingConns] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  // 占位符模式下的预览样本日期（默认昨日）
  const [previewSampleDate, setPreviewSampleDate] = useState<Dayjs | null>(null);

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

  // ── 错误详情 Modal ──────────────────────────────────────────────────────────
  const [errorModalJob, setErrorModalJob] = useState<ExportJob | null>(null);

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
    if (sqlHasPartialPlaceholders(sql)) {
      message.error('SQL 中 {{date_start}}/{{date_end}} 或 {{ts_start}}/{{ts_end}} 必须各自成对出现');
      return;
    }
    setPreviewing(true);
    setPreviewError('');
    setPreview(null);
    try {
      const sampleDate = previewSampleDate
        ? previewSampleDate.format('YYYY-MM-DD')
        : undefined;
      const result = await dataExportApi.previewQuery(
        sql, selectedEnv, 'clickhouse', 100, sampleDate,
      );
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
      const isChunked = values.export_mode === 'date_chunked';
      let chunk_config: ChunkConfig | undefined;
      if (isChunked) {
        const range = values.date_range as [Dayjs, Dayjs] | undefined;
        if (!range || range.length !== 2) {
          message.error('请选择日期范围');
          setExporting(false);
          return;
        }
        chunk_config = {
          date_column: values.date_column?.trim() || null,
          date_start: range[0].format('YYYY-MM-DD'),
          date_end: range[1].format('YYYY-MM-DD'),
          chunk_days: values.chunk_days || 10,
          min_subdivide_unit: values.min_subdivide_unit || 'day',
          cursor_column: values.cursor_column?.trim() || null,
          pre_split_hours:
            values.pre_split_mode === 'auto'
              ? 'auto'
              : (values.pre_split_hours || null),
          auto_split_column: values.auto_split_column?.trim() || null,
          auto_split_target_rows: values.auto_split_target_rows || null,
          prefer_chunked: values.prefer_chunked ?? null,
        };
      }
      const result = await dataExportApi.executeExport({
        query_sql: sql,
        connection_env: selectedEnv,
        job_name: values.job_name || '',
        batch_size: values.batch_size || 50000,
        output_format: values.output_format || 'xlsx',
        xlsx_engine: values.xlsx_engine || 'auto',
        chunk_config,
      });
      message.success(
        `导出任务已提交（${isChunked ? '按日期分块' : '单文件'}）：${result.output_filename}`,
      );
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
  // 分块模式下需指定 file 子项；单文件模式 file 为 undefined。
  const handleDownload = async (job: ExportJob, file?: ExportFileEntry) => {
    const dlKey = file ? `${job.job_id}#${file.index}` : job.job_id;
    setDownloadingIds(prev => new Set(prev).add(dlKey));
    try {
      const blob = await dataExportApi.downloadFile(job.job_id, file?.index);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download =
        file?.filename ??
        (job.export_mode === 'date_chunked'
          ? `export_${job.job_id}_${file?.index ?? 0}.xlsx`
          : job.output_filename ?? `export_${job.job_id}.xlsx`);
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
        next.delete(dlKey);
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
        if (r.status === 'partial_failed') {
          // v2.14.7: 部分成功 — 显示已导出行数 + 查看详情按钮
          const okCount = r.output_files?.filter((f) => f.status === 'completed').length ?? 0;
          const totalCount = r.output_files?.length ?? 0;
          return (
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Button
                type="link"
                size="small"
                style={{ padding: 0, height: 'auto', textAlign: 'left', color: '#fa8c16' }}
                onClick={() => setErrorModalJob(r)}
              >
                <Text style={{ color: '#fa8c16' }}>
                  部分完成 · {okCount}/{totalCount} 块 · 查看详情
                </Text>
              </Button>
              <Text type="secondary" style={{ fontSize: 11 }}>
                已导出 {r.exported_rows?.toLocaleString() ?? 0} 行
              </Text>
            </Space>
          );
        }
        if (r.status === 'failed') {
          return (
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Button
                type="link"
                size="small"
                danger
                style={{ padding: 0, height: 'auto', textAlign: 'left' }}
                onClick={() => setErrorModalJob(r)}
              >
                <Text type="danger">失败 · 查看详情</Text>
              </Button>
              {r.error_message && (
                <Text
                  type="secondary"
                  style={{
                    fontSize: 11,
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                  }}
                  title={r.error_message}
                >
                  {r.error_message.split('[技术细节]')[0].trim()}
                </Text>
              )}
            </Space>
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
          {r.status === 'completed' && r.export_mode !== 'date_chunked' && (
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
          {(r.status === 'completed' || r.status === 'partial_failed')
            && r.export_mode === 'date_chunked' && (
            r.output_format === 'csv_zip' ? (
              <Tooltip title={
                downloadingIds.has(r.job_id)
                  ? '下载中…'
                  : r.status === 'partial_failed'
                    ? '下载 CSV ZIP (部分块,文件名含 partial_ 前缀)'
                    : '下载 CSV ZIP'
              }>
                <Button
                  type="link"
                  size="small"
                  icon={<DownloadOutlined />}
                  loading={downloadingIds.has(r.job_id)}
                  onClick={() => handleDownload(r)}
                />
              </Tooltip>
            ) : (
              <Tooltip title={
                r.status === 'partial_failed'
                  ? '展开行查看可下载块(部分块成功)'
                  : '展开行查看分块文件下载'
              }>
                <Tag
                  color={r.status === 'partial_failed' ? 'orange' : 'blue'}
                  icon={<FileExcelOutlined />}
                >
                  {r.output_files?.filter((f) => f.status === 'completed').length ?? 0}
                  {r.status === 'partial_failed'
                    ? ` / ${r.output_files?.length ?? 0} 块可下载`
                    : ' 个文件'}
                </Tag>
              </Tooltip>
            )
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

        {sqlHasAnyDatePlaceholders(sql) && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={`检测到日期占位符 ${placeholderPairText(sql)}`}
            description={
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  预览时占位符会被替换为下方「样本日期」（留空默认昨日），
                  让带占位符的 SQL 也能查询预览。导出时按实际日期范围替换为每个块的起止日期；
                  <code>{'{{ts_start}}'}</code> / <code>{'{{ts_end}}'}</code> 会按样本日
                  00:00:00 到次日 00:00:00 的半开区间替换。
                </Text>
                <Space>
                  <Text>预览样本日期：</Text>
                  <DatePicker
                    value={previewSampleDate}
                    onChange={setPreviewSampleDate}
                    placeholder="留空默认昨日"
                    allowClear
                  />
                </Space>
              </Space>
            }
          />
        )}
        {sqlHasPartialPlaceholders(sql) && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="占位符须成对"
            description="SQL 中 {{date_start}}/{{date_end}} 或 {{ts_start}}/{{ts_end}} 必须各自同时出现，仅写一个会导致预览/导出失败"
          />
        )}

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
                {preview.preview_date && ` · 占位符替换为 ${preview.preview_date}`}
              </Text>
            </Space>
          }
          extra={
            <Button
              type="primary"
              icon={<ExportOutlined />}
              onClick={() => {
                exportForm.resetFields();
                exportForm.setFieldsValue({
            export_mode: 'single',
            output_format: 'xlsx',
            xlsx_engine: 'auto',
            job_name: '',
            batch_size: 50000,
                  chunk_days: 10,
                });
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
          expandable={{
            rowExpandable: (r) =>
              r.export_mode === 'date_chunked' && (r.output_files?.length ?? 0) > 0,
            expandedRowRender: (r) => (
              <ChunkFileList
                job={r}
                downloadingIds={downloadingIds}
                onDownload={(file) => handleDownload(r, file)}
              />
            ),
          }}
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
        width={560}
      >
        <Form
          form={exportForm}
          layout="vertical"
          initialValues={{
            export_mode: 'single',
            output_format: 'xlsx',
            xlsx_engine: 'auto',
            chunk_days: 10,
            batch_size: 50000,
          }}
        >
          <Form.Item name="export_mode" label="导出模式">
            <Radio.Group>
              <Radio.Button value="single">单文件</Radio.Button>
              <Radio.Button value="date_chunked">按日期分块（多文件）</Radio.Button>
            </Radio.Group>
          </Form.Item>

          <Form.Item
            name="output_format"
            label="输出格式"
            extra="CSV/CSV ZIP 为极速导出；XLSX 支持自动分 Sheet。分块 + CSV ZIP 会把全部 CSV 子文件打包为一个 ZIP 下载。"
          >
            <Radio.Group>
              <Radio.Button value="xlsx">Excel (.xlsx)</Radio.Button>
              <Radio.Button value="csv">CSV</Radio.Button>
              <Radio.Button value="csv_zip">CSV ZIP</Radio.Button>
            </Radio.Group>
          </Form.Item>

          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.output_format !== cur.output_format}
          >
            {({ getFieldValue }) =>
              getFieldValue('output_format') === 'xlsx' ? (
                <Form.Item
                  name="xlsx_engine"
                  label="XLSX 写入策略"
                  extra="auto：系统判断。分块/大数据默认先极速落 CSV 临时文件，再本地转 XLSX，减少远端查询连接占用；小任务可直接写 XLSX。"
                >
                  <Select
                    options={[
                      { value: 'auto', label: '自动（推荐）' },
                      { value: 'csv_staging', label: 'CSV 临时落盘后转 XLSX（更稳）' },
                      { value: 'direct', label: '直接写 XLSX（少中间文件）' },
                    ]}
                  />
                </Form.Item>
              ) : null
            }
          </Form.Item>

          <Form.Item
            name="job_name"
            label="文件名（可选）"
            extra="留空自动生成；分块模式下用作子文件前缀"
          >
            <Input placeholder="如：用户行为分析_2024Q1" maxLength={50} />
          </Form.Item>

          {/* 分块模式专用字段 */}
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.export_mode !== cur.export_mode}
          >
            {({ getFieldValue }) =>
              getFieldValue('export_mode') === 'date_chunked' ? (
                <>
                  <Divider style={{ margin: '8px 0 16px' }}>分块配置</Divider>
                  <Form.Item
                    name="date_range"
                    label="日期范围（含起止日）"
                    rules={[{ required: true, message: '请选择日期范围' }]}
                  >
                    <DatePicker.RangePicker style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item
                    name="chunk_days"
                    label="单块天数"
                    rules={[{ required: true, type: 'number', min: 1, max: 90 }]}
                    extra="每个 Excel 文件覆盖的天数，范围 1-90"
                  >
                    <InputNumber min={1} max={90} step={1} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item
                    name="date_column"
                    label="日期列名（包装模式必填）"
                    extra={
                      <span>
                        若 SQL 中已写 <code>{'{{date_start}}'}</code> /{' '}
                        <code>{'{{date_end}}'}</code> 或 <code>{'{{ts_start}}'}</code> /{' '}
                        <code>{'{{ts_end}}'}</code> 占位符，则可省略此项（推荐：性能最佳）。
                        否则填写表中的日期列名（仅字母/数字/下划线）。
                      </span>
                    }
                  >
                    <Input placeholder="如：event_date" maxLength={64} />
                  </Form.Item>
                  <Form.Item
                    name="min_subdivide_unit"
                    label="最小再细分粒度（高级）"
                    extra={
                      <span>
                        块失败时自动对半再细分的最小粒度：
                        <b>天</b>=不下钻到 sub-day（默认，与 v2.13 行为一致）；
                        <b>小时/分钟</b>=允许在 1 天块失败后继续拆 12h+12h→6h+6h...
                        <br />
                        <b>仅当过滤列为 DateTime 时</b>启用小时/分钟才有效；Date 列下
                        sub-day 字面量会被 ClickHouse 截到 Date 触发无效细分循环。
                      </span>
                    }
                  >
                    <Select
                      allowClear
                      options={[
                        { value: 'day', label: '天（默认）' },
                        { value: 'hour', label: '小时' },
                        { value: 'minute', label: '分钟' },
                      ]}
                      placeholder="天（默认）"
                      style={{ width: '100%' }}
                      onChange={(val: string | undefined) => {
                        if (val !== 'hour' && val !== 'minute') {
                          exportForm.setFieldsValue({
                            pre_split_mode: undefined,
                            auto_split_target_rows: undefined,
                            auto_split_column: undefined,
                          });
                        }
                      }}
                    />
                  </Form.Item>
                  <Form.Item
                    noStyle
                    shouldUpdate={(prev, cur) =>
                      prev.min_subdivide_unit !== cur.min_subdivide_unit
                      || prev.pre_split_mode !== cur.pre_split_mode
                    }
                  >
                    {({ getFieldValue }) => {
                      const unit = getFieldValue('min_subdivide_unit');
                      const showAutoOption = unit === 'hour' || unit === 'minute';
                      const mode = getFieldValue('pre_split_mode');
                      return (
                        <>
                          {showAutoOption && (
                            <Form.Item
                              name="pre_split_mode"
                              label="预分裂模式"
                              extra='自动模式：先统计每时段行数，按目标行数自动切分窗口，无需手动设定小时数'
                            >
                              <Select
                                allowClear
                                placeholder="留空默认 6 小时"
                                style={{ width: '100%' }}
                                options={[
                                  { value: 'fixed', label: '固定小时数' },
                                  { value: 'auto', label: '自动（按数据量）' },
                                ]}
                              />
                            </Form.Item>
                          )}
                          {(!showAutoOption || mode === 'fixed') && (
                            <Form.Item
                              name="pre_split_hours"
                              label="预分裂窗口（小时）"
                              extra={
                                showAutoOption
                                  ? '每个预分裂窗口的小时数（1-24），留空默认 6 小时'
                                  : '启用小时/分钟级再细分时，留空默认 6 小时。提前拆小块，减少先跑大块约 5 分钟后断流再重试的浪费。'
                              }
                            >
                              <InputNumber min={1} max={24} step={1} placeholder="默认 6" style={{ width: '100%' }} />
                            </Form.Item>
                          )}
                          {showAutoOption && mode === 'auto' && (
                            <>
                              <Form.Item
                                name="auto_split_target_rows"
                                label="每窗口目标行数"
                                extra="自动模式下后端按此阈值切分窗口（默认 1,000,000，即每窗口最多 100 万行）"
                              >
                                <InputNumber
                                  min={100000}
                                  max={10000000}
                                  step={100000}
                                  placeholder="1,000,000"
                                  formatter={(v) => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
                                  parser={(v) => Number((v || '').replace(/,/g, '')) as unknown as 100000}
                                  style={{ width: '100%' }}
                                />
                              </Form.Item>
                              <Form.Item
                                name="auto_split_column"
                                label="统计时间列（可选）"
                                extra='auto 模式统计行数的时间列。留空则使用上方"日期列名"。仅允许字母/数字/下划线。'
                              >
                                <Input placeholder="留空使用 date_column" maxLength={64} />
                              </Form.Item>
                            </>
                          )}
                        </>
                      );
                    }}
                  </Form.Item>
                  <Form.Item
                    name="cursor_column"
                    label="游标列名（可选，启用键集分页）"
                    extra={
                      <span>
                        提供后，流式断开自动回退时使用 <code>WHERE cursor &gt; last
                        ORDER BY cursor LIMIT N</code> 推进，代替 LIMIT/OFFSET。
                        大数据集大幅提速 + 消除窗口非确定性。要求列单调可排序
                        （主键 / 时间戳）；<b>不适用于 GROUP BY / DISTINCT 等聚合 SQL</b>。
                        <br />
                        <b>填写 SELECT 子句中的别名</b>（系统包装为子查询，外层只看到别名）。
                        支持字母/数字/下划线/<b>空格/中文</b>；如别名是 <code>`Call ID`</code>，
                        填 <code>Call ID</code> 即可（系统自动反引号包裹）。
                      </span>
                    }
                  >
                    <Input placeholder="如：id、event_time、Call ID、订单_id" maxLength={64} />
                  </Form.Item>
                  <Form.Item
                    name="prefer_chunked"
                    valuePropName="checked"
                    label="首选分批模式（跳过单流首试）"
                    extra={
                      <span>
                        跨境/不稳网络下，单流常在 5 分钟左右被 LB/NAT 切断，每块先单流试错
                        5 分钟再回退非常浪费。勾选后<b>跳过单流首试</b>，直接走
                        keyset（若上方填了游标列）或 LIMIT/OFFSET。
                        <br />
                        <b>强烈建议同时填写游标列名</b>——无游标列时走 LIMIT/OFFSET，
                        后期窗口 OFFSET 重扫开销大且 ClickHouse 并行扫描下结果可能非确定。
                      </span>
                    }
                  >
                    <Switch checkedChildren="跳过单流" unCheckedChildren="试单流" />
                  </Form.Item>
                </>
              ) : null
            }
          </Form.Item>

          <Form.Item
            name="batch_size"
            label="批次大小（高级）"
            extra="每批从数据库读取的行数，影响内存与速度，默认 50,000 行"
          >
            <InputNumber min={1000} max={200000} step={1000} style={{ width: '100%' }} />
          </Form.Item>

          <Alert
            type="info"
            showIcon
            message="导出说明"
            description={
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                <li>每超过 100 万行自动插入新 Sheet，每 Sheet 均含标题行</li>
                <li>Int64 / UInt64 等大整数列自动转为字符串，避免科学计数法</li>
                <li>CSV 会使用 UTF-8 BOM，Excel 直接打开中文不乱码；CSV 转 XLSX 使用标准 CSV 解析，避免引号内逗号/换行错误分列</li>
                <li>分块模式：每块单独生成一个 Excel 文件；优先使用 SQL 占位符以获得最佳查询性能</li>
                <li>导出过程可随时取消，已完成块/文件保留可下载</li>
              </ul>
            }
          />
        </Form>
      </Modal>

      {/* ── 错误详情 Modal ─────────────────────────────────────────────────── */}
      <Modal
        title={
          <Space>
            <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
            <span>导出失败详情</span>
          </Space>
        }
        open={errorModalJob !== null}
        onCancel={() => setErrorModalJob(null)}
        footer={[
          <Button
            key="copy"
            onClick={() => {
              if (errorModalJob?.error_message) {
                navigator.clipboard
                  .writeText(errorModalJob.error_message)
                  .then(() => message.success('错误信息已复制到剪贴板'))
                  .catch(() => message.error('复制失败'));
              }
            }}
          >
            复制错误信息
          </Button>,
          <Button key="close" type="primary" onClick={() => setErrorModalJob(null)}>
            关闭
          </Button>,
        ]}
        width={680}
      >
        {errorModalJob && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Row gutter={[16, 8]}>
              <Col span={8}><Text type="secondary">任务名称：</Text></Col>
              <Col span={16}>
                <Text>{errorModalJob.job_name || errorModalJob.output_filename || '-'}</Text>
              </Col>
              <Col span={8}><Text type="secondary">导出模式：</Text></Col>
              <Col span={16}>
                <Tag>{errorModalJob.export_mode === 'date_chunked' ? '按日期分块' : '单文件'}</Tag>
              </Col>
              <Col span={8}><Text type="secondary">连接：</Text></Col>
              <Col span={16}><Text>{errorModalJob.connection_env}</Text></Col>
              <Col span={8}><Text type="secondary">已导出行数：</Text></Col>
              <Col span={16}>
                <Text>{(errorModalJob.exported_rows ?? 0).toLocaleString()}</Text>
                {errorModalJob.export_mode === 'date_chunked' && (
                  <Text type="secondary" style={{ marginLeft: 8 }}>
                    （已完成 {errorModalJob.done_batches}/{errorModalJob.total_batches ?? '?'} 块）
                  </Text>
                )}
              </Col>
              <Col span={8}><Text type="secondary">创建时间：</Text></Col>
              <Col span={16}>
                <Text>
                  {errorModalJob.created_at
                    ? new Date(errorModalJob.created_at).toLocaleString('zh-CN', { hour12: false })
                    : '-'}
                </Text>
              </Col>
            </Row>
            <Divider style={{ margin: '8px 0' }} />
            <div>
              <Text strong>错误信息：</Text>
              <Input.TextArea
                value={errorModalJob.error_message ?? '（无）'}
                readOnly
                autoSize={{ minRows: 4, maxRows: 12 }}
                style={{ fontFamily: 'monospace', fontSize: 12, marginTop: 8 }}
              />
            </div>
            {errorModalJob.export_mode === 'date_chunked'
              && errorModalJob.output_files
              && errorModalJob.output_files.some(f => f.status === 'completed') && (
              <Alert
                type={errorModalJob.status === 'partial_failed' ? 'success' : 'info'}
                showIcon
                message={
                  errorModalJob.status === 'partial_failed'
                    ? '部分块已完成,可单独或打包下载'
                    : '部分块已完成,可单独下载'
                }
                description={
                  <span>
                    已有
                    {' '}
                    <b>
                      {errorModalJob.output_files.filter(f => f.status === 'completed').length}
                    </b>
                    {' / '}
                    <b>{errorModalJob.output_files.length}</b>
                    {' '}
                    个块完成。关闭此对话框后展开任务行可逐个下载;CSV ZIP 模式可直接下载 partial 包(含已成功块)。
                  </span>
                }
              />
            )}
            {/* v2.14.7: 失败块明细列表 */}
            {errorModalJob.export_mode === 'date_chunked'
              && errorModalJob.output_files
              && errorModalJob.output_files.some(f => f.status === 'failed') && (
              <div>
                <Text strong>失败块明细：</Text>
                <div
                  style={{
                    maxHeight: 200,
                    overflowY: 'auto',
                    fontFamily: 'monospace',
                    fontSize: 12,
                    background: '#fafafa',
                    border: '1px solid #f0f0f0',
                    borderRadius: 4,
                    padding: 8,
                    marginTop: 4,
                  }}
                >
                  {errorModalJob.output_files
                    .filter(f => f.status === 'failed')
                    .map((f) => (
                      <div key={f.index} style={{ marginBottom: 4 }}>
                        <Tag color="error">块 {f.index + 1}</Tag>
                        <span>{f.date_start} ~ {f.date_end}</span>
                        {f.error_summary && (
                          <div style={{ color: '#999', marginLeft: 16, fontSize: 11 }}>
                            {f.error_summary}
                          </div>
                        )}
                      </div>
                    ))}
                </div>
              </div>
            )}
          </Space>
        )}
      </Modal>
    </div>
  );
};

export default DataExport;
