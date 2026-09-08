/**
 * 小工具 - 合并CSV文件 API 客户端
 *
 * 对应后端 /api/v1/tools/merge-csv/* 路由。
 * 所有请求均需 tools:merge_csv 权限（superadmin 专属）。
 *
 * 与 mergeExcelApi 的两处刻意差异：
 *   1. 没有 blob 下载方法 —— CSV 结果可能几十 GB，把整个文件读进浏览器内存
 *      会直接把标签页打爆。取而代之的是 downloadUrl()（交给浏览器原生流式落盘）
 *      与 job.file_path（本地部署下复制路径最快，零拷贝）。
 *   2. 多了 preview() —— 在**建 job 之前**跑完全部校验，让排序、表头一致性、
 *      编码一致性、磁盘空间这些问题在提交前就暴露。
 */
import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
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

export type MergeCsvJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelling'
  | 'cancelled';

export type SourceOrigin = 'upload' | 'server';

export type SortMode = 'natural' | 'lexicographic';

/** 已选文件（提交前）/ 源文件明细（提交后回填 rows 等字段） */
export interface CsvSourceFile {
  filename: string;
  file_path: string;
  size: number;
  origin: SourceOrigin;
  /** 编码分类结果，如 utf-8 / gb18030 */
  encoding?: string | null;
  /** 本文件贡献的数据行数（合并过程中回填） */
  rows?: number | null;
  /** 物理行数；与 rows 的差 = 字段内换行条数 */
  physical_lines?: number | null;
  bytes_written?: number | null;
  /** 来自导出任务时：所属导出 job 与该分块的期望行数（用于对账） */
  export_job_id?: string | null;
  expected_rows?: number | null;
}

export interface MergeCsvJob {
  job_id: string;
  user_id: string;
  username: string;
  job_name: string | null;
  has_header: boolean;
  strict_header: boolean;
  sort_mode: SortMode;
  source_files: CsvSourceFile[] | null;
  status: MergeCsvJobStatus;

  total_files: number | null;
  done_files: number;
  total_bytes: number | null;
  done_bytes: number;
  total_rows: number;
  total_physical_lines: number;

  /** 最后一个**完整**合入的源文件名 —— 取消/失败时据此判断续传起点 */
  last_merged_file: string | null;

  output_filename: string | null;
  /** 服务器端绝对路径。本地部署下直接复制它比下载快得多 */
  file_path: string | null;
  file_size: number | null;
  output_encoding: string | null;

  expected_total_rows: number | null;
  reconcile_status: 'matched' | 'mismatched' | 'unavailable' | null;
  reconcile_detail: ReconcileDetailItem[] | null;

  warnings: string[] | null;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ReconcileDetailItem {
  filename: string;
  expected_rows: number | null;
  actual_rows: number;
  diff: number;
}

export interface MergeCsvJobListResult {
  total: number;
  page: number;
  page_size: number;
  items: MergeCsvJob[];
}

/** 入口① 导出任务 */
export interface ExportJobSummary {
  export_job_id: string;
  job_name: string | null;
  status: string;
  created_at: string | null;
  /** 该任务下可用于合并的 CSV 分块 */
  csv_files: number;
  total_size: number;
  /** 分块自报的行数合计；为 null 表示无法对账 */
  total_rows: number | null;
  /** 未完成的分块（自动排除，但要让用户看见缺口） */
  incomplete_chunks: string[];
  /** csv 已被压缩、原文件不在了的分块 */
  compressed_chunks: string[];
  files: CsvSourceFile[];
}

/** 入口② 目录 */
export interface DirSummary {
  dir_path: string;
  /** 相对 customer_data/{username}/ 的展示名 */
  display_path: string;
  csv_files: number;
  total_size: number;
  latest_mtime: string | null;
}

/** 提交前预检结果 */
export interface PreviewResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
  /** 排序后的完整清单，前端据此展示"首/末/总数" */
  sorted_files: CsvSourceFile[];
  baseline_header: string[];
  col_count: number;
  output_encoding: string;
  output_bom: boolean;
  total_bytes: number;
  /** 磁盘剩余字节；不足时 errors 里会有说明 */
  disk_free: number | null;
  disk_required: number | null;
  /** 预估耗时（秒），按实测吞吐推算 */
  estimated_seconds: number | null;
  expected_total_rows: number | null;
}

