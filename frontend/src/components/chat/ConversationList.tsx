import React from 'react';
import { Button, List, Popconfirm, Tooltip, Empty, Spin } from 'antd';
import {
  PlusOutlined,
  MessageOutlined,
  DeleteOutlined,
  PushpinOutlined,
  PushpinFilled,
} from '@ant-design/icons';
import type { Conversation } from '../../store/useChatStore';

interface ConversationListProps {
  conversations: Conversation[];
  currentConversation: Conversation | null;
  onSelect: (conversation: Conversation) => void;
  onCreate: () => void;
  onDelete: (conversationId: string) => void;
  loading?: boolean;
}

const ConversationList: React.FC<ConversationListProps> = ({
  conversations,
  currentConversation,
  onSelect,
  onCreate,
  onDelete,
  loading = false,
}) => {
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

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 新建对话按钮 */}
      <div style={{ padding: '16px' }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={onCreate}
          block
          size="large"
        >
          新建对话
        </Button>
      </div>

      {/* 对话列表 */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 8px' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin />
          </div>
        ) : conversations.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无对话"
            style={{ marginTop: 60 }}
          />
        ) : (
          <List
            dataSource={conversations}
            renderItem={(conv) => (
              <div
                key={conv.id}
                onClick={() => onSelect(conv)}
                style={{
                  padding: '12px',
                  marginBottom: '8px',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  background:
                    currentConversation?.id === conv.id ? '#e6f7ff' : '#fff',
                  border: '1px solid',
                  borderColor:
                    currentConversation?.id === conv.id ? '#1890ff' : '#f0f0f0',
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
                    {/* 标题 */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        marginBottom: '4px',
                      }}
                    >
                      <MessageOutlined
                        style={{ marginRight: '6px', color: '#1890ff' }}
                      />
                      <span
                        style={{
                          fontWeight: 500,
                          fontSize: '14px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {conv.title}
                      </span>
                      {conv.is_pinned && (
                        <PushpinFilled
                          style={{
                            marginLeft: '6px',
                            color: '#faad14',
                            fontSize: '12px',
                          }}
                        />
                      )}
                    </div>

                    {/* 信息 */}
                    <div
                      style={{
                        fontSize: '12px',
                        color: '#999',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                      }}
                    >
                      <span>{conv.message_count} 条消息</span>
                      <span>•</span>
                      <span>
                        {formatDate(conv.last_message_at || conv.updated_at)}
                      </span>
                    </div>
                  </div>

                  {/* 操作按钮 */}
                  <div
                    style={{
                      display: 'flex',
                      gap: '4px',
                      marginLeft: '8px',
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Popconfirm
                      title="确定删除这个对话吗?"
                      onConfirm={() => onDelete(conv.id)}
                      okText="确定"
                      cancelText="取消"
                    >
                      <Tooltip title="删除">
                        <Button
                          type="text"
                          size="small"
                          icon={<DeleteOutlined />}
                          danger
                        />
                      </Tooltip>
                    </Popconfirm>
                  </div>
                </div>
              </div>
            )}
          />
        )}
      </div>

      {/* 底部统计 */}
      {conversations.length > 0 && (
        <div
          style={{
            padding: '12px 16px',
            borderTop: '1px solid #f0f0f0',
            fontSize: '12px',
            color: '#999',
            textAlign: 'center',
          }}
        >
          共 {conversations.length} 个对话
        </div>
      )}
    </div>
  );
};

export default ConversationList;
