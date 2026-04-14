/**
 * DataCenterCopilot — 数据管理中心 AI 助手
 *
 * 导出两个组件：
 *   DataCenterCopilotContent  — 纯内容（消息列表 + 输入框 + 模型选择），可内嵌任意容器
 *   DataCenterCopilot (default) — Drawer 包装版，供各列表页使用
 *
 * 变更（2026-04-14）：
 *   - LocalMessage 新增 files?: FileInfo[] 字段
 *   - handleSend() 检测 specUpdated 后自动挂载文件卡片到最后一条 assistant 消息
 *   - 新增 PilotFilesDisplay 组件：文件名 + 预览按钮 + 固定按钮
 *   - CopilotSharedProps 新增 contextRefreshToken？作为 contextSpec 缺失时的预览 token 回退
 */
import React, { useCallback, useRef, useState } from 'react';
import {
  Button,
  Drawer,
  Empty,
  Input,
  Spin,
  Tag,
  Tooltip,
  message as antMessage,
} from 'antd';
import {
  BarChartOutlined,
  CheckCircleOutlined,
  CloseOutlined,
  EyeOutlined,
  PushpinOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { conversationApi, reportApi } from '../services/chatApi';
import type { FileInfo } from '../store/useChatStore';
import { useAuthStore } from '../store/useAuthStore';
import ModelSelectorMini from './ModelSelectorMini';
import ReportPreviewModal from './chat/ReportPreviewModal';

const { TextArea } = Input;
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string) || '/api/v1';

// ── Types ────────────────────────────────────────────────────────────────────

export type CopilotContextType = 'dashboard' | 'document' | 'schedule';

/** Props shared by both the content component and the drawer wrapper */
export interface CopilotSharedProps {
  contextType: CopilotContextType;
  contextId: string;
  contextName: string;
  /** Raw spec/config object injected into the AI system prompt */
  contextSpec?: Record<string, any> | null;
  /** Called when the AI successfully updates the report/schedule spec */
  onSpecUpdated?: () => void;
  /**
   * 报表刷新令牌——当 contextSpec 尚未加载时作为预览 token 回退。
   * ReportViewerPage 将 URL 参数中的 token 传入此处，确保即使 spec-meta 请求失败
   * 也能在文件卡片中提供预览功能。
   */
  contextRefreshToken?: string;
}

export interface CopilotContentProps extends CopilotSharedProps {
  /** Controls greeting initialization — true when the panel/drawer is visible */
  open: boolean;
}

export interface DataCenterCopilotProps extends CopilotSharedProps {
  open: boolean;
  onClose: () => void;
}

