/**
 * DataCenterCopilot — 数据管理中心 AI 助手
 *
 * 导出两个组件：
 *   DataCenterCopilotContent  — 纯内容（消息列表 + 输入框 + 模型选择），可内嵌任意容器
 *   DataCenterCopilot (default) — Drawer 包装版，供各列表页使用
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
import { CloseOutlined, RobotOutlined, SendOutlined, UserOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { conversationApi } from '../services/chatApi';
import { useAuthStore } from '../store/useAuthStore';
import ModelSelectorMini from './ModelSelectorMini';

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
  return `你是数据管理中心的智能助手（Co-pilot）。
当前用户正在查看${label}「${contextName}」。
${label} spec 配置：
\`\`\`json
${specJson}
\`\`\`

你可以帮助用户：
- 修改图表类型（折线图/柱状图/饼图等）
- 调整 SQL 查询和时间范围
- 添加/删除图表
- 修改主题和标题

修改${label}时调用以下接口：
PUT /api/v1/reports/${contextSpec?.id ?? '{report_id}'}/spec
Body: {"spec": <完整新 spec JSON>}

修改完成后，请告知用户「${label}已更新，请查看预览」。
请用简洁中文回复，修改前向用户简要确认改动内容。`;
}

// ── MessageBubble ─────────────────────────────────────────────────────────────

const MessageBubble: React.FC<{ msg: LocalMessage }> = ({ msg }) => {
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
      <div
        style={{
          maxWidth: '78%',
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
}) => {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [sending, setSending] = useState(false);
  const [initialized, setInitialized] = useState(false);
  const [selectedModel, setSelectedModel] = useState('');

  const conversationIdRef = useRef<string | null>(null);
  const currentAsstIdRef = useRef<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const accessToken = useAuthStore((s) => s.accessToken);

  // Reset state when context switches
  const prevContextIdRef = useRef<string>('');
  if (contextId !== prevContextIdRef.current) {
    prevContextIdRef.current = contextId;
    conversationIdRef.current = null;
    // Use function form to avoid stale closure lint warning; these are safe resets
    setMessages([]);
    setInitialized(false);
  }

  // Greeting on first open
  React.useEffect(() => {
    if (open && !initialized && contextId) {
      const label =
        contextType === 'schedule'
          ? '推送任务'
          : contextType === 'document'
            ? '报告'
            : '报表';
      setMessages([
        {
          id: 'greeting',
          role: 'assistant',
          content: `你好！我是 AI 助手。当前${label}是「**${contextName}**」。\n\n你可以告诉我需要做哪些修改，例如：\n- 调整推送时间\n- 修改图表类型\n- 调整 SQL 时间范围\n- 添加/删除图表`,
          created_at: new Date().toISOString(),
        },
      ]);
      setInitialized(true);
    }
  }, [open, initialized, contextId, contextName, contextType]);

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
              const resultStr =
                typeof data.data === 'string' ? data.data : JSON.stringify(data.data ?? '');
              if (resultStr.includes('/spec') || resultStr.includes('报表已更新')) {
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

      if (specUpdated) onSpecUpdated?.();
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
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="向 AI 助手发送消息开始对话"
            style={{ marginTop: 40 }}
          />
        ) : (
          messages.map((msg) => <MessageBubble key={msg.id} msg={msg} />)
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
      />
    </Drawer>
  );
};

export default DataCenterCopilot;
