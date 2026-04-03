import axios, { AxiosRequestConfig } from 'axios';
import {
  Agent,
  Task,
  Skill,
  SystemHealth,
  TaskSubmitRequest,
  TaskSubmitResponse,
  TaskStatus,
  AgentMetrics,
  RoutingSuggestion,
} from '@/types/api';
import { logger } from './logger';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,  // 允许发送/接收 httpOnly Cookie（refresh_token）
  headers: {
    'Content-Type': 'application/json',
  },
});

// ── Bearer token 注入拦截器 ──────────────────────────────────────────────────
// 从 AuthStore 读取 access_token 并注入 Authorization header
// 使用动态 import 避免循环依赖
apiClient.interceptors.request.use(
  (config) => {
    // 动态读取最新 token（store 可能已更新）
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { useAuthStore } = require('@/store/useAuthStore');
      const token: string | null = useAuthStore.getState().accessToken;
      if (token && config.headers) {
        config.headers['Authorization'] = `Bearer ${token}`;
      }
    } catch { /* AuthStore 不可用时忽略 */ }

    logger.logApiRequest(config);
    return config;
  },
  (error) => {
    logger.error('API', '请求配置错误', error);
    return Promise.reject(error);
  }
);

// ── 401 自动 refresh 拦截器 ──────────────────────────────────────────────────
// 单一拦截器同时负责：成功日志、401 自动刷新（排队防并发）、其他错误日志
let _isRefreshing = false;
let _refreshQueue: Array<(token: string | null) => void> = [];

apiClient.interceptors.response.use(
  (response) => {
    logger.logApiResponse(response);
    return response;
  },
  async (error) => {
    const originalRequest = error.config;

    // 只处理 401，且只重试一次，且不是 /auth/login 或 /auth/refresh 自身
    if (
      error.response?.status === 401 &&
      !originalRequest._retried &&
      !originalRequest.url?.includes('/auth/login') &&
      !originalRequest.url?.includes('/auth/refresh')
    ) {
      originalRequest._retried = true;

      if (_isRefreshing) {
        // 排队等待 refresh 完成
        return new Promise((resolve, reject) => {
          _refreshQueue.push((token) => {
            if (token) {
              originalRequest.headers['Authorization'] = `Bearer ${token}`;
              resolve(apiClient(originalRequest));
            } else {
              reject(error);
            }
          });
        });
      }

      _isRefreshing = true;
      try {
        const { useAuthStore } = await import('@/store/useAuthStore');
        const success = await useAuthStore.getState().refreshToken();
        const newToken = useAuthStore.getState().accessToken;

        _refreshQueue.forEach((cb) => cb(success ? newToken : null));
        _refreshQueue = [];

        if (success && newToken) {
          originalRequest.headers['Authorization'] = `Bearer ${newToken}`;
          return apiClient(originalRequest);
        } else {
          // refresh 失败：refreshToken() 内部已 set({ user:null, accessToken:null })
          // RequireAuth 监听 Zustand 状态，会通过 React Router 软导航到 /login
          // 不做硬跳转（整页重载）避免白屏
          return Promise.reject(error);
        }
      } finally {
        _isRefreshing = false;
      }
    }

    // 其他错误：记录日志，保留原始 error 对象（含 error.response）供调用方检查状态码
    logger.logApiError(error);
    return Promise.reject(error);
  }
);

