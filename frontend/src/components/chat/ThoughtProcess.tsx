/**
 * ThoughtProcess — 推理过程展示组件
 *
 * 收集并展示 Agent 执行过程中产生的 thinking / tool_call / tool_result 事件，
 * 以可折叠面板形式嵌入在助手消息泡泡之上。
 *
 * 每种事件用不同图标 + 颜色区分：
 *   thinking   → 脑图图标（蓝色）
 *   tool_call  → 工具图标（橙色）
 *   tool_result → 检查图标（绿色 / 红色）
 */
import React, { useState } from 'react';
import { Collapse, Tag, Typography, Space, Badge, Alert } from 'antd';
import {
  BulbOutlined,
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CaretRightOutlined,
  ThunderboltOutlined,
  PushpinOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { AgentEvent, SkillMatchInfo, SkillMatchedSkill } from '../../store/useChatStore';

const { Text, Paragraph } = Typography;

interface ThoughtProcessProps {
  events: AgentEvent[];
  /** 可选：Agent 名称，显示在推理面板标题中（如"数据分析师 · 推理过程"）*/
  agentLabel?: string;
}

// ──────────────────────────────────────────────────────────
// Per-event rendering helpers
// ──────────────────────────────────────────────────────────

function renderThinking(data: any): React.ReactNode {
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  return (
    <Paragraph
      style={{
        margin: 0,
        fontSize: 13,
        color: '#595959',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      {text}
    </Paragraph>
  );
}

function renderToolCall(data: any): React.ReactNode {
  const name: string = data?.name || '未知工具';
  const input = data?.input ?? {};
  return (
    <div>
      <Space style={{ marginBottom: 6 }}>
        <Tag color="orange">{name}</Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          调用参数
        </Text>
      </Space>
      <pre
        style={{
          margin: 0,
          padding: '8px 12px',
          background: '#fff7e6',
          border: '1px solid #ffd591',
          borderRadius: 4,
          fontSize: 12,
          overflowX: 'auto',
          maxHeight: 200,
        }}
      >
        {JSON.stringify(input, null, 2)}
      </pre>
    </div>
  );
}

function renderToolResult(data: any, metadata: Record<string, any> = {}): React.ReactNode {
  const success: boolean = metadata?.success ?? data?.success ?? true;
  const name: string = data?.name || '工具';
  const result = data?.result ?? data;
  const resultStr =
    typeof result === 'string'
      ? result
      : JSON.stringify(result, null, 2);

  return (
    <div>
      <Space style={{ marginBottom: 6 }}>
        {success ? (
          <CheckCircleOutlined style={{ color: '#52c41a' }} />
        ) : (
          <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
        )}
        <Tag color={success ? 'green' : 'red'}>{name}</Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {success ? '执行成功' : '执行失败'}
        </Text>
      </Space>
      <pre
        style={{
          margin: 0,
          padding: '8px 12px',
          background: success ? '#f6ffed' : '#fff2f0',
          border: `1px solid ${success ? '#b7eb8f' : '#ffccc7'}`,
          borderRadius: 4,
          fontSize: 12,
          overflowX: 'auto',
          maxHeight: 200,
        }}
      >
        {resultStr.length > 2000 ? resultStr.slice(0, 2000) + '\n…（内容已截断）' : resultStr}
      </pre>
    </div>
  );
}

function renderSkillMatched(data: SkillMatchInfo): React.ReactNode {
  if (!data) return null;
  const modeColor: Record<string, string> = {
    keyword: 'blue',
    hybrid: 'purple',
    llm: 'geekblue',
  };
  const methodColor = (m: string) => (m === 'keyword' ? 'blue' : 'purple');

  return (
    <div style={{ fontSize: 13 }}>
      {/* Mode badge */}
      <Space style={{ marginBottom: 8 }} wrap>
        <Tag color={modeColor[data.mode] ?? 'default'}>{data.mode} 模式</Tag>
        {data.summary_mode && (
          <Tag color="orange" icon={<WarningOutlined />}>摘要注入（超出字符上限）</Tag>
        )}
        {!data.summary_mode && data.total_chars > 0 && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            注入 {data.total_chars.toLocaleString()} 字符
          </Text>
        )}
      </Space>

      {/* Matched skills */}
      {data.matched.length > 0 ? (
        <div style={{ marginBottom: 6 }}>
          {data.matched.map((s: SkillMatchedSkill) => (
            <div
              key={s.name}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 6,
                marginBottom: 4,
                padding: '4px 8px',
                background: '#f6ffed',
                border: '1px solid #b7eb8f',
                borderRadius: 4,
              }}
            >
              <CheckCircleOutlined style={{ color: '#52c41a', marginTop: 2, flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <Space size={4} wrap>
                  <Text strong style={{ fontSize: 13 }}>{s.name}</Text>
                  <Tag color="default" style={{ fontSize: 11 }}>{s.tier}</Tag>
                  <Tag color={methodColor(s.method)} style={{ fontSize: 11 }}>{s.method}</Tag>
                </Space>
                {s.hit_triggers.length > 0 && (
                  <div style={{ marginTop: 2 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      命中: {s.hit_triggers.map(t => (
                        <Tag key={t} color="cyan" style={{ fontSize: 10, marginRight: 2, marginBottom: 0 }}>{t}</Tag>
                      ))}
                    </Text>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ marginBottom: 6, color: '#8c8c8c', fontSize: 12 }}>
          <WarningOutlined style={{ marginRight: 4 }} />
          无匹配技能 — Agent 将依赖通用能力
        </div>
      )}

      {/* Always-inject base skills */}
      {data.always_inject.length > 0 && (
        <div style={{ marginBottom: 4 }}>
          {data.always_inject.map((s) => (
            <div
              key={s.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                marginBottom: 2,
                padding: '2px 8px',
                background: '#f9f0ff',
                border: '1px solid #d3adf7',
                borderRadius: 4,
              }}
            >
              <PushpinOutlined style={{ color: '#722ed1', fontSize: 11 }} />
              <Text style={{ fontSize: 12 }}>{s.name}</Text>
              <Tag color="default" style={{ fontSize: 10 }}>{s.tier}</Tag>
              <Text type="secondary" style={{ fontSize: 10 }}>始终注入</Text>
            </div>
          ))}
        </div>
      )}

      {/* Load errors */}
      {data.load_errors.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 6, fontSize: 11 }}
          message={`${data.load_errors.length} 个技能文件格式错误，未加载`}
          description={data.load_errors.map(e => (
            <div key={e.filepath} style={{ fontSize: 10 }}>
              <Text code style={{ fontSize: 10 }}>{e.filepath.split(/[\\/]/).pop()}</Text>
              {' — '}{e.reason}
            </div>
          ))}
        />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Event item
// ──────────────────────────────────────────────────────────

function eventIcon(type: string): React.ReactNode {
  switch (type) {
    case 'thinking':
      return <BulbOutlined style={{ color: '#1890ff' }} />;
    case 'tool_call':
      return <ApiOutlined style={{ color: '#fa8c16' }} />;
    case 'tool_result':
      return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
    case 'skill_matched':
      return <ThunderboltOutlined style={{ color: '#722ed1' }} />;
    default:
      return <CaretRightOutlined />;
  }
}

function eventLabel(type: string, data: any): string {
  switch (type) {
    case 'thinking':
      return '思考中…';
    case 'tool_call':
      return `调用工具: ${data?.name || '?'}`;
    case 'tool_result':
      return `工具返回: ${data?.name || '?'}`;
    case 'skill_matched': {
      const count = data?.matched?.length ?? 0;
      const mode = data?.mode ?? '';
      if (count === 0) return `技能路由 [${mode}] — 无匹配`;
      return `技能路由 [${mode}] — 加载 ${count} 个技能`;
    }
    default:
      return type;
  }
}

// ──────────────────────────────────────────────────────────
// Main component
// ──────────────────────────────────────────────────────────

const ThoughtProcess: React.FC<ThoughtProcessProps> = ({ events, agentLabel }) => {
  const [open, setOpen] = useState(false);

  if (!events || events.length === 0) return null;

  // Count tool calls for the badge
  const toolCount = events.filter(e => e.type === 'tool_call').length;

  const items = events.map((evt, idx) => ({
    key: String(idx),
    label: (
      <Space size={6}>
        {eventIcon(evt.type)}
        <Text style={{ fontSize: 13 }}>{eventLabel(evt.type, evt.data)}</Text>
      </Space>
    ),
    children: (
      <div style={{ paddingLeft: 4 }}>
        {evt.type === 'thinking' && renderThinking(evt.data)}
        {evt.type === 'tool_call' && renderToolCall(evt.data)}
        {evt.type === 'tool_result' && renderToolResult(evt.data, evt.metadata)}
        {evt.type === 'skill_matched' && renderSkillMatched(evt.data as SkillMatchInfo)}
      </div>
    ),
  }));

  return (
    <div style={{ marginBottom: 8 }}>
      <Collapse
        ghost
        size="small"
        activeKey={open ? ['process'] : []}
        onChange={() => setOpen(!open)}
        style={{
          background: '#fafafa',
          border: '1px solid #f0f0f0',
          borderRadius: 6,
        }}
        items={[
          {
            key: 'process',
            label: (
              <Space>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {agentLabel ? `${agentLabel} · 推理过程` : '推理过程'}
                </Text>
                {toolCount > 0 && (
                  <Badge
                    count={toolCount}
                    size="small"
                    style={{ backgroundColor: '#fa8c16' }}
                    title={`调用了 ${toolCount} 个工具`}
                  />
                )}
                <Badge
                  count={events.length}
                  size="small"
                  style={{ backgroundColor: '#1890ff' }}
                  title={`共 ${events.length} 个推理步骤`}
                />
              </Space>
            ),
            children: (
              <Collapse
                size="small"
                ghost
                items={items}
                style={{ marginTop: 4 }}
              />
            ),
          },
        ]}
      />
    </div>
  );
};

export default ThoughtProcess;
