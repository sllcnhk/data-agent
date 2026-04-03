/**
 * Excel → ClickHouse 数据导入页面
 *
 * 4 步流程：
 *   Step 1  选择 ClickHouse 连接
 *   Step 2  上传 Excel + Sheet 预览
 *   Step 3  配置每个 Sheet（目标库/表/是否含表头/是否启用）
 *   Step 4  导入进度 + 完成状态
 *
 * 页面底部展示历史任务列表（时间倒序，分页，每页 10 条）。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  message,
  Pagination,
  Popover,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Steps,
  Switch,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import type { UploadFile } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudUploadOutlined,
  ImportOutlined,
  LoadingOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import {
  dataImportApi,
  Connection,
  SheetPreview,
  SheetConfig,
  ImportJobStatus,
  JobListResult,
} from '@/services/dataImportApi';

const { Title, Text } = Typography;
const { Option } = Select;
const { Step } = Steps;

// ─── 内部状态类型 ─────────────────────────────────────────────────────────────

interface SheetFormRow extends SheetPreview {
  database: string;
  table: string;
  has_header: boolean;
  enabled: boolean;
  databases: string[];   // 已加载的 DB 列表
  tables: string[];      // 已加载的 Table 列表
  loadingDbs: boolean;
  loadingTables: boolean;
}


// ─── 辅助：任务状态标签 ────────────────────────────────────────────────────────

const StatusTag: React.FC<{ status: string }> = ({ status }) => {
  const map: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
    pending:   { color: 'default',   icon: <LoadingOutlined />,     label: '等待中' },
    running:   { color: 'processing',icon: <SyncOutlined spin />,   label: '导入中' },
    completed: { color: 'success',   icon: <CheckCircleOutlined />, label: '已完成' },
    failed:    { color: 'error',     icon: <CloseCircleOutlined />, label: '失败' },
  };
  const cfg = map[status] ?? { color: 'default', icon: null, label: status };
  return (
    <Tag color={cfg.color} icon={cfg.icon}>
      {cfg.label}
    </Tag>
  );
};


// ─── 主组件 ───────────────────────────────────────────────────────────────────

const DataImportPage: React.FC = () => {
  // ── Step 控制 ────────────────────────────────────────────────────────────────
  const [step, setStep] = useState(0);

  // ── Step 1: 连接选择 ─────────────────────────────────────────────────────────
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loadingConns, setLoadingConns] = useState(false);
  const [selectedEnv, setSelectedEnv] = useState<string | undefined>();

  // ── Step 2: 上传 ─────────────────────────────────────────────────────────────
  const [uploading, setUploading] = useState(false);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [uploadFilename, setUploadFilename] = useState<string>('');
  const [sheets, setSheets] = useState<SheetFormRow[]>([]);
  const [fileList, setFileList] = useState<UploadFile[]>([]);

  // ── Step 3: 配置 ─────────────────────────────────────────────────────────────
  const [batchSize] = useState(1000);

  // ── Step 4: 执行/进度 ────────────────────────────────────────────────────────
  const [executing, setExecuting] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<ImportJobStatus | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── 历史列表 ─────────────────────────────────────────────────────────────────
  const [history, setHistory] = useState<JobListResult | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [loadingHistory, setLoadingHistory] = useState(false);


  // ─── 加载连接列表 ────────────────────────────────────────────────────────────
  useEffect(() => {
    setLoadingConns(true);
    dataImportApi.getConnections()
      .then(setConnections)
      .catch((e) => message.error(`获取连接列表失败: ${e?.response?.data?.detail ?? e.message}`))
      .finally(() => setLoadingConns(false));
  }, []);

  // ─── 加载历史记录 ────────────────────────────────────────────────────────────
  const loadHistory = useCallback((page: number) => {
    setLoadingHistory(true);
    dataImportApi.listJobs(page, 10)
      .then((data) => { setHistory(data); setHistoryPage(page); })
      .catch(() => {/* silently ignore */})
      .finally(() => setLoadingHistory(false));
  }, []);

  useEffect(() => { loadHistory(1); }, [loadHistory]);


  // ─── Step 2: 上传 Excel ──────────────────────────────────────────────────────
  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const result = await dataImportApi.uploadExcel(file);
      setUploadId(result.upload_id);
      setUploadFilename(result.filename);
      // 初始化 sheet 配置
      const rows: SheetFormRow[] = result.sheets.map((s) => ({
        ...s,
        database: '',
        table: '',
        has_header: true,
        enabled: true,
        databases: [],
        tables: [],
        loadingDbs: false,
        loadingTables: false,
      }));
      setSheets(rows);
      setStep(2);
    } catch (e: any) {
      message.error(`上传失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setUploading(false);
    }
    return false; // 阻止 antd Upload 自动提交
  };


  // ─── Step 3: 加载 DB / Table ─────────────────────────────────────────────────
  const loadDatabases = async (sheetIdx: number) => {
    if (!selectedEnv) return;
    setSheets((prev) => prev.map((s, i) =>
      i === sheetIdx ? { ...s, loadingDbs: true } : s
    ));
    try {
      const dbs = await dataImportApi.getDatabases(selectedEnv);
      setSheets((prev) => prev.map((s, i) =>
        i === sheetIdx ? { ...s, databases: dbs, loadingDbs: false } : s
      ));
    } catch (e: any) {
      message.error(`加载数据库失败: ${e?.response?.data?.detail ?? e.message}`);
      setSheets((prev) => prev.map((s, i) =>
        i === sheetIdx ? { ...s, loadingDbs: false } : s
      ));
    }
  };

  const loadTables = async (sheetIdx: number, db: string) => {
    if (!selectedEnv) return;
    setSheets((prev) => prev.map((s, i) =>
      i === sheetIdx ? { ...s, loadingTables: true } : s
    ));
    try {
      const tables = await dataImportApi.getTables(selectedEnv, db);
      setSheets((prev) => prev.map((s, i) =>
        i === sheetIdx ? { ...s, tables, loadingTables: false } : s
      ));
    } catch (e: any) {
      message.error(`加载表失败: ${e?.response?.data?.detail ?? e.message}`);
      setSheets((prev) => prev.map((s, i) =>
        i === sheetIdx ? { ...s, loadingTables: false } : s
      ));
    }
  };

  const updateSheet = (idx: number, patch: Partial<SheetFormRow>) => {
    setSheets((prev) => prev.map((s, i) => i === idx ? { ...s, ...patch } : s));
  };


  // ─── Step 4: 执行导入 ────────────────────────────────────────────────────────
  const startImport = async () => {
    if (!selectedEnv || !uploadId) return;
    const enabledSheets = sheets.filter((s) => s.enabled);
    if (enabledSheets.some((s) => !s.database || !s.table)) {
      message.warning('请为所有启用的 Sheet 选择目标数据库和表');
      return;
    }

    setExecuting(true);
    try {
      const sheetConfigs: SheetConfig[] = sheets.map((s) => ({
        sheet_name: s.sheet_name,
        database: s.database,
        table: s.table,
        has_header: s.has_header,
        enabled: s.enabled,
      }));
      const result = await dataImportApi.executeImport({
        upload_id: uploadId,
        connection_env: selectedEnv,
        batch_size: batchSize,
        sheets: sheetConfigs,
      });
      setJobId(result.job_id);
      setStep(3);
      startPolling(result.job_id);
    } catch (e: any) {
      message.error(`提交失败: ${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setExecuting(false);
    }
  };

  const startPolling = (jid: string) => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = setInterval(async () => {
      try {
        const status = await dataImportApi.getJobStatus(jid);
        setJobStatus(status);
        if (status.status === 'completed' || status.status === 'failed') {
          clearInterval(pollingRef.current!);
          pollingRef.current = null;
          loadHistory(1); // 刷新历史
        }
      } catch {/* ignore */}
    }, 2000);
  };

  useEffect(() => {
    return () => { if (pollingRef.current) clearInterval(pollingRef.current); };
  }, []);


  // ─── 重置表单 ─────────────────────────────────────────────────────────────────
  const resetForm = () => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    setStep(0);
    setSelectedEnv(undefined);
    setUploadId(null);
    setUploadFilename('');
    setSheets([]);
    setFileList([]);
    setJobId(null);
    setJobStatus(null);
    setExecuting(false);
  };


  // ─── 历史表格列 ───────────────────────────────────────────────────────────────
  const historyColumns = [
    {
      title: '任务ID',
      dataIndex: 'job_id',
      width: 100,
      render: (v: string) => <Text code style={{ fontSize: 11 }}>{v.slice(0, 8)}…</Text>,
    },
    { title: '文件名', dataIndex: 'filename', ellipsis: true },
    { title: '目标环境', dataIndex: 'connection_env', width: 100 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => <StatusTag status={s} />,
    },
    {
      title: '进度',
      width: 140,
      render: (_: any, r: ImportJobStatus) => {
        const pct = r.total_batches > 0
          ? Math.round((r.done_batches / r.total_batches) * 100)
          : (r.status === 'completed' ? 100 : 0);
        return (
          <Progress
            percent={pct}
            size="small"
            status={r.status === 'failed' ? 'exception' : r.status === 'completed' ? 'success' : 'active'}
          />
        );
      },
    },
    {
      title: '已导入行',
      dataIndex: 'imported_rows',
      width: 90,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '操作用户',
      dataIndex: 'username',
      width: 100,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
    {
      title: '错误',
      width: 60,
      render: (_: any, r: ImportJobStatus) =>
        r.errors?.length ? (
          <Popover
            title="错误详情"
            content={
              <div style={{ maxWidth: 400, maxHeight: 200, overflow: 'auto' }}>
                {r.errors.map((e, i) => (
                  <div key={i} style={{ marginBottom: 4 }}>
                    <Text type="danger">[{e.sheet} 批次{e.batch}]</Text> {e.message}
                  </div>
                ))}
              </div>
            }
          >
            <Badge count={r.errors.length} style={{ cursor: 'pointer' }} />
          </Popover>
        ) : null,
    },
  ];


  // ─── 渲染 ─────────────────────────────────────────────────────────────────────
  return (
    <div style={{ padding: '24px', maxWidth: 1200, margin: '0 auto' }}>
      <Title level={3} style={{ marginBottom: 24 }}>
        <ImportOutlined style={{ marginRight: 8 }} />
        Excel 数据导入
      </Title>

      {/* ── 步骤导航 ── */}
      <Card style={{ marginBottom: 24 }}>
        <Steps current={step} style={{ marginBottom: 24 }}>
          <Step title="选择连接" description="选择目标 ClickHouse" />
          <Step title="上传文件" description="上传 Excel 并预览" />
          <Step title="配置 Sheet" description="设置目标表与选项" />
          <Step title="执行导入" description="进度与结果" />
        </Steps>

        {/* ──── Step 0: 选择连接 ──── */}
        {step === 0 && (
          <div>
            <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
              选择具有写入权限的 ClickHouse 连接（只读连接不在列表中）：
            </Text>
            <Select
              placeholder="请选择 ClickHouse 连接"
              style={{ width: 360 }}
              loading={loadingConns}
              value={selectedEnv}
              onChange={setSelectedEnv}
            >
              {connections.map((c) => (
                <Option key={c.env} value={c.env}>
                  {c.display_name} — {c.host}:{c.http_port}
                </Option>
              ))}
            </Select>
            <div style={{ marginTop: 24 }}>
              <Button
                type="primary"
                disabled={!selectedEnv}
                onClick={() => setStep(1)}
              >
                下一步
              </Button>
            </div>
          </div>
        )}

        {/* ──── Step 1: 上传 Excel ──── */}
        {step === 1 && (
          <div>
            <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
              支持 .xlsx / .xls，文件大小限制 100 MB。
            </Text>
            <Upload.Dragger
              accept=".xlsx,.xls"
              multiple={false}
              fileList={fileList}
              beforeUpload={(file) => {
                setFileList([file as any]);
                handleUpload(file as unknown as File);
                return false;
              }}
              onRemove={() => { setFileList([]); setUploadId(null); }}
              disabled={uploading}
            >
              {uploading ? (
                <Spin tip="上传解析中..." />
              ) : (
                <>
                  <p className="ant-upload-drag-icon">
                    <CloudUploadOutlined style={{ fontSize: 48, color: '#1890ff' }} />
                  </p>
                  <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
                  <p className="ant-upload-hint">仅支持单个 Excel 文件（.xlsx / .xls）</p>
                </>
              )}
            </Upload.Dragger>
            <div style={{ marginTop: 24 }}>
              <Button onClick={() => setStep(0)} style={{ marginRight: 8 }}>上一步</Button>
            </div>
          </div>
        )}

        {/* ──── Step 2: 配置 Sheet ──── */}
        {step === 2 && (
          <div>
            <Alert
              message={`已解析文件：${uploadFilename}，共 ${sheets.length} 个 Sheet`}
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
            />
            {sheets.map((sheet, idx) => (
              <Card
                key={sheet.sheet_name}
                size="small"
                title={
                  <Space>
                    <Switch
                      size="small"
                      checked={sheet.enabled}
                      onChange={(v) => updateSheet(idx, { enabled: v })}
                    />
                    <Text strong>Sheet: {sheet.sheet_name}</Text>
                    <Text type="secondary">（约 {sheet.row_count_estimate} 行）</Text>
                  </Space>
                }
                style={{ marginBottom: 12, opacity: sheet.enabled ? 1 : 0.5 }}
              >
                {/* 预览行 */}
                {sheet.preview_rows.length > 0 && (
                  <div style={{ overflowX: 'auto', marginBottom: 12 }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: 12, whiteSpace: 'nowrap' }}>
                      <tbody>
                        {sheet.preview_rows.map((row, ri) => (
                          <tr key={ri} style={{ background: ri === 0 ? '#fafafa' : 'transparent' }}>
                            {row.map((cell, ci) => (
                              <td
                                key={ci}
                                style={{
                                  border: '1px solid #e8e8e8',
                                  padding: '2px 8px',
                                  fontWeight: ri === 0 ? 600 : 'normal',
                                }}
                              >
                                {cell || <Text type="secondary">—</Text>}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <Row gutter={12} align="middle">
                  <Col>
                    <Space>
                      <Text>首行为表头：</Text>
                      <Switch
                        size="small"
                        checked={sheet.has_header}
                        disabled={!sheet.enabled}
                        onChange={(v) => updateSheet(idx, { has_header: v })}
                      />
                    </Space>
                  </Col>
                  <Col>
                    <Space>
                      <Text>目标数据库：</Text>
                      <Select
                        style={{ width: 180 }}
                        placeholder="选择数据库"
                        disabled={!sheet.enabled}
                        loading={sheet.loadingDbs}
                        value={sheet.database || undefined}
                        onFocus={() => { if (sheet.databases.length === 0) loadDatabases(idx); }}
                        onChange={(v) => updateSheet(idx, { database: v, table: '', tables: [] })}
                      >
                        {sheet.databases.map((db) => (
                          <Option key={db} value={db}>{db}</Option>
                        ))}
                      </Select>
                    </Space>
                  </Col>
                  <Col>
                    <Space>
                      <Text>目标表：</Text>
                      <Select
                        style={{ width: 200 }}
                        placeholder="选择表"
                        disabled={!sheet.enabled || !sheet.database}
                        loading={sheet.loadingTables}
                        value={sheet.table || undefined}
                        onFocus={() => { if (sheet.database && sheet.tables.length === 0) loadTables(idx, sheet.database); }}
                        onChange={(v) => updateSheet(idx, { table: v })}
                      >
                        {sheet.tables.map((t) => (
                          <Option key={t} value={t}>{t}</Option>
                        ))}
                      </Select>
                    </Space>
                  </Col>
                </Row>
              </Card>
            ))}

            <div style={{ marginTop: 24 }}>
              <Button onClick={() => setStep(1)} style={{ marginRight: 8 }}>上一步</Button>
              <Button
                type="primary"
                loading={executing}
                onClick={startImport}
                icon={<ImportOutlined />}
              >
                开始导入
              </Button>
            </div>
          </div>
        )}

        {/* ──── Step 3: 执行进度 ──── */}
        {step === 3 && (
          <div>
            {!jobStatus ? (
              <Spin tip="正在启动导入任务..." />
            ) : (
              <>
                <Row gutter={24} style={{ marginBottom: 24 }}>
                  <Col span={12}>
                    <Card size="small" title="任务状态">
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <div>
                          <Text type="secondary">状态：</Text>
                          <StatusTag status={jobStatus.status} />
                        </div>
                        <div>
                          <Text type="secondary">文件：</Text>
                          <Text>{jobStatus.filename}</Text>
                        </div>
                        <div>
                          <Text type="secondary">目标环境：</Text>
                          <Text>{jobStatus.connection_env}</Text>
                        </div>
                        {jobStatus.current_sheet && (
                          <div>
                            <Text type="secondary">当前 Sheet：</Text>
                            <Text>{jobStatus.current_sheet}</Text>
                          </div>
                        )}
                      </Space>
                    </Card>
                  </Col>
                  <Col span={12}>
                    <Card size="small" title="导入进度">
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <div>
                          <Text type="secondary">批次进度：</Text>
                          <Text>{jobStatus.done_batches} / {jobStatus.total_batches}</Text>
                        </div>
                        <Progress
                          percent={
                            jobStatus.total_batches > 0
                              ? Math.round((jobStatus.done_batches / jobStatus.total_batches) * 100)
                              : (jobStatus.status === 'completed' ? 100 : 0)
                          }
                          status={
                            jobStatus.status === 'failed'
                              ? 'exception'
                              : jobStatus.status === 'completed'
                              ? 'success'
                              : 'active'
                          }
                        />
                        <div>
                          <Text type="secondary">已导入行数：</Text>
                          <Text strong>{jobStatus.imported_rows.toLocaleString()}</Text>
                        </div>
                        <div>
                          <Text type="secondary">Sheet 进度：</Text>
                          <Text>{jobStatus.done_sheets} / {jobStatus.total_sheets}</Text>
                        </div>
                      </Space>
                    </Card>
                  </Col>
                </Row>

                {jobStatus.status === 'failed' && (
                  <Alert
                    type="error"
                    showIcon
                    message="导入失败"
                    description={jobStatus.error_message}
                    style={{ marginBottom: 16 }}
                  />
                )}

                {jobStatus.status === 'completed' && (
                  <Alert
                    type="success"
                    showIcon
                    message={`导入完成！共导入 ${jobStatus.imported_rows.toLocaleString()} 行数据。`}
                    style={{ marginBottom: 16 }}
                  />
                )}

                {(jobStatus.status === 'completed' || jobStatus.status === 'failed') && (
                  <Button type="primary" onClick={resetForm}>
                    重新导入
                  </Button>
                )}
              </>
            )}
          </div>
        )}
      </Card>

      {/* ── 历史任务列表 ── */}
      <Card
        title={
          <Space>
            <Text strong>历史导入任务</Text>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => loadHistory(historyPage)}
              loading={loadingHistory}
            >
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="job_id"
          columns={historyColumns}
          dataSource={history?.items ?? []}
          loading={loadingHistory}
          pagination={false}
          size="small"
          scroll={{ x: 900 }}
        />
        {history && history.total > 10 && (
          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Pagination
              current={historyPage}
              pageSize={10}
              total={history.total}
              onChange={(page) => loadHistory(page)}
              showTotal={(total) => `共 ${total} 条`}
            />
          </div>
        )}
      </Card>
    </div>
  );
};

export default DataImportPage;
