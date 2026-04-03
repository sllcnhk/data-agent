/**
 * 用户管理页面（需 users:read 权限）
 *
 * 功能：
 * - 用户列表（服务端分页 + 列排序）
 * - 创建本地账号
 * - 修改用户状态（启用/停用）
 * - 分配/撤销角色
 */
import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, Tag, Space,
  Typography, Card, message, Switch, Tooltip,
} from 'antd';
import type { TablePaginationConfig } from 'antd/es/table';
import type { SorterResult } from 'antd/es/table/interface';
import {
  PlusOutlined, UserOutlined, CrownOutlined,
} from '@ant-design/icons';
import axios from 'axios';
import { useAuthStore } from '@/store/useAuthStore';

const { Title } = Typography;
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';

interface UserRecord {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  auth_source: string;
  is_active: boolean;
  is_superadmin: boolean;
  roles: string[];
  last_login_at: string | null;
  created_at: string;
}

interface RoleRecord {
  id: string;
  name: string;
  description: string;
}

const ROLE_COLOR: Record<string, string> = {
  viewer: 'default',
  analyst: 'blue',
  admin: 'orange',
  superadmin: 'red',
};

const Users: React.FC = () => {
  const { accessToken, hasPermission } = useAuthStore();
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [roles, setRoles] = useState<RoleRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 });
  const [sortField, setSortField] = useState('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

  // use ref to avoid stale closure in fetchUsers
  const paginationRef = useRef(pagination);
  const sortFieldRef = useRef(sortField);
  const sortOrderRef = useRef(sortOrder);
  paginationRef.current = pagination;
  sortFieldRef.current = sortField;
  sortOrderRef.current = sortOrder;

  const authHeaders = { Authorization: `Bearer ${accessToken}` };

  const fetchUsers = useCallback(async (
    page = paginationRef.current.current,
    pageSize = paginationRef.current.pageSize,
    sf = sortFieldRef.current,
    so = sortOrderRef.current,
  ) => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/users`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        params: { page, page_size: pageSize, sort_by: sf, sort_order: so },
      });
      setUsers(res.data.items);
      setTotal(res.data.total);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '获取用户列表失败');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  const fetchRoles = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/roles`, { headers: authHeaders });
      setRoles(res.data);
    } catch { /* ignore */ }
  }, [accessToken]);

  useEffect(() => {
    fetchUsers();
    fetchRoles();
  }, [fetchUsers, fetchRoles]);

  const handleTableChange = (
    pag: TablePaginationConfig,
    _filters: any,
    sorter: SorterResult<UserRecord> | SorterResult<UserRecord>[],
  ) => {
    const s = Array.isArray(sorter) ? sorter[0] : sorter;
    const newPage = pag.current ?? 1;
    const newPageSize = pag.pageSize ?? 20;
    const newField = (s.field as string) || 'created_at';
    const newOrder: 'asc' | 'desc' = s.order === 'ascend' ? 'asc' : 'desc';

    setPagination({ current: newPage, pageSize: newPageSize });
    setSortField(newField);
    setSortOrder(newOrder);
    fetchUsers(newPage, newPageSize, newField, newOrder);
  };

  const handleCreate = async (values: any) => {
    try {
      await axios.post(`${API_BASE}/users`, {
        username: values.username,
        password: values.password,
        display_name: values.display_name,
        email: values.email,
        role_names: values.role_names || ['analyst'],
      }, { headers: authHeaders });
      message.success('用户创建成功');
      setCreateOpen(false);
      form.resetFields();
      fetchUsers();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '创建失败');
    }
  };

  const handleToggleActive = async (user: UserRecord) => {
    try {
      await axios.put(`${API_BASE}/users/${user.id}`, {
        is_active: !user.is_active,
      }, { headers: authHeaders });
      message.success(user.is_active ? '已停用用户' : '已启用用户');
      fetchUsers();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '操作失败');
    }
  };

  const handleAssignRole = async (userId: string, roleName: string) => {
    try {
      await axios.post(`${API_BASE}/users/${userId}/roles`, { role_name: roleName }, {
        headers: authHeaders,
      });
      message.success(`已分配角色 ${roleName}`);
      fetchUsers();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '分配角色失败');
    }
  };

  const handleRevokeRole = async (userId: string, roleId: string) => {
    try {
      await axios.delete(`${API_BASE}/users/${userId}/roles/${roleId}`, {
        headers: authHeaders,
      });
      message.success('已撤销角色');
      fetchUsers();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '撤销角色失败');
    }
  };

  // 将 'asc'/'desc' 转换为 antd 的 'ascend'/'descend'
  const antSortOrder = (field: string) =>
    sortField === field ? (sortOrder === 'asc' ? 'ascend' : 'descend') : null;

  const columns = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      sorter: true,
      sortOrder: antSortOrder('username'),
      render: (name: string, rec: UserRecord) => (
        <Space>
          {rec.is_superadmin && (
            <Tooltip title="超级管理员">
              <CrownOutlined style={{ color: '#f5a623' }} />
            </Tooltip>
          )}
          <Tooltip title={`账号: ${name}`}>
            <span>{rec.display_name || name}</span>
          </Tooltip>
        </Space>
      ),
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      render: (e: string) => e || '-',
    },
    {
      title: '来源',
      dataIndex: 'auth_source',
      key: 'auth_source',
      sorter: true,
      sortOrder: antSortOrder('auth_source'),
      render: (s: string) => <Tag>{s}</Tag>,
    },
    {
      title: '角色',
      dataIndex: 'roles',
      key: 'roles',
      render: (roleList: string[], rec: UserRecord) => (
        <Space wrap size={4}>
          {roleList.map((r) => (
            <Tag
              color={ROLE_COLOR[r] || 'purple'}
              key={r}
              closable={hasPermission('users:assign_role') && !rec.is_superadmin}
              onClose={() => {
                const role = roles.find((ro) => ro.name === r);
                if (role) handleRevokeRole(rec.id, role.id);
              }}
            >
              {r}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      sorter: true,
      sortOrder: antSortOrder('is_active'),
      render: (active: boolean, rec: UserRecord) => (
        <Switch
          checked={active}
          size="small"
          disabled={rec.is_superadmin || !hasPermission('users:write')}
          onChange={() => handleToggleActive(rec)}
        />
      ),
    },
    {
      title: '最后登录',
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      sorter: true,
      sortOrder: antSortOrder('last_login_at'),
      render: (t: string) => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      sorter: true,
      sortOrder: antSortOrder('created_at'),
      render: (t: string) => new Date(t).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: any, rec: UserRecord) => (
        <Space>
          {hasPermission('users:assign_role') && !rec.is_superadmin && (
            <Select
              placeholder="分配角色"
              size="small"
              style={{ width: 120 }}
              value={null}
              onChange={(roleName) => handleAssignRole(rec.id, roleName)}
            >
              {roles
                .filter((r) => !rec.roles.includes(r.name))
                .map((r) => (
                  <Select.Option key={r.id} value={r.name}>{r.name}</Select.Option>
                ))}
            </Select>
          )}
        </Space>
      ),
    },
  ];

  if (!hasPermission('users:read')) {
    return (
      <Card>
        <Typography.Text type="danger">权限不足：需要 users:read 权限</Typography.Text>
      </Card>
    );
  }

  return (
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>用户管理</Title>
        {hasPermission('users:write') && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建用户
          </Button>
        )}
      </div>

      <Table
        dataSource={users}
        columns={columns}
        rowKey="id"
        loading={loading}
        onChange={handleTableChange}
        pagination={{
          current: pagination.current,
          pageSize: pagination.pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: ['20', '50', '100'],
          showTotal: (t) => `共 ${t} 个用户`,
        }}
      />

      {/* 创建用户 Modal */}
      <Modal
        title="新建本地账号"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 2 }]}>
            <Input prefix={<UserOutlined />} placeholder="小写字母/数字/下划线" />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6 }]}>
            <Input.Password placeholder="至少 6 位" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="role_names" label="初始角色" initialValue={['analyst']}>
            <Select mode="multiple">
              {roles.map((r) => (
                <Select.Option key={r.name} value={r.name}>
                  {r.name} — {r.description}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
};

export default Users;
