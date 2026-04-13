/**
 * DataCenterLayout — 数据管理中心独立布局
 *
 * 独立于 AppLayout，拥有自己的顶部 header 和左侧导航。
 * 顶部 header：深色背景，左侧标题，右侧"返回对话"按钮 + 用户名。
 * 左侧 sidebar：200px，深色主题，三个导航项（报表清单 / 报告清单 / 推送任务）。
 */
import React from 'react';
import { Layout, Menu, Button, Typography } from 'antd';
import {
  BarChartOutlined,
  FileTextOutlined,
  CalendarOutlined,
  ArrowLeftOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/store/useAuthStore';

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

interface DataCenterLayoutProps {
  children: React.ReactNode;
}

interface MenuItem {
  key: string;
  icon: React.ReactNode;
  label: string;
  perm?: string;
}

const ALL_MENU_ITEMS: MenuItem[] = [
  { key: '/data-center/dashboards', icon: <BarChartOutlined />, label: '报表清单',  perm: 'reports:read' },
  { key: '/data-center/documents',  icon: <FileTextOutlined />,  label: '报告清单', perm: 'reports:read' },
  { key: '/data-center/schedules',  icon: <CalendarOutlined />,  label: '推送任务', perm: 'schedules:read' },
];

const DataCenterLayout: React.FC<DataCenterLayoutProps> = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, hasPermission } = useAuthStore();

  const visibleMenuItems = ALL_MENU_ITEMS.filter(
    (item) => !item.perm || hasPermission(item.perm),
  );

  // 当前选中的菜单 key（/data-center 根路径高亮到 dashboards）
  const selectedKey = (() => {
    if (location.pathname === '/data-center') return '/data-center/dashboards';
    return location.pathname;
  })();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* ── 顶部 Header ─────────────────────────────────────────────────── */}
      <Header
        style={{
          background: '#001529',
          padding: '0 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: 56,
          lineHeight: '56px',
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          zIndex: 100,
          boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
        }}
      >
        {/* 左侧标题 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 20 }}>🗄</span>
          <Text
            strong
            style={{ color: '#fff', fontSize: 18, margin: 0, letterSpacing: 1 }}
          >
            数据管理中心
          </Text>
        </div>

        {/* 右侧操作区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {user && (
            <Text style={{ color: 'rgba(255,255,255,0.75)', fontSize: 13 }}>
              {user.display_name || user.username}
            </Text>
          )}
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/chat')}
            style={{
              background: 'rgba(255,255,255,0.1)',
              borderColor: 'rgba(255,255,255,0.3)',
              color: '#fff',
            }}
          >
            返回对话
          </Button>
        </div>
      </Header>

      {/* ── 主体（侧边栏 + 内容区）──────────────────────────────────────── */}
      <Layout style={{ marginTop: 56 }}>
        {/* 左侧导航 */}
        <Sider
          theme="dark"
          width={200}
          style={{
            minHeight: 'calc(100vh - 56px)',
            position: 'fixed',
            top: 56,
            left: 0,
            bottom: 0,
            overflowY: 'auto',
            background: '#001529',
          }}
        >
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selectedKey]}
            style={{ marginTop: 8, borderRight: 0 }}
            items={visibleMenuItems.map((item) => ({
              key: item.key,
              icon: item.icon,
              label: item.label,
              onClick: () => navigate(item.key),
            }))}
          />
        </Sider>

        {/* 右侧内容区 */}
        <Content
          style={{
            marginLeft: 200,
            minHeight: 'calc(100vh - 56px)',
            background: '#f0f2f5',
          }}
        >
          {children}
        </Content>
      </Layout>
    </Layout>
  );
};

export default DataCenterLayout;
