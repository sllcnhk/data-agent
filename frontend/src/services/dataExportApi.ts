/**
 * 数据导出 API 客户端
 *
 * 对应后端 /api/v1/data-export/* 路由。
 * 所有请求均需 data:export 权限（superadmin 专属）。
 */
import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

// 注入 Bearer token
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token && config.headers) {
    config.headers['Authorization'] = `Bearer ${token}`;
  }
  return config;
});


// ─── Types ────────────────────────────────────────────────────────────────────

export interface Connection {
  env: string;
  server_name: string;
  host: string;
  http_port: number;
  database: string;
  display_name: string;
}

export interface ColumnMeta {
  name: string;
  type: string;
}

export interface QueryPreviewResult {
  columns: ColumnMeta[];
  rows: any[][];
  row_count: number;
  /** 占位符模式时实际使用的样本日期（ISO YYYY-MM-DD）；非占位符模式为 null */
  preview_date?: string | null;
}

export type ExportJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'partial_failed'  // v2.14.7: 分块模式下部分块成功 + 部分块失败
  | 'cancelling'
  | 'cancelled';

export type ExportMode = 'single' | 'date_chunked';
export type OutputFormat = 'xlsx' | 'csv' | 'csv_zip';
export type XlsxEngine = 'auto' | 'direct' | 'csv_staging';

export interface ChunkConfig {
  /** 日期列名，包装模式必填；占位符模式可省 */
  date_column?: string | null;
  /** 起始日期（含），ISO YYYY-MM-DD */
  date_start: string;
  /** 结束日期（含），ISO YYYY-MM-DD */
  date_end: string;
  /** 单块天数 [1, 90] */
  chunk_days: number;
  /**
   * 块失败自动对半再细分的最小粒度。day=不下钻到 sub-day(默认,老行为);
   * hour/minute=允许在 1 天块失败后继续拆 12h+12h→6h+6h...
   * 仅当过滤列为 DateTime 类型时启用 hour/minute 才有效。
   */
  min_subdivide_unit?: 'day' | 'hour' | 'minute';
  /**
   * 游标列名(可选,启用键集分页代替 LIMIT/OFFSET)。
   * 提供后,流式断开自动回退时用 WHERE cursor > last ORDER BY cursor LIMIT N 推进。
   * 大数据集大幅提速 + 消除 LIMIT/OFFSET 非确定性。
   * 要求列单调可排序(主键 / 时间戳);不适用于 GROUP BY/DISTINCT 聚合。
   */
  cursor_column?: string | null;
  /** 预分裂窗口小时数；启用 hour/minute 再细分时留空默认 6 小时；"auto"=按数据量自动计算 */
  pre_split_hours?: number | 'auto' | null;
  /** auto 模式统计时间列（可选）。pre_split_hours="auto" 时使用。留空则使用 date_column */
  auto_split_column?: string | null;
  /** auto 模式每窗口目标行数阈值（默认 1,000,000） */
  auto_split_target_rows?: number | null;
  /**
   * 首选分批模式(跳过单流首试) — 跨境/不稳网络下,5 分钟左右 LB 切断单流的现象稳定,
   * 每块先单流试 5 分钟再 fallback 浪费严重。勾选 → 直接走 keyset(若 cursor_column 提供)
   * 或 LIMIT/OFFSET。建议同时填 cursor_column。
   * null=后端用 EXPORT_PREFER_CHUNKED env 默认;true/false=显式覆盖
   */
  prefer_chunked?: boolean | null;
}

/**
 * 一组配置的真实能力（后端 GET /data-export/capabilities 下发）。
 *
 * 为什么由后端算：前端曾把每条提示都硬编码成「对后端行为的假设」，没有任何机制
 * 保证同步，结果积累了 9 处错位（CSV 下 batch_size 是死配置却可填、ORDER BY 警告
 * 显示在没有该风险的路径上而在有风险的路径上被隐藏、CSV 分块下写「每块一个 Excel
 * 文件」等）。现在判定收敛到实现方，前端只渲染。**不要在前端重新推导这些字段。**
 */
export interface ExportCapability {
  export_mode: ExportMode;
  output_format: OutputFormat;
  xlsx_engine: XlsxEngine;
  has_cursor_column: boolean;
  /** auto 解析后实际生效的引擎；CSV 格式为 null */
  effective_engine: 'direct' | 'csv_staging' | null;

  /** 产物形态，如「N 个 .csv（每个日期块一个）」 */
  artifact: string;
  /** 是否每满 100 万行新建 Sheet（仅 xlsx） */
  sheet_splitting: boolean;
  /** xlsx 所有单元格都是文本（两条引擎一致，无开关可改） */
  all_cells_text: boolean;
  utf8_bom: boolean;
  null_representation: string;
  big_int_excel_safe: boolean;

  /** batch_size 是否真的被读取（否则应置灰） */
  batch_size_effective: boolean;
  /** batch_size 在当前路径下的角色说明 */
  batch_size_role: string | null;
  cursor_column_effective: boolean;
  cursor_column_role: string | null;
  prefer_chunked_effective: boolean;

  /** 断流后是否有自动回退（LIMIT/OFFSET 或 keyset 重跑） */
  stream_fallback: boolean;
  /** 断流后能否基于已下载数据继续（keyset 多窗口） */
  resumable_on_disconnect: boolean;
  /** 是否存在 LIMIT/OFFSET 导致的重复/漏行风险 */
  order_by_risk: boolean;

  cancel_partial_downloadable: boolean;
  retry_failed_chunks: boolean;

  /** 按真实风险生成的警告，逐条渲染 */
  warnings: string[];
  /** 「你将得到什么」摘要，逐条渲染 */
  summary: string[];
}

