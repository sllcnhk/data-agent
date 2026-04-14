/**
 * ReportPreviewModal — 在聊天页/数据管理中心内嵌预览 HTML 报告
 *
 * 功能：
 *  - 全屏 Modal + iframe 加载报告 HTML
 *  - 顶部工具栏：刷新、导出 PDF、导出 PPTX、新标签打开、关闭
 *  - 右下角悬浮 Pilot 按钮（可选），点击展开 380px 宽 AI 对话侧边面板
 *  - 侧边面板：DataCenterCopilotContent（含模型切换），与 iframe 并排显示
 *  - 监听 iframe postMessage（来自 B2 注入的按钮）自动打开 Pilot
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button,
  Modal,
  Spin,
  Tooltip,
  message as antMessage,
  Space,
  Tag,
} from 'antd';
import {
  FilePdfOutlined,
  FileOutlined,
  GlobalOutlined,
  CloseOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/useAuthStore';
import {
  DataCenterCopilotContent,
  type CopilotContextType,
} from '../DataCenterCopilot';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1') as string;
const PILOT_WIDTH = 380;

// ── Types ─────────────────────────────────────────────────────────────────────

export interface PilotContext {
  contextType: CopilotContextType;
  contextId: string;
  contextName: string;
  contextSpec?: Record<string, any> | null;
  onSpecUpdated?: () => void;
}

interface ReportPreviewModalProps {
  open: boolean;
  onClose: () => void;
  reportId?: string;
  refreshToken?: string;
  filePath?: string;
  fileName: string;
  /** 可选：传入后在右下角显示 Pilot FAB 并支持侧边 AI 对话 */
  pilotContext?: PilotContext;
}

type ExportStatus = 'idle' | 'pending' | 'running' | 'done' | 'failed';

// ── Component ─────────────────────────────────────────────────────────────────

