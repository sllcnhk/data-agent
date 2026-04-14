import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

// ── Cancel state for the active stream ──────────────────────────────────────
let _activeController: AbortController | null = null;
let _activeConvId: string | null = null;

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 为 apiClient 注入 Authorization header（与 api.ts 保持一致）
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token && config.headers) {
    config.headers['Authorization'] = `Bearer ${token}`;
  }
  return config;
});

// ========== 对话管理API ==========

export const conversationApi = {
  // 创建对话
  createConversation: async (data: {
    title?: string;
    model_key?: string;
    system_prompt?: string;
  }) => {
    const response = await apiClient.post('/conversations', data);
    return response.data;
  },

  // 获取对话列表
  listConversations: async (params?: {
    status?: string;
    limit?: number;
    offset?: number;
  }) => {
    const response = await apiClient.get('/conversations', { params });
    return response.data;
  },

  // 获取对话详情
  getConversation: async (conversationId: string, includeMessages = true) => {
    const response = await apiClient.get(`/conversations/${conversationId}`, {
      params: { include_messages: includeMessages }
    });
    return response.data;
  },

  // 更新对话
  updateConversation: async (conversationId: string, data: {
    title?: string;
    is_pinned?: boolean;
    status?: string;
    tags?: string[];
    model_key?: string;
  }) => {
    const response = await apiClient.put(`/conversations/${conversationId}`, data);
    return response.data;
  },

  // 删除对话
  deleteConversation: async (conversationId: string, hardDelete = false) => {
    const response = await apiClient.delete(`/conversations/${conversationId}`, {
      params: { hard_delete: hardDelete }
    });
    return response.data;
  },

  // 发送消息(非流式)
  sendMessage: async (conversationId: string, data: {
    content: string;
    model_key?: string;
    stream?: boolean;
  }) => {
    const response = await apiClient.post(
      `/conversations/${conversationId}/messages`,
      { ...data, stream: false }
    );
    return response.data;
  },

  // 获取消息列表
  getMessages: async (conversationId: string, params?: {
    limit?: number;
    offset?: number;
  }) => {
    const response = await apiClient.get(
      `/conversations/${conversationId}/messages`,
      { params }
    );
    return response.data;
  },

  // 重新生成最后一条消息
  regenerateLastMessage: async (conversationId: string) => {
    const response = await apiClient.post(
      `/conversations/${conversationId}/regenerate`
    );
    return response.data;
  },

  // 清空对话
  clearConversation: async (conversationId: string, keepSystem = true) => {
    const response = await apiClient.post(
      `/conversations/${conversationId}/clear`,
      null,
      { params: { keep_system: keepSystem } }
    );
    return response.data;
  },

  // 重命名对话
  renameConversation: async (conversationId: string, title: string) => {
    const response = await apiClient.put(
      `/conversations/${conversationId}/title`,
      { title }
    );
    return response.data;
  },

  // 移动对话到分组
  moveToGroup: async (conversationId: string, groupId: string | null) => {
    const response = await apiClient.put(
      `/conversations/${conversationId}/group`,
      { group_id: groupId }
    );
    return response.data;
  },

  // 流式发送消息
  sendMessageStream: async (
    conversationId: string,
    content: string,
    modelKey?: string,
    attachments?: Array<{ name: string; mime_type: string; size: number; data: string }>,
    onChunk?: (chunk: any) => void,
    onComplete?: () => void,
    onError?: (error: any) => void
  ) => {
    const controller = new AbortController();
    _activeController = controller;
    _activeConvId = conversationId;

    try {
      // 读取 access_token 注入 Authorization header
      const fetchHeaders: Record<string, string> = { 'Content-Type': 'application/json' };
      const token = useAuthStore.getState().accessToken;
      if (token) fetchHeaders['Authorization'] = `Bearer ${token}`;

      const response = await fetch(
        `${API_BASE_URL}/conversations/${conversationId}/messages`,
        {
          method: 'POST',
          headers: fetchHeaders,
          body: JSON.stringify({
            content,
            model_key: modelKey,
            stream: true,
            ...(attachments && attachments.length > 0 ? { attachments } : {})
          }),
          signal: controller.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) {
        throw new Error('No reader available');
      }

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          onComplete?.();
          break;
        }

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));

              if (data.type === 'error') {
                // AgentEvent 序列化后错误信息在 data.data；
                // conversation_service 直接抛出的异常错误在 data.error
                const errMsg = data.data || data.error || '推理出错，请重试';
                // 先通过 onChunk 把错误文本写入聊天气泡，让用户看到完整信息
                onChunk?.({ type: 'error', data: errMsg, metadata: data.metadata });
                onError?.(errMsg);
                return;
              }

              if (data.type === 'done') {
                onComplete?.();
                return;
              }

              onChunk?.(data);
            } catch (e) {
              console.error('Parse error:', e);
            }
          }
        }
      }
    } catch (error: any) {
      if (error?.name === 'AbortError') {
        // Aborted by cancelConversationStream — not a real error
        onComplete?.();
      } else {
        console.error('Stream error:', error);
        onError?.(error);
      }
    } finally {
      if (_activeConvId === conversationId) {
        _activeController = null;
        _activeConvId = null;
      }
    }
  }
};

/**
 * Request the backend to stop generating for the given conversation,
 * then abort the fetch after 1 second (fallback to ensure reader closes).
 */
export async function cancelConversationStream(conversationId: string): Promise<void> {
  try {
    await apiClient.post(`/conversations/${conversationId}/cancel`);
  } catch {
    // Best-effort — even if the request fails we still abort the reader
  }
  // Delay abort so backend has time to flush assistant_message SSE event
  setTimeout(() => {
    if (_activeConvId === conversationId && _activeController) {
      _activeController.abort();
    }
  }, 1000);
}