export interface ExportFileEntry {
  index: number;
  date_start: string;
  date_end: string;
  filename: string;
  file_path: string;
  file_size: number | null;
  rows: number;
  sheets: number;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  /** v2.14.7: 块失败时记录的错误摘要(≤200 字符,用于错误 Modal 展示) */
  error_summary?: string | null;
}

export interface ExportJob {
  job_id: string;
  user_id: string;
  username: string;
  job_name: string | null;
  query_sql: string;
  connection_env: string;
  connection_type: string;
  db_name: string | null;
  status: ExportJobStatus;
  total_rows: number | null;
  exported_rows: number;
  total_batches: number | null;
  done_batches: number;
  current_sheet: string | null;
  total_sheets: number;
  output_filename: string | null;
  file_size: number | null;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  // 分块导出（v2.13）
  export_mode: ExportMode;
  chunk_config: ChunkConfig | null;
  output_files: ExportFileEntry[] | null;
  output_format?: OutputFormat;
  xlsx_engine?: XlsxEngine;
}

export interface ExportJobListResult {
  total: number;
  page: number;
  page_size: number;
  items: ExportJob[];
}

export interface ExecuteExportRequest {
  query_sql: string;
  connection_env: string;
  connection_type?: string;
  job_name?: string;
  batch_size?: number;
  output_format?: OutputFormat;
  xlsx_engine?: XlsxEngine;
  /**
   * 游标列名（单文件模式；分块模式填在 chunk_config.cursor_column）。
   * CSV / CSV ZIP 填了此列即启用 keyset 多窗口，断流可基于已下载数据继续；
   * XLSX 路径下用于流式断开后的 keyset 回退（替代 LIMIT/OFFSET）。
   * 强烈建议选表 ORDER BY 键的前缀，否则每窗口都要真排序。
   */
  cursor_column?: string | null;
  /** 提供则启用按日期分块导出（多文件） */
  chunk_config?: ChunkConfig;
}


// ─── API 方法 ─────────────────────────────────────────────────────────────────

export const dataExportApi = {
  /**
   * 获取导出能力矩阵（36 条，覆盖全部配置组合）。
   * 挂载时取一次即可，按四元组查表，无需随输入实时请求。
   */
  getCapabilities: async (): Promise<ExportCapability[]> => {
    const res = await apiClient.get('/data-export/capabilities');
    return res.data?.data ?? [];
  },

  /** 获取所有可写连接列表（复用 import 端点） */
  getConnections: async (): Promise<Connection[]> => {
    const res = await apiClient.get('/data-export/connections');
    return res.data?.data ?? [];
  },

  /**
   * 执行 SQL 预览，返回列信息和前 N 行。
   *
   * 如果 SQL 含 {{date_start}}/{{date_end}} 或 {{ts_start}}/{{ts_end}} 占位符，
   * previewDate 指定占位符替换的样本日期（ISO YYYY-MM-DD）；不传默认昨日。
   */
  previewQuery: async (
    querySql: string,
    connectionEnv: string,
    connectionType = 'clickhouse',
    limit = 100,
    previewDate?: string,
  ): Promise<QueryPreviewResult> => {
    const res = await apiClient.post(
      '/data-export/preview',
      {
        query_sql: querySql,
        connection_env: connectionEnv,
        connection_type: connectionType,
        limit,
        preview_date: previewDate,
      },
      { timeout: 30000 },
    );
    return res.data?.data;
  },

  /** 提交导出任务，返回 job_id */
  executeExport: async (
    req: ExecuteExportRequest,
  ): Promise<{ job_id: string; status: string; output_filename: string }> => {
    const res = await apiClient.post('/data-export/execute', req);
    return res.data?.data;
  },

  /** 查询单个任务状态 */
  getJobStatus: async (jobId: string): Promise<ExportJob> => {
    const res = await apiClient.get(`/data-export/jobs/${jobId}`);
    return res.data?.data;
  },

  /** 历史任务列表（时间倒序，分页） */
  listJobs: async (page = 1, pageSize = 10): Promise<ExportJobListResult> => {
    const res = await apiClient.get('/data-export/jobs', {
      params: { page, page_size: pageSize },
    });
    return res.data?.data;
  },

  /** 取消任务 */
  cancelJob: async (jobId: string): Promise<{ status: string }> => {
    const res = await apiClient.post(`/data-export/jobs/${jobId}/cancel`);
    return res.data?.data;
  },

  /** 删除任务记录（同时删除本地文件） */
  deleteJob: async (jobId: string): Promise<void> => {
    await apiClient.delete(`/data-export/jobs/${jobId}`);
  },

  /**
   * 对指定 date_chunked 任务下所有 failed 子块发起串行重试。
   * batchSize 可与原始不同，重试时生效。
   */
  retryFailedChunks: async (
    jobId: string,
    batchSize: number,
  ): Promise<{ status: string; failed_chunk_count: number; batch_size: number }> => {
    const res = await apiClient.post(`/data-export/jobs/${jobId}/retry-failed-chunks`, {
      batch_size: batchSize,
    });
    return res.data?.data;
  },

  /**
   * 以 blob 方式下载导出文件。
   * 通过 axios 发起请求，自动携带 Authorization Bearer token，
   * 避免原生 <a href> 导航绕过认证拦截器导致 401。
   * 大文件给 2 分钟超时（默认 30s 不够）。
   *
   * 分块模式必须传 fileIndex 指向 output_files[i]；单文件模式忽略此参数。
   */
  downloadFile: async (jobId: string, fileIndex?: number): Promise<Blob> => {
    const params = fileIndex !== undefined ? { file_index: fileIndex } : undefined;
    const res = await apiClient.get(`/data-export/jobs/${jobId}/download`, {
      responseType: 'blob',
      timeout: 120000,
      params,
    });
    return res.data as Blob;
  },
};
