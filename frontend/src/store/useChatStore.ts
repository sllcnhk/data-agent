import { create } from 'zustand';

// ──────────────────────────────────────────────────────────
// Agent event types (mirroring backend AgentEvent)
// ──────────────────────────────────────────────────────────

export interface FileInfo {
  path: string;
  name: string;
  size: number;
  mime_type: string;
  /** 是否为 HTML 报告文件（路径含 /reports/ 且 MIME 为 text/html） */
  is_report?: boolean;
  /** 区分 dashboard（纯图表报表）和 document（含 LLM 总结报告） */
  doc_type?: 'dashboard' | 'document';
  /** 已固定为正式报表后的 Report DB id，用于恢复按钮"已固定"状态 */
  pinned_report_id?: string;
  /** 固定后的数据刷新令牌 */
  refresh_token?: string;
}

export interface FilesWrittenInfo {
  files: FileInfo[];
}

export interface SkillMatchedSkill {
  name: string;
  tier: string;
  method: 'keyword' | 'semantic' | string;
  hit_triggers: string[];
  score: number;
}

export interface SkillMatchInfo {
  mode: 'keyword' | 'hybrid' | 'llm' | string;
  matched: SkillMatchedSkill[];
  always_inject: Array<{ name: string; tier: string }>;
  summary_mode: boolean;
  total_chars: number;
  load_errors: Array<{ filepath: string; reason: string }>;
}

export interface AgentEvent {
  type: 'thinking' | 'tool_call' | 'tool_result' | 'skill_matched' | 'approval_required' | 'content' | 'error' | 'done' | string;
  data: any;
  metadata?: Record<string, any>;
}

export interface PendingApproval {
  approval_id: string;
  approval_type: 'sql' | 'file_write' | string; // 审批类型
  tool: string;
  message: string;
  // SQL approval fields
  sql?: string;
  warnings?: string[];
  // File write approval fields
  path?: string;
  content_preview?: string;
  session_grant?: boolean;
}

export interface PendingContinuation {
  message: string;
  pending_tasks: string[];
  conclusions: string;
}

export interface AgentInfo {
  agent_type: string;
  agent_label: string;
  skills: Array<{ name: string; title: string }>;
}

// ──────────────────────────────────────────────────────────
// 类型定义
// ──────────────────────────────────────────────────────────

export interface LLMConfig {
  id: string;
  model_key: string;
  model_name: string;
  model_type: string;
  api_base_url?: string;
  api_key?: string;
  default_model?: string;
  temperature: string;
  max_tokens: string;
  is_enabled: boolean;
  is_default: boolean;
  description?: string;
  icon?: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  /** 'continuation' = Agent 自动续接消息，区别于真实用户输入 */
  role: 'user' | 'assistant' | 'system' | 'continuation';
  content: string;
  model?: string;
  created_at: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  /** 历史推理过程事件，从 DB extra_metadata.thinking_events 加载 */
  thinking_events?: AgentEvent[];
  /** 本条消息写入的文件列表，从 DB extra_metadata.files_written 加载 */
  files_written?: FileInfo[];
  extra_metadata?: Record<string, any>;
}

export interface ConversationGroup {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  sort_order: number;
  is_expanded: boolean;
  conversation_count: number;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  current_model: string;
  status: string;
  is_pinned: boolean;
  is_shared?: boolean;
  group_id?: string;
  message_count: number;
  total_tokens: number;
  created_at: string;
  updated_at: string;
  last_message_at?: string;
  extra_metadata?: Record<string, any>;
}

interface ChatState {
  // 对话列表
  conversations: Conversation[];
  currentConversation: Conversation | null;

  // 分组列表
  groups: ConversationGroup[];

  // 消息列表
  messages: Message[];

  // 模型配置
  llmConfigs: LLMConfig[];
  selectedModel: string;

  // UI状态
  loading: boolean;
  sending: boolean;
  error: string | null;

  // Agent 推理过程事件（按 messageId 分组）
  messageThoughts: Record<string, AgentEvent[]>;

  // Agent 信息（按 messageId 分组，来自 agent_start SSE 事件）
  messageAgentInfo: Record<string, AgentInfo>;

  // 待审批操作（来自 approval_required SSE 事件）
  pendingApproval: PendingApproval | null;

  // 待人工确认的续接（来自 continuation_approval_required SSE 事件）
  pendingContinuation: PendingContinuation | null;

  // 是否正在取消生成（防止重复点击）
  isCancelling: boolean;

  // Actions - 对话管理
  setConversations: (conversations: Conversation[]) => void;
  setCurrentConversation: (conversation: Conversation | null) => void;
  addConversation: (conversation: Conversation) => void;
  updateConversation: (id: string, updates: Partial<Conversation>) => void;
  deleteConversation: (id: string) => void;

