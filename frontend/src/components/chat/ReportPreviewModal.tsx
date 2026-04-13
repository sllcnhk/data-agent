/**
 * ReportPreviewModal — 在聊天页内嵌预览 HTML 报告
 *
 * 功能：
 *  - 全屏 Modal + iframe 加载报告 HTML
 *  - 顶部工具栏：刷新、导出 PDF、导出 PPTX、新标签打开、关闭
 *  - 导出触发 /api/v1/reports/{id}/export 并轮询状态
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
  Progress,
} from 'antd';
import {
  FullscreenOutlined,
  ReloadOutlined,
  FilePdfOutlined,
  FileOutlined,
  GlobalOutlined,
  CloseOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/useAuthStore';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1') as string;

interface ReportPreviewModalProps {
  open: boolean;
  onClose: () => void;
  /** report_id（来自 POST /reports/build 返回） */
  reportId?: string;
  /** HTML 文件下载路径（customer_data/xxx/reports/xxx.html） */
  filePath: string;
  fileName: string;
}

type ExportStatus = 'idle' | 'pending' | 'running' | 'done' | 'failed';

const ReportPreviewModal: React.FC<ReportPreviewModalProps> = ({
  open,
  onClose,
  reportId,
  filePath,
  fileName,
}) => {
  const { accessToken } = useAuthStore();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [iframeLoading, setIframeLoading] = useState(true);
  const [exportStatus, setExportStatus] = useState<ExportStatus>('idle');
  const [exportFormat, setExportFormat] = useState<'pdf' | 'pptx'>('pdf');
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportDownloadUrl, setExportDownloadUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // HTML 文件通过 /files/download 代理，附带 token
  const iframeSrc = filePath
    ? `${API_BASE}/files/download?path=${encodeURIComponent(filePath)}${accessToken ? `&access_token=${accessToken}` : ''}`
    : '';

  // 重置状态
  useEffect(() => {
    if (open) {
      setIframeLoading(true);
      setExportStatus('idle');
      setExportJobId(null);
      setExportDownloadUrl(null);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [open]);

  // 导出
  const handleExport = useCallback(async (fmt: 'pdf' | 'pptx') => {
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
      setExportJobId(jobId);
      setExportStatus('running');

      // 轮询状态
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
        } catch { /* ignore poll error */ }
      }, 2000);
    } catch (e: any) {
      setExportStatus('failed');
      antMessage.error(`导出请求失败: ${e.message}`);
    }
  }, [reportId, accessToken]);

  // 下载已导出文件
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

  // 新标签页打开
  const openInNewTab = useCallback(() => {
    if (iframeSrc) window.open(iframeSrc, '_blank');
  }, [iframeSrc]);

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
      styles={{ body: { padding: 0, height: 'calc(95vh - 110px)', display: 'flex', flexDirection: 'column' } }}
      footer={null}
      closeIcon={<CloseOutlined />}
      title={
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingRight: 32 }}>
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
      {/* iframe 预览区 */}
      <div style={{ flex: 1, position: 'relative', background: '#f4f6fa' }}>
        {iframeLoading && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex',
            alignItems: 'center', justifyContent: 'center', background: '#f4f6fa', zIndex: 10,
          }}>
            <Spin size="large" tip="加载报告中…" />
          </div>
        )}
        {iframeSrc && (
          <iframe
            ref={iframeRef}
            src={iframeSrc}
            title={fileName}
            onLoad={() => setIframeLoading(false)}
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
    </Modal>
  );
};

export default ReportPreviewModal;
