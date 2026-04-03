/**
 * 数据导入 API 客户端
 *
 * 对应后端 /api/v1/data-import/* 路由。
 * 所有请求均需 data:import 权限（superadmin 专属）。
 */
import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
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

export interface SheetPreview {
  sheet_name: string;
  row_count_estimate: number;
  /** 前 5 行原始数据（含表头行，若有） */
  preview_rows: string[][];
}

export interface UploadResult {
  upload_id: string;
  filename: string;
  file_size: number;
  sheets: SheetPreview[];
}

export interface SheetConfig {
  sheet_name: string;
  database: string;
  table: string;
  has_header: boolean;
  enabled: boolean;
}

export interface ExecuteImportRequest {
  upload_id: string;
  connection_env: string;
  batch_size?: number;
  sheets: SheetConfig[];
}

export interface ImportJobStatus {
  job_id: string;
  user_id: string;
  username: string;
  upload_id: string;
  filename: string;
  connection_env: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  total_sheets: number;
  done_sheets: number;
  current_sheet: string | null;
  total_rows: number;
  imported_rows: number;
  total_batches: number;
  done_batches: number;
  error_message: string | null;
  errors: Array<{ sheet: string; batch: number; message: string }>;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobListResult {
  total: number;
  page: number;
  page_size: number;
  items: ImportJobStatus[];
}


// ─── API 方法 ─────────────────────────────────────────────────────────────────

export const dataImportApi = {
  /** 获取所有可写 ClickHouse 连接 */
  getConnections: async (): Promise<Connection[]> => {
    const res = await apiClient.get('/data-import/connections');
    return res.data?.data ?? [];
  },

  /** 获取指定环境的数据库列表 */
  getDatabases: async (env: string): Promise<string[]> => {
    const res = await apiClient.get(`/data-import/connections/${encodeURIComponent(env)}/databases`);
    return res.data?.data ?? [];
  },

  /** 获取指定数据库的表列表 */
  getTables: async (env: string, db: string): Promise<string[]> => {
    const res = await apiClient.get(
      `/data-import/connections/${encodeURIComponent(env)}/databases/${encodeURIComponent(db)}/tables`
    );
    return res.data?.data ?? [];
  },

  /** 上传 Excel 文件，返回 sheet 预览 */
  uploadExcel: async (file: File): Promise<UploadResult> => {
    const formData = new FormData();
    formData.append('file', file);

    const token = useAuthStore.getState().accessToken;
    const res = await apiClient.post('/data-import/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      timeout: 120000, // 大文件上传最多 2 分钟
    });
    return res.data?.data;
  },

  /** 提交导入任务，返回 job_id */
  executeImport: async (req: ExecuteImportRequest): Promise<{ job_id: string; status: string }> => {
    const res = await apiClient.post('/data-import/execute', req);
    return res.data?.data;
  },

  /** 查询单个任务状态 */
  getJobStatus: async (jobId: string): Promise<ImportJobStatus> => {
    const res = await apiClient.get(`/data-import/jobs/${jobId}`);
    return res.data?.data;
  },

  /** 历史任务列表（时间倒序，分页） */
  listJobs: async (page = 1, pageSize = 10): Promise<JobListResult> => {
    const res = await apiClient.get('/data-import/jobs', {
      params: { page, page_size: pageSize },
    });
    return res.data?.data;
  },
};
