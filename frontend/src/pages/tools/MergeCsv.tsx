/**
 * 小工具 - 合并CSV文件页面
 *
 * 布局分四区：
 *   区域 1  选择文件 —— 四个**并列**入口（导出任务 / 目录 / 手工选择 / 本地上传）
 *   区域 2  已选文件 —— 提交的唯一事实源，可混合来源，自然排序后展示
 *   区域 3  选项与预检 —— 预检未通过则不允许提交
 *   区域 4  历史任务 —— 进度按字节（精确）、结果给路径、完成后行数对账
 *
 * 与「合并Excel文件」页面的关键差异：
 *   · 结果不走 blob 下载（几十 GB 会打爆标签页），主路径是**复制服务器路径**
 *   · 进度用 done_bytes/total_bytes，字节数 100% 精确且免费
 *   · 完成后展示**双向行数对账**：导出侧自报行数 vs 合并实际行数
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Input,
  Modal,
  Pagination,
  Popconfirm,
  Progress,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import type { UploadFile, UploadProps } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  LoadingOutlined,
  MergeCellsOutlined,
  ReloadOutlined,
  StopOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  CsvSourceFile,
  DirSummary,
  ExportJobSummary,
  MergeCsvJob,
  MergeCsvJobListResult,
  PreviewResult,
  SortMode,
  mergeCsvApi,
} from '@/services/mergeCsvApi';

const { Title, Text, Paragraph } = Typography;
const { Dragger } = Upload;

const ACTIVE_STATUSES = new Set(['pending', 'running', 'cancelling']);

type EntryKey = 'export' | 'dir' | 'file' | 'upload';

const ENTRY_OPTIONS = [
  { label: '导出任务', value: 'export' as EntryKey, icon: <FileTextOutlined /> },
  { label: '目录', value: 'dir' as EntryKey, icon: <FolderOpenOutlined /> },
  { label: '手工选择', value: 'file' as EntryKey, icon: <MergeCellsOutlined /> },
  { label: '本地上传', value: 'upload' as EntryKey, icon: <InboxOutlined /> },
];

const STATUS_TAG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  pending: { color: 'default', icon: <SyncOutlined spin />, label: '等待中' },
  running: { color: 'processing', icon: <LoadingOutlined spin />, label: '合并中' },
  completed: { color: 'success', icon: <CheckCircleOutlined />, label: '已完成' },
  failed: { color: 'error', icon: <CloseCircleOutlined />, label: '失败' },
  cancelling: { color: 'warning', icon: <SyncOutlined spin />, label: '取消中' },
  cancelled: { color: 'default', icon: <StopOutlined />, label: '已取消' },
};

function StatusTag({ status }: { status: string }) {
  const cfg = STATUS_TAG[status] ?? { color: 'default', icon: null, label: status };
  return (
    <Tag color={cfg.color} icon={cfg.icon}>
      {cfg.label}
    </Tag>
  );
}

function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatDuration(seconds?: number | null): string {
  if (!seconds || seconds < 1) return '< 1 秒';
  if (seconds < 60) return `约 ${Math.round(seconds)} 秒`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `约 ${m} 分 ${s} 秒`;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 非 HTTPS / 旧浏览器下 clipboard API 不可用，退回 execCommand
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

const MergeCsv: React.FC = () => {
  // ── 区域 1：入口 ────────────────────────────────────────────────────────────
  const [entry, setEntry] = useState<EntryKey>('export');

  const [exportJobs, setExportJobs] = useState<ExportJobSummary[]>([]);
  const [exportLoading, setExportLoading] = useState(false);

  const [dirs, setDirs] = useState<DirSummary[]>([]);
  const [dirLoading, setDirLoading] = useState(false);

  const [pickedDir, setPickedDir] = useState<string | undefined>();
  const [dirFiles, setDirFiles] = useState<CsvSourceFile[]>([]);
  const [dirFilesLoading, setDirFilesLoading] = useState(false);
  const [filePattern, setFilePattern] = useState('');
  const [checkedPaths, setCheckedPaths] = useState<string[]>([]);

  const [uploadList, setUploadList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);

  // ── 区域 2：已选文件（提交的唯一事实源）────────────────────────────────────
  const [selected, setSelected] = useState<CsvSourceFile[]>([]);
  const [uploadIds, setUploadIds] = useState<Record<string, string>>({}); // file_path → upload_id

  // ── 区域 3：选项与预检 ──────────────────────────────────────────────────────
  const [hasHeader, setHasHeader] = useState(true);
  const [strictHeader, setStrictHeader] = useState(true);
  const [sortMode, setSortMode] = useState<SortMode>('natural');
  const [jobName, setJobName] = useState('');
  const [allowActive, setAllowActive] = useState(false);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // ── 区域 4：历史任务 ────────────────────────────────────────────────────────
  const [jobList, setJobList] = useState<MergeCsvJobListResult | null>(null);
  const [listPage, setListPage] = useState(1);
  const listPageSize = 10;
  const [listLoading, setListLoading] = useState(false);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [detailJob, setDetailJob] = useState<MergeCsvJob | null>(null);

  // ── 载入 ────────────────────────────────────────────────────────────────────

  const loadExportJobs = useCallback(async () => {
    setExportLoading(true);
    try {
      const r = await mergeCsvApi.listExportJobs(1, 30);
      setExportJobs(r?.items ?? []);
    } catch (e: any) {
      message.error(`加载导出任务失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setExportLoading(false);
    }
  }, []);

  const loadDirs = useCallback(async () => {
    setDirLoading(true);
    try {
      setDirs(await mergeCsvApi.listDirs());
    } catch (e: any) {
      message.error(`加载目录失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setDirLoading(false);
    }
  }, []);

  const loadDirFiles = useCallback(async (dirPath: string, pattern?: string) => {
    setDirFilesLoading(true);
    try {
      const files = await mergeCsvApi.listFiles(dirPath, pattern || undefined);
      setDirFiles(files);
      setCheckedPaths([]);
    } catch (e: any) {
      message.error(`加载文件失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setDirFilesLoading(false);
    }
  }, []);

  const loadJobList = useCallback(
    async (page = listPage, silent = false) => {
      if (!silent) setListLoading(true);
      try {
        setJobList(await mergeCsvApi.listJobs(page, listPageSize));
      } catch (e: any) {
        if (!silent) message.error(`加载任务列表失败: ${e?.response?.data?.detail ?? e.message}`);
      } finally {
        if (!silent) setListLoading(false);
      }
    },
    [listPage],
  );

  useEffect(() => {
    loadExportJobs();
    loadDirs();
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

  // 已选文件一变，之前的预检结果立即失效
  useEffect(() => {
    setPreview(null);
  }, [selected, hasHeader, strictHeader, sortMode, allowActive]);

  // ── 已选清单操作 ────────────────────────────────────────────────────────────

  const addFiles = useCallback((files: CsvSourceFile[]) => {
    setSelected((prev) => {
      const known = new Set(prev.map((f) => f.file_path));
      const fresh = files.filter((f) => !known.has(f.file_path));
      if (fresh.length === 0) {
        message.info('这些文件已在已选清单中');
        return prev;
      }
      if (fresh.length < files.length) {
        message.info(`已加入 ${fresh.length} 个，跳过 ${files.length - fresh.length} 个重复文件`);
      } else {
        message.success(`已加入 ${fresh.length} 个文件`);
      }
      return [...prev, ...fresh];
    });
  }, []);

  const removeFile = (filePath: string) => {
    setSelected((prev) => prev.filter((f) => f.file_path !== filePath));
    setUploadIds((prev) => {
      const next = { ...prev };
      delete next[filePath];
      return next;
    });
  };

  const clearSelected = () => {
    setSelected([]);
    setUploadIds({});
    setUploadList([]);
  };

  const totalSelectedBytes = useMemo(
    () => selected.reduce((s, f) => s + (f.size ?? 0), 0),
    [selected],
  );

  // ── 入口①：导出任务 ────────────────────────────────────────────────────────

  const pickExportJob = (job: ExportJobSummary) => {
    if (!job.files?.length) {
      message.warning('该导出任务下没有可用于合并的 CSV 文件');
      return;
    }
    addFiles(job.files);
    if (job.incomplete_chunks?.length) {
      message.warning(
        `已排除 ${job.incomplete_chunks.length} 个未完成的分块：${job.incomplete_chunks.join('、')}`,
        6,
      );
    }
    if (job.compressed_chunks?.length) {
      message.warning(
        `${job.compressed_chunks.length} 个分块的 CSV 已被压缩，请先解压：${job.compressed_chunks.join('、')}`,
        6,
      );
    }
  };

  // ── 入口④：上传 ────────────────────────────────────────────────────────────

  const uploadProps: UploadProps = {
    multiple: true,
    accept: '.csv',
    fileList: uploadList,
    beforeUpload: (file) => {
      if (!file.name.toLowerCase().endsWith('.csv')) {
        message.error(`${file.name} 不是 CSV 文件`);
        return Upload.LIST_IGNORE;
      }
      setUploadList((prev) => [...prev, file as unknown as UploadFile]);
      return false;
    },
    onRemove: (file) => {
      setUploadList((prev) => prev.filter((f) => f.uid !== file.uid));
    },
  };

  const doUpload = async () => {
    if (!uploadList.length) {
      message.warning('请先选择要上传的 CSV 文件');
      return;
    }
    setUploading(true);
    try {
      const added: CsvSourceFile[] = [];
      const idMap: Record<string, string> = {};
      for (const f of uploadList) {
        const raw = (f.originFileObj ?? f) as unknown as File;
        const r = await mergeCsvApi.uploadFile(raw);
        added.push({
          filename: r.filename,
          file_path: r.file_path,
          size: r.file_size,
          origin: 'upload',
        });
        idMap[r.file_path] = r.upload_id;
      }
      setUploadIds((prev) => ({ ...prev, ...idMap }));
      addFiles(added);
      setUploadList([]);
    } catch (e: any) {
      message.error(`上传失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setUploading(false);
    }
  };

  // ── 预检与提交 ──────────────────────────────────────────────────────────────

  const buildPayload = () => {
    const serverPaths = selected.filter((f) => f.origin === 'server').map((f) => f.file_path);
    const ids = selected
      .filter((f) => f.origin === 'upload')
      .map((f) => uploadIds[f.file_path])
      .filter(Boolean);
    return {
      server_paths: serverPaths,
      upload_ids: ids,
      has_header: hasHeader,
      strict_header: strictHeader,
      sort_mode: sortMode,
      job_name: jobName || undefined,
      allow_active_files: allowActive,
    };
  };

  const doPreview = async () => {
    if (selected.length < 1) {
      message.warning('请先选择要合并的 CSV 文件');
      return;
    }
    setPreviewing(true);
    try {
      setPreview(await mergeCsvApi.preview(buildPayload()));
    } catch (e: any) {
      message.error(`预检失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setPreviewing(false);
    }
  };

  const doSubmit = async () => {
    if (!preview?.ok) {
      message.warning('请先通过预检');
      return;
    }
    setSubmitting(true);
    try {
      await mergeCsvApi.execute(buildPayload());
      message.success('合并任务已提交');
      clearSelected();
      setJobName('');
      setPreview(null);
      setListPage(1);
      await loadJobList(1);
    } catch (e: any) {
      message.error(`提交失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  // ── 任务操作 ────────────────────────────────────────────────────────────────

  const handleCancel = async (jobId: string) => {
    try {
      const r = await mergeCsvApi.cancelJob(jobId);
      message.success(r.status === 'cancelled' ? '任务已取消' : '取消请求已发送');
      await loadJobList(listPage, true);
    } catch (e: any) {
      message.error(`取消失败: ${e?.response?.data?.detail ?? e.message}`);
    }
  };

  const handleDelete = async (jobId: string) => {
    try {
      await mergeCsvApi.deleteJob(jobId);
      message.success('任务已删除');
      await loadJobList(listPage);
    } catch (e: any) {
      message.error(`删除失败: ${e?.response?.data?.detail ?? e.message}`);
    }
  };

  const handleCopyPath = async (path: string) => {
    message[(await copyText(path)) ? 'success' : 'error'](
      (await copyText(path)) ? '路径已复制' : '复制失败，请手动选择路径文本',
    );
  };

  // ── 区域 1 渲染 ─────────────────────────────────────────────────────────────

  const renderEntryExport = () => (
    <Table
      rowKey="export_job_id"
      size="small"
      loading={exportLoading}
      dataSource={exportJobs}
      pagination={false}
      scroll={{ y: 260 }}
      locale={{ emptyText: <Empty description="没有找到 CSV 导出任务" /> }}
      columns={[
        {
          title: '导出任务',
          dataIndex: 'job_name',
          ellipsis: true,
          render: (v: string | null, r: ExportJobSummary) => (
            <Space direction="vertical" size={0}>
              <Text>{v || r.export_job_id.slice(0, 8)}</Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {r.created_at ? new Date(r.created_at).toLocaleString('zh-CN', { hour12: false }) : '-'}
              </Text>
            </Space>
          ),
        },
        {
          title: 'CSV 分块',
          dataIndex: 'csv_files',
          width: 100,
          render: (v: number) => `${v} 个`,
        },
        { title: '合计大小', dataIndex: 'total_size', width: 110, render: formatBytes },
        {
          title: '导出侧行数',
          dataIndex: 'total_rows',
          width: 120,
          render: (v: number | null) => (v === null ? <Text type="secondary">未知</Text> : v.toLocaleString()),
        },
        {
          title: '缺口',
          key: 'gaps',
          width: 140,
          render: (_: any, r: ExportJobSummary) => {
            const n = (r.incomplete_chunks?.length ?? 0) + (r.compressed_chunks?.length ?? 0);
            return n === 0 ? (
              <Tag color="success">完整</Tag>
            ) : (
              <Tooltip
                title={
                  <>
                    {r.incomplete_chunks?.length ? <div>未完成：{r.incomplete_chunks.join('、')}</div> : null}
                    {r.compressed_chunks?.length ? <div>已压缩：{r.compressed_chunks.join('、')}</div> : null}
                  </>
                }
              >
                <Tag color="warning" icon={<WarningOutlined />}>
                  {n} 个分块不可用
                </Tag>
              </Tooltip>
            );
          },
        },
        {
          title: '操作',
          key: 'act',
          width: 110,
          render: (_: any, r: ExportJobSummary) => (
            <Button type="link" size="small" onClick={() => pickExportJob(r)}>
              加入全部
            </Button>
          ),
        },
      ]}
    />
  );

  const renderEntryDir = () => (
    <Table
      rowKey="dir_path"
      size="small"
      loading={dirLoading}
      dataSource={dirs}
      pagination={false}
      scroll={{ y: 260 }}
      locale={{ emptyText: <Empty description="没有找到含 CSV 的目录" /> }}
      columns={[
        { title: '目录', dataIndex: 'display_path', ellipsis: true },
        { title: 'CSV 文件', dataIndex: 'csv_files', width: 100, render: (v: number) => `${v} 个` },
        { title: '合计大小', dataIndex: 'total_size', width: 110, render: formatBytes },
        {
          title: '最新修改',
          dataIndex: 'latest_mtime',
          width: 170,
          render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '-'),
        },
        {
          title: '操作',
          key: 'act',
          width: 160,
          render: (_: any, r: DirSummary) => (
            <Space size={4}>
              <Button
                type="link"
                size="small"
                onClick={async () => {
                  const files = await mergeCsvApi.listFiles(r.dir_path);
                  addFiles(files);
                }}
              >
                加入全部
              </Button>
              <Button
                type="link"
                size="small"
                onClick={() => {
                  setPickedDir(r.dir_path);
                  setEntry('file');
                  loadDirFiles(r.dir_path);
                }}
              >
                逐个挑
              </Button>
            </Space>
          ),
        },
      ]}
    />
  );

  const renderEntryFile = () => (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space wrap>
        <Text>目录：</Text>
        <Select
          style={{ width: 380 }}
          placeholder="选择一个目录"
          value={pickedDir}
          options={dirs.map((d) => ({ label: `${d.display_path}（${d.csv_files} 个）`, value: d.dir_path }))}
          onChange={(v) => {
            setPickedDir(v);
            loadDirFiles(v, filePattern);
          }}
          showSearch
          optionFilterProp="label"
        />
        <Input
          style={{ width: 200 }}
          placeholder="文件名过滤，如 *202606*"
          value={filePattern}
          onChange={(e) => setFilePattern(e.target.value)}
          onPressEnter={() => pickedDir && loadDirFiles(pickedDir, filePattern)}
          allowClear
        />
        <Button
          icon={<ReloadOutlined />}
          disabled={!pickedDir}
          onClick={() => pickedDir && loadDirFiles(pickedDir, filePattern)}
        >
          刷新
        </Button>
      </Space>

      <Spin spinning={dirFilesLoading}>
        {dirFiles.length === 0 ? (
          <Empty description="选择目录后在此勾选文件" />
        ) : (
          <>
            <Space style={{ marginBottom: 8 }}>
              <Checkbox
                indeterminate={checkedPaths.length > 0 && checkedPaths.length < dirFiles.length}
                checked={checkedPaths.length === dirFiles.length}
                onChange={(e) =>
                  setCheckedPaths(e.target.checked ? dirFiles.map((f) => f.file_path) : [])
                }
              >
                全选（{dirFiles.length} 个）
              </Checkbox>
              <Button
                type="primary"
                size="small"
                disabled={checkedPaths.length === 0}
                onClick={() => {
                  addFiles(dirFiles.filter((f) => checkedPaths.includes(f.file_path)));
                  setCheckedPaths([]);
                }}
              >
                加入已选（{checkedPaths.length}）
              </Button>
            </Space>
            <div style={{ maxHeight: 220, overflow: 'auto', border: '1px solid #f0f0f0', padding: 8 }}>
              <Checkbox.Group
                value={checkedPaths}
                onChange={(v) => setCheckedPaths(v as string[])}
                style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
              >
                {dirFiles.map((f) => (
                  <Checkbox key={f.file_path} value={f.file_path}>
                    <Text style={{ fontSize: 12 }}>{f.filename}</Text>{' '}
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {formatBytes(f.size)}
                    </Text>
                  </Checkbox>
                ))}
              </Checkbox.Group>
            </div>
          </>
        )}
      </Spin>
    </Space>
  );

  const renderEntryUpload = () => (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Dragger {...uploadProps} style={{ padding: '12px 0' }}>
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽 CSV 文件到此区域</p>
        <p className="ant-upload-hint">仅 .csv，单文件上限 1 GB。更大的文件请走「导出任务」或「目录」入口，零拷贝</p>
      </Dragger>
      <Button type="primary" loading={uploading} disabled={!uploadList.length} onClick={doUpload}>
        上传并加入已选（{uploadList.length} 个）
      </Button>
    </Space>
  );

  // ── 历史任务列 ──────────────────────────────────────────────────────────────

  const jobColumns = [
    {
      title: '任务名称',
      dataIndex: 'job_name',
      width: 150,
      ellipsis: true,
      render: (v: string | null, r: MergeCsvJob) => v || r.output_filename || r.job_id.slice(0, 8),
    },
    {
      title: '文件',
      key: 'files',
      width: 80,
      render: (_: any, r: MergeCsvJob) => `${r.done_files ?? 0}/${r.total_files ?? '-'}`,
    },
    { title: '状态', dataIndex: 'status', width: 96, render: (s: string) => <StatusTag status={s} /> },
    {
      title: '进度 / 结果',
      key: 'progress',
      width: 300,
      render: (_: any, r: MergeCsvJob) => {
        if (r.status === 'completed') {
          return (
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Text type="success">{(r.total_rows ?? 0).toLocaleString()} 行</Text>
              {r.reconcile_status === 'matched' && (
                <Text style={{ fontSize: 11, color: '#52c41a' }}>
                  <CheckCircleOutlined /> 对账一致：导出侧{' '}
                  {(r.expected_total_rows ?? 0).toLocaleString()} 行
                </Text>
              )}
              {r.reconcile_status === 'mismatched' && (
                <Button
                  type="link"
                  size="small"
                  danger
                  style={{ padding: 0, height: 'auto' }}
                  onClick={() => setDetailJob(r)}
                >
                  <Text type="danger" style={{ fontSize: 11 }}>
                    <ExclamationCircleOutlined /> 对账不一致：导出侧{' '}
                    {(r.expected_total_rows ?? 0).toLocaleString()} 行 · 查看详情
                  </Text>
                </Button>
              )}
              {r.warnings && r.warnings.length > 0 && (
                <Button
                  type="link"
                  size="small"
                  style={{ padding: 0, height: 'auto' }}
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
        if (r.status === 'cancelled') {
          return (
            <Space direction="vertical" size={0}>
              <Text type="secondary">{(r.total_rows ?? 0).toLocaleString()} 行（已取消）</Text>
              {r.last_merged_file && (
                <Text type="secondary" style={{ fontSize: 11 }}>
                  已合并到：{r.last_merged_file}（第 {r.done_files}/{r.total_files} 个）
                </Text>
              )}
            </Space>
          );
        }
        const pct =
          r.total_bytes && r.total_bytes > 0
            ? Math.min(100, Math.round(((r.done_bytes ?? 0) / r.total_bytes) * 100))
            : 0;
        return (
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Progress percent={pct} status="active" size="small" />
            <Text type="secondary" style={{ fontSize: 11 }}>
              {formatBytes(r.done_bytes)} / {formatBytes(r.total_bytes)} ·{' '}
              {(r.total_rows ?? 0).toLocaleString()} 行
              {r.last_merged_file ? ` · ${r.last_merged_file}` : ''}
            </Text>
          </Space>
        );
      },
    },
    { title: '结果大小', dataIndex: 'file_size', width: 96, render: formatBytes },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 190,
      fixed: 'right' as const,
      render: (_: any, r: MergeCsvJob) => (
        <Space size={2}>
          {(r.status === 'completed' || r.status === 'cancelled') && r.file_path && (
            <>
              <Tooltip title="复制服务器路径（本地部署下最快，零拷贝）">
                <Button
                  type="link"
                  size="small"
                  icon={<CopyOutlined />}
                  onClick={() => handleCopyPath(r.file_path!)}
                />
              </Tooltip>
              <Tooltip title="浏览器下载（流式，大文件也不吃内存）">
                <Button
                  type="link"
                  size="small"
                  icon={<DownloadOutlined />}
                  href={mergeCsvApi.downloadUrl(r.job_id)}
                  target="_blank"
                />
              </Tooltip>
            </>
          )}
          <Button type="link" size="small" onClick={() => setDetailJob(r)}>
            明细
          </Button>
          {(r.status === 'pending' || r.status === 'running') && (
            <Popconfirm
              title="确认取消该合并任务？"
              description="已合并的部分会保留，并告知合并到哪个文件为止"
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
              title="确认删除该任务记录？"
              description="会删除合并结果文件；源文件（导出产物）不会被删除"
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

  // ── 渲染 ────────────────────────────────────────────────────────────────────

  const sortedPreviewFiles = preview?.sorted_files ?? [];

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>合并 CSV 文件</Title>
      <Text type="secondary">
        把多个字段相同的 CSV 按文件名排序拼成一个文件，表头只保留一份。字节级拼接，
        几 GB 也是分钟级；行数按 RFC4180 精确统计（引号内的换行不算换行）。
      </Text>

      {/* 区域 1 ─ 选择文件 */}
      <Card style={{ marginTop: 16 }} title="1 · 选择文件" size="small">
        <Segmented
          options={ENTRY_OPTIONS}
          value={entry}
          onChange={(v) => setEntry(v as EntryKey)}
          style={{ marginBottom: 12 }}
        />
        {entry === 'export' && renderEntryExport()}
        {entry === 'dir' && renderEntryDir()}
        {entry === 'file' && renderEntryFile()}
        {entry === 'upload' && renderEntryUpload()}
      </Card>

      {/* 区域 2 ─ 已选文件 */}
      <Card
        style={{ marginTop: 16 }}
        size="small"
        title={
          <Space>
            <span>2 · 已选文件</span>
            <Tag>{selected.length} 个</Tag>
            <Tag>{formatBytes(totalSelectedBytes)}</Tag>
          </Space>
        }
        extra={
          selected.length > 0 && (
            <Button size="small" danger onClick={clearSelected}>
              清空
            </Button>
          )
        }
      >
        {selected.length === 0 ? (
          <Empty description="从上面四个入口任选其一加入文件，可混合来源" />
        ) : (
          <Table
            rowKey="file_path"
            size="small"
            dataSource={selected}
            pagination={selected.length > 10 ? { pageSize: 10, size: 'small' } : false}
            columns={[
              { title: '文件名', dataIndex: 'filename', ellipsis: true },
              { title: '大小', dataIndex: 'size', width: 100, render: formatBytes },
              {
                title: '来源',
                dataIndex: 'origin',
                width: 90,
                render: (v: string) => (
                  <Tag color={v === 'upload' ? 'blue' : 'default'}>
                    {v === 'upload' ? '上传' : '服务器'}
                  </Tag>
                ),
              },
              {
                title: '',
                key: 'act',
                width: 50,
                render: (_: any, r: CsvSourceFile) => (
                  <Button
                    type="link"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => removeFile(r.file_path)}
                  />
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 区域 3 ─ 选项与预检 */}
      <Card style={{ marginTop: 16 }} size="small" title="3 · 选项与预检">
        <Space size="large" wrap>
          <Space>
            <Text>包含表头：</Text>
            <Switch checked={hasHeader} onChange={setHasHeader} />
          </Space>
          <Space>
            <Tooltip title="表头文字不一致时阻断。关闭后按位置合并 —— 若列顺序变了会把不同语义的数据混进同一列，且不报错">
              <Text>
                严格表头校验 <ExclamationCircleOutlined style={{ color: '#faad14' }} />：
              </Text>
            </Tooltip>
            <Switch checked={strictHeader} onChange={setStrictHeader} disabled={!hasHeader} />
          </Space>
          <Space>
            <Text>排序：</Text>
            <Select
              style={{ width: 190 }}
              value={sortMode}
              onChange={(v) => setSortMode(v)}
              options={[
                { label: '自然排序（推荐）', value: 'natural' },
                { label: '字典排序', value: 'lexicographic' },
              ]}
            />
          </Space>
          <Space>
            <Text>任务名称：</Text>
            <Input
              placeholder="可选，用于输出文件名"
              style={{ width: 200 }}
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
            />
          </Space>
          <Space>
            <Tooltip title="放行「无法确认来源且刚被修改」的文件。仅在你确定文件已写完时使用">
              <Text>允许可疑文件：</Text>
            </Tooltip>
            <Switch checked={allowActive} onChange={setAllowActive} />
          </Space>
        </Space>

        <Space style={{ marginTop: 16 }}>
          <Button loading={previewing} disabled={selected.length === 0} onClick={doPreview}>
            预检
          </Button>
          <Button
            type="primary"
            icon={<MergeCellsOutlined />}
            loading={submitting}
            disabled={!preview?.ok}
            onClick={doSubmit}
          >
            开始合并
          </Button>
          {!preview && selected.length > 0 && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              先跑一次预检，确认排序、表头与编码一致性、磁盘空间
            </Text>
          )}
        </Space>

        {preview && (
          <div style={{ marginTop: 16 }}>
            {preview.errors.map((e, i) => (
              <Alert key={`e${i}`} type="error" message={e} showIcon style={{ marginBottom: 8 }} />
            ))}
            {preview.warnings.map((w, i) => (
              <Alert key={`w${i}`} type="warning" message={w} showIcon style={{ marginBottom: 8 }} />
            ))}
            {preview.ok && (
              <Alert
                type="success"
                showIcon
                style={{ marginBottom: 8 }}
                message="预检通过"
                description={
                  <Descriptions size="small" column={2} style={{ marginTop: 8 }}>
                    <Descriptions.Item label="文件数">{sortedPreviewFiles.length}</Descriptions.Item>
                    <Descriptions.Item label="合计大小">{formatBytes(preview.total_bytes)}</Descriptions.Item>
                    <Descriptions.Item label="列数">{preview.col_count}</Descriptions.Item>
                    <Descriptions.Item label="输出编码">
                      {preview.output_encoding}
                      {preview.output_bom ? ' + BOM' : ''}
                    </Descriptions.Item>
                    <Descriptions.Item label="首个文件">
                      {sortedPreviewFiles[0]?.filename ?? '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="末个文件">
                      {sortedPreviewFiles[sortedPreviewFiles.length - 1]?.filename ?? '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="磁盘剩余">{formatBytes(preview.disk_free)}</Descriptions.Item>
                    <Descriptions.Item label="预估耗时">
                      {formatDuration(preview.estimated_seconds)}
                    </Descriptions.Item>
                    {preview.expected_total_rows !== null && (
                      <Descriptions.Item label="导出侧行数" span={2}>
                        {preview.expected_total_rows.toLocaleString()} 行（完成后会自动对账）
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                }
              />
            )}
            {sortedPreviewFiles.length > 0 && (
              <Card size="small" title={`排序结果（合并顺序，共 ${sortedPreviewFiles.length} 个）`}>
                <div style={{ maxHeight: 180, overflow: 'auto' }}>
                  {sortedPreviewFiles.map((f, i) => (
                    <div key={f.file_path} style={{ fontSize: 12, lineHeight: '20px' }}>
                      <Text type="secondary">{String(i + 1).padStart(3, ' ')}. </Text>
                      {f.filename}{' '}
                      <Text type="secondary">
                        {formatBytes(f.size)}
                        {f.encoding ? ` · ${f.encoding}` : ''}
                      </Text>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
        )}
      </Card>

      {/* 区域 4 ─ 历史任务 */}
      <Card title="4 · 历史任务" style={{ marginTop: 16 }} size="small" loading={listLoading}>
        <Table
          rowKey="job_id"
          dataSource={jobList?.items ?? []}
          columns={jobColumns as any}
          pagination={false}
          scroll={{ x: 1150 }}
          size="small"
          expandable={{
            rowExpandable: (r) => !!r.source_files?.length,
            expandedRowRender: (r) => (
              <Space direction="vertical" style={{ width: '100%' }}>
                {r.file_path && (
                  <Space>
                    <Text strong>结果路径：</Text>
                    <Text code copyable={{ text: r.file_path }} style={{ fontSize: 12 }}>
                      {r.file_path}
                    </Text>
                  </Space>
                )}
                <Table
                  rowKey="file_path"
                  size="small"
                  pagination={{ pageSize: 8, size: 'small' }}
                  dataSource={r.source_files ?? []}
                  columns={[
                    { title: '#', key: 'i', width: 50, render: (_: any, __: any, i: number) => i + 1 },
                    { title: '源文件', dataIndex: 'filename', ellipsis: true },
                    { title: '大小', dataIndex: 'size', width: 100, render: formatBytes },
                    {
                      title: '贡献行数',
                      dataIndex: 'rows',
                      width: 110,
                      render: (v: number | null) => (v ?? 0).toLocaleString(),
                    },
                    {
                      title: '字段内换行',
                      key: 'embedded',
                      width: 110,
                      render: (_: any, f: CsvSourceFile) => {
                        const d = (f.physical_lines ?? 0) - (f.rows ?? 0);
                        return d > 0 ? <Text type="warning">{d.toLocaleString()} 处</Text> : '-';
                      },
                    },
                    {
                      title: '导出侧行数',
                      dataIndex: 'expected_rows',
                      width: 110,
                      render: (v: number | null, f: CsvSourceFile) => {
                        if (v === null || v === undefined) return <Text type="secondary">-</Text>;
                        const ok = v === (f.rows ?? 0);
                        return (
                          <Text type={ok ? 'success' : 'danger'}>
                            {v.toLocaleString()} {ok ? '✓' : '✗'}
                          </Text>
                        );
                      },
                    },
                    { title: '编码', dataIndex: 'encoding', width: 90 },
                  ]}
                />
              </Space>
            ),
          }}
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

      {/* 详情 Modal */}
      <Modal
        open={!!detailJob}
        title="任务详情"
        onCancel={() => setDetailJob(null)}
        footer={null}
        width={720}
      >
        {detailJob && (
          <Space direction="vertical" style={{ width: '100%' }}>
            {detailJob.error_message && (
              <Alert type="error" message={detailJob.error_message} showIcon />
            )}
            {detailJob.status === 'cancelled' && detailJob.last_merged_file && (
              <Alert
                type="info"
                showIcon
                message={`已成功合并到：${detailJob.last_merged_file}（第 ${detailJob.done_files}/${detailJob.total_files} 个）`}
                description="把剩下的文件再提一个任务即可继续。"
              />
            )}
            {detailJob.reconcile_status === 'mismatched' && (
              <Alert
                type="error"
                showIcon
                message={`行数对账不一致：导出侧 ${(detailJob.expected_total_rows ?? 0).toLocaleString()} 行，合并结果 ${(detailJob.total_rows ?? 0).toLocaleString()} 行`}
                description={
                  <Table
                    rowKey="filename"
                    size="small"
                    pagination={false}
                    dataSource={(detailJob.reconcile_detail ?? []).filter((d) => d.diff !== 0)}
                    columns={[
                      { title: '文件', dataIndex: 'filename', ellipsis: true },
                      {
                        title: '导出侧',
                        dataIndex: 'expected_rows',
                        width: 100,
                        render: (v: number | null) => (v ?? 0).toLocaleString(),
                      },
                      {
                        title: '实际',
                        dataIndex: 'actual_rows',
                        width: 100,
                        render: (v: number) => v.toLocaleString(),
                      },
                      {
                        title: '差',
                        dataIndex: 'diff',
                        width: 90,
                        render: (v: number) => <Text type="danger">{v > 0 ? `+${v}` : v}</Text>,
                      },
                    ]}
                  />
                }
              />
            )}
            {detailJob.file_path && (
              <Paragraph style={{ marginBottom: 0 }}>
                <Text strong>结果路径：</Text>
                <br />
                <Text code copyable={{ text: detailJob.file_path }} style={{ fontSize: 12 }}>
                  {detailJob.file_path}
                </Text>
              </Paragraph>
            )}
            {detailJob.warnings?.map((w, i) => (
              <Alert key={i} type="warning" message={w} showIcon />
            ))}
          </Space>
        )}
      </Modal>
    </div>
  );
};

export default MergeCsv;
