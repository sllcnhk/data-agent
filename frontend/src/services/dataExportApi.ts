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
}

export type ExportJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelling'
  | 'cancelled';

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
}


// ─── API 方法 ─────────────────────────────────────────────────────────────────

export const dataExportApi = {
  /** 获取所有可写连接列表（复用 import 端点） */
  getConnections: async (): Promise<Connection[]> => {
    const res = await apiClient.get('/data-export/connections');
    return res.data?.data ?? [];
  },

  /** 执行 SQL 预览，返回列信息和前 N 行 */
  previewQuery: async (
    querySql: string,
    connectionEnv: string,
    connectionType = 'clickhouse',
    limit = 100,
  ): Promise<QueryPreviewResult> => {
    const res = await apiClient.post(
      '/data-export/preview',
      { query_sql: querySql, connection_env: connectionEnv, connection_type: connectionType, limit },
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
   */
  downloadFile: async (jobId: string): Promise<Blob> => {
    const res = await apiClient.get(`/data-export/jobs/${jobId}/download`, {
      responseType: 'blob',
      timeout: 120000,
    });
    return res.data as Blob;
  },
};