  // Actions - 分组管理
  setGroups: (groups: ConversationGroup[]) => void;
  addGroup: (group: ConversationGroup) => void;
  updateGroup: (id: string, updates: Partial<ConversationGroup>) => void;
  deleteGroup: (id: string) => void;
  toggleGroupExpand: (id: string) => void;

  // Actions - 消息管理
  setMessages: (messages: Message[]) => void;
  addMessage: (message: Message) => void;
  appendMessageContent: (content: string) => void;
  clearMessages: () => void;

  // Actions - 推理过程事件
  addThoughtEvent: (messageId: string, event: AgentEvent) => void;
  clearThoughtEvents: (messageId: string) => void;

  // Actions - Agent 信息
  setMessageAgentInfo: (messageId: string, info: AgentInfo) => void;

  // Actions - 消息 ID 迁移（占位 ID → 真实 DB ID）
  migrateMessageId: (oldId: string, newId: string) => void;

  // Actions - 文件写入（下载链接）
  setMessageFilesWritten: (messageId: string, files: FileInfo[]) => void;
  /** 固定报表成功后，更新对应文件条目的 pinned_report_id 和 refresh_token */
  markFilePinned: (messageId: string, filePath: string, reportId: string, refreshToken: string) => void;

  // Actions - 审批
  setPendingApproval: (approval: PendingApproval | null) => void;

  // Actions - 续接确认
  setPendingContinuation: (continuation: PendingContinuation | null) => void;

  // Actions - 取消状态
  setIsCancelling: (v: boolean) => void;

  // Actions - 模型配置
  setLLMConfigs: (configs: LLMConfig[]) => void;
  setSelectedModel: (modelKey: string) => void;
  getSelectedModelConfig: () => LLMConfig | undefined;

  // Actions - UI状态
  setLoading: (loading: boolean) => void;
  setSending: (sending: boolean) => void;
  setError: (error: string | null) => void;

  // Actions - 重置
  reset: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  // 初始状态
  conversations: [],
  currentConversation: null,
  groups: [],
  messages: [],
  llmConfigs: [],
  selectedModel: 'claude',
  loading: false,
  sending: false,
  error: null,
  messageThoughts: {},
  messageAgentInfo: {},
  pendingApproval: null,
  pendingContinuation: null,
  isCancelling: false,

  // 对话管理
  setConversations: (conversations) => set({ conversations }),

  setCurrentConversation: (conversation) => {
    set({ currentConversation: conversation });
    if (conversation) {
      localStorage.setItem('lastConversationId', conversation.id);
      // 切换对话时同步模型选择：使用该对话上次使用的模型
      const { llmConfigs } = get();
      const convModel = conversation.current_model;
      if (convModel && llmConfigs.find((c) => c.model_key === convModel && c.is_enabled)) {
        set({ selectedModel: convModel });
        localStorage.setItem('selectedModel', convModel);
      }
    }
  },

  addConversation: (conversation) => set((state) => ({
    conversations: [conversation, ...state.conversations]
  })),

  updateConversation: (id, updates) => set((state) => ({
    conversations: state.conversations.map((conv) =>
      conv.id === id ? { ...conv, ...updates } : conv
    ),
    currentConversation:
      state.currentConversation?.id === id
        ? { ...state.currentConversation, ...updates }
        : state.currentConversation
  })),

  deleteConversation: (id) => set((state) => ({
    conversations: state.conversations.filter((conv) => conv.id !== id),
    currentConversation:
      state.currentConversation?.id === id ? null : state.currentConversation
  })),

  // 分组管理
  setGroups: (groups) => set({ groups }),

  addGroup: (group) => set((state) => ({
    groups: [...state.groups, group].sort((a, b) => a.sort_order - b.sort_order)
  })),

  updateGroup: (id, updates) => set((state) => ({
    groups: state.groups.map((group) =>
      group.id === id ? { ...group, ...updates } : group
    )
  })),

  deleteGroup: (id) => set((state) => ({
    groups: state.groups.filter((group) => group.id !== id),
    // 将该分组下的对话移到未分组
    conversations: state.conversations.map((conv) =>
      conv.group_id === id ? { ...conv, group_id: undefined } : conv
    )
  })),

  toggleGroupExpand: (id) => set((state) => ({
    groups: state.groups.map((group) =>
      group.id === id ? { ...group, is_expanded: !group.is_expanded } : group
    )
  })),

  // 消息管理
  setMessages: (messages) => set({ messages }),