interface LocalMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  /** AI 修改报表后挂载的文件卡片信息（仅 assistant 消息可有） */
  files?: FileInfo[];
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildSystemPrompt(
  contextType: CopilotContextType,
  contextName: string,
  contextSpec: Record<string, any> | null | undefined,
): string {
  const specJson = contextSpec ? JSON.stringify(contextSpec, null, 2) : '(暂无配置)';

  if (contextType === 'schedule') {
    return `你是数据管理中心的智能助手（Co-pilot）。
当前用户正在查看推送任务「${contextName}」。
任务配置：
\`\`\`json
${specJson}
\`\`\`

你可以帮助用户：
- 修改推送频率（cron表达式）
- 修改推送渠道（邮件/企微/飞书）
- 启用或暂停推送任务
- 查看推送历史

调用 API 时使用以下接口：
- 修改配置：PUT /api/v1/scheduled-reports/{id}
- 暂停/启用：PUT /api/v1/scheduled-reports/{id}/toggle
- 立即执行：POST /api/v1/scheduled-reports/{id}/run-now

当前任务 ID：${contextSpec?.id ?? '未知'}
请用简洁中文回复，操作前务必向用户确认。`;
  }

  const label = contextType === 'document' ? '报告' : '报表';
  const reportId = contextSpec?.id ?? '{report_id}';
  const refreshToken = contextSpec?.refresh_token ?? '{refresh_token}';
  const charts: any[] = contextSpec?.charts ?? [];
  const chartSummary = charts
    .map((c: any, i: number) => `  [${i + 1}] id="${c.id ?? '?'}" title="${c.title ?? '?'}" type="${c.chart_type ?? '?'}"`)
    .join('\n');

  return `你是数据管理中心的智能助手（Co-pilot）。
当前用户正在查看${label}「${contextName}」。

${label} spec 配置（含 refresh_token，请勿对外展示）：
\`\`\`json
${specJson}
\`\`\`

当前${label}共有 ${charts.length} 个图表：
${chartSummary || '  （无图表）'}

---
## ⚠️ 修改${label}使用 MCP 工具（不是 HTTP 接口）

以下三个 MCP 工具直接可用，无需 HTTP 权限：

### 工具 1：report__get_spec（修改前先读取）
读取报表完整 spec，确认所有图表 ID。
\`\`\`
{ "report_id": "${reportId}", "token": "${refreshToken}" }
\`\`\`

### 工具 2：report__update_single_chart（改单图时优先使用）
局部 merge 更新单个图表，不影响其他 ${charts.length - 1} 个图表。
\`\`\`
{ "report_id": "${reportId}", "token": "${refreshToken}", "chart_id": "<图表id>", "chart_patch": { "chart_type": "area", ... } }
\`\`\`

### 工具 3：report__update_spec（添加/删除图表或批量修改时使用）
全量更新 spec，⚠️ spec.charts 必须包含所有 ${charts.length} 个图表！
\`\`\`
{ "report_id": "${reportId}", "token": "${refreshToken}", "spec": { "title": "...", "charts": [...全部图表], "theme": "light", "filters": [], "data_sources": [], "data": {} } }
\`\`\`

修改完成后，请告知用户「${label}已更新，请查看预览」。
请用简洁中文回复，修改前向用户简要确认改动内容。`;
}

// ── PilotFilesDisplay ─────────────────────────────────────────────────────────

/**
 * Pilot 对话中的文件卡片组件。
 *
 * 与 ChatMessages.tsx 中的 FileDownloadCards 对比：
 * - Pilot 中修改的都是已固定报表（file.pinned_report_id 始终有值），
 *   因此固定按钮固定显示"已生成固定报表"（绿色禁用）。
 * - 若 pinned_report_id 未知（极少数边缘情况），则显示可点击的"生成固定报表"按钮。
 * - hasSpec=false 时仅显示预览按钮（无固定按钮），保证可用性降级。
 */
interface PilotFilesDisplayProps {
  files: FileInfo[];
  onPreview: (file: FileInfo) => void;
  hasSpec: boolean;
}

