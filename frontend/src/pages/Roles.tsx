/**
 * 角色权限管理页面（需 users:read 权限）
 *
 * 功能：
 * - 角色列表（卡片视图）
 * - 创建/删除自定义角色
 * - 查看角色权限详情
 * - 为角色分配/移除权限
 */
import React, { useEffect, useState, useCallback } from 'react';
import {
  Row, Col, Card, Button, Tag, Modal, Form, Input, Spin,
  Popconfirm, Typography, Space, Tooltip, message,
  Checkbox, Divider,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, SafetyOutlined, LockOutlined,
  CheckOutlined,
} from '@ant-design/icons';
import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

const { Title, Text } = Typography;
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';

interface PermissionRecord {
  id: string;
  resource: string;
  action: string;
  description: string | null;
}

interface RoleRecord {
  id: string;
  name: string;
  description: string | null;
  is_system: boolean;
  permissions: PermissionRecord[];
}

const RESOURCE_COLOR: Record<string, string> = {
  chat: 'blue',
  models: 'cyan',
  'skills.user': 'green',
  'skills.project': 'lime',
  'skills.system': 'purple',
  users: 'orange',
  tasks: 'geekblue',
  agents: 'magenta',
};

const ROLE_TAG_COLOR: Record<string, string> = {
  viewer: 'default',
  analyst: 'blue',
  admin: 'orange',
  superadmin: 'red',
};

