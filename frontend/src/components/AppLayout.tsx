import React, { useEffect, useState } from 'react';
import { Layout, Menu } from 'antd';
import type { MenuProps } from 'antd';
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
  ImportOutlined,
  ExportOutlined,
  BarChartOutlined,
  DatabaseOutlined,
  AppstoreOutlined,
  MergeCellsOutlined,
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
  perm?: string;      // 需要的权限 key；无 perm 则始终显示
  children?: MenuItem[]; // 子菜单；父项若无可见子项则整体隐藏
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
  { key: '/reports',      icon: <BarChartOutlined />,      label: '图表报告', perm: 'reports:read' },
  { key: '/data-center',  icon: <DatabaseOutlined />,      label: '数据管理中心', perm: 'reports:read' },
  { key: '/data-import',  icon: <ImportOutlined />,        label: '数据导入', perm: 'data:import' },
  { key: '/data-export', icon: <ExportOutlined />,         label: '数据导出', perm: 'data:export' },
  {
    key: '/tools', icon: <AppstoreOutlined />, label: '小工具',
    children: [
      { key: '/tools/merge-excel', icon: <MergeCellsOutlined />, label: '合并Excel文件', perm: 'tools:merge_excel' },
      { key: '/tools/merge-csv', icon: <MergeCellsOutlined />, label: '合并CSV文件', perm: 'tools:merge_csv' },
    ],
  },
  { key: '/logs',        icon: <FileTextOutlined />,      label: '日志查看' },
];

/** 递归按权限过滤菜单树；父项若过滤后无可见子项（且自身无 perm）则整体隐藏 */
function filterMenuItems(items: MenuItem[], hasPermission: (perm: string) => boolean): MenuItem[] {
  const result: MenuItem[] = [];
  for (const item of items) {
    if (item.children) {
      const visibleChildren = filterMenuItems(item.children, hasPermission);
      if (visibleChildren.length > 0) {
        result.push({ ...item, children: visibleChildren });
      }
      continue;
    }
    if (!item.perm || hasPermission(item.perm)) {
      result.push(item);
    }
  }
  return result;
}

const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { hasPermission } = useAuthStore();

  // 按权限过滤菜单项（无权限的菜单不渲染，不是灰色；父项无可见子项也隐藏）
  const visibleMenuItems = filterMenuItems(ALL_MENU_ITEMS, hasPermission);

  // 当前路径所属的父级菜单 key（用于展开对应子菜单）
  const activeParentKey = visibleMenuItems.find((item) =>
    item.children?.some((child) => location.pathname.startsWith(child.key)),
  )?.key;
  const [openKeys, setOpenKeys] = useState<string[]>(activeParentKey ? [activeParentKey] : []);

  useEffect(() => {
    if (activeParentKey && !openKeys.includes(activeParentKey)) {
      setOpenKeys((prev) => [...prev, activeParentKey]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeParentKey]);

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
          openKeys={openKeys}
          onOpenChange={setOpenKeys}
          items={visibleMenuItems as MenuProps['items']}
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