// Agent相关API
export const agentApi = {
  // 获取所有Agent
  getAgents: async (agentType?: string): Promise<Agent[]> => {
    try {
      logger.info('API', '获取Agent列表', { agentType });
      const params = agentType ? { agent_type: agentType } : {};
      const response = await apiClient.get('/agents', { params });
      logger.info('API', 'Agent列表获取成功', { count: response.data.length });
      return response.data;
    } catch (error) {
      logger.error('API', '获取Agent列表失败', error);
      throw error;
    }
  },

  // 创建Agent
  createAgent: async (agentType: string, agentId?: string): Promise<any> => {
    try {
      logger.info('API', '创建Agent', { agentType, agentId });
      const response = await apiClient.post('/agents', {
        agent_type: agentType,
        agent_id: agentId,
      });
      logger.info('API', 'Agent创建成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '创建Agent失败', error);
      throw error;
    }
  },

  // 获取Agent详情
  getAgent: async (agentId: string): Promise<Agent> => {
    try {
      logger.info('API', '获取Agent详情', { agentId });
      const response = await apiClient.get(`/agents/${agentId}`);
      logger.info('API', 'Agent详情获取成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '获取Agent详情失败', error);
      throw error;
    }
  },

  // 删除Agent
  deleteAgent: async (agentId: string): Promise<any> => {
    try {
      logger.info('API', '删除Agent', { agentId });
      const response = await apiClient.delete(`/agents/${agentId}`);
      logger.info('API', 'Agent删除成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '删除Agent失败', error);
      throw error;
    }
  },

  // 获取Agent指标
  getAgentMetrics: async (agentId: string): Promise<AgentMetrics> => {
    try {
      logger.info('API', '获取Agent指标', { agentId });
      const response = await apiClient.get(`/agents/${agentId}/metrics`);
      logger.info('API', 'Agent指标获取成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '获取Agent指标失败', error);
      throw error;
    }
  },

  // 获取Agent任务历史
  getAgentTasks: async (agentId: string, status?: string, limit?: number): Promise<Task[]> => {
    try {
      logger.info('API', '获取Agent任务历史', { agentId, status, limit });
      const params: any = {};
      if (status) params.status = status;
      if (limit) params.limit = limit;
      const response = await apiClient.get(`/agents/${agentId}/tasks`, { params });
      logger.info('API', 'Agent任务历史获取成功', { count: response.data.length });
      return response.data;
    } catch (error) {
      logger.error('API', '获取Agent任务历史失败', error);
      throw error;
    }
  },
};

// 任务相关API
export const taskApi = {
  // 提交任务
  submitTask: async (request: TaskSubmitRequest): Promise<TaskSubmitResponse> => {
    try {
      logger.info('API', '提交任务', request);
      const response = await apiClient.post('/agents/tasks', request);
      logger.info('API', '任务提交成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '任务提交失败', error);
      throw error;
    }
  },

  // 获取任务状态
  getTaskStatus: async (taskId: string): Promise<TaskStatus> => {
    try {
      logger.info('API', '获取任务状态', { taskId });
      const response = await apiClient.get(`/agents/tasks/${taskId}/status`);
      logger.info('API', '任务状态获取成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '获取任务状态失败', error);
      throw error;
    }
  },

  // 取消任务
  cancelTask: async (taskId: string): Promise<any> => {
    try {
      logger.info('API', '取消任务', { taskId });
      const response = await apiClient.delete(`/agents/tasks/${taskId}`);
      logger.info('API', '任务取消成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '取消任务失败', error);
      throw error;
    }
  },

  // 重试任务
  retryTask: async (taskId: string): Promise<any> => {
    try {
      logger.info('API', '重试任务', { taskId });
      const response = await apiClient.post(`/agents/tasks/${taskId}/retry`);
      logger.info('API', '任务重试成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '重试任务失败', error);
      throw error;
    }
  },
};

// 技能相关API
export const skillApi = {
  // 获取所有技能
  getSkills: async (): Promise<Skill[]> => {
    try {
      logger.info('API', '获取技能列表');
      const response = await apiClient.get('/skills');
      logger.info('API', '技能列表获取成功', { count: response.data.length });
      return response.data;
    } catch (error) {
      logger.error('API', '获取技能列表失败', error);
      throw error;
    }
  },

  // 获取技能详情
  getSkill: async (skillName: string): Promise<Skill> => {
    try {
      logger.info('API', '获取技能详情', { skillName });
      const response = await apiClient.get(`/skills/${skillName}`);
      logger.info('API', '技能详情获取成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '获取技能详情失败', error);
      throw error;
    }
  },

  // 执行技能
  executeSkill: async (skillName: string, parameters: any): Promise<any> => {
    try {
      logger.info('API', '执行技能', { skillName, parameters });
      const response = await apiClient.post(`/skills/${skillName}/execute`, parameters);
      logger.info('API', '技能执行成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '技能执行失败', error);
      throw error;
    }
  },

  // 数据库查询
  executeDatabaseQuery: async (params: any): Promise<any> => {
    try {
      logger.info('API', '执行数据库查询', params);
      const response = await apiClient.post('/skills/database_query/execute', params);
      logger.info('API', '数据库查询成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '数据库查询失败', error);
      throw error;
    }
  },

  // 数据分析
  executeDataAnalysis: async (params: any): Promise<any> => {
    try {
      logger.info('API', '执行数据分析', params);
      const response = await apiClient.post('/skills/data_analysis/execute', params);
      logger.info('API', '数据分析成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '数据分析失败', error);
      throw error;
    }
  },

  // SQL生成
  executeSqlGeneration: async (params: any): Promise<any> => {
    try {
      logger.info('API', '执行SQL生成', params);
      const response = await apiClient.post('/skills/sql_generation/execute', params);
      logger.info('API', 'SQL生成成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', 'SQL生成失败', error);
      throw error;
    }
  },

  // 图表生成
  executeChartGeneration: async (params: any): Promise<any> => {
    try {
      logger.info('API', '执行图表生成', params);
      const response = await apiClient.post('/skills/chart_generation/execute', params);
      logger.info('API', '图表生成成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', '图表生成失败', error);
      throw error;
    }
  },

  // ETL设计
  executeEtlDesign: async (params: any): Promise<any> => {
    try {
      logger.info('API', '执行ETL设计', params);
      const response = await apiClient.post('/skills/etl_design/execute', params);
      logger.info('API', 'ETL设计成功', response.data);
      return response.data;
    } catch (error) {
      logger.error('API', 'ETL设计失败', error);
      throw error;
    }
  },

  // 获取所有 SKILL.md 技能
  getMdSkills: async (): Promise<any[]> => {
    try {
      const response = await apiClient.get('/skills/md-skills');
      return response.data;
    } catch (error) {
      logger.error('API', '获取 SKILL.md 技能失败', error);
      throw error;
    }
  },

  // 获取用户自定义技能列表
  getUserSkills: async (): Promise<any[]> => {
    try {
      const response = await apiClient.get('/skills/user-defined');
      return response.data;
    } catch (error) {
      logger.error('API', '获取用户自定义技能失败', error);
      throw error;
    }
  },

  // 创建用户自定义技能
  createUserSkill: async (skill: {
    name: string;
    description: string;
    triggers: string[];
    category: string;
    priority: string;
    content: string;
  }): Promise<any> => {
    try {
      const response = await apiClient.post('/skills/user-defined', skill);
      return response.data;
    } catch (error) {
      logger.error('API', '创建用户自定义技能失败', error);
      throw error;
    }
  },

  // 更新用户自定义技能
  updateUserSkill: async (
    skillName: string,
    update: {
      description?: string;
      triggers?: string[];
      category?: string;
      priority?: string;
      content?: string;
    }
  ): Promise<any> => {
    try {
      const response = await apiClient.put(`/skills/user-defined/${skillName}`, update);
      return response.data;
    } catch (error) {
      logger.error('API', '更新用户自定义技能失败', error);
      throw error;
    }
  },

  // 删除用户自定义技能
  deleteUserSkill: async (skillName: string): Promise<any> => {
    try {
      const response = await apiClient.delete(`/skills/user-defined/${skillName}`);
      return response.data;
    } catch (error) {
      logger.error('API', '删除用户自定义技能失败', error);
      throw error;
    }
  },

  // 预览消息触发的 Skill（测试触发器）
  previewSkillTrigger: async (message: string): Promise<any> => {
    try {
      const response = await apiClient.get('/skills/preview', { params: { message } });
      return response.data;
    } catch (error) {
      logger.error('API', '获取 Skill 触发预览失败', error);
      throw error;
    }
  },

  // 获取项目技能列表
  getProjectSkills: async (): Promise<any[]> => {
    try {
      const response = await apiClient.get('/skills/project-skills');
      return response.data;
    } catch (error) {
      logger.error('API', '获取项目技能失败', error);
      throw error;
    }
  },

  // 创建项目技能 (需要管理员 Token)
  createProjectSkill: async (
    skill: {
      name: string;
      description: string;
      triggers: string[];
      category: string;
      priority: string;
      content: string;
    },
    adminToken: string
  ): Promise<any> => {
    try {
      const response = await apiClient.post('/skills/project-skills', skill, {
        headers: { 'X-Admin-Token': adminToken },
      });
      return response.data;
    } catch (error) {
      logger.error('API', '创建项目技能失败', error);
      throw error;
    }
  },

  // 更新项目技能 (需要管理员 Token)
  updateProjectSkill: async (
    skillName: string,
    update: {
      description?: string;
      triggers?: string[];
      category?: string;
      priority?: string;
      content?: string;
    },
    adminToken: string
  ): Promise<any> => {
    try {
      const response = await apiClient.put(`/skills/project-skills/${skillName}`, update, {
        headers: { 'X-Admin-Token': adminToken },
      });
      return response.data;
    } catch (error) {
      logger.error('API', '更新项目技能失败', error);
      throw error;
    }
  },

  // 删除项目技能 (需要管理员 Token)
  deleteProjectSkill: async (skillName: string, adminToken: string): Promise<any> => {
    try {
      const response = await apiClient.delete(`/skills/project-skills/${skillName}`, {
        headers: { 'X-Admin-Token': adminToken },
      });
      return response.data;
    } catch (error) {
      logger.error('API', '删除项目技能失败', error);
      throw error;
    }
  },
};

// MCP 服务器相关API
export const mcpApi = {
  // 获取已注册 MCP 服务器列表（需要 settings:read 权限，即 admin/analyst+ 角色）
  // 后端返回 {"success": true, "data": [...]}，取 data 字段
  // 401/403 由调用方捕获处理，不会抛出
  getServers: async (): Promise<{ name: string; type: string; version: string; tool_count: number; resource_count: number }[]> => {
    const response = await apiClient.get('/mcp/servers');
    // response.data = { success: true, data: [...] }
    return (response.data?.data ?? []);
  },
};

// 系统相关API
export const systemApi = {
  // 系统健康检查
  healthCheck: async (): Promise<SystemHealth> => {
    const response = await apiClient.get('/agents/health');
    return response;
  },

  // 获取路由建议
  getRoutingSuggestions: async (query: string): Promise<RoutingSuggestion[]> => {
    const response = await apiClient.get('/agents/routing/suggestions', {
      params: { query },
    });
    return response;
  },

  // 列出路由规则
  listRoutingRules: async (): Promise<any[]> => {
    const response = await apiClient.get('/agents/routing/rules');
    return response;
  },
};

export default apiClient;
