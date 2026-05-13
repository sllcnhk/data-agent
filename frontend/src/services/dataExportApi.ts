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
  /** 提供则启用按日期分块导出（多文件） */
  chunk_config?: ChunkConfig;
}


// ─── API 方法 ─────────────────────────────────────────────────────────────────

export const dataExportApi = {
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