const Roles: React.FC = () => {
  const { accessToken, hasPermission } = useAuthStore();
  const [roles, setRoles] = useState<RoleRecord[]>([]);
  const [allPerms, setAllPerms] = useState<PermissionRecord[]>([]);
  const [loading, setLoading] = useState(false);

  // 创建角色 Modal
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();

  // 角色详情/权限分配 Modal
  const [detailRole, setDetailRole] = useState<RoleRecord | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const authHeaders = { Authorization: `Bearer ${accessToken}` };

  const fetchRoles = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/roles`, { headers: authHeaders });
      setRoles(res.data);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '获取角色列表失败');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  const fetchPermissions = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/permissions`, { headers: authHeaders });
      setAllPerms(res.data);
    } catch { /* ignore */ }
  }, [accessToken]);

  useEffect(() => {
    fetchRoles();
    fetchPermissions();
  }, [fetchRoles, fetchPermissions]);

  const handleCreateRole = async (values: any) => {
    try {
      await axios.post(`${API_BASE}/roles`, {
        name: values.name,
        description: values.description,
      }, { headers: authHeaders });
      message.success('角色创建成功');
      setCreateOpen(false);
      createForm.resetFields();
      fetchRoles();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '创建角色失败');
    }
  };

  const handleDeleteRole = async (roleId: string, roleName: string) => {
    try {
      await axios.delete(`${API_BASE}/roles/${roleId}`, { headers: authHeaders });
      message.success(`已删除角色 ${roleName}`);
      fetchRoles();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '删除角色失败');
    }
  };

  const handleTogglePermission = async (role: RoleRecord, perm: PermissionRecord, checked: boolean) => {
    try {
      if (checked) {
        await axios.post(`${API_BASE}/roles/${role.id}/permissions`, {
          permission_id: perm.id,
        }, { headers: authHeaders });
        message.success(`已赋予 ${perm.resource}:${perm.action}`);
      } else {
        await axios.delete(`${API_BASE}/roles/${role.id}/permissions/${perm.id}`, {
          headers: authHeaders,
        });
        message.success(`已移除 ${perm.resource}:${perm.action}`);
      }
      // 刷新角色列表，并更新 detailRole
      const res = await axios.get(`${API_BASE}/roles`, { headers: authHeaders });
      setRoles(res.data);
      const updated = res.data.find((r: RoleRecord) => r.id === role.id);
      if (updated) setDetailRole(updated);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '操作失败');
    }
  };

  // 按 resource 分组权限
  const permsByResource = allPerms.reduce<Record<string, PermissionRecord[]>>((acc, p) => {
    if (!acc[p.resource]) acc[p.resource] = [];
    acc[p.resource].push(p);
    return acc;
  }, {});

  if (!hasPermission('users:read')) {
    return (
      <Card>
        <Text type="danger">权限不足：需要 users:read 权限</Text>
      </Card>
    );
  }

  return (
    <Spin spinning={loading}>
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0 }}>角色权限管理</Title>
        {hasPermission('users:write') && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建角色
          </Button>
        )}
      </div>

      <Row gutter={[16, 16]}>
        {roles.map((role) => (
          <Col xs={24} sm={12} lg={8} xl={6} key={role.id}>
            <Card
              hoverable
              title={
                <Space>
                  <Tag color={ROLE_TAG_COLOR[role.name] || 'purple'} style={{ margin: 0 }}>
                    {role.name}
                  </Tag>
                  {role.is_system && (
                    <Tooltip title="系统预置角色，不可删除">
                      <LockOutlined style={{ color: '#999', fontSize: 12 }} />
                    </Tooltip>
                  )}
                </Space>
              }
              extra={
                <Space>
                  <Button
                    size="small"
                    icon={<SafetyOutlined />}
                    onClick={() => { setDetailRole(role); setDetailOpen(true); }}
                  >
                    权限
                  </Button>
                  {!role.is_system && hasPermission('users:write') && (
                    <Popconfirm
                      title={`确认删除角色 "${role.name}"？`}
                      description="该角色将从所有用户中撤销"
                      onConfirm={() => handleDeleteRole(role.id, role.name)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                    >
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  )}
                </Space>
              }
              style={{ height: '100%' }}
            >
              <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
                {role.description || '暂无描述'}
              </Text>
              <div>
                <Text style={{ fontSize: 12, color: '#999' }}>已授权限（{role.permissions.length}）：</Text>
                <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {role.permissions.length === 0 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>无</Text>
                  ) : (
                    role.permissions.map((p) => (
                      <Tag
                        key={p.id}
                        color={RESOURCE_COLOR[p.resource] || 'default'}
                        style={{ fontSize: 11 }}
                      >
                        {p.resource}:{p.action}
                      </Tag>
                    ))
                  )}
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 创建角色 Modal */}
      <Modal
        title="新建自定义角色"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        onOk={() => createForm.submit()}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreateRole}>
          <Form.Item
            name="name"
            label="角色名称"
            rules={[{ required: true, min: 2, message: '角色名称至少 2 个字符' }]}
          >
            <Input placeholder="小写字母/数字/下划线，如 data_viewer" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea placeholder="可选，描述该角色的用途" rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 角色权限详情 Modal */}
      <Modal
        title={
          <Space>
            <SafetyOutlined />
            <span>角色权限详情 — {detailRole?.name}</span>
            {detailRole?.is_system && <Tag color="orange">系统角色</Tag>}
          </Space>
        }
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={[
          <Button key="close" onClick={() => setDetailOpen(false)}>关闭</Button>
        ]}
        width={680}
      >
        {detailRole && (
          <div>
            {detailRole.description && (
              <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
                {detailRole.description}
              </Text>
            )}
            <Divider style={{ margin: '12px 0' }} />
            <Text strong style={{ display: 'block', marginBottom: 8 }}>
              权限分配（勾选即授权，取消即撤权）
            </Text>
            {Object.entries(permsByResource).map(([resource, perms]) => (
              <div key={resource} style={{ marginBottom: 12 }}>
                <Tag color={RESOURCE_COLOR[resource] || 'default'} style={{ marginBottom: 6 }}>
                  {resource}
                </Tag>
                <Row gutter={[8, 4]}>
                  {perms.map((perm) => {
                    const hasIt = detailRole.permissions.some((p) => p.id === perm.id);
                    const canEdit = hasPermission('users:assign_role');
                    return (
                      <Col span={12} key={perm.id}>
                        <Tooltip title={perm.description || ''}>
                          <Checkbox
                            checked={hasIt}
                            disabled={!canEdit}
                            onChange={(e) => handleTogglePermission(detailRole, perm, e.target.checked)}
                          >
                            <Text style={{ fontSize: 13 }}>{perm.action}</Text>
                            {hasIt && <CheckOutlined style={{ color: '#52c41a', marginLeft: 4, fontSize: 11 }} />}
                          </Checkbox>
                        </Tooltip>
                      </Col>
                    );
                  })}
                </Row>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
    </Spin>
  );
};

export default Roles;
