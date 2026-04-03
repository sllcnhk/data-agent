/**
 * ApprovalModal — Human-in-the-Loop 审批弹窗
 *
 * 当 Agent 检测到高危 SQL（DROP / TRUNCATE / DELETE）时，
 * SSE 流发出 ``approval_required`` 事件，Chat.tsx 将信息存入
 * useChatStore.pendingApproval，本组件随即展示。
 *
 * 用户在 60 秒倒计时内点击：
 *   - 同意执行 → POST /api/v1/approvals/{id}/approve
 *   - 拒绝操作 → POST /api/v1/approvals/{id}/reject
 * 组件关闭后 setPendingApproval(null) 清除状态。
 */
import React, { useEffect, useState } from 'react';
import {
  Modal,
  Alert,
  Typography,
  Space,
  Tag,
  Button,
  Divider,
  Progress,
} from 'antd';
import {
  WarningOutlined,
  CheckOutlined,
  CloseOutlined,
  CodeOutlined,
  FileAddOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';
import type { PendingApproval } from '../../store/useChatStore';

const { Text, Paragraph } = Typography;

const COUNTDOWN_SECONDS = 120;
const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL || '/api/v1';

interface ApprovalModalProps {
  approval: PendingApproval | null;
  onClose: () => void;
}

const ApprovalModal: React.FC<ApprovalModalProps> = ({ approval, onClose }) => {
  const [countdown, setCountdown] = useState(COUNTDOWN_SECONDS);
  const [submitting, setSubmitting] = useState(false);

  // Reset + start countdown when a new approval arrives
  useEffect(() => {
    if (!approval) return;
    setCountdown(COUNTDOWN_SECONDS);

    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          // Auto-close without explicit reject — backend will timeout on its own
          onClose();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [approval?.approval_id]);

  if (!approval) return null;

  const handleApprove = async () => {
    setSubmitting(true);
    try {
      await fetch(`${API_BASE}/approvals/${approval.approval_id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
    } catch (e) {
      console.error('Approve request failed', e);
    } finally {
      setSubmitting(false);
      onClose();
    }
  };

  const handleReject = async () => {
    setSubmitting(true);
    try {
      await fetch(`${API_BASE}/approvals/${approval.approval_id}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: '用户拒绝执行高危操作' }),
      });
    } catch (e) {
      console.error('Reject request failed', e);
    } finally {
      setSubmitting(false);
      onClose();
    }
  };

  const progressPercent = Math.round((countdown / COUNTDOWN_SECONDS) * 100);
  const progressStatus = countdown <= 10 ? 'exception' : 'active';
  const isFileWrite = approval.approval_type === 'file_write';

  return (
    <Modal
      open={!!approval}
      title={
        <Space>
          {isFileWrite
            ? <FileAddOutlined style={{ color: '#1890ff', fontSize: 18 }} />
            : <WarningOutlined style={{ color: '#fa8c16', fontSize: 18 }} />}
          <span>{isFileWrite ? '文件写入授权' : '高危操作需要确认'}</span>
        </Space>
      }
      onCancel={handleReject}
      closable={!submitting}
      maskClosable={false}
      footer={null}
      width={600}
      styles={{
        header: { borderBottom: '1px solid #f0f0f0', paddingBottom: 12 },
        body: { padding: '20px 24px' },
      }}
    >
      {isFileWrite ? (
        /* ── File Write approval UI ── */
        <>
          <Alert
            type="info"
            showIcon
            icon={<InfoCircleOutlined />}
            message={
              approval.session_grant
                ? 'Agent 请求写入文件。授权后，本次对话将持续拥有文件写入权限，无需重复确认。'
                : 'Agent 请求写入文件，请确认是否允许。'
            }
            style={{ marginBottom: 16 }}
          />

          {/* File path */}
          {approval.path && (
            <div style={{ marginBottom: 12 }}>
              <Space style={{ marginBottom: 4 }}>
                <FileAddOutlined style={{ color: '#1890ff' }} />
                <Text strong style={{ fontSize: 13 }}>目标文件路径</Text>
              </Space>
              <div
                style={{
                  background: '#f0f5ff',
                  border: '1px solid #adc6ff',
                  borderRadius: 4,
                  padding: '8px 12px',
                  fontFamily: 'monospace',
                  fontSize: 13,
                  wordBreak: 'break-all',
                }}
              >
                {approval.path}
              </div>
            </div>
          )}

          {/* Content preview */}
          {approval.content_preview && (
            <div style={{ marginBottom: 16 }}>
              <Space style={{ marginBottom: 6 }}>
                <CodeOutlined style={{ color: '#1890ff' }} />
                <Text strong style={{ fontSize: 13 }}>内容预览（前 500 字符）</Text>
              </Space>
              <pre
                style={{
                  background: '#f6ffed',
                  border: '1px solid #b7eb8f',
                  borderRadius: 4,
                  padding: '10px 14px',
                  fontSize: 12,
                  overflowX: 'auto',
                  maxHeight: 200,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {approval.content_preview}
              </pre>
            </div>
          )}
        </>
      ) : (
        /* ── SQL approval UI ── */
        <>
          <Alert
            type="warning"
            showIcon
            icon={<WarningOutlined />}
            message="以下 SQL 操作属于高危操作，执行后可能无法恢复数据。"
            style={{ marginBottom: 16 }}
          />

          <Space style={{ marginBottom: 12 }}>
            <Text type="secondary">调用工具：</Text>
            <Tag color="orange">{approval.tool}</Tag>
          </Space>

          {(approval.warnings ?? []).length > 0 && (
            <div style={{ marginBottom: 12 }}>
              {(approval.warnings ?? []).map((w, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <WarningOutlined style={{ color: '#fa8c16', fontSize: 13 }} />
                  <Text style={{ fontSize: 13, color: '#d48806' }}>{w}</Text>
                </div>
              ))}
            </div>
          )}

          <div style={{ marginBottom: 16 }}>
            <Space style={{ marginBottom: 6 }}>
              <CodeOutlined style={{ color: '#1890ff' }} />
              <Text strong style={{ fontSize: 13 }}>SQL 内容</Text>
            </Space>
            <pre
              style={{
                background: '#fff7e6',
                border: '1px solid #ffd591',
                borderRadius: 4,
                padding: '10px 14px',
                fontSize: 13,
                overflowX: 'auto',
                maxHeight: 200,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}
            >
              {approval.sql}
            </pre>
          </div>
        </>
      )}

      <Divider style={{ margin: '12px 0' }} />

      {/* Countdown */}
      <div style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 4 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>剩余确认时间：</Text>
          <Text strong style={{ fontSize: 12, color: countdown <= 10 ? '#ff4d4f' : '#595959' }}>
            {countdown} 秒
          </Text>
        </Space>
        <Progress
          percent={progressPercent}
          status={progressStatus}
          showInfo={false}
          size="small"
          strokeColor={countdown <= 10 ? '#ff4d4f' : (isFileWrite ? '#1890ff' : '#fa8c16')}
        />
      </div>

      {/* Action buttons */}
      <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
        <Button icon={<CloseOutlined />} onClick={handleReject} disabled={submitting} danger>
          {isFileWrite ? '拒绝写入' : '拒绝操作'}
        </Button>
        <Button
          type="primary"
          icon={<CheckOutlined />}
          onClick={handleApprove}
          loading={submitting}
          style={isFileWrite
            ? { background: '#1890ff', borderColor: '#1890ff' }
            : { background: '#52c41a', borderColor: '#52c41a' }}
        >
          {isFileWrite ? '授权写入' : '同意执行'}
        </Button>
      </Space>
    </Modal>
  );
};

export default ApprovalModal;