const PilotFilesDisplay: React.FC<PilotFilesDisplayProps> = ({ files, onPreview, hasSpec }) => {
  const [pinning, setPinning] = useState<string | null>(null);
  const [localPinnedIds, setLocalPinnedIds] = useState<Record<string, { report_id: string; refresh_token: string }>>({});

  const handlePin = async (file: FileInfo) => {
    if (pinning === (file.path || file.name)) return;
    setPinning(file.path || file.name);
    try {
      const docType = file.doc_type ?? 'dashboard';
      const result = await reportApi.pinReport({
        file_path: file.path,
        doc_type: docType,
      });
      setLocalPinnedIds((prev) => ({
        ...prev,
        [file.path || file.name]: { report_id: result.report_id, refresh_token: result.refresh_token },
      }));
      const label = docType === 'document' ? '报告' : '报表';
      antMessage.success(`已生成固定${label}，可在数据管理中心查看`);
    } catch (e: any) {
      antMessage.error(`固定失败: ${e?.response?.data?.detail ?? e?.message ?? '未知错误'}`);
    } finally {
      setPinning(null);
    }
  };

  return (
    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 2 }}>📎 生成的文件</div>
      {files.map((file) => {
        const key = file.path || file.name;
        const docType = file.doc_type ?? 'dashboard';
        const docLabel = docType === 'document' ? '报告' : '报表';
        const localPin = localPinnedIds[key];
        const effectivePinnedId = file.pinned_report_id ?? localPin?.report_id;
        const effectiveRefreshToken = file.refresh_token ?? localPin?.refresh_token;
        const isPinned = !!effectivePinnedId;
        const canPreview = !!(effectivePinnedId && effectiveRefreshToken);

        return (
          <div
            key={key}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '7px 10px',
              background: '#f0f7ff',
              borderRadius: 7,
              border: '1px solid #91caff',
              flexWrap: 'wrap',
            }}
          >
            <BarChartOutlined style={{ color: '#1677ff', fontSize: 16, flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 500,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    maxWidth: 160,
                  }}
                >
                  {file.name}
                </span>
                <Tag
                  color={docType === 'document' ? 'purple' : 'blue'}
                  style={{ fontSize: 10, margin: 0, lineHeight: '16px', padding: '0 4px' }}
                >
                  {docType === 'document' ? '分析报告' : '交互报表'}
                </Tag>
              </div>
              <div style={{ fontSize: 10, color: '#999', marginTop: 1 }}>已更新 · 可预览最新版本</div>
            </div>
            {/* 预览按钮：有 pinned_report_id + refresh_token 时才可用 */}
            {canPreview && (
              <Button
                size="small"
                type="primary"
                icon={<EyeOutlined />}
                onClick={() =>
                  onPreview({
                    ...file,
                    pinned_report_id: effectivePinnedId,
                    refresh_token: effectiveRefreshToken,
                  })
                }
              >
                预览
              </Button>
            )}
            {/* 固定按钮：hasSpec=true 时显示；contextSpec 缺失时降级为纯预览卡片 */}
            {hasSpec && (
              isPinned ? (
                <Button
                  size="small"
                  icon={<CheckCircleOutlined />}
                  disabled
                  style={{ color: '#52c41a', borderColor: '#52c41a', background: '#f6ffed' }}
                >
                  已生成固定{docLabel}
                </Button>
              ) : (
                <Tooltip title={`将此${docLabel}固定到数据管理中心`}>
                  <Button
                    size="small"
                    icon={<PushpinOutlined />}
                    loading={pinning === key}
                    onClick={() => handlePin(file)}
                    style={{ borderColor: '#1677ff', color: '#1677ff' }}
                  >
                    生成固定{docLabel}
                  </Button>
                </Tooltip>
              )
            )}
          </div>
        );
      })}
    </div>
  );
};

// ── MessageBubble ─────────────────────────────────────────────────────────────

interface MessageBubbleProps {
  msg: LocalMessage;
  onPreviewFile: (file: FileInfo) => void;
  hasSpec: boolean;
}

const MessageBubble: React.FC<MessageBubbleProps> = ({ msg, onPreviewFile, hasSpec }) => {
  const isUser = msg.role === 'user';
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        gap: 8,
        marginBottom: 12,
        alignItems: 'flex-start',
      }}
    >
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: '50%',
          background: isUser ? '#1677ff' : '#52c41a',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        {isUser ? (
          <UserOutlined style={{ color: '#fff', fontSize: 13 }} />
        ) : (
          <RobotOutlined style={{ color: '#fff', fontSize: 13 }} />
        )}
      </div>
      <div style={{ maxWidth: '78%', display: 'flex', flexDirection: 'column', gap: 0 }}>
        <div
          style={{
            padding: '8px 12px',
            borderRadius: isUser ? '12px 4px 12px 12px' : '4px 12px 12px 12px',
            background: isUser ? '#1677ff' : '#f5f5f5',
            color: isUser ? '#fff' : '#333',
            fontSize: 13,
            lineHeight: 1.6,
            wordBreak: 'break-word',
          }}
        >
          {msg.content ? (
            isUser ? (
              <span style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</span>
            ) : (
              <div className="copilot-markdown">
                <ReactMarkdown>{msg.content}</ReactMarkdown>
              </div>
            )
          ) : (
            <Spin size="small" />
          )}
        </div>
        {/* 文件卡片：仅 assistant 消息且有 files 时渲染，位于气泡正下方 */}
        {!isUser && msg.files && msg.files.length > 0 && (
          <PilotFilesDisplay
            files={msg.files}
            onPreview={onPreviewFile}
            hasSpec={hasSpec}
          />
        )}
      </div>
    </div>
  );
};