export interface CsvUploadResult {
  upload_id: string;
  filename: string;
  file_path: string;
  file_size: number;
}

export interface ExecuteMergeCsvPayload {
  /** 服务器端文件的绝对路径清单（入口①②③） */
  server_paths?: string[];
  /** 已上传文件的 upload_id 清单（入口④） */
  upload_ids?: string[];
  has_header: boolean;
  strict_header: boolean;
  sort_mode: SortMode;
  job_name?: string;
  /** 显式放行 provenance 未知且刚被修改的文件 */
  allow_active_files?: boolean;
}


// ─── API 方法 ─────────────────────────────────────────────────────────────────

const P = '/tools/merge-csv';

export const mergeCsvApi = {
  /** 入口①：列出本用户的 CSV 导出任务及其分块 */
  listExportJobs: async (page = 1, pageSize = 20): Promise<{ total: number; items: ExportJobSummary[] }> => {
    const res = await apiClient.get(`${P}/export-jobs`, { params: { page, page_size: pageSize } });
    return res.data?.data;
  },

  /** 入口②：列出 customer_data/{username}/ 下含 CSV 的目录 */
  listDirs: async (): Promise<DirSummary[]> => {
    const res = await apiClient.get(`${P}/dirs`);
    return res.data?.data ?? [];
  },

  /** 入口③：列出指定目录下的 .csv（忽略 .zip 等非 CSV 文件） */
  listFiles: async (dirPath: string, pattern?: string): Promise<CsvSourceFile[]> => {
    const res = await apiClient.get(`${P}/files`, { params: { dir_path: dirPath, pattern } });
    return res.data?.data ?? [];
  },

  /** 入口④：上传单个 CSV（上限 1 GB，流式落盘） */
  uploadFile: async (file: File, onProgress?: (percent: number) => void): Promise<CsvUploadResult> => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await apiClient.post(`${P}/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0, // 1 GB 上传不设超时
      onUploadProgress: (evt) => {
        if (onProgress && evt.total) onProgress(Math.round((evt.loaded / evt.total) * 100));
      },
    });
    return res.data?.data;
  },

  /** 提交前预检：跑完全部校验但**不建 job** */
  preview: async (payload: ExecuteMergeCsvPayload): Promise<PreviewResult> => {
    const res = await apiClient.post(`${P}/preview`, payload, { timeout: 120000 });
    return res.data?.data;
  },

  /** 提交合并任务 */
  execute: async (payload: ExecuteMergeCsvPayload): Promise<{ job_id: string; status: string }> => {
    const res = await apiClient.post(`${P}/execute`, payload);
    return res.data?.data;
  },

  getJobStatus: async (jobId: string): Promise<MergeCsvJob> => {
    const res = await apiClient.get(`${P}/jobs/${jobId}`);
    return res.data?.data;
  },

  listJobs: async (page = 1, pageSize = 10): Promise<MergeCsvJobListResult> => {
    const res = await apiClient.get(`${P}/jobs`, { params: { page, page_size: pageSize } });
    return res.data?.data;
  },

  cancelJob: async (jobId: string): Promise<{ status: string }> => {
    const res = await apiClient.post(`${P}/jobs/${jobId}/cancel`);
    return res.data?.data;
  },

  deleteJob: async (jobId: string): Promise<void> => {
    await apiClient.delete(`${P}/jobs/${jobId}`);
  },

  /**
   * 下载地址。**刻意不提供 blob 下载** —— 结果文件可能几十 GB，
   * `URL.createObjectURL(blob)` 会把整个文件读进浏览器内存并打爆标签页。
   * 交给浏览器原生下载（流式落盘 + 支持 Range 续传）。
   */
  downloadUrl: (jobId: string): string => `${API_BASE_URL}${P}/jobs/${jobId}/download`,
};
