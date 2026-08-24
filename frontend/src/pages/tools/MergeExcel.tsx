/**
 * 小工具 - 合并Excel文件页面
 *
 * 布局分两区：
 *   区域 1  文件选择区（多文件拖拽上传 + 是否含表头 + 提交）
 *   区域 2  历史任务列表（分页 + 进度轮询 + 下载/取消/删除）
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Input,
  message,
  Modal,
  Pagination,
  Popconfirm,
  Progress,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd';
import type { UploadFile, UploadProps } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  FileExcelOutlined,
  InboxOutlined,
  LoadingOutlined,
  StopOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { mergeExcelApi, MergeJob, MergeJobListResult } from '@/services/mergeExcelApi';

const { Title, Text } = Typography;
const { Dragger } = Upload;

// 活跃任务状态（需要轮询）
const ACTIVE_STATUSES = new Set(['pending', 'running', 'cancelling']);

const STATUS_TAG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  pending:    { color: 'default',    icon: <SyncOutlined spin />,     label: '等待中' },
  running:    { color: 'processing', icon: <LoadingOutlined spin />,  label: '合并中' },
  completed:  { color: 'success',    icon: <CheckCircleOutlined />,   label: '已完成' },
  failed:     { color: 'error',      icon: <CloseCircleOutlined />,   label: '失败' },
  cancelling: { color: 'warning',    icon: <SyncOutlined spin />,     label: '取消中' },
  cancelled:  { color: 'default',    icon: <StopOutlined />,          label: '已取消' },
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

const MergeExcel: React.FC = () => {
  // ── 文件选择区 ───────────────────────────────────────────────────────────────
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [hasHeader, setHasHeader] = useState(true);
  const [jobName, setJobName] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // ── 任务历史 ─────────────────────────────────────────────────────────────────
  const [jobList, setJobList] = useState<MergeJobListResult | null>(null);
  const [listPage, setListPage] = useState(1);
  const [listPageSize] = useState(10);
  const [listLoading, setListLoading] = useState(false);
  const [downloadingIds, setDownloadingIds] = useState<Set<string>>(new Set());
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── 错误/警告详情 Modal ─────────────────────────────────────────────────────
  const [detailJob, setDetailJob] = useState<MergeJob | null>(null);

  const loadJobList = useCallback(
    async (page = listPage, silent = false) => {
      if (!silent) setListLoading(true);
      try {
        const result = await mergeExcelApi.listJobs(page, listPageSize);
        setJobList(result);
      } catch (e: any) {
        if (!silent) message.error(`加载任务列表失败: ${e?.response?.data?.detail ?? e.message}`);
      } finally {
        if (!silent) setListLoading(false);
      }
    },
    [listPage, listPageSize],
  );

  useEffect(() => {
    loadJobList(1);
  }, []);

  useEffect(() => {
    const hasActive = jobList?.items.some((j) => ACTIVE_STATUSES.has(j.status));
    if (hasActive) {
      pollTimerRef.current = setInterval(() => loadJobList(listPage, true), 2000);
    } else if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [jobList, listPage]);

  // ── 文件选择 ─────────────────────────────────────────────────────────────────
  const uploadProps: UploadProps = {
    multiple: true,
    accept: '.xlsx,.xls',
    fileList,
    beforeUpload: (file) => {
      if (!file.name.toLowerCase().match(/\.(xlsx|xls)$/)) {
        message.error(`${file.name} 不是 Excel 文件`);
        return Upload.LIST_IGNORE;
      }
      setFileList((prev) => [...prev, file as unknown as UploadFile]);
      return false;
    },
    onRemove: (file) => {
      setFileList((prev) => prev.filter((f) => f.uid !== file.uid));
    },
  };

  // ── 提交合并 ─────────────────────────────────────────────────────────────────
  const handleMerge = async () => {
    if (fileList.length < 2) {
      message.warning('请至少选择 2 个 Excel 文件');
      return;
    }
    setSubmitting(true);
    try {
      const fileIds: string[] = [];
      for (const f of fileList) {
        const raw = (f.originFileObj ?? f) as unknown as File;
        const result = await mergeExcelApi.uploadFile(raw);
        fileIds.push(result.upload_id);
      }
      await mergeExcelApi.executeMerge(fileIds, hasHeader, jobName || undefined);
      message.success('合并任务已提交');
      setFileList([]);
      setJobName('');
      await loadJobList(1);
      setListPage(1);
    } catch (e: any) {
      message.error(`提交失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  // ── 取消 / 删除 / 下载 ───────────────────────────────────────────────────────
  const handleCancel = async (jobId: string) => {
    try {
      const r = await mergeExcelApi.cancelJob(jobId);
      message.success(r.status === 'cancelled' ? '任务已取消' : '取消请求已发送');
      await loadJobList(listPage, true);
    } catch (e: any) {
      message.error(`取消失败: ${e?.response?.data?.detail ?? e.message}`);
    }
  };

  const handleDelete = async (jobId: string) => {
    try {
      await mergeExcelApi.deleteJob(jobId);
      message.success('任务已删除');
      await loadJobList(listPage);
    } catch (e: any) {
      message.error(`删除失败: ${e?.response?.data?.detail ?? e.message}`);
    }
  };

  const handleDownload = async (job: MergeJob) => {
    setDownloadingIds((prev) => new Set(prev).add(job.job_id));
    try {
      const blob = await mergeExcelApi.downloadFile(job.job_id);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = job.output_filename ?? `merged_${job.job_id}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    } catch (e: any) {
      let detail = e?.message ?? '未知错误';
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
      setDownloadingIds((prev) => {
        const next = new Set(prev);
        next.delete(job.job_id);
        return next;
      });
    }
  };

  // ── 历史任务表格列 ────────────────────────────────────────────────────────────
  const jobColumns = [
    {
      title: '任务名称',
      dataIndex: 'job_name',
      key: 'job_name',
      width: 160,
      ellipsis: true,
      render: (v: string | null, r: MergeJob) => v || r.output_filename || '-',
    },
    {
      title: '文件数',
      key: 'files',
      width: 90,
      render: (_: any, r: MergeJob) => `${r.done_files ?? 0}/${r.total_files ?? '-'}`,
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
      width: 200,
      render: (_: any, r: MergeJob) => {
        if (r.status === 'completed') {
          return (
            <Space direction="vertical" size={0}>
              <Text type="success">
                {r.merged_rows?.toLocaleString()} 行 / {r.total_sheets} Sheet
              </Text>
              {r.warnings && r.warnings.length > 0 && (
                <Button
                  type="link"
                  size="small"
                  style={{ padding: 0, height: 'auto', color: '#faad14' }}
                  onClick={() => setDetailJob(r)}
                >
                  <Text style={{ fontSize: 11, color: '#faad14' }}>
                    <ExclamationCircleOutlined /> {r.warnings.length} 条提示
                  </Text>
                </Button>
              )}
            </Space>
          );
        }
        if (r.status === 'failed') {
          return (
            <Button
              type="link"
              size="small"
              danger
              style={{ padding: 0, height: 'auto', textAlign: 'left' }}
              onClick={() => setDetailJob(r)}
            >
              <Text type="danger">失败 · 查看详情</Text>
            </Button>
          );
        }
        if (r.status === 'cancelled' || r.status === 'cancelling') {
          return <Text type="secondary">{r.merged_rows?.toLocaleString() ?? 0} 行（已取消）</Text>;
        }
        const pct =
          r.total_files && r.total_files > 0
            ? Math.round(((r.done_files ?? 0) / r.total_files) * 100)
            : 0;
        return (
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Progress percent={pct} status="active" size="small" />
            <Text type="secondary" style={{ fontSize: 11 }}>
              {r.merged_rows?.toLocaleString() ?? 0} 行
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
      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      fixed: 'right' as const,
      render: (_: any, r: MergeJob) => (
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
              title="确认取消该合并任务？"
              onConfirm={() => handleCancel(r.job_id)}
              okText="取消任务"
              cancelText="保留"
            >
              <Tooltip title="取消">
                <Button type="link" size="small" danger icon={<StopOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          {['completed', 'failed', 'cancelled'].includes(r.status) && (
            <Popconfirm
              title="确认删除该任务记录？（同时删除本地文件）"
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

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>合并 Excel 文件</Title>
      <Text type="secondary">
        选择多个 Excel 文件，按文件名排序合并为一个文件；首文件表头保留一次，超过 100 万行自动分 Sheet。
      </Text>

      <Card style={{ marginTop: 16 }}>
        <Dragger {...uploadProps} style={{ padding: '16px 0' }}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽多个 Excel 文件到此区域</p>
          <p className="ant-upload-hint">支持 .xlsx / .xls，可多选（至少 2 个文件）</p>
        </Dragger>

        <Space style={{ marginTop: 16 }} size="large" wrap>
          <Space>
            <Text>包含表头：</Text>
            <Switch checked={hasHeader} onChange={setHasHeader} />
          </Space>
          <Space>
            <Text>任务名称：</Text>
            <Input
              placeholder="可选，用于输出文件名"
              style={{ width: 220 }}
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
            />
          </Space>
          <Button
            type="primary"
            icon={<FileExcelOutlined />}
            loading={submitting}
            disabled={fileList.length < 2}
            onClick={handleMerge}
          >
            开始合并（{fileList.length} 个文件）
          </Button>
        </Space>
      </Card>

      <Card title="历史任务" style={{ marginTop: 16 }} loading={listLoading}>
        <Table
          rowKey="job_id"
          dataSource={jobList?.items ?? []}
          columns={jobColumns as any}
          pagination={false}
          scroll={{ x: 900 }}
          size="small"
        />
        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Pagination
            current={listPage}
            pageSize={listPageSize}
            total={jobList?.total ?? 0}
            onChange={(page) => {
              setListPage(page);
              loadJobList(page);
            }}
            showSizeChanger={false}
          />
        </div>
      </Card>

      <Modal
        open={!!detailJob}
        title={detailJob?.status === 'failed' ? '任务失败详情' : '任务提示详情'}
        onCancel={() => setDetailJob(null)}
        footer={null}
      >
        {detailJob?.status === 'failed' && (
          <Alert type="error" message={detailJob.error_message} showIcon />
        )}
        {detailJob?.warnings && detailJob.warnings.length > 0 && (
          <Space direction="vertical" style={{ width: '100%' }}>
            {detailJob.warnings.map((w, i) => (
              <Alert key={i} type="warning" message={w} showIcon />
            ))}
          </Space>
        )}
      </Modal>
    </div>
  );
};

export default MergeExcel;
