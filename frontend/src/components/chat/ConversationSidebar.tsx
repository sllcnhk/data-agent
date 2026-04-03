import React, { useState } from 'react';
import {
  Button,
  Collapse,
  Dropdown,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Spin,
  Tabs,
  Tooltip,
  message,
} from 'antd';
import {
  PlusOutlined,
  MessageOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderAddOutlined,
  FolderOutlined,
  MoreOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { Conversation, ConversationGroup } from '../../store/useChatStore';
import type { OtherUserConversations } from '../../services/chatApi';
import GroupItem from './GroupItem';

interface ConversationSidebarProps {
  conversations: Conversation[];
  groups: ConversationGroup[];
  currentConversation: Conversation | null;
  onSelectConversation: (conversation: Conversation) => void;
  onCreateConversation: () => void;
  onDeleteConversation: (conversationId: string) => void;
  onRenameConversation: (conversationId: string, newTitle: string) => void;
  onMoveToGroup: (conversationId: string, groupId: string | null) => void;
  onCreateGroup: (name: string, color?: string) => void;
  onRenameGroup: (groupId: string, newName: string) => void;
  onDeleteGroup: (groupId: string) => void;
  onToggleGroupExpand: (groupId: string) => void;
  loading?: boolean;
  otherUsersData?: OtherUserConversations[];
}

const ConversationSidebar: React.FC<ConversationSidebarProps> = ({
  conversations,
  groups,
  currentConversation,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onRenameConversation,
  onMoveToGroup,
  onCreateGroup,
  onRenameGroup,
  onDeleteGroup,
  onToggleGroupExpand,
  loading = false,
  otherUsersData = [],
}) => {
  const [isCreatingGroup, setIsCreatingGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [activeTab, setActiveTab] = useState<'mine' | 'others'>('mine');

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));

    if (days === 0) {
      return '今天';
    } else if (days === 1) {
      return '昨天';
    } else if (days < 7) {
      return `${days}天前`;
    } else {
      return date.toLocaleDateString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
      });
    }
  };

  const handleCreateGroupConfirm = () => {
    if (newGroupName.trim()) {
      onCreateGroup(newGroupName.trim());
      setNewGroupName('');
      setIsCreatingGroup(false);
    } else {
      message.warning('请输入分组名称');
    }
  };

  const handleCreateGroupCancel = () => {
    setNewGroupName('');
    setIsCreatingGroup(false);
  };

  // 分组对话：按 group_id 分组
  const groupedConversations: Record<string, Conversation[]> = {};
  const ungroupedConversations: Conversation[] = [];

  conversations.forEach((conv) => {
    if (conv.group_id) {
      if (!groupedConversations[conv.group_id]) {
        groupedConversations[conv.group_id] = [];
      }
      groupedConversations[conv.group_id].push(conv);
    } else {
      ungroupedConversations.push(conv);
    }
  });

  // 渲染单个对话
  const renderConversation = (conv: Conversation, paddingLeft = 12) => {
    const moveToGroupMenuItems = [
      {
        key: 'ungrouped',
        label: '移到未分组',
        onClick: () => onMoveToGroup(conv.id, null),
      },
      { type: 'divider' as const },
      ...groups.map((group) => ({
        key: group.id,
        label: group.name,
        icon: <FolderOutlined />,
        onClick: () => onMoveToGroup(conv.id, group.id),
      })),
    ];

    const conversationMenuItems = [
      {
        key: 'rename',
        label: '重命名',
        icon: <EditOutlined />,
        onClick: () => {
          const newTitle = prompt('请输入新的对话标题:', conv.title);
          if (newTitle && newTitle.trim()) {
            onRenameConversation(conv.id, newTitle.trim());
          }
        },
      },
      {
        key: 'move',
        label: '移动到',
        icon: <FolderOutlined />,
        children: moveToGroupMenuItems,
      },
      { type: 'divider' as const },
      {
        key: 'delete',
        label: (
          <Popconfirm
            title="确定删除这个对话吗?"
            onConfirm={() => onDeleteConversation(conv.id)}
            okText="确定"
            cancelText="取消"
          >
            <span style={{ color: '#ff4d4f' }}>删除对话</span>
          </Popconfirm>
        ),
        icon: <DeleteOutlined />,
        danger: true,
      },
    ];

    return (
      <div
        key={conv.id}
        onClick={() => onSelectConversation(conv)}
        style={{
          padding: '10px 12px',
          paddingLeft: `${paddingLeft}px`,
          marginBottom: '4px',
          borderRadius: '6px',
          cursor: 'pointer',
          background: currentConversation?.id === conv.id ? '#e6f7ff' : '#fff',
          border: '1px solid',
          borderColor: currentConversation?.id === conv.id ? '#1890ff' : '#f0f0f0',
          transition: 'all 0.2s',
        }}
        onMouseEnter={(e) => {
          if (currentConversation?.id !== conv.id) {
            e.currentTarget.style.borderColor = '#d9d9d9';
          }
        }}
        onMouseLeave={(e) => {
          if (currentConversation?.id !== conv.id) {
            e.currentTarget.style.borderColor = '#f0f0f0';
          }
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                marginBottom: '4px',
              }}
            >
              <MessageOutlined style={{ marginRight: '6px', color: '#1890ff', fontSize: '12px' }} />
              <span
                style={{
                  fontWeight: 400,
                  fontSize: '13px',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {conv.title}
              </span>
            </div>
            <div
              style={{
                fontSize: '11px',
                color: '#999',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <span>{conv.message_count} 条</span>
              <span>•</span>
              <span>{formatDate(conv.last_message_at || conv.updated_at)}</span>
            </div>
          </div>
          <Dropdown menu={{ items: conversationMenuItems }} trigger={['click']}>
            <Button
              type="text"
              size="small"
              icon={<MoreOutlined />}
              onClick={(e) => e.stopPropagation()}
              style={{ marginLeft: '4px' }}
            />
          </Dropdown>
        </div>
      </div>
    );
  };

  // Tab1: 我的对话内容
  const myConversationsContent = (
    <>
      {/* 顶部按钮 */}
      <div style={{ padding: '12px 16px', display: 'flex', gap: '8px' }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={onCreateConversation}
          block
          size="middle"
        >
          新建对话
        </Button>
        <Tooltip title="新建分组">
          <Button
            icon={<FolderAddOutlined />}
            onClick={() => setIsCreatingGroup(true)}
            size="middle"
          />
        </Tooltip>
      </div>

      {/* 对话列表 */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 8px' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin />
          </div>
        ) : (
          <>
            {/* 分组列表 */}
            {groups.map((group) => (
              <div key={group.id} style={{ marginBottom: '8px' }}>
                <GroupItem
                  group={group}
                  conversationCount={groupedConversations[group.id]?.length ?? 0}
                  onToggleExpand={onToggleGroupExpand}
                  onRename={onRenameGroup}
                  onDelete={onDeleteGroup}
                />
                {group.is_expanded && groupedConversations[group.id] && (
                  <div style={{ marginLeft: '8px', marginTop: '4px' }}>
                    {groupedConversations[group.id].map((conv) =>
                      renderConversation(conv, 24)
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* 未分组对话 */}
            {ungroupedConversations.length > 0 && (
              <div style={{ marginTop: groups.length > 0 ? '16px' : '0' }}>
                {groups.length > 0 && (
                  <div
                    style={{
                      padding: '8px 12px',
                      fontSize: '12px',
                      color: '#999',
                      fontWeight: 500,
                    }}
                  >
                    未分组
                  </div>
                )}
                {ungroupedConversations.map((conv) => renderConversation(conv))}
              </div>
            )}

            {/* 空状态 */}
            {conversations.length === 0 && (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="暂无对话"
                style={{ marginTop: 60 }}
              />
            )}
          </>
        )}
      </div>

      {/* 底部统计 */}
      {conversations.length > 0 && (
        <div
          style={{
            padding: '10px 16px',
            borderTop: '1px solid #f0f0f0',
            fontSize: '12px',
            color: '#999',
            textAlign: 'center',
          }}
        >
          共 {conversations.length} 个对话
          {groups.length > 0 && ` · ${groups.length} 个分组`}
        </div>
      )}
    </>
  );

  // Tab2: 其他用户内容（仅 superadmin）
  const otherUsersContent = (
    <div style={{ flex: 1, overflow: 'auto', padding: '0 8px' }}>
      {otherUsersData.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无其他用户对话"
          style={{ marginTop: 60 }}
        />
      ) : (
        <Collapse
          ghost
          size="small"
          defaultActiveKey={otherUsersData.map((u) => u.user_id)}
          items={otherUsersData.map((userData) => ({
            key: userData.user_id,
            label: (
              <span style={{ fontSize: 13, color: '#555' }}>
                <UserOutlined style={{ marginRight: 6 }} />
                {userData.display_name}
                <span style={{ color: '#bbb', marginLeft: 6 }}>
                  ({userData.conversations.length})
                </span>
              </span>
            ),
            children: (
              <div>
                {userData.conversations.map((conv) => (
                  <div
                    key={conv.id}
                    onClick={() => onSelectConversation(conv as any)}
                    style={{
                      padding: '8px 12px',
                      marginBottom: '4px',
                      borderRadius: '6px',
                      cursor: 'pointer',
                      background: currentConversation?.id === conv.id ? '#e6f7ff' : '#fafafa',
                      border: '1px solid',
                      borderColor: currentConversation?.id === conv.id ? '#1890ff' : '#f0f0f0',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <MessageOutlined style={{ color: '#aaa', fontSize: 11 }} />
                      <span
                        style={{
                          fontSize: 12,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          color: '#555',
                        }}
                      >
                        {conv.title}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: '#bbb', marginTop: 2 }}>
                      {conv.message_count} 条
                    </div>
                  </div>
                ))}
              </div>
            ),
          }))}
        />
      )}
    </div>
  );

  // 计算其他用户总对话数（用于 Tab 标签计数）
  const otherUsersConvTotal = otherUsersData.reduce(
    (sum, u) => sum + u.conversations.length,
    0
  );

  const tabItems = [
    {
      key: 'mine',
      label: '我的对话',
      children: (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          {myConversationsContent}
        </div>
      ),
    },
    ...(otherUsersData.length > 0
      ? [
          {
            key: 'others',
            label: (
              <span>
                其他用户
                {otherUsersConvTotal > 0 && (
                  <span
                    style={{
                      marginLeft: 4,
                      fontSize: 11,
                      color: '#999',
                      background: '#f0f0f0',
                      borderRadius: 8,
                      padding: '0 5px',
                    }}
                  >
                    {otherUsersConvTotal}
                  </span>
                )}
              </span>
            ),
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                {otherUsersContent}
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {otherUsersData.length > 0 ? (
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as 'mine' | 'others')}
          size="small"
          style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
          tabBarStyle={{ margin: '0 8px', marginBottom: 0 }}
          items={tabItems}
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
          {myConversationsContent}
        </div>
      )}

      {/* 新建分组对话框 */}
      <Modal
        title="新建分组"
        open={isCreatingGroup}
        onOk={handleCreateGroupConfirm}
        onCancel={handleCreateGroupCancel}
        okText="确定"
        cancelText="取消"
        width={400}
      >
        <Input
          value={newGroupName}
          onChange={(e) => setNewGroupName(e.target.value)}
          onPressEnter={handleCreateGroupConfirm}
          placeholder="请输入分组名称"
          autoFocus
          maxLength={50}
        />
      </Modal>
    </div>
  );
};

export default ConversationSidebar;