// ========== 模型配置API ==========

export const llmConfigApi = {
  // 获取模型配置列表
  listConfigs: async (enabledOnly = false) => {
    const response = await apiClient.get('/llm-configs', {
      params: { enabled_only: enabledOnly }
    });
    return response.data;
  },

  // 获取模型配置详情
  getConfig: async (modelKey: string, includeSecrets = false) => {
    const response = await apiClient.get(`/llm-configs/${modelKey}`, {
      params: { include_secrets: includeSecrets }
    });
    return response.data;
  },

  // 创建模型配置
  createConfig: async (data: any) => {
    const response = await apiClient.post('/llm-configs', data);
    return response.data;
  },

  // 更新模型配置
  updateConfig: async (modelKey: string, data: any) => {
    const response = await apiClient.put(`/llm-configs/${modelKey}`, data);
    return response.data;
  },

  // 删除模型配置
  deleteConfig: async (modelKey: string) => {
    const response = await apiClient.delete(`/llm-configs/${modelKey}`);
    return response.data;
  },

  // 初始化默认配置
  initDefaults: async (force = false) => {
    const response = await apiClient.post('/llm-configs/init-defaults', null, {
      params: { force }
    });
    return response.data;
  },

  // 获取默认配置
  getDefault: async () => {
    const response = await apiClient.get('/llm-configs/default/current');
    return response.data;
  },

  // 测试配置
  testConfig: async (modelKey: string) => {
    const response = await apiClient.post(`/llm-configs/${modelKey}/test`);
    return response.data;
  }
};

// ========== 超管：其他用户对话视图 ==========

export interface OtherUserConversations {
  username: string;
  display_name: string;
  user_id: string;
  conversations: Array<{
    id: string;
    title: string;
    message_count: number;
    last_message_at?: string;
    updated_at: string;
    group_id?: string;
    status: string;
  }>;
}

export const adminApi = {
  fetchAllUsersConversations: async (): Promise<{ users: OtherUserConversations[] }> => {
    const response = await apiClient.get('/conversations/all-users-view');
    return response.data;
  },
};

// ========== 分组管理API ==========

export const groupApi = {
  // 获取分组列表
  listGroups: async () => {
    const response = await apiClient.get('/groups');
    return response.data;
  },

  // 获取分组详情
  getGroup: async (groupId: string) => {
    const response = await apiClient.get(`/groups/${groupId}`);
    return response.data;
  },

  // 创建分组
  createGroup: async (data: {
    name: string;
    description?: string;
    icon?: string;
    color?: string;
  }) => {
    const response = await apiClient.post('/groups', data);
    return response.data;
  },

  // 更新分组
  updateGroup: async (groupId: string, data: {
    name?: string;
    description?: string;
    icon?: string;
    color?: string;
    is_expanded?: boolean;
  }) => {
    const response = await apiClient.put(`/groups/${groupId}`, data);
    return response.data;
  },

  // 删除分组
  deleteGroup: async (groupId: string) => {
    const response = await apiClient.delete(`/groups/${groupId}`);
    return response.data;
  },

  // 批量重排序
  reorderGroups: async (orders: Array<{ id: string; sort_order: number }>) => {
    const response = await apiClient.post('/groups/reorder', { orders });
    return response.data;
  },

  // 获取分组内的对话列表
  getGroupConversations: async (groupId: string, params?: {
    limit?: number;
    offset?: number;
  }) => {
    const response = await apiClient.get(`/groups/${groupId}/conversations`, { params });
    return response.data;
  },
};

/**
 * 文件下载 API
 *
 * 从 customer_data/{username}/ 下载文件，触发浏览器保存对话框。
 */
export const fileApi = {
  /**
   * 下载文件：fetch → blob → <a download> 触发浏览器下载
   * @param filePath  文件相对路径，如 "customer_data/alice/report.csv"
   * @param filename  保存时显示的文件名
   */
  downloadFile: async (filePath: string, filename: string): Promise<void> => {
    const response = await apiClient.get('/files/download', {
      params: { path: filePath },
      responseType: 'blob',
    });
    const url = URL.createObjectURL(response.data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },
};

export interface PinReportParams {
  file_path: string;
  doc_type: string;
  name?: string;
  conversation_id?: string;
  message_id?: string;
}

export interface PinReportResult {
  report_id: string;
  refresh_token: string;
  doc_type: string;
  is_new: boolean;
}

/** 单条文件的固定状态检测结果 */
export interface PinStatusResult {
  file_path: string;
  pinned: boolean;
  report_id?: string;
  refresh_token?: string;
  doc_type?: string;
  name?: string;
}

export const reportApi = {
  /**
   * 将对话中已生成的 HTML 文件固定为正式报表/报告，写入 reports 数据库。
   * 幂等：同一 file_path 已固定则返回已有记录（is_new=false）。
   */
  pinReport: async (params: PinReportParams): Promise<PinReportResult> => {
    const response = await apiClient.post('/reports/pin', params);
    return response.data?.data as PinReportResult;
  },

  /**
   * 批量检查多个文件是否已固定（只读，不创建记录）。
   * 用于文件卡片渲染时的初始固定状态检测，避免 N 次独立请求。
   */
  checkPinStatusBatch: async (filePaths: string[]): Promise<PinStatusResult[]> => {
    if (!filePaths.length) return [];
    const response = await apiClient.post('/reports/check-pin-status-batch', {
      file_paths: filePaths,
    });
    return response.data?.data?.results ?? [];
  },
};

export default apiClient;
