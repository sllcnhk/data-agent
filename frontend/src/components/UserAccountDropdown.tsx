import React from 'react';
import { Avatar, Dropdown, Space, Typography } from 'antd';
import { UserOutlined, LogoutOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/useAuthStore';
import { message } from 'antd';

const { Text } = Typography;

interface UserAccountDropdownProps {
  /** 是否显示日期时间（AppLayout 用到，Chat 页面不需要） */
  showDateTime?: boolean;
}

const UserAccountDropdown: React.FC<UserAccountDropdownProps> = ({ showDateTime = false }) => {
  const navigate = useNavigate();
  const { user, logout } = useAuthStore();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
    message.success('已成功登出');
  };

  const userMenuItems = [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: user?.display_name || user?.username || '用户',
      disabled: true,
    },
    { type: 'divider' as const },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
    },
  ];

  if (!user) return null;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      {showDateTime && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {new Date().toLocaleString('zh-CN')}
        </Text>
      )}
      <Dropdown
        menu={{
          items: userMenuItems,
          onClick: ({ key }) => { if (key === 'logout') handleLogout(); },
        }}
        trigger={['click']}
      >
        <Space style={{ cursor: 'pointer' }}>
          <Avatar
            size="small"
            icon={<UserOutlined />}
            style={{ background: '#1890ff' }}
          />
          <Text>{user.display_name || user.username}</Text>
        </Space>
      </Dropdown>
    </div>
  );
};

export default UserAccountDropdown;