const ReportPreviewModal: React.FC<ReportPreviewModalProps> = ({
  open,
  onClose,
  reportId,
  refreshToken,
  filePath,
  fileName,
  pilotContext,
}) => {
  const { accessToken } = useAuthStore();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [iframeLoading, setIframeLoading] = useState(true);
  // iframeKey：AI 更新 spec 后递增，强制 iframe 重载最新生成的 HTML
  const [iframeKey, setIframeKey] = useState(0);
  const [exportStatus, setExportStatus] = useState<ExportStatus>('idle');
  const [exportFormat, setExportFormat] = useState<'pdf' | 'pptx'>('pdf');
  const [exportDownloadUrl, setExportDownloadUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [pilotOpen, setPilotOpen] = useState(false);

  const iframeSrc = (() => {
    if (reportId && refreshToken) {
      return `${API_BASE}/reports/${reportId}/html?token=${encodeURIComponent(refreshToken)}`;
    }
    if (filePath) {
      return `${API_BASE}/reports/html-serve?path=${encodeURIComponent(filePath)}${accessToken ? `&token=${encodeURIComponent(accessToken)}` : ''}`;
    }
    return '';
  })();

  useEffect(() => {
    if (open) {
      setIframeLoading(true);
      setExportStatus('idle');
      setExportDownloadUrl(null);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [open]);

  // AI 修改 spec 并重新生成 HTML 后，重载 iframe 显示最新内容
  const handleSpecUpdatedInModal = useCallback(() => {
    setIframeLoading(true);
    setIframeKey((k) => k + 1);
    // 调用外部 onSpecUpdated（刷新报表列表等）
    pilotContext?.onSpecUpdated?.();
  }, [pilotContext]);

  // 监听 iframe postMessage（B2 注入的 pilot 按钮发送的消息）
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (
        event.data?.type === 'openPilot' &&
        pilotContext &&
        event.data?.reportId === pilotContext.contextId
      ) {
        setPilotOpen(true);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [pilotContext]);

  const handleExport = useCallback(
    async (fmt: 'pdf' | 'pptx') => {
      if (!reportId) {
        antMessage.warning('该报告无 ID，无法导出（仅查看模式）');
        return;
      }
      setExportFormat(fmt);
      setExportStatus('pending');
      setExportDownloadUrl(null);

      try {
        const res = await fetch(`${API_BASE}/reports/${reportId}/export`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
          },
          body: JSON.stringify({ format: fmt }),
        });
        const json = await res.json();
        if (!json.success) throw new Error(json.detail || '导出请求失败');
        const jobId = json.data?.job_id;
        setExportStatus('running');

        pollRef.current = setInterval(async () => {
          try {
            const r = await fetch(
              `${API_BASE}/reports/${reportId}/export-status?job_id=${jobId}`,
              { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {} },
            );
            const j = await r.json();
            const st = j.data?.status;
            if (st === 'done') {
              clearInterval(pollRef.current!);
              setExportStatus('done');
              setExportDownloadUrl(j.data?.download_url || null);
            } else if (st === 'failed') {
              clearInterval(pollRef.current!);
              setExportStatus('failed');
              antMessage.error(`导出失败: ${j.data?.error || '未知错误'}`);
            }
          } catch {/* ignore poll error */}
        }, 2000);
      } catch (e: any) {
        setExportStatus('failed');
        antMessage.error(`导出请求失败: ${e.message}`);
      }
    },
    [reportId, accessToken],
  );

  const handleDownloadExport = useCallback(() => {
    if (!exportDownloadUrl) return;
    const url = exportDownloadUrl.startsWith('http')
      ? exportDownloadUrl
      : `${window.location.origin}${exportDownloadUrl}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName.replace('.html', `.${exportFormat}`);
    a.click();
  }, [exportDownloadUrl, exportFormat, fileName]);

  // iframe 加载完成：隐藏 iframe 内 server 注入的 Pilot FAB，避免与 React FAB 重叠
  const handleIframeLoad = useCallback(() => {
    setIframeLoading(false);
    iframeRef.current?.contentWindow?.postMessage({ type: 'pilotHideFab' }, '*');
  }, []);

  const openInNewTab = useCallback(() => {
    if (reportId && refreshToken && pilotContext) {
      // 打开分屏查看页：左侧报表 + 右侧 Copilot，而非裸 HTML
      const docType = pilotContext.contextType === 'document' ? 'document' : 'dashboard';
      const name = encodeURIComponent(pilotContext.contextName || fileName);
      window.open(
        `/report-view?id=${encodeURIComponent(reportId)}&token=${encodeURIComponent(refreshToken)}&doc_type=${docType}&name=${name}`,
        '_blank',
      );
    } else if (iframeSrc) {
      window.open(iframeSrc, '_blank');
    }
  }, [reportId, refreshToken, pilotContext, iframeSrc, fileName]);

  const exportStatusTag = () => {
    if (exportStatus === 'idle') return null;
    if (exportStatus === 'pending' || exportStatus === 'running') {
      return (
        <Tag icon={<LoadingOutlined />} color="processing">
          {exportFormat.toUpperCase()} 导出中…
        </Tag>
      );
    }
    if (exportStatus === 'done') {
      return (
        <Tag
          icon={<CheckCircleOutlined />}
          color="success"
          style={{ cursor: 'pointer' }}
          onClick={handleDownloadExport}
        >
          {exportFormat.toUpperCase()} 已就绪，点击下载
        </Tag>
      );
    }
    if (exportStatus === 'failed') {
      return <Tag color="error">导出失败</Tag>;
    }
    return null;
  };

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width="95vw"
      style={{ top: 20, maxWidth: 1600 }}
      styles={{
        body: {
          padding: 0,
          height: 'calc(95vh - 110px)',
          display: 'flex',
          flexDirection: 'column',
        },
      }}
      footer={null}
      closeIcon={<CloseOutlined />}
      title={
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingRight: 32,
          }}
        >
          <span style={{ fontWeight: 600, fontSize: 15 }}>📊 {fileName}</span>
          <Space size={8}>
            {exportStatusTag()}
            <Tooltip title="新标签页打开">
              <Button size="small" icon={<GlobalOutlined />} onClick={openInNewTab}>
                新窗口
              </Button>
            </Tooltip>
            <Tooltip title="导出 PDF（需后端 playwright）">
              <Button
                size="small"
                icon={<FilePdfOutlined />}
                loading={exportStatus === 'running' && exportFormat === 'pdf'}
                onClick={() => handleExport('pdf')}
              >
                PDF
              </Button>
            </Tooltip>
            <Tooltip title="导出 PPTX（需后端 playwright + python-pptx）">
              <Button
                size="small"
                icon={<FileOutlined />}
                loading={exportStatus === 'running' && exportFormat === 'pptx'}
                onClick={() => handleExport('pptx')}
              >
                PPTX
              </Button>
            </Tooltip>
          </Space>
        </div>
      }
    >
      {/* ── 主体区：iframe + pilot 面板并排 ─────────────────────────────── */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          overflow: 'hidden',
          position: 'relative',
          background: '#f4f6fa',
        }}
      >
        {/* iframe 区域 */}
        <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
          {iframeLoading && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: '#f4f6fa',
                zIndex: 10,
              }}
            >
              <Spin size="large" tip="加载报告中…" />
            </div>
          )}
          {iframeSrc && (
            <iframe
              key={iframeKey}
              ref={iframeRef}
              src={iframeSrc}
              title={fileName}
              onLoad={handleIframeLoad}
              style={{
                width: '100%',
                height: '100%',
                border: 'none',
                display: iframeLoading ? 'none' : 'block',
              }}
              sandbox="allow-scripts allow-same-origin allow-popups"
            />
          )}
        </div>

        {/* ── Pilot 侧边面板（CSS slide-in） ──────────────────────────── */}
        {pilotContext && (
          <div
            style={{
              width: pilotOpen ? PILOT_WIDTH : 0,
              minWidth: 0,
              flexShrink: 0,
              overflow: 'hidden',
              transition: 'width 0.3s ease',
              borderLeft: pilotOpen ? '1px solid #e8e8e8' : 'none',
              background: '#fff',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {/* 面板头部 */}
            <div
              style={{
                padding: '9px 12px',
                borderBottom: '1px solid #f0f0f0',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: '#fafafa',
                flexShrink: 0,
              }}
            >
              <RobotOutlined style={{ color: '#52c41a', fontSize: 14 }} />
              <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>AI 助手 Pilot</span>
              <Tooltip title="关闭面板">
                <Button
                  size="small"
                  type="text"
                  icon={<CloseOutlined style={{ fontSize: 11 }} />}
                  onClick={() => setPilotOpen(false)}
                />
              </Tooltip>
            </div>
            {/* 内容 */}
            <div
              style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}
            >
              <DataCenterCopilotContent
                open={pilotOpen}
                contextType={pilotContext.contextType}
                contextId={pilotContext.contextId}
                contextName={pilotContext.contextName}
                contextSpec={pilotContext.contextSpec}
                onSpecUpdated={handleSpecUpdatedInModal}
              />
            </div>
          </div>
        )}

        {/* ── Pilot FAB 悬浮按钮 ─────────────────────────────────────── */}
        {pilotContext && (
          <Tooltip title={pilotOpen ? '关闭 AI 助手' : 'AI 助手 Pilot'} placement="left">
            <button
              onClick={() => setPilotOpen((v) => !v)}
              style={{
                position: 'absolute',
                bottom: 20,
                right: pilotOpen ? PILOT_WIDTH + 16 : 20,
                width: 44,
                height: 44,
                borderRadius: '50%',
                background: pilotOpen ? '#ff7875' : '#52c41a',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: pilotOpen
                  ? '0 4px 12px rgba(255,120,117,0.4)'
                  : '0 4px 12px rgba(82,196,26,0.4)',
                transition: 'right 0.3s ease, background 0.2s',
                zIndex: 20,
                color: '#fff',
              }}
            >
              {pilotOpen ? (
                <CloseOutlined style={{ fontSize: 15 }} />
              ) : (
                <RobotOutlined style={{ fontSize: 18 }} />
              )}
            </button>
          </Tooltip>
        )}
      </div>
    </Modal>
  );
};

export default ReportPreviewModal;
