/**
 * ReportViewerPage — 独立标签页报表分屏查看
 *
 * URL 格式：/report-view?id=...&token=...&doc_type=dashboard|document&name=...
 *
 * 布局：
 *  - 左侧：iframe 全高展示报表 HTML（通过 refresh_token 访问，无需 JWT）
 *  - 右侧：DataCenterCopilotContent 侧边面板（380px，可滑入/出）
 *  - 右下角：React Pilot FAB 按钮切换面板
 *
 * iframe 加载完成后自动发送 {type:'pilotHideFab'} 消息隐藏
 * 报表 HTML 内注入的 server-side Pilot 按钮，避免重叠。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button, Spin, Tooltip } from 'antd';
import { CloseOutlined, RobotOutlined } from '@ant-design/icons';
import {
  DataCenterCopilotContent,
  type CopilotContextType,
} from '../components/DataCenterCopilot';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1') as string;
const PILOT_WIDTH = 380;

const ReportViewerPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const reportId  = searchParams.get('id')       || '';
  const token     = searchParams.get('token')    || '';
  const docTypeRaw = searchParams.get('doc_type') || 'dashboard';
  const name      = searchParams.get('name')     || '报表查看';

  const contextType: CopilotContextType =
    docTypeRaw === 'document' ? 'document' : 'dashboard';

  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [iframeLoading, setIframeLoading] = useState(true);
  const [pilotOpen, setPilotOpen] = useState(false);

  // ── 报表 spec（通过 refresh_token 获取，无需 JWT） ────────────────────────
  const [reportSpec, setReportSpec] = useState<Record<string, any> | null>(null);
  // iframe key：每次 spec 更新后递增，强制 iframe 重新加载最新 HTML
  const [iframeKey, setIframeKey] = useState(0);

  const iframeSrc = reportId && token
    ? `${API_BASE}/reports/${encodeURIComponent(reportId)}/html?token=${encodeURIComponent(token)}`
    : '';

  // iframe 加载完成：隐藏 iframe 内 server 注入的 Pilot FAB，避免与本页 React FAB 重叠
  const handleIframeLoad = useCallback(() => {
    setIframeLoading(false);
    iframeRef.current?.contentWindow?.postMessage({ type: 'pilotHideFab' }, '*');
  }, []);

  // 监听 iframe postMessage（iframe 内 Pilot 按钮的兜底事件）
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type === 'openPilot' && event.data?.reportId === reportId) {
        setPilotOpen(true);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [reportId]);

  // ── 拉取报表 spec（给 Pilot 提供上下文，使 AI 能理解并修改报表） ──────────
  useEffect(() => {
    if (!reportId || !token) return;
    fetch(`${API_BASE}/reports/${encodeURIComponent(reportId)}/spec-meta?token=${encodeURIComponent(token)}`)
      .then((r) => r.json())
      .then((json) => {
        if (json.success && json.data) setReportSpec(json.data);
      })
      .catch(() => {
        // 网络错误：Pilot 会显示"(暂无配置)"，功能降级但不崩溃
      });
  }, [reportId, token]);

  // ── Pilot 通知：AI 更新 spec 后刷新 iframe + 重新拉取最新 spec ───────────
  const handleSpecUpdated = useCallback(async () => {
    setIframeLoading(true);
    setIframeKey((k) => k + 1);
    if (!reportId || !token) return;
    try {
      const res = await fetch(
        `${API_BASE}/reports/${encodeURIComponent(reportId)}/spec-meta?token=${encodeURIComponent(token)}`,
      );
      const json = await res.json();
      if (json.success && json.data) setReportSpec(json.data);
    } catch {
      /* 忽略，旧 spec 仍在内存 */
    }
  }, [reportId, token]);

  // 更新页面标题
  useEffect(() => {
    document.title = name ? `${decodeURIComponent(name)} — Pilot 查看` : '报表查看';
  }, [name]);

  if (!iframeSrc) {
    return (
      <div
        style={{
          height: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#999',
          fontSize: 15,
        }}
      >
        缺少必要参数（id / token），请从报表清单重新打开。
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        overflow: 'hidden',
        background: '#f4f6fa',
        position: 'relative',
      }}
    >
      {/* ── 左侧：报表 iframe ─────────────────────────────────────────────── */}
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
            <Spin size="large" tip="加载报表中…" />
          </div>
        )}
        <iframe
          key={iframeKey}
          ref={iframeRef}
          src={iframeSrc}
          title={decodeURIComponent(name)}
          onLoad={handleIframeLoad}
          style={{
            width: '100%',
            height: '100%',
            border: 'none',
            display: iframeLoading ? 'none' : 'block',
          }}
          sandbox="allow-scripts allow-same-origin allow-popups"
        />
      </div>

      {/* ── 右侧：Pilot Copilot 侧边面板 ─────────────────────────────────── */}
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
        {/* 面板顶栏 */}
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
        {/* Copilot 内容 */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <DataCenterCopilotContent
            open={pilotOpen}
            contextType={contextType}
            contextId={reportId}
            contextName={decodeURIComponent(name)}
            contextSpec={reportSpec}
            onSpecUpdated={handleSpecUpdated}
          />
        </div>
      </div>

      {/* ── 右下角：Pilot FAB 按钮 ────────────────────────────────────────── */}
      <Tooltip title={pilotOpen ? '关闭 AI 助手' : 'AI 助手 Pilot'} placement="left">
        <button
          onClick={() => setPilotOpen((v) => !v)}
          style={{
            position: 'fixed',
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
            zIndex: 1000,
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
    </div>
  );
};

export default ReportViewerPage;