// ── DataCenterCopilotContent ──────────────────────────────────────────────────

export const DataCenterCopilotContent: React.FC<CopilotContentProps> = ({
  open,
  contextType,
  contextId,
  contextName,
  contextSpec,
  onSpecUpdated,
  contextRefreshToken,
}) => {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [sending, setSending] = useState(false);
  const [initialized, setInitialized] = useState(false);
  const [selectedModel, setSelectedModel] = useState('');
  const [previewFile, setPreviewFile] = useState<FileInfo | null>(null);

  const conversationIdRef = useRef<string | null>(null);
  const currentAsstIdRef = useRef<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const accessToken = useAuthStore((s) => s.accessToken);
  const authUser = useAuthStore((s) => s.user);
  const [initializing, setInitializing] = useState(false);

  // Reset state when context switches
  const prevContextIdRef = useRef<string>('');
  if (contextId !== prevContextIdRef.current) {
    prevContextIdRef.current = contextId;
    conversationIdRef.current = null;
    // Use function form to avoid stale closure lint warning; these are safe resets
    setMessages([]);
    setInitialized(false);
  }

  // ── Helper: localStorage key for this pilot conversation ─────────────────
  const getPilotConvKey = useCallback((): string | null => {
    const isReport = contextType === 'dashboard' || contextType === 'document';
    if (!isReport || !contextId) return null;
    const uid = authUser?.id && authUser.id !== 'default' ? authUser.id : '';
    return uid
      ? `pilot_conv_${contextType}_${contextId}_${uid}`
      : `pilot_conv_${contextType}_${contextId}`;
  }, [contextType, contextId, authUser]);

  // ── Helper: show initial greeting message ─────────────────────────────────
  const showGreeting = useCallback(() => {
    const label =
      contextType === 'schedule'
        ? '推送任务'
        : contextType === 'document'
          ? '报告'
          : '报表';
    setMessages([{
      id: 'greeting',
      role: 'assistant',
      content: `你好！我是 AI 助手。当前${label}是「**${contextName}**」。\n\n你可以告诉我需要做哪些修改，例如：\n- 调整推送时间\n- 修改图表类型\n- 调整 SQL 时间范围\n- 添加/删除图表`,
      created_at: new Date().toISOString(),
    }]);
  }, [contextType, contextName]);

  // ── Helper: load conversation history and render; fall back to greeting ───
  const loadHistory = useCallback(async (convId: string) => {
    try {
      const res = await conversationApi.getMessages(convId, { limit: 50 });
      const items: any[] = res.success && Array.isArray(res.data?.items)
        ? res.data.items
        : Array.isArray(res.data)
          ? res.data
          : [];
      const msgs: LocalMessage[] = items
        .filter((m: any) => m.role === 'user' || m.role === 'assistant')
        .map((m: any) => ({
          id: m.id,
          role: m.role as 'user' | 'assistant',
          content: m.content || '',
          created_at: m.created_at || new Date().toISOString(),
        }));
      if (msgs.length > 0) {
        setMessages([
          ...msgs,
          {
            id: 'history-sep',
            role: 'assistant',
            content: '— 以上为历史对话，可继续操作 —',
            created_at: new Date().toISOString(),
          },
        ]);
        return;
      }
    } catch {
      // fall through
    }
    showGreeting();
  }, [showGreeting]);

  // ── Initialize conversation on first open ─────────────────────────────────
  React.useEffect(() => {
    if (!open || initialized || !contextId) return;
    const isReport = contextType === 'dashboard' || contextType === 'document';

    if (!isReport) {
      // 非报表上下文（推送任务等）：直接显示欢迎语，对话在首次发送时懒创建
      showGreeting();
      setInitialized(true);
      return;
    }

    // 报表上下文：通过 upsert 端点确保同用户同报表复用同一对话
    let cancelled = false;
    setInitializing(true);

    (async () => {
      try {
        const lsKey = getPilotConvKey();
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;

        // 1. 优先读 localStorage 缓存
        const cachedId = lsKey ? localStorage.getItem(lsKey) : null;
        if (cachedId) {
          try {
            const validRes = await conversationApi.getConversation(cachedId, false);
            if (!cancelled && validRes.success && validRes.data?.id) {
              conversationIdRef.current = cachedId;
              await loadHistory(cachedId);
              if (!cancelled) setInitialized(true);
              return;
            }
          } catch {
            // 对话已不存在，清除缓存
          }
          if (lsKey) localStorage.removeItem(lsKey);
        }

        // 2. 调用 upsert 端点（后端保证同用户同报表只有一个对话）
        const upsertRes = await fetch(`${API_BASE_URL}/reports/${contextId}/copilot`, {
          method: 'POST',
          headers,
          body: JSON.stringify({}),
        });
        if (!upsertRes.ok) throw new Error(`HTTP ${upsertRes.status}`);
        const json = await upsertRes.json();
        if (!json.success) throw new Error('copilot upsert 失败');

        if (cancelled) return;

        const convId: string = json.data.conversation_id;
        const isNew: boolean = json.data.created !== false;

        conversationIdRef.current = convId;
        if (lsKey) localStorage.setItem(lsKey, convId);

        if (isNew) {
          showGreeting();
        } else {
          await loadHistory(convId);
        }
        if (!cancelled) setInitialized(true);
      } catch {
        if (!cancelled) {
          showGreeting();
          setInitialized(true);
        }
      } finally {
        if (!cancelled) setInitializing(false);
      }
    })();

    return () => { cancelled = true; };
  }, [open, initialized, contextId, contextType, accessToken, getPilotConvKey, showGreeting, loadHistory]);

  // Auto-scroll to bottom
  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ── Streaming helpers ──────────────────────────────────────────────────────

  const appendToLastAssistant = useCallback((text: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === currentAsstIdRef.current ? { ...m, content: m.content + text } : m,
      ),
    );
  }, []);

  const ensureConversation = async (): Promise<string> => {
    if (conversationIdRef.current) return conversationIdRef.current;

    const isReport = contextType === 'dashboard' || contextType === 'document';

    if (isReport && contextId) {
      // 报表上下文：upsert 确保不会重复创建对话
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
      const res = await fetch(`${API_BASE_URL}/reports/${contextId}/copilot`, {
        method: 'POST',
        headers,
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (!json.success) throw new Error('copilot upsert 失败');
      const convId: string = json.data.conversation_id;
      conversationIdRef.current = convId;
      const lsKey = getPilotConvKey();
      if (lsKey) localStorage.setItem(lsKey, convId);
      return convId;
    }

    // 非报表上下文（推送任务等）：懒创建新对话
    const systemPrompt = buildSystemPrompt(contextType, contextName, contextSpec ?? null);
    const body: Record<string, any> = {
      title: `Co-pilot · ${contextName}`,
      system_prompt: systemPrompt,
    };
    if (selectedModel) body.model_key = selectedModel;

    const res = await conversationApi.createConversation(body);
    if (!res.success) throw new Error('创建对话失败');
    const convId: string = res.data.id;
    conversationIdRef.current = convId;
    return convId;
  };

  const handleModelChange = async (modelKey: string) => {
    setSelectedModel(modelKey);
    // 若对话已创建，同步更新 current_model
    if (conversationIdRef.current) {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
      fetch(`${API_BASE_URL}/conversations/${conversationIdRef.current}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ model_key: modelKey }),
      }).catch(() => {/* 非关键，静默失败 */});
    }
  };

  /**
   * AI 修改 spec 后，将已固定报表的文件信息挂载到最后一条 assistant 消息，
   * 在 Pilot 对话中展示"生成的文件"卡片（与 Chat 页面保持一致）。
   *
   * 文件信息优先从 contextSpec（spec-meta 返回）获取；
   * 若 contextSpec 未加载，则使用 contextRefreshToken 作为回退 token，
   * 以 contextName 推算文件名，仍能显示文件卡片和预览按钮。
   */
  const attachFileCardToLastMessage = useCallback(
    (targetMsgId: string) => {
      if (!contextId) return;

      const filePath = contextSpec?.report_file_path ?? '';
      const fileName = filePath
        ? filePath.split('/').pop()!
        : contextName.replace(/\.html$/i, '') + '.html';
      const refreshToken = contextSpec?.refresh_token ?? contextRefreshToken ?? '';
      const docType =
        contextSpec?.doc_type ?? (contextType === 'document' ? 'document' : 'dashboard');

      const fileInfo: FileInfo = {
        path: filePath,
        name: fileName,
        size: 0,
        mime_type: 'text/html',
        is_report: true,
        doc_type: docType as 'dashboard' | 'document',
        pinned_report_id: contextId,   // Pilot 中修改的都是已固定报表，contextId 即 reportId
        refresh_token: refreshToken,
      };

      setMessages((prev) =>
        prev.map((m) => (m.id === targetMsgId ? { ...m, files: [fileInfo] } : m)),
      );
    },
    [contextId, contextSpec, contextRefreshToken, contextName, contextType],
  );

  const handleSend = async () => {
    const content = inputValue.trim();
    if (!content || sending) return;

    setInputValue('');

    const userId = `u_${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: userId, role: 'user', content, created_at: new Date().toISOString() },
    ]);

    const asstId = `a_${Date.now() + 1}`;
    currentAsstIdRef.current = asstId;
    setMessages((prev) => [
      ...prev,
      { id: asstId, role: 'assistant', content: '', created_at: new Date().toISOString() },
    ]);

    setSending(true);

    try {
      const convId = await ensureConversation();

      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;

      const response = await fetch(`${API_BASE_URL}/conversations/${convId}/messages`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ content, stream: true }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error('No reader');

      let specUpdated = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'content') {
              appendToLastAssistant(data.data ?? '');
            } else if (data.type === 'tool_result') {
              const toolName: string = data.data?.name ?? '';
              const resultStr =
                typeof data.data === 'string' ? data.data : JSON.stringify(data.data ?? '');
              // 检测报表更新：
              // 1) MCP 工具名精确匹配（最可靠）
              // 2) 响应体含 spec_updated:true（B3 注入，覆盖 /spec 与 /charts/ 两条路径）
              // 3) 关键字兜底（兼容历史）
              if (
                toolName === 'report__update_spec' ||
                toolName === 'report__update_single_chart' ||
                resultStr.includes('"spec_updated":true') ||
                resultStr.includes('"spec_updated": true') ||
                resultStr.includes('报表已更新') ||
                resultStr.includes('/spec')
              ) {
                specUpdated = true;
              }
            } else if (data.type === 'error') {
              appendToLastAssistant(`\n\n> ⚠️ ${data.data}`);
            }
          } catch {
            // malformed SSE line — ignore
          }
        }
      }

      if (specUpdated) {
        onSpecUpdated?.();
        // 将文件卡片挂载到刚完成的 assistant 消息
        attachFileCardToLastMessage(asstId);
      }
    } catch (err: any) {
      antMessage.error('发送失败: ' + (err.message ?? err));
      setMessages((prev) => prev.filter((m) => m.id !== currentAsstIdRef.current));
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const contextLabel =
    contextType === 'schedule' ? '推送任务' : contextType === 'document' ? '报告' : '报表';

  /** contextSpec 是否已加载（决定是否显示固定按钮） */
  const hasSpec = !!(contextSpec && Object.keys(contextSpec).length > 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Context bar + model selector */}
      <div
        style={{
          padding: '7px 12px',
          background: '#f6ffed',
          borderBottom: '1px solid #b7eb8f',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          flexShrink: 0,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 12,
            color: '#389e0d',
            flex: 1,
            minWidth: 0,
          }}
        >
          <span style={{ flexShrink: 0 }}>当前{contextLabel}：</span>
          <strong
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {contextName}
          </strong>
        </div>
        <ModelSelectorMini
          value={selectedModel}
          onChange={handleModelChange}
          accessToken={accessToken}
        />
      </div>

      {/* Message list */}
      <div style={{ flex: 1, overflow: 'auto', padding: '14px 12px' }}>
        {messages.length === 0 ? (
          initializing ? (
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 60 }}>
              <Spin tip="加载对话中…" />
            </div>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="向 AI 助手发送消息开始对话"
              style={{ marginTop: 40 }}
            />
          )
        ) : (
          messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              onPreviewFile={setPreviewFile}
              hasSpec={hasSpec}
            />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div
        style={{
          padding: '10px 12px',
          borderTop: '1px solid #f0f0f0',
          background: '#fff',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行…"
            autoSize={{ minRows: 1, maxRows: 5 }}
            disabled={sending}
            style={{ flex: 1, fontSize: 13 }}
          />
          <Tooltip title={sending ? '生成中…' : '发送 (Enter)'}>
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              loading={sending}
              disabled={!inputValue.trim()}
              size="middle"
            />
          </Tooltip>
        </div>
        <div style={{ fontSize: 11, color: '#bbb', marginTop: 5, textAlign: 'center' }}>
          AI 可直接调用 API 修改{contextLabel}配置
        </div>
      </div>

      {/* 报告预览弹窗（由 PilotFilesDisplay 中预览按钮触发） */}
      {previewFile && (
        <ReportPreviewModal
          open
          onClose={() => setPreviewFile(null)}
          reportId={previewFile.pinned_report_id}
          refreshToken={previewFile.refresh_token}
          fileName={previewFile.name}
          // Pilot 内预览不嵌套 Pilot，避免双重 Pilot 体验混乱
        />
      )}
    </div>
  );
};

// ── DataCenterCopilot (Drawer wrapper) ───────────────────────────────────────

const DataCenterCopilot: React.FC<DataCenterCopilotProps> = ({
  open,
  onClose,
  contextType,
  contextId,
  contextName,
  contextSpec,
  onSpecUpdated,
  contextRefreshToken,
}) => {
  const contextLabel =
    contextType === 'schedule' ? '推送任务' : contextType === 'document' ? '报告' : '报表';

  return (
    <Drawer
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <RobotOutlined style={{ color: '#52c41a' }} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>AI 助手</span>
          <Tag color="green" style={{ marginLeft: 4, fontSize: 11 }}>
            {contextLabel}
          </Tag>
        </div>
      }
      placement="right"
      width={380}
      open={open}
      onClose={onClose}
      mask={false}
      style={{ position: 'absolute' }}
      styles={{
        body: { padding: 0, display: 'flex', flexDirection: 'column', height: '100%' },
        header: { padding: '12px 16px', borderBottom: '1px solid #f0f0f0' },
      }}
      closeIcon={<CloseOutlined style={{ fontSize: 12 }} />}
    >
      <DataCenterCopilotContent
        open={open}
        contextType={contextType}
        contextId={contextId}
        contextName={contextName}
        contextSpec={contextSpec}
        onSpecUpdated={onSpecUpdated}
        contextRefreshToken={contextRefreshToken}
      />
    </Drawer>
  );
};

export default DataCenterCopilot;
