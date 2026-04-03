import React, { useState, useRef, useEffect } from 'react';
import { Input, Button, Tag, Tooltip, message as antMessage } from 'antd';
import { SendOutlined, PaperClipOutlined, CloseOutlined, FileOutlined, FilePdfOutlined, FileImageOutlined } from '@ant-design/icons';

const { TextArea } = Input;

export interface AttachmentItem {
  name: string;
  mime_type: string;
  size: number;
  data: string; // base64
}

interface ChatInputProps {
  onSend: (content: string, attachments?: AttachmentItem[]) => void;
  disabled?: boolean;
  readOnly?: boolean;
  placeholder?: string;
}

const MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024; // 20MB
const ALLOWED_MIME_TYPES = new Set([
  'image/jpeg', 'image/png', 'image/gif', 'image/webp',
  'application/pdf',
  'text/plain', 'text/csv', 'text/markdown',
  'application/json',
]);

// Extension → MIME fallback for systems that don't report file.type
const EXT_MIME_MAP: Record<string, string> = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg',
  png: 'image/png', gif: 'image/gif', webp: 'image/webp',
  pdf: 'application/pdf',
  txt: 'text/plain', csv: 'text/csv',
  md: 'text/markdown', markdown: 'text/markdown',
  json: 'application/json',
};

function inferMimeType(file: File): string {
  if (file.type) return file.type;
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  return EXT_MIME_MAP[ext] ?? '';
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Strip the "data:mime/type;base64," prefix
      resolve(result.split(',')[1] || '');
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function getFileIcon(mimeType: string) {
  if (mimeType.startsWith('image/')) return <FileImageOutlined />;
  if (mimeType === 'application/pdf') return <FilePdfOutlined />;
  return <FileOutlined />;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

const ChatInput: React.FC<ChatInputProps> = ({
  onSend,
  disabled = false,
  readOnly = false,
  placeholder = '输入消息...',
}) => {
  const [content, setContent] = useState('');
  const [attachments, setAttachments] = useState<AttachmentItem[]>([]);
  const textAreaRef = useRef<any>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!disabled) {
      textAreaRef.current?.focus();
    }
  }, [disabled]);

  const handleSend = () => {
    const trimmedContent = content.trim();
    if ((!trimmedContent && attachments.length === 0) || disabled) return;

    onSend(trimmedContent, attachments.length > 0 ? attachments : undefined);
    setContent('');
    setAttachments([]);

    setTimeout(() => {
      textAreaRef.current?.focus();
    }, 100);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      handleSend();
    }
  };

  const processFile = async (file: File) => {
    const mimeType = inferMimeType(file);
    if (!ALLOWED_MIME_TYPES.has(mimeType)) {
      antMessage.error(`不支持的文件类型: ${file.name}（支持图片/PDF/文本/CSV/JSON）`);
      return;
    }
    if (file.size > MAX_ATTACHMENT_SIZE) {
      antMessage.error(`文件过大: ${file.name}（最大 20MB）`);
      return;
    }
    try {
      const data = await fileToBase64(file);
      setAttachments(prev => [...prev, {
        name: file.name || '粘贴图片',
        mime_type: mimeType,
        size: file.size,
        data,
      }]);
    } catch (e) {
      console.error('Failed to read file:', e);
      antMessage.error(`读取文件失败: ${file.name}`);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    files.forEach(processFile);
    // Reset input so same file can be re-added
    e.target.value = '';
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items);
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) {
          e.preventDefault();
          processFile(file);
        }
      }
    }
  };

  const removeAttachment = (index: number) => {
    setAttachments(prev => prev.filter((_, i) => i !== index));
  };

  const canSend = (content.trim().length > 0 || attachments.length > 0) && !disabled && !readOnly;

  if (readOnly) {
    return (
      <div
        style={{
          padding: '10px 16px',
          background: '#fffbe6',
          border: '1px solid #ffe58f',
          borderRadius: 6,
          fontSize: 13,
          color: '#ad6800',
          textAlign: 'center',
        }}
      >
        👁 仅查看模式 — 当前对话属于其他用户
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {attachments.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {attachments.map((att, idx) => (
            <Tag
              key={idx}
              icon={getFileIcon(att.mime_type)}
              closable
              onClose={() => removeAttachment(idx)}
              style={{ display: 'flex', alignItems: 'center', padding: '2px 8px' }}
            >
              <Tooltip title={`${att.name} (${formatSize(att.size)})`}>
                <span style={{ maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'middle' }}>
                  {att.name}
                </span>
              </Tooltip>
            </Tag>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-end' }}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.txt,.csv,.md,.json"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />
        <Tooltip title="上传附件 (图片/PDF/文本)">
          <Button
            icon={<PaperClipOutlined />}
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
            size="large"
            style={{ height: 40, flexShrink: 0 }}
          />
        </Tooltip>
        <TextArea
          ref={textAreaRef}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={placeholder}
          autoSize={{ minRows: 1, maxRows: 6 }}
          disabled={disabled}
          style={{ resize: 'none', fontSize: 14 }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={handleSend}
          disabled={!canSend}
          size="large"
          style={{ height: 40, paddingLeft: 20, paddingRight: 20 }}
        >
          发送
        </Button>
      </div>
    </div>
  );
};

export default ChatInput;