  addMessage: (message) => set((state) => ({
    messages: [...state.messages, message]
  })),

  appendMessageContent: (content) => set((state) => {
    const messages = [...state.messages];
    if (messages.length > 0) {
      const lastMessage = messages[messages.length - 1];
      if (lastMessage.role === 'assistant') {
        lastMessage.content += content;
      }
    }
    return { messages };
  }),

  clearMessages: () => set({ messages: [] }),

  // 推理过程事件
  addThoughtEvent: (messageId, event) => set((state) => {
    const existing = state.messageThoughts[messageId] || [];
    return {
      messageThoughts: {
        ...state.messageThoughts,
        [messageId]: [...existing, event],
      },
    };
  }),

  clearThoughtEvents: (messageId) => set((state) => {
    const next = { ...state.messageThoughts };
    delete next[messageId];
    return { messageThoughts: next };
  }),

  // Agent 信息
  setMessageAgentInfo: (messageId, info) => set((state) => ({
    messageAgentInfo: { ...state.messageAgentInfo, [messageId]: info },
  })),

  // 消息 ID 迁移：将占位 ID 下的 thoughts / agentInfo 移到真实 ID
  migrateMessageId: (oldId, newId) => set((state) => {
    if (oldId === newId) return {};
    const nextThoughts = { ...state.messageThoughts };
    if (nextThoughts[oldId]) {
      nextThoughts[newId] = nextThoughts[oldId];
      delete nextThoughts[oldId];
    }
    const nextAgentInfo = { ...state.messageAgentInfo };
    if (nextAgentInfo[oldId]) {
      nextAgentInfo[newId] = nextAgentInfo[oldId];
      delete nextAgentInfo[oldId];
    }
    return { messageThoughts: nextThoughts, messageAgentInfo: nextAgentInfo };
  }),

  // 文件写入（下载链接）
  setMessageFilesWritten: (messageId, files) => set((state) => ({
    messages: state.messages.map((m) =>
      m.id === messageId ? { ...m, files_written: files } : m
    ),
  })),

  // 固定报表：更新对应文件条目的 pinned_report_id
  markFilePinned: (messageId, filePath, reportId, refreshToken) => set((state) => ({
    messages: state.messages.map((m) => {
      if (m.id !== messageId || !m.files_written) return m;
      return {
        ...m,
        files_written: m.files_written.map((f) =>
          f.path === filePath
            ? { ...f, pinned_report_id: reportId, refresh_token: refreshToken }
            : f
        ),
      };
    }),
  })),

  // 审批
  setPendingApproval: (approval) => set({ pendingApproval: approval }),

  // 续接确认
  setPendingContinuation: (continuation) => set({ pendingContinuation: continuation }),

  // 取消状态
  setIsCancelling: (v) => set({ isCancelling: v }),

  // 模型配置
  setLLMConfigs: (configs) => {
    set({ llmConfigs: configs });
    // 若当前对话已激活，模型跟随对话；否则优先恢复 localStorage 中保存的模型，
    // 均不满足时再使用系统默认模型。
    const { currentConversation } = get();
    if (currentConversation) {
      // 对话已激活时不覆盖（setCurrentConversation 已经同步过了）
      return;
    }
    const savedModel = localStorage.getItem('selectedModel');
    if (savedModel && configs.find((c) => c.model_key === savedModel && c.is_enabled)) {
      set({ selectedModel: savedModel });
    } else {
      const defaultConfig = configs.find((c) => c.is_default && c.is_enabled);
      if (defaultConfig) {
        set({ selectedModel: defaultConfig.model_key });
        localStorage.setItem('selectedModel', defaultConfig.model_key);
      }
    }
  },

  setSelectedModel: (modelKey) => {
    set({ selectedModel: modelKey });
    // 保存到localStorage
    localStorage.setItem('selectedModel', modelKey);
  },

  getSelectedModelConfig: () => {
    const { llmConfigs, selectedModel } = get();
    return llmConfigs.find((c) => c.model_key === selectedModel);
  },

  // UI状态
  setLoading: (loading) => set({ loading }),
  setSending: (sending) => set({ sending }),
  setError: (error) => set({ error }),

  // 重置
  reset: () => set({
    conversations: [],
    currentConversation: null,
    groups: [],
    messages: [],
    loading: false,
    sending: false,
    error: null,
    messageThoughts: {},
    messageAgentInfo: {},
    pendingApproval: null,
    pendingContinuation: null,
    isCancelling: false,
  }),
}));

// 初始化时恢复上次选择的模型
const savedModel = localStorage.getItem('selectedModel');
if (savedModel) {
  useChatStore.setState({ selectedModel: savedModel });
}
