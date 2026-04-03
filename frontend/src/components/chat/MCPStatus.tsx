import React, { useEffect, useState } from 'react';
import { Tag, Tooltip, Spin } from 'antd';
import { CheckCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { mcpApi } from '@/services/api';

interface MCPServer {
  name: string;
  type: string;
  version: string;
  tool_count: number;
  resource_count: number;
}

interface MCPStatusProps {
  style?: React.CSSProperties;
}

const MCPStatus: React.FC<MCPStatusProps> = ({ style }) => {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchServers();
  }, []);

  const fetchServers = async () => {
    try {
      setLoading(true);
      const data = await mcpApi.getServers();
      // 防御性检查：确保 data 是数组（避免后端返回格式变化时 map() 崩溃）
      setServers(Array.isArray(data) ? data : []);
    } catch (err: any) {
      // 401（未认证）或 403（权限不足）时静默降级：不显示错误，仅展示空列表
      // 这样 analyst/viewer 用户不会看到报错信息
      const status = err?.response?.status;
      if (status !== 401 && status !== 403) {
        console.error('获取MCP服务器失败:', err);
      }
      setServers([]);
    } finally {
      setLoading(false);
    }
  };

  const getServerIcon = (type: string) => {
    const icons: Record<string, string> = {
      clickhouse: '🏢',
      mysql: '🐬',
      filesystem: '📁',
      lark: '📝',
    };
    return icons[type] || '🔧';
  };

  const getServerColor = (type: string) => {
    const colors: Record<string, string> = {
      clickhouse: 'blue',
      mysql: 'orange',
      filesystem: 'green',
      lark: 'purple',
    };
    return colors[type] || 'default';
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', ...style }}>
        <Spin size="small" indicator={<LoadingOutlined style={{ fontSize: 14 }} spin />} />
        <span style={{ fontSize: '12px', color: '#8c8c8c' }}>加载MCP服务器...</span>
      </div>
    );
  }

  if (servers.length === 0) {
    // 无服务器或无权限时，不渲染任何内容（静默降级）
    return null;
  }

  return (
    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', ...style }}>
      {servers.map(server => (
        <Tooltip
          key={server.name}
          title={
            <div>
              <div><strong>{server.name}</strong></div>
              <div>类型: {server.type}</div>
              <div>版本: {server.version}</div>
              <div>工具数: {server.tool_count}</div>
              <div>资源数: {server.resource_count}</div>
            </div>
          }
        >
          <Tag
            icon={<CheckCircleOutlined />}
            color={getServerColor(server.type)}
            style={{ cursor: 'pointer', fontSize: '12px' }}
          >
            {getServerIcon(server.type)} {server.name}
          </Tag>
        </Tooltip>
      ))}
    </div>
  );
};

export default MCPStatus;
