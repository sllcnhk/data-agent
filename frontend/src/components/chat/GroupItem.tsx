import React, { useState } from 'react';
import { Button, Dropdown, Input, Modal, Popconfirm, Tooltip } from 'antd';
import {
  FolderOutlined,
  FolderOpenOutlined,
  MoreOutlined,
  EditOutlined,
  DeleteOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons';
import type { ConversationGroup } from '../../store/useChatStore';

interface GroupItemProps {
  group: ConversationGroup;
  /** 实时客户端计数（优先于 group.conversation_count 的后端缓存值） */
  conversationCount?: number;
  onToggleExpand: (groupId: string) => void;
  onRename: (groupId: string, newName: string) => void;
  onDelete: (groupId: string) => void;
}

const GroupItem: React.FC<GroupItemProps> = ({
  group,
  conversationCount,
  onToggleExpand,
  onRename,
  onDelete,
}) => {
  const [isRenaming, setIsRenaming] = useState(false);
  const [newName, setNewName] = useState(group.name);

  const handleRenameConfirm = () => {
    if (newName.trim() && newName !== group.name) {
      onRename(group.id, newName.trim());
    }
    setIsRenaming(false);
  };

  const handleRenameCancel = () => {
    setNewName(group.name);
    setIsRenaming(false);
  };

  const menuItems = [
    {
      key: 'rename',
      label: '重命名',
      icon: <EditOutlined />,
      onClick: () => setIsRenaming(true),
    },
    {
      key: 'delete',
      label: (
        <Popconfirm
          title="确定删除这个分组吗?"
          description="分组内的对话将移到未分组"
          onConfirm={() => onDelete(group.id)}
          okText="确定"
          cancelText="取消"
        >
          <span style={{ color: '#ff4d4f' }}>删除分组</span>
        </Popconfirm>
      ),
      icon: <DeleteOutlined />,
      danger: true,
    },
  ];

  return (
    <>
      <div
        style={{
          padding: '8px 12px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          cursor: 'pointer',
          borderRadius: '6px',
          transition: 'background 0.2s',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = '#f5f5f5';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'transparent';
        }}
      >
        <div
          style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '8px' }}
          onClick={() => onToggleExpand(group.id)}
        >
          {/* 展开/折叠图标 */}
          {group.is_expanded ? (
            <DownOutlined style={{ fontSize: '10px', color: '#999' }} />
          ) : (
            <RightOutlined style={{ fontSize: '10px', color: '#999' }} />
          )}

          {/* 分组图标 */}
          {group.is_expanded ? (
            <FolderOpenOutlined
              style={{
                fontSize: '16px',
                color: group.color || '#1890ff',
              }}
            />
          ) : (
            <FolderOutlined
              style={{
                fontSize: '16px',
                color: group.color || '#1890ff',
              }}
            />
          )}

          {/* 分组名称 */}
          <span
            style={{
              fontWeight: 500,
              fontSize: '14px',
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {group.name}
          </span>

          {/* 对话数量（优先使用父组件实时计算值，避免后端缓存值滞后） */}
          <span
            style={{
              fontSize: '12px',
              color: '#999',
              marginLeft: '4px',
            }}
          >
            {conversationCount ?? group.conversation_count}
          </span>
        </div>

        {/* 更多操作 */}
        <Dropdown menu={{ items: menuItems }} trigger={['click']}>
          <Button
            type="text"
            size="small"
            icon={<MoreOutlined />}
            onClick={(e) => e.stopPropagation()}
            style={{ marginLeft: '4px' }}
          />
        </Dropdown>
      </div>

      {/* 重命名对话框 */}
      <Modal
        title="重命名分组"
        open={isRenaming}
        onOk={handleRenameConfirm}
        onCancel={handleRenameCancel}
        okText="确定"
        cancelText="取消"
        width={400}
      >
        <Input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onPressEnter={handleRenameConfirm}
          placeholder="请输入分组名称"
          autoFocus
          maxLength={50}
        />
      </Modal>
    </>
  );
};

export default GroupItem;
