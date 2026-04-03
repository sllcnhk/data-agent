/**
 * ContinuationCard — Agent 自动续接提示卡片
 *
 * 当 Agent 推理轮次达到上限、生成阶段性结论后自动开启下一轮时，
 * 系统在消息流中插入一条 role='continuation' 的记录。
 * 本组件将其渲染为紧凑的横幅卡片（非用户/助手气泡），
 * 清晰标注"Agent 内部行为"而非用户操作。
 *
 * 参考：LangGraph checkpoint、Claude Projects 上下文衔接标记
 */
import React, { useState } from 'react';
import { Collapse, Tag, Typography, Space } from 'antd';
import { SyncOutlined, UnorderedListOutlined, FileTextOutlined } from '@ant-design/icons';
import type { Message } from '../../store/useChatStore';

const { Text, Paragraph } = Typography;

interface ContinuationCardProps {
  message: Message;
}

const ContinuationCard: React.FC<ContinuationCardProps> = ({ message }) => {
  const [open, setOpen] = useState(false);

  const meta = message.extra_metadata || {};
  const round: number = meta.continuation_round ?? 1;
  const maxRounds: number = meta.max_rounds ?? 3;
  const pendingTasks: string[] = meta.pending_tasks ?? [];
  const conclusions: string = meta.conclusions ?? '';

  // 从 content 中提取结构信息（兼容旧格式：content 包含完整续接指令文本）
  // 若 extra_metadata 中已有结构化数据，优先使用；否则尝试从 content 解析
  const hasTasks = pendingTasks.length > 0;
  const hasConclusions = conclusions.length > 0;

  // 若 extra_metadata 中没有 pending_tasks，从 content 中降级提取（兼容旧数据）
  const displayConclusions = hasConclusions
    ? conclusions
    : (() => {
        const match = message.content.match(/上一轮结论摘要：\n([\s\S]*?)\n\n待完成任务/);
        return match ? match[1].trim() : '';
      })();

  const displayTasks = hasTasks
    ? pendingTasks
    : (() => {
        const match = message.content.match(/待完成任务：\n([\s\S]*?)$/);
        if (!match) return [];
        return match[1]
          .split('\n')
          .map((l) => l.replace(/^[-•]\s*/, '').trim())
          .filter(Boolean);
      })();

  const hasDetails = displayConclusions || displayTasks.length > 0;

  return (
    <div
      style={{
        margin: '4px 0 4px 48px', // 左对齐到消息内容区域，跳过头像宽度
        borderRadius: 6,
        border: '1px dashed #d9d9d9',
        background: '#fafafa',
        overflow: 'hidden',
      }}
    >
      {/* 主横幅行 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          cursor: hasDetails ? 'pointer' : 'default',
        }}
        onClick={() => hasDetails && setOpen(!open)}
      >
        <SyncOutlined
          spin={false}
          style={{ color: '#8c8c8c', fontSize: 12 }}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          Agent 自动续接
        </Text>
        <Tag
          color="default"
          style={{ fontSize: 11, padding: '0 5px', margin: 0, lineHeight: '18px' }}
        >
          {round}/{maxRounds}
        </Tag>
        {hasDetails && (
          <Text type="secondary" style={{ fontSize: 11, marginLeft: 'auto' }}>
            {open ? '收起' : '展开详情'}
          </Text>
        )}
      </div>

      {/* 可展开的详情区 */}
      {hasDetails && open && (
        <div
          style={{
            padding: '8px 12px 10px',
            borderTop: '1px dashed #f0f0f0',
            background: '#fff',
          }}
        >
          {displayConclusions && (
            <div style={{ marginBottom: displayTasks.length > 0 ? 8 : 0 }}>
              <Space size={4} style={{ marginBottom: 4 }}>
                <FileTextOutlined style={{ color: '#1890ff', fontSize: 12 }} />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  上一轮结论摘要
                </Text>
              </Space>
              <Paragraph
                style={{
                  margin: 0,
                  fontSize: 12,
                  color: '#595959',
                  whiteSpace: 'pre-wrap',
                  background: '#f0f5ff',
                  border: '1px solid #d6e4ff',
                  borderRadius: 4,
                  padding: '6px 8px',
                }}
              >
                {displayConclusions}
              </Paragraph>
            </div>
          )}

          {displayTasks.length > 0 && (
            <div>
              <Space size={4} style={{ marginBottom: 4 }}>
                <UnorderedListOutlined style={{ color: '#fa8c16', fontSize: 12 }} />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  待完成任务（{displayTasks.length} 项）
                </Text>
              </Space>
              <ul
                style={{
                  margin: 0,
                  paddingLeft: 16,
                  fontSize: 12,
                  color: '#595959',
                }}
              >
                {displayTasks.map((task, i) => (
                  <li key={i} style={{ lineHeight: '20px' }}>
                    {task}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ContinuationCard;
