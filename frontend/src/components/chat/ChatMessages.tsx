import React, { useEffect, useRef, useState } from 'react';
import { Avatar, Button, Empty, Spin, Tag, Tooltip, message as antMessage } from 'antd';
import { UserOutlined, RobotOutlined, ReloadOutlined, FileOutlined, FilePdfOutlined, FileImageOutlined, DownloadOutlined, FileTextOutlined, FileExcelOutlined, FileZipOutlined, BarChartOutlined, EyeOutlined, PushpinOutlined, CheckCircleOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import type { Message, AgentEvent, AgentInfo, LLMConfig, FileInfo } from '../../store/useChatStore';
import { useChatStore } from '../../store/useChatStore';
import ThoughtProcess from './ThoughtProcess';
import { AgentBadge } from './AgentBadge';
import ContinuationCard from './ContinuationCard';
import { fileApi, reportApi } from '../../services/chatApi';
import ReportPreviewModal from './ReportPreviewModal';

interface ChatMessagesProps {
  messages: Message[];
  loading?: boolean;
  onRegenerate?: () => void;
  /** 推理过程事件，按 messageId 分组 */
  messageThoughts?: Record<string, AgentEvent[]>;
  /** Agent 信息，按 messageId 分组 */
  messageAgentInfo?: Record<string, AgentInfo>;
}

// ── 文件下载卡片辅助 ────────────────────────────────────────────────────────

const _formatFileSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const _getFileIcon = (mimeType: string, name: string) => {
  if (mimeType.startsWith('image/')) return <FileImageOutlined style={{ color: '#52c41a' }} />;
  if (mimeType.includes('pdf')) return <FilePdfOutlined style={{ color: '#ff4d4f' }} />;
  if (mimeType.includes('excel') || mimeType.includes('spreadsheet') || name.endsWith('.xlsx') || name.endsWith('.xls'))
    return <FileExcelOutlined style={{ color: '#52c41a' }} />;
  if (mimeType.includes('zip') || mimeType.includes('gzip'))
    return <FileZipOutlined style={{ color: '#faad14' }} />;
  if (mimeType.startsWith('text/') || mimeType.includes('json') || mimeType.includes('sql'))
    return <FileTextOutlined style={{ color: '#1677ff' }} />;
  return <FileOutlined style={{ color: '#8c8c8c' }} />;
};

interface FileDownloadCardsProps {
  files: FileInfo[];
  messageId: string;
  conversationId?: string;
}

const FileDownloadCards: React.FC<FileDownloadCardsProps> = ({ files, messageId, conversationId }) => {
  const [downloading, setDownloading] = useState<string | null>(null);
  const [pinning, setPinning] = useState<string | null>(null);
  const [previewFile, setPreviewFile] = useState<FileInfo | null>(null);
  const markFilePinned = useChatStore((s) => s.markFilePinned);

  const handleDownload = async (file: FileInfo) => {
    setDownloading(file.path);
    try {
      await fileApi.downloadFile(file.path, file.name);
    } catch {
      antMessage.error(`下载失败: ${file.name}`);
    } finally {
      setDownloading(null);
    }
  };

  const handlePin = async (file: FileInfo) => {
    if (pinning === file.path) return;
    setPinning(file.path);
    try {
      const docType = file.doc_type ?? 'dashboard';
      const result = await reportApi.pinReport({
        file_path: file.path,
        doc_type: docType,
        conversation_id: conversationId,
        message_id: messageId,
      });
      markFilePinned(messageId, file.path, result.report_id, result.refresh_token);
      const label = docType === 'document' ? '报告' : '报表';
      antMessage.success(`已生成固定${label}，可在数据管理中心查看`);
    } catch (e: any) {
      antMessage.error(`固定失败: ${e?.response?.data?.detail ?? e?.message ?? '未知错误'}`);
    } finally {
      setPinning(null);
    }
  };

  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 2 }}>
        📎 生成的文件（点击下载）
      </div>
      {files.map((file) => {
        const isReport = file.is_report === true;
        const isPinned = !!file.pinned_report_id;
        const docType = file.doc_type ?? 'dashboard';
        const docLabel = docType === 'document' ? '报告' : '报表';
        return (
          <div
            key={file.path}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '8px 12px',
              background: isReport ? '#f0f7ff' : '#f6f8fa',
              borderRadius: 8,
              border: isReport ? '1px solid #91caff' : '1px solid #e8e8e8',
            }}
          >
            <span style={{ fontSize: 18 }}>
              {isReport
                ? <BarChartOutlined style={{ color: '#1677ff' }} />
                : _getFileIcon(file.mime_type, file.name)}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {file.name}
                </div>
                {isReport && (
                  <Tag color={docType === 'document' ? 'purple' : 'blue'} style={{ fontSize: 11, margin: 0 }}>
                    {docType === 'document' ? '分析报告' : '交互报表'}
                  </Tag>
                )}
                {isPinned && (
                  <Tag color="green" style={{ fontSize: 11, margin: 0 }}>已固定</Tag>
                )}
              </div>
              <div style={{ fontSize: 11, color: '#999' }}>
                {_formatFileSize(file.size)}
                {isReport && ' · 含图表 · 可刷新数据'}
              </div>
            </div>
            {/* 预览按钮（报告） */}
            {isReport && (
              <Button
                size="small"
                type="primary"
                icon={<EyeOutlined />}
                onClick={() => setPreviewFile(file)}
              >
                预览
              </Button>
            )}
            {/* 下载按钮 */}
            <Button
              size="small"
              icon={<DownloadOutlined />}
              loading={downloading === file.path}
              onClick={() => handleDownload(file)}
            >
              下载
            </Button>
            {/* 固定按钮（仅 HTML 报告） */}
            {isReport && (
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
                <Tooltip title={`点击将此${docLabel}固定到数据管理中心`}>
                  <Button
                    size="small"
                    icon={<PushpinOutlined />}
                    loading={pinning === file.path}
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

      {/* 报告预览弹窗 */}
      {previewFile && (
        <ReportPreviewModal
          open={!!previewFile}
          onClose={() => setPreviewFile(null)}
          reportId={previewFile.pinned_report_id}
          refreshToken={previewFile.refresh_token}
          filePath={previewFile.path}
          fileName={previewFile.name}
          pilotContext={previewFile.pinned_report_id ? {
            contextType: (previewFile.doc_type ?? 'dashboard') as 'dashboard' | 'document',
            contextId: previewFile.pinned_report_id,
            contextName: previewFile.name,
          } : undefined}
        />
      )}
    </div>
  );
};

// ── ChatMessages ─────────────────────────────────────────────────────────────

const ChatMessages: React.FC<ChatMessagesProps> = ({
  messages,
  loading = false,
  onRegenerate,
  messageThoughts = {},
  messageAgentInfo = {},
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const llmConfigs = useChatStore((s) => s.llmConfigs);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // 根据 message.model（model_key）查找配置，返回展示标签和 tooltip
  const getModelLabel = (modelKey?: string): { label: string; tooltip: string } | null => {
    if (!modelKey) return null;
    const cfg: LLMConfig | undefined = llmConfigs.find((c) => c.model_key === modelKey);
    if (!cfg) return { label: modelKey, tooltip: modelKey };
    const spec = cfg.default_model || '';
    const label = spec ? `${cfg.model_name} · ${spec}` : cfg.model_name;
    return { label, tooltip: `模型: ${cfg.model_name}${spec ? ` (${spec})` : ''}` };
  };

  if (messages.length === 0 && !loading) {
    return (
      <div
        style={{
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Empty
          description={
            <div>
              <div style={{ fontSize: 16, marginBottom: 8 }}>开始新对话</div>
              <div style={{ color: '#999', fontSize: 14 }}>
                输入你的问题,我会帮你解答
              </div>
            </div>
          }
        />
      </div>
    );
  }

  return (
    <div
      style={{
        height: '100%',
        overflowY: 'auto',
        padding: '24px',
      }}
    >
      {messages.map((message, index) => {
        // continuation 角色：Agent 自动续接提示，渲染为横幅卡片，不占用消息气泡
        if (message.role === 'continuation') {
          return <ContinuationCard key={message.id} message={message} />;
        }

        const isUser = message.role === 'user';
        const isLastMessage = index === messages.length - 1;
        const agentInfo = messageAgentInfo[message.id];
        const agentLabel = agentInfo?.agent_label ?? 'AI助手';

        return (
          <div
            key={message.id}
            style={{
              marginBottom: 24,
              display: 'flex',
              gap: '12px',
            }}
          >
            {/* 头像 */}
            <Avatar
              size={36}
              icon={isUser ? <UserOutlined /> : <RobotOutlined />}
              style={{
                backgroundColor: isUser ? '#1890ff' : '#52c41a',
                flexShrink: 0,
              }}
            />

            {/* 消息内容 */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {/* 用户名、时间、模型标签 */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  marginBottom: '6px',
                  flexWrap: 'wrap',
                }}
              >
                <span style={{ fontWeight: 500, fontSize: 14 }}>
                  {isUser ? '你' : agentLabel}
                </span>
                <span style={{ fontSize: 12, color: '#999' }}>
                  {formatTime(message.created_at)}
                </span>
                {/* 仅助手消息展示模型标签 */}
                {!isUser && (() => {
                  const modelInfo = getModelLabel(message.model);
                  return modelInfo ? (
                    <Tooltip title={modelInfo.tooltip}>
                      <span
                        style={{
                          fontSize: 11,
                          color: '#8c8c8c',
                          background: '#f5f5f5',
                          border: '1px solid #e8e8e8',
                          borderRadius: 4,
                          padding: '1px 6px',
                          cursor: 'default',
                          userSelect: 'none',
                        }}
                      >
                        {modelInfo.label}
                      </span>
                    </Tooltip>
                  ) : null;
                })()}
              </div>

              {/* Agent 徽章 + 技能标签（仅助手消息） */}
              {!isUser && agentInfo && (
                <AgentBadge info={agentInfo} />
              )}

              {/* 推理过程（仅助手消息，且有事件时展示） */}
              {!isUser && messageThoughts[message.id]?.length > 0 && (
                <ThoughtProcess
                  events={messageThoughts[message.id]}
                  agentLabel={agentInfo?.agent_label}
                />
              )}

              {/* 消息文本 */}
              <div
                style={{
                  padding: '12px 16px',
                  borderRadius: '8px',
                  background: isUser ? '#f0f0f0' : '#fff',
                  border: isUser ? 'none' : '1px solid #f0f0f0',
                  fontSize: 14,
                  lineHeight: 1.6,
                }}
              >
                {isUser ? (
                  <div>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{message.content}</div>
                    {(() => {
                      const atts = (message as any).extra_metadata?.attachments;
                      if (!atts || atts.length === 0) return null;
                      return (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                          {atts.map((att: any, idx: number) => {
                            const icon = att.mime_type?.startsWith('image/')
                              ? <FileImageOutlined />
                              : att.mime_type === 'application/pdf'
                              ? <FilePdfOutlined />
                              : <FileOutlined />;
                            const kb = att.size ? `${(att.size / 1024).toFixed(1)}KB` : '';
                            return (
                              <Tag key={idx} icon={icon} style={{ fontSize: 12 }}>
                                {att.name}{kb ? ` (${kb})` : ''}
                              </Tag>
                            );
                          })}
                        </div>
                      );
                    })()}
                  </div>
                ) : (
                  <ReactMarkdown
                    components={{
                      code: ({ node, inline, className, children, ...props }: any) => {
                        return inline ? (
                          <code
                            style={{
                              background: '#f5f5f5',
                              padding: '2px 6px',
                              borderRadius: '3px',
                              fontSize: '0.9em',
                            }}
                            {...props}
                          >
                            {children}
                          </code>
                        ) : (
                          <pre
                            style={{
                              background: '#f5f5f5',
                              padding: '12px',
                              borderRadius: '6px',
                              overflow: 'auto',
                              fontSize: '0.9em',
                            }}
                          >
                            <code className={className} {...props}>
                              {children}
                            </code>
                          </pre>
                        );
                      },
                    }}
                  >
                    {message.content || '...'}
                  </ReactMarkdown>
                )}
              </div>

              {/* 文件下载卡片（仅助手消息，有文件时展示） */}
              {!isUser && message.files_written && message.files_written.length > 0 && (
                <FileDownloadCards
                  files={message.files_written}
                  messageId={message.id}
                  conversationId={message.conversation_id}
                />
              )}

              {/* 操作按钮(仅最后一条助手消息) */}
              {!isUser && isLastMessage && !loading && message.content && (
                <div style={{ marginTop: '8px' }}>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={onRegenerate}
                  >
                    重新生成
                  </Button>
                </div>
              )}

              {/* Token统计 */}
              {!isUser && message.total_tokens && message.total_tokens > 0 && (
                <div
                  style={{
                    marginTop: '8px',
                    fontSize: 12,
                    color: '#999',
                  }}
                >
                  用量: {message.total_tokens} tokens (输入: {message.prompt_tokens}, 输出: {message.completion_tokens})
                </div>
              )}
            </div>
          </div>
        );
      })}

      {/* 加载中 */}
      {loading && (
        <div
          style={{
            display: 'flex',
            gap: '12px',
            marginBottom: 24,
          }}
        >
          <Avatar
            size={36}
            icon={<RobotOutlined />}
            style={{
              backgroundColor: '#52c41a',
              flexShrink: 0,
            }}
          />
          <div>
            <div
              style={{
                fontWeight: 500,
                fontSize: 14,
                marginBottom: 8,
              }}
            >
              AI助手
            </div>
            <div
              style={{
                padding: '12px 16px',
                borderRadius: '8px',
                background: '#fff',
                border: '1px solid #f0f0f0',
              }}
            >
              <Spin size="small" />
              <span style={{ marginLeft: 8, color: '#999' }}>正在思考...</span>
            </div>
          </div>
        </div>
      )}

      <div ref={messagesEndRef} />
    </div>
  );
};

export default ChatMessages;
