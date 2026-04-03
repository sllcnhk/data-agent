import React, { useState } from 'react';
import { Layout, Menu } from 'antd';
import {
  DashboardOutlined,
  RobotOutlined,
  CheckCircleOutlined,
  ToolOutlined,
  MessageOutlined,
  FileTextOutlined,
  TeamOutlined,
  ApiOutlined,
  SafetyOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/store/useAuthStore';
import UserAccountDropdown from './UserAccountDropdown';

const { Header, Sider } = Layout;

interface AppLayoutProps {
  children: React.ReactNode;
}

interface MenuItem {
  key: string;
  icon: React.ReactNode;
  label: string;
  perm?: string;  // 需要的权限 key；无 perm 则始终显示
}

const ALL_MENU_ITEMS: MenuItem[] = [
  { key: '/chat',         icon: <MessageOutlined />,      label: 'AI对话',    perm: 'chat:use' },
  { key: '/model-config', icon: <ApiOutlined />,          label: '模型配置',  perm: 'models:read' },
  { key: '/dashboard',   icon: <DashboardOutlined />,     label: '系统仪表盘' },
  { key: '/agents',      icon: <RobotOutlined />,         label: 'Agent管理' },
  { key: '/tasks',       icon: <CheckCircleOutlined />,   label: '任务管理' },
  { key: '/skills',      icon: <ToolOutlined />,          label: '技能中心', perm: 'skills.user:read' },
  { key: '/users',       icon: <TeamOutlined />,          label: '用户管理', perm: 'users:read' },
  { key: '/roles',       icon: <SafetyOutlined />,        label: '角色权限', perm: 'users:read' },
  { key: '/logs',        icon: <FileTextOutlined />,      label: '日志查看' },
];

const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { hasPermission } = useAuthStore();

  // 按权限过滤菜单项（无权限的菜单不渲染，不是灰色）
  const visibleMenuItems = ALL_MENU_ITEMS.filter(
    (item) => !item.perm || hasPermission(item.perm)
  );

  const handleMenuClick = ({ key }: { key: string }) => {
    navigate(key);
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        theme="dark"
        width={240}
      >
        <div
          style={{
            height: 64,
            margin: 16,
            background: 'rgba(255, 255, 255, 0.2)',
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontSize: collapsed ? 14 : 18,
            fontWeight: 'bold',
          }}
        >
          {collapsed ? 'DA' : 'Data Agent'}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={visibleMenuItems}
          onClick={handleMenuClick}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: '0 24px',
            background: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            boxShadow: '0 1px 4px rgba(0,21,41,0.08)',
          }}
        >
          <h1 style={{ margin: 0, fontSize: 20, color: '#1890ff' }}>
            数据智能分析Agent系统
          </h1>
          <UserAccountDropdown showDateTime />
        </Header>
        {children}
      </Layout>
    </Layout>
  );
};

export default AppLayout;
