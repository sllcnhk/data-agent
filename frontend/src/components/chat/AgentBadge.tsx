import React from 'react';
import { Tag, Space } from 'antd';
import {
  ThunderboltOutlined,
  BarChartOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import type { AgentInfo } from '../../store/useChatStore';

const AGENT_CONFIG: Record<string, { color: string; icon: React.ReactNode }> = {
  etl_engineer: { color: 'orange', icon: <ThunderboltOutlined /> },
  analyst: { color: 'blue', icon: <BarChartOutlined /> },
  general: { color: 'default', icon: <RobotOutlined /> },
};

interface AgentBadgeProps {
  info: AgentInfo;
}

export const AgentBadge: React.FC<AgentBadgeProps> = ({ info }) => {
  const config = AGENT_CONFIG[info.agent_type] ?? AGENT_CONFIG.general;

  return (
    <Space size={4} wrap style={{ marginBottom: 6, marginTop: 2 }}>
      <Tag icon={config.icon} color={config.color} style={{ fontSize: 12 }}>
        {info.agent_label}
      </Tag>
      {info.skills.map((s) => (
        <Tag key={s.name} color="geekblue" style={{ fontSize: 11 }}>
          {s.title}
        </Tag>
      ))}
    </Space>
  );
};
