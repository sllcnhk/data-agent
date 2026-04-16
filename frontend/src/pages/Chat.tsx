import React, { useEffect, useRef, useState } from 'react';
import { Layout, message, Button, Space, Modal, List, Typography } from 'antd';
import { FileTextOutlined, StopOutlined } from '@ant-design/icons';
import ConversationSidebar from '../components/chat/ConversationSidebar';
import ChatMessages from '../components/chat/ChatMessages';
import ChatInput, { AttachmentItem } from '../components/chat/ChatInput';
import ModelSelector from '../components/chat/ModelSelector';
import MCPStatus from '../components/chat/MCPStatus';
import UserAccountDropdown from '../components/UserAccountDropdown';
import ApprovalModal from '../components/chat/ApprovalModal';
import { useChatStore } from '../store/useChatStore';
import { conversationApi, llmConfigApi, groupApi, cancelConversationStream, adminApi } from '../services/chatApi';
import type { OtherUserConversations } from '../services/chatApi';
import { useAuthStore } from '../store/useAuthStore';
import { useNavigate } from 'react-router-dom';

const { Sider, Content } = Layout;

const Chat: React.FC = () => {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [otherUsersData, setOtherUsersData] = useState<OtherUserConversations[]>([]);
  const [isViewingOtherUserConv, setIsViewingOtherUserConv] = useState(false);
  const authUser = useAuthStore((s) => s.user);

  // 显示带日志链接的错误消息
  const showErrorWithLogs = (errorMsg: string) => {
    const key = 'error-with-logs';
    message.error({
      content: (
        <Space>
          <span>{errorMsg}</span>
          <Button
            type="link"
            icon={<FileTextOutlined />}
            onClick={() => navigate('/logs')}
            size="small"
          >
            查看日志
          </Button>
        </Space>
      ),
      key,
      duration: 5,
    });
  };

  const {
    conversations,
    currentConversation,
    groups,
    messages,
    llmConfigs,
    selectedModel,
    loading,
    sending,
    messageThoughts,
    messageAgentInfo,
    pendingApproval,
    pendingContinuation,
    isCancelling,
    setConversations,
    setCurrentConversation,
    setGroups,
    setMessages,
    setLLMConfigs,
    setSelectedModel,
    setLoading,
    setSending,
    setIsCancelling,
    addMessage,
    appendMessageContent,
    addThoughtEvent,
    setMessageAgentInfo,
    migrateMessageId,
    setPendingApproval,
    setPendingContinuation,
    setMessageFilesWritten,
    updateConversation,
    addGroup,
    updateGroup,
    deleteGroup,
    toggleGroupExpand,
  } = useChatStore();

  // Track the current assistant message ID for attaching thought events
  const currentAssistantIdRef = useRef<string>('');

  // 初始化:加载模型配置和对话列表
  useEffect(() => {
    loadInitialData();
  }, []);

  // 当切换对话时,加载消息
  useEffect(() => {
    if (currentConversation) {
      loadMessages(currentConversation.id);
    } else {
      setMessages([]);
    }
  }, [currentConversation?.id]);

  // 超管"其他用户"区块：在 auth 完成后 reactive 触发加载
  // 解决时序竞争：loadInitialData 在 mount 时 user 尚未就绪，
  // 此处依赖 authUser 状态变化，auth 完成后自动触发一次
  useEffect(() => {
    if (authUser?.is_superadmin) {
      loadOtherUsersData();
    } else {
      setOtherUsersData([]);
    }
  }, [authUser?.is_superadmin, authUser?.id]);

  // 超管：加载其他用户的对话（独立函数，可按需刷新）
  const loadOtherUsersData = async () => {
    try {
      const allUsersRes = await adminApi.fetchAllUsersConversations();
      const usersData = allUsersRes.users || [];
      setOtherUsersData(usersData);
    } catch (error: any) {
      // 非超管或接口不可用时静默忽略
      setOtherUsersData([]);
    }
  };

  const loadInitialData = async () => {
    setLoading(true);
    try {
      // 加载模型配置
      const configsRes = await llmConfigApi.listConfigs(true);
      if (configsRes.success) {
        setLLMConfigs(configsRes.data);
      }

      // 加载分组列表
      const groupsRes = await groupApi.listGroups();
      if (groupsRes.groups) {
        setGroups(groupsRes.groups);
      }

      // 加载对话列表
      const convsRes = await conversationApi.listConversations({
        status: 'active',
        limit: 200
      });
      if (convsRes.conversations) {
        setConversations(convsRes.conversations);

        // 恢复上次的对话
        const lastConvId = localStorage.getItem('lastConversationId');
        if (lastConvId) {
          const lastConv = convsRes.conversations.find((c: any) => c.id === lastConvId);
          if (lastConv) {
            setCurrentConversation(lastConv);
          }
        }
      }
      // 注：other users 数据由上方 useEffect([authUser]) 负责加载，此处不再处理
    } catch (error: any) {
      showErrorWithLogs('加载数据失败: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const loadMessages = async (conversationId: string) => {
    try {
      const res = await conversationApi.getMessages(conversationId, {
        limit: 100
      });
      if (res.success) {
        setMessages(res.data);
        // 恢复历史推理过程：将 DB 中持久化的 thinking_events 填入 messageThoughts
        // 恢复文件写入：将 DB 中持久化的 files_written 写回 message
        for (const msg of res.data) {
          if (msg.role === 'assistant') {
            if (msg.thinking_events?.length) {
              for (const evt of msg.thinking_events) {
                addThoughtEvent(msg.id, evt);
              }
            }
            const fw = msg.extra_metadata?.files_written;
            if (fw?.length) {
              setMessageFilesWritten(msg.id, fw);
            }
          }
        }
      }
    } catch (error: any) {
      showErrorWithLogs('加载消息失败: ' + error.message);
    }
  };

  const handleCreateConversation = async () => {
    try {
      const res = await conversationApi.createConversation({
        title: '新对话',
        model_key: selectedModel
      });

      if (res.success) {
        const newConv = res.data;
        useChatStore.getState().addConversation(newConv);
        setCurrentConversation(newConv);
        message.success('创建新对话成功');
      }
    } catch (error: any) {
      showErrorWithLogs('创建对话失败: ' + error.message);
    }
  };

  const handleSelectConversation = (conversation: any) => {
    setCurrentConversation(conversation);
    // 检测是否选择了其他用户的对话（只读模式）
    const isOtherUserConv = otherUsersData.some((userData) =>
      userData.conversations.some((conv) => conv.id === conversation.id)
    );
    setIsViewingOtherUserConv(isOtherUserConv);
  };

  const handleDeleteConversation = async (conversationId: string) => {
    try {
      await conversationApi.deleteConversation(conversationId, false);
      useChatStore.getState().deleteConversation(conversationId);
      message.success('删除对话成功');

      // 如果删除的是当前对话,清空选择
      if (currentConversation?.id === conversationId) {
        setCurrentConversation(null);
      }
    } catch (error: any) {
      message.error('删除对话失败: ' + error.message);
    }
  };

  const handleSendMessage = async (content: string, attachments?: AttachmentItem[]) => {
    // 只读模式：不允许向其他用户的对话发送消息
    if (isViewingOtherUserConv) return;

    if (!currentConversation) {
      // 如果没有当前对话,创建新对话
      try {
        const res = await conversationApi.createConversation({
          title: content.substring(0, 30) + (content.length > 30 ? '...' : ''),
          model_key: selectedModel
        });

        if (res.success) {
          const newConv = res.data;
          useChatStore.getState().addConversation(newConv);
          setCurrentConversation(newConv);

          // 创建后发送消息
          await sendMessageToConversation(newConv.id, content, attachments);
        }
      } catch (error: any) {
        message.error('创建对话失败: ' + error.message);
      }
      return;
    }

    await sendMessageToConversation(currentConversation.id, content, attachments);
  };

  const sendMessageToConversation = async (conversationId: string, content: string, attachments?: AttachmentItem[]) => {
    // 添加用户消息到UI
    const userMessage = {
      id: Date.now().toString(),
      conversation_id: conversationId,
      role: 'user' as const,
      content,
      created_at: new Date().toISOString(),
      ...(attachments && attachments.length > 0 ? {
        extra_metadata: {
          attachments: attachments.map(a => ({ name: a.name, mime_type: a.mime_type, size: a.size }))
        }
      } : {})
    };
    addMessage(userMessage);

    // 添加空的助手消息占位
    const assistantPlaceholderId = (Date.now() + 1).toString();
    const assistantMessage = {
      id: assistantPlaceholderId,
      conversation_id: conversationId,
      role: 'assistant' as const,
      content: '',
      created_at: new Date().toISOString()
    };
    addMessage(assistantMessage);
    currentAssistantIdRef.current = assistantPlaceholderId;

    setSending(true);

    try {
      // 使用流式API
      await conversationApi.sendMessageStream(
        conversationId,
        content,
        selectedModel,
        attachments,
        (chunk) => {
          const aid = currentAssistantIdRef.current;
          // 处理流式数据块
          if (chunk.type === 'content') {
            appendMessageContent(chunk.data);
          } else if (chunk.type === 'user_message') {
            if (chunk.data?.role === 'continuation') {
              // 自动续接消息：直接加入消息列表（展示为 ContinuationCard），
              // 并为下一轮 Agent 回复创建新的空白占位符
              addMessage(chunk.data);
              const newPlaceholderId = `placeholder_${Date.now()}`;
              addMessage({
                id: newPlaceholderId,
                conversation_id: chunk.data.conversation_id,
                role: 'assistant' as const,
                content: '',
                created_at: new Date().toISOString(),
              });
              currentAssistantIdRef.current = newPlaceholderId;
            } else {
              // 原始用户消息：更新占位 ID 为真实 DB ID
              const msgs = useChatStore.getState().messages;
              const lastUserMsg = msgs.find((m: any) => m.id === userMessage.id);
              if (lastUserMsg) {
                lastUserMsg.id = chunk.data.id;
              }
            }
          } else if (chunk.type === 'assistant_message') {
            // 更新助手消息元数据（id, tokens等）
            const msgs = useChatStore.getState().messages;
            const lastAssistantMsg = msgs[msgs.length - 1];
            if (lastAssistantMsg) {
              const newId = chunk.data?.id;
              if (newId && newId !== aid) {
                // 将占位 ID 下的 thoughts / agentInfo 迁移到真实 DB ID
                migrateMessageId(aid, newId);
                currentAssistantIdRef.current = newId;
              }
              Object.assign(lastAssistantMsg, chunk.data);
            }
          } else if (
            chunk.type === 'thinking' ||
            chunk.type === 'tool_call' ||
            chunk.type === 'tool_result' ||
            chunk.type === 'skill_matched'
          ) {
            // 推理过程事件（含技能路由结果） → 附加到当前助手消息
            addThoughtEvent(currentAssistantIdRef.current, {
              type: chunk.type,
              data: chunk.data,
              metadata: chunk.metadata,
            });
          } else if (chunk.type === 'files_written') {
            // Agent 写入了文件 → 更新当前助手消息，显示下载链接
            const files = chunk.data?.files;
            if (files?.length && currentAssistantIdRef.current) {
              setMessageFilesWritten(currentAssistantIdRef.current, files);
            }
          } else if (chunk.type === 'context_compressed') {
            // 对话历史已自动压缩（Claude Code 风格）
            message.info(chunk.data?.message || '对话历史已自动压缩', 2);
          } else if (chunk.type === 'error') {
            // 推理错误 — 将错误文本写入助手消息气泡，让用户看到完整信息
            appendMessageContent(
              `\n\n> ⚠️ **推理中断**：${chunk.data}`
            );
          } else if (chunk.type === 'agent_start') {
            // Agent 路由结果 + 技能信息 → 更新当前助手消息的 Agent 信息
            if (currentAssistantIdRef.current) {
              setMessageAgentInfo(currentAssistantIdRef.current, chunk.data);
            }
          } else if (chunk.type === 'cancelled') {
            // 生成已被用户中断 — 显示提示
            message.info('已停止生成', 2);
          } else if (chunk.type === 'auto_continuing') {
            // 自动续接提示（轻量 toast）
            message.info(chunk.data?.message || '自动续接对话...', 3);
          } else if (chunk.type === 'continuation_approval_required') {
            // 已超出自动续接次数，需要人工确认
            setPendingContinuation({
              message:       chunk.data?.message       || '是否继续完成剩余任务？',
              pending_tasks: chunk.data?.pending_tasks || [],
              conclusions:   chunk.data?.conclusions   || '',
            });
          } else if (chunk.type === 'approval_required') {
            // 审批弹窗 — 支持 sql 和 file_write 两种类型
            setPendingApproval({
              approval_id:     chunk.data?.approval_id     || '',
              approval_type:   chunk.data?.approval_type   || 'sql',
              tool:            chunk.data?.tool             || '',
              message:         chunk.data?.message          || '',
              // SQL fields
              sql:             chunk.data?.sql              || '',
              warnings:        chunk.data?.warnings         || [],
              // file_write fields
              path:            chunk.data?.path             || '',
              content_preview: chunk.data?.content_preview  || '',
              session_grant:   chunk.data?.session_grant    ?? false,
            });
          }
        },
        () => {
          // 完成（包括取消后的正常完成）
          setSending(false);
          setIsCancelling(false);
          // 刷新对话列表(更新时间戳)
          loadInitialData();
        },
        (error) => {
          // 错误
          setSending(false);
          setIsCancelling(false);
          message.error('发送消息失败: ' + error);
        }
      );
    } catch (error: any) {
      setSending(false);
      setIsCancelling(false);
      message.error('发送消息失败: ' + error.message);
    }
  };

  const handleStop = async () => {
    if (!currentConversation || isCancelling) return;
    setIsCancelling(true);
    await cancelConversationStream(currentConversation.id);
  };

  const handleContinuationApproved = () => {
    if (!pendingContinuation || !currentConversation) return;
    const { conclusions, pending_tasks } = pendingContinuation;
    setPendingContinuation(null);
    const tasksText = pending_tasks.map((t) => `- ${t}`).join('\n');
    const continuationMsg =
      `基于上一轮的分析结论，请继续完成以下未完成的任务：\n\n` +
      `上一轮结论摘要：\n${conclusions}\n\n` +
      `待完成任务：\n${tasksText}`;
    handleSendMessage(continuationMsg);
  };

  const handleModelChange = async (modelKey: string) => {
    setSelectedModel(modelKey);
    // 立即持久化到当前对话，使下次加载时恢复正确模型
    if (currentConversation) {
      try {
        await conversationApi.updateConversation(currentConversation.id, { model_key: modelKey });
        updateConversation(currentConversation.id, { current_model: modelKey });
      } catch (_e) {
        // 非关键操作，静默失败
      }
    }
  };

  const handleRegenerateMessage = async () => {
    if (!currentConversation) return;

    setSending(true);
    try {
      const res = await conversationApi.regenerateLastMessage(currentConversation.id);
      if (res.success) {
        // 重新加载消息
        await loadMessages(currentConversation.id);
        message.success('重新生成成功');
      }
    } catch (error: any) {
      message.error('重新生成失败: ' + error.message);
    } finally {
      setSending(false);
    }
  };

  // 分组相关操作
  const handleCreateGroup = async (name: string, color?: string) => {
    try {
      const res = await groupApi.createGroup({ name, color });
      if (res.success) {
        addGroup(res.data);
        message.success('创建分组成功');
      }
    } catch (error: any) {
      message.error('创建分组失败: ' + error.message);
    }
  };

  const handleRenameGroup = async (groupId: string, newName: string) => {
    try {
      const res = await groupApi.updateGroup(groupId, { name: newName });
      if (res.success) {
        updateGroup(groupId, { name: newName });
        message.success('重命名分组成功');
      }
    } catch (error: any) {
      message.error('重命名分组失败: ' + error.message);
    }
  };

  const handleDeleteGroup = async (groupId: string) => {
    try {
      await groupApi.deleteGroup(groupId);
      deleteGroup(groupId);
      message.success('删除分组成功');
    } catch (error: any) {
      message.error('删除分组失败: ' + error.message);
    }
  };

  const handleToggleGroupExpand = (groupId: string) => {
    // 先获取当前状态，再翻转，用于持久化
    const currentGroup = groups.find((g) => g.id === groupId);
    const newExpanded = currentGroup ? !currentGroup.is_expanded : true;
    toggleGroupExpand(groupId);  // 立即更新本地 Zustand（UI 无感刷新）
    // 静默持久化到后端，失败不影响本地操作
    groupApi.updateGroup(groupId, { is_expanded: newExpanded }).catch(() => {});
  };

  // 对话相关操作
  const handleRenameConversation = async (conversationId: string, newTitle: string) => {
    try {
      const res = await conversationApi.renameConversation(conversationId, newTitle);
      if (res.success) {
        updateConversation(conversationId, { title: newTitle });
        message.success('重命名对话成功');
      }
    } catch (error: any) {
      message.error('重命名对话失败: ' + error.message);
    }
  };

  const handleMoveToGroup = async (conversationId: string, groupId: string | null) => {
    try {
      const res = await conversationApi.moveToGroup(conversationId, groupId);
      if (res.success) {
        updateConversation(conversationId, { group_id: groupId || undefined });
        message.success(groupId ? '移动到分组成功' : '移动到未分组成功');
        // 刷新分组列表以更新对话数量
        const groupsRes = await groupApi.listGroups();
        if (groupsRes.groups) {
          setGroups(groupsRes.groups);
        }
      }
    } catch (error: any) {
      message.error('移动对话失败: ' + error.message);
    }
  };

  return (
    <Layout style={{ height: '100vh', minHeight: 0, overflow: 'hidden' }}>
      {/* Human-in-the-Loop 审批弹窗 */}
      <ApprovalModal
        approval={pendingApproval}
        onClose={() => setPendingApproval(null)}
      />
      {/* 自动续接已达上限，请人工确认 */}
      <Modal
        open={!!pendingContinuation}
        title="任务未完成 — 是否继续？"
        onOk={handleContinuationApproved}
        onCancel={() => setPendingContinuation(null)}
        okText="继续"
        cancelText="暂停"
      >
        <Typography.Paragraph>
          {pendingContinuation?.message}
        </Typography.Paragraph>
        {pendingContinuation?.pending_tasks?.length ? (
          <>
            <Typography.Text strong>待完成任务：</Typography.Text>
            <List
              size="small"
              dataSource={pendingContinuation.pending_tasks}
              renderItem={(task) => (
                <List.Item style={{ padding: '4px 0' }}>• {task}</List.Item>
              )}
            />
          </>
        ) : null}
      </Modal>
      {/* 左侧对话列表 */}
      <Sider
        className="chat-page-sider"
        width={280}
        collapsedWidth={56}
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        theme="light"
        style={{
          borderRight: '1px solid #f0f0f0',
          minHeight: 0,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <ConversationSidebar
          conversations={conversations}
          groups={groups}
          currentConversation={currentConversation}
          onSelectConversation={handleSelectConversation}
          onCreateConversation={handleCreateConversation}
          onDeleteConversation={handleDeleteConversation}
          onRenameConversation={handleRenameConversation}
          onMoveToGroup={handleMoveToGroup}
          onCreateGroup={handleCreateGroup}
          onRenameGroup={handleRenameGroup}
          onDeleteGroup={handleDeleteGroup}
          onToggleGroupExpand={handleToggleGroupExpand}
          loading={loading}
          otherUsersData={authUser?.is_superadmin ? otherUsersData : []}
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((c) => !c)}
        />
      </Sider>

      {/* 右侧聊天区域 */}
      <Layout style={{ minHeight: 0 }}>
        <Content
          style={{
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            minHeight: 0,
            background: '#fff',
          }}
        >
          {/* 顶部模型选择器 */}
          <div
            style={{
              padding: '12px 24px',
              borderBottom: '1px solid #f0f0f0',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <div style={{ flex: 1 }}>
              <h2 style={{ margin: 0, fontSize: 18, marginBottom: 8 }}>
                {currentConversation?.title || '开始新对话'}
              </h2>
              <MCPStatus />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <ModelSelector
                configs={llmConfigs}
                selectedModel={selectedModel}
                onSelect={handleModelChange}
              />
              <UserAccountDropdown />
            </div>
          </div>

          {/* 消息区域 */}
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <ChatMessages
              messages={messages}
              loading={sending}
              onRegenerate={handleRegenerateMessage}
              messageThoughts={messageThoughts}
              messageAgentInfo={messageAgentInfo}
            />
          </div>

          {/* 输入区域 */}
          <div
            style={{
              borderTop: '1px solid #f0f0f0',
              padding: '16px 24px',
              background: '#fafafa',
            }}
          >
            {sending && !isViewingOtherUserConv && (
              <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 8 }}>
                <Button
                  danger
                  icon={<StopOutlined />}
                  loading={isCancelling}
                  onClick={handleStop}
                  size="small"
                >
                  停止生成
                </Button>
              </div>
            )}
            <ChatInput
              onSend={(content, attachments) => handleSendMessage(content, attachments)}
              disabled={sending}
              readOnly={isViewingOtherUserConv}
              placeholder={
                currentConversation
                  ? '输入消息...'
                  : '开始新对话,输入你的问题...'
              }
            />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
};

export default Chat;
