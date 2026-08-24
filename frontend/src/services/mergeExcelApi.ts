/**
 * 小工具 - 合并Excel文件 API 客户端
 *
 * 对应后端 /api/v1/tools/merge-excel/* 路由。
 * 所有请求均需 tools:merge_excel 权限（superadmin 专属）。
 */
import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
});

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token && config.headers) {
    config.headers['Authorization'] = `Bearer ${token}`;
  }
  return config;
});


// ─── Types ────────────────────────────────────────────────────────────────────

export type MergeJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelling'
  | 'cancelled';

export interface SourceFileEntry {
  filename: string;
  file_path: string;
  size: number;
}

export interface MergeJob {
  job_id: string;
  user_id: string;
  username: string;
  job_name: string | null;
  has_header: boolean;
  source_files: SourceFileEntry[] | null;
  status: MergeJobStatus;
  total_files: number | null;
  done_files: number;
  total_rows: number | null;
  merged_rows: number;
  current_sheet: string | null;
  total_sheets: number;
  output_filename: string | null;
  file_size: number | null;
  warnings: string[] | null;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface MergeJobListResult {
  total: number;
  page: number;
  page_size: number;
  items: MergeJob[];
}

export interface UploadResult {
  upload_id: string;
  filename: string;
  file_path: string;
  file_size: number;
}


// ─── API 方法 ─────────────────────────────────────────────────────────────────

export const mergeExcelApi = {
  /** 上传单个源文件，返回 upload_id（多文件由前端逐个调用） */
  uploadFile: async (file: File, onProgress?: (percent: number) => void): Promise<UploadResult> => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await apiClient.post('/tools/merge-excel/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
      onUploadProgress: (evt) => {
        if (onProgress && evt.total) {
          onProgress(Math.round((evt.loaded / evt.total) * 100));
        }
      },
    });
    return res.data?.data;
  },

  /** 提交合并任务，返回 job_id */
  executeMerge: async (
    fileIds: string[],
    hasHeader: boolean,
    jobName?: string,
  ): Promise<{ job_id: string; status: string }> => {
    const res = await apiClient.post('/tools/merge-excel/execute', {
      file_ids: fileIds,
      has_header: hasHeader,
      job_name: jobName,
    });
    return res.data?.data;
  },

  /** 查询单个任务状态 */
  getJobStatus: async (jobId: string): Promise<MergeJob> => {
    const res = await apiClient.get(`/tools/merge-excel/jobs/${jobId}`);
    return res.data?.data;
  },

  /** 历史任务列表（时间倒序，分页） */
  listJobs: async (page = 1, pageSize = 10): Promise<MergeJobListResult> => {
    const res = await apiClient.get('/tools/merge-excel/jobs', {
      params: { page, page_size: pageSize },
    });
    return res.data?.data;
  },

  /** 取消任务 */
  cancelJob: async (jobId: string): Promise<{ status: string }> => {
    const res = await apiClient.post(`/tools/merge-excel/jobs/${jobId}/cancel`);
    return res.data?.data;
  },

  /** 删除任务记录（同时删除本地文件） */
  deleteJob: async (jobId: string): Promise<void> => {
    await apiClient.delete(`/tools/merge-excel/jobs/${jobId}`);
  },

  /** 以 blob 方式下载合并结果文件 */
  downloadFile: async (jobId: string): Promise<Blob> => {
    const res = await apiClient.get(`/tools/merge-excel/jobs/${jobId}/download`, {
      responseType: 'blob',
      timeout: 120000,
    });
    return res.data as Blob;
  },
};
