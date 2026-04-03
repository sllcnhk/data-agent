import React, { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  Select,
  message,
  Tabs,
  Tag,
  Drawer,
  Typography,
  Popconfirm,
  Row,
  Col,
  Badge,
  Empty,
  Spin,
  Tooltip,
  Alert,
  Collapse,
  List,
  Divider,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  EyeOutlined,
  ReloadOutlined,
  BookOutlined,
  UserOutlined,
  TagOutlined,
  FileMarkdownOutlined,
  LockOutlined,
  EditOutlined,
  ProjectOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
  RiseOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { skillApi } from '@/services/api';

const { TextArea } = Input;
const { Option } = Select;
const { Text, Paragraph } = Typography;
const { Panel } = Collapse;

// ─── constants ───────────────────────────────────────────────────────────────

const CATEGORY_COLOR: Record<string, string> = {
  engineering: 'blue',
  analytics: 'green',
  general: 'default',
  system: 'purple',
};

const CATEGORY_LABEL: Record<string, string> = {
  engineering: '数据加工',
  analytics: '数据分析',
  general: '通用',
  system: '系统',
};

const PRIORITY_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'default',
};

const PRIORITY_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

const TIER_COLOR: Record<string, string> = {
  system: 'purple',
  project: 'blue',
  user: 'green',
};

const TIER_LABEL: Record<string, string> = {
  system: '系统',
  project: '项目',
  user: '用户',
};

// ─── SkillMD interface ────────────────────────────────────────────────────────

interface SkillMD {
  name: string;
  version: string;
  description: string;
  triggers: string[];
  category: string;
  priority: string;
  content: string;
  filepath: string;
  is_user_defined?: boolean;
  is_readonly?: boolean;
  tier?: string;
  always_inject?: boolean;
}

// ─── SkillCard ────────────────────────────────────────────────────────────────

const SkillCard: React.FC<{
  skill: SkillMD;
  onView: (skill: SkillMD) => void;
  onEdit?: (skill: SkillMD) => void;
  onDelete?: (skill: SkillMD) => void;
  onPromote?: (skill: SkillMD) => void;
}> = ({ skill, onView, onEdit, onDelete, onPromote }) => (
  <Card
    size="small"
    style={{ height: '100%', opacity: skill.is_readonly ? 0.92 : 1 }}
    actions={[
      <Button
        key="view"
        type="link"
        icon={<EyeOutlined />}
        onClick={() => onView(skill)}
        size="small"
      >
        查看
      </Button>,
      skill.is_readonly ? (
        <Tooltip key="readonly" title="系统内置技能由开发者通过 VS Code 管理，前端只读">
          <Button type="link" icon={<LockOutlined />} size="small" disabled>
            只读
          </Button>
        </Tooltip>
      ) : onEdit ? (
        <Button key="edit" type="link" icon={<EditOutlined />} size="small" onClick={() => onEdit(skill)}>
          编辑
        </Button>
      ) : null,
      !skill.is_readonly && onDelete ? (
        <Popconfirm
          key="del"
          title={`确定删除技能 "${skill.name}" 吗？`}
          onConfirm={() => onDelete(skill)}
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
        >
          <Button type="link" danger icon={<DeleteOutlined />} size="small">
            删除
          </Button>
        </Popconfirm>
      ) : null,
      !skill.is_readonly && onPromote ? (
        <Tooltip key="promote" title="申请将此技能晋升为项目级技能，需要管理员审核">
          <Button type="link" icon={<RiseOutlined />} size="small" onClick={() => onPromote(skill)}>
            晋升
          </Button>
        </Tooltip>
      ) : null,
    ].filter(Boolean)}
  >
    <div style={{ marginBottom: 8 }}>
      <Space size={4} wrap>
        {skill.tier && (
          <Tag color={TIER_COLOR[skill.tier] || 'default'} style={{ fontSize: 11 }}>
            {TIER_LABEL[skill.tier] || skill.tier}
          </Tag>
        )}
        <Tag color={CATEGORY_COLOR[skill.category] || 'default'}>
          {CATEGORY_LABEL[skill.category] || skill.category}
        </Tag>
        <Tag color={PRIORITY_COLOR[skill.priority] || 'default'}>
          优先级: {PRIORITY_LABEL[skill.priority] || skill.priority}
        </Tag>
        {skill.always_inject && (
          <Tooltip title="始终注入：每次对话均自动激活，不依赖触发词">
            <Tag icon={<SafetyOutlined />} color="volcano" style={{ fontSize: 11 }}>
              始终注入
            </Tag>
          </Tooltip>
        )}
        <Text type="secondary" style={{ fontSize: 11 }}>
          v{skill.version}
        </Text>
      </Space>
    </div>

    <div style={{ fontWeight: 600, marginBottom: 4, fontFamily: 'monospace', fontSize: 13 }}>
      {skill.name}
    </div>

    <Paragraph
      ellipsis={{ rows: 2 }}
      style={{ marginBottom: 8, fontSize: 13, color: '#595959' }}
    >
      {skill.description || '暂无描述'}
    </Paragraph>

    <div>
      <TagOutlined style={{ color: '#8c8c8c', marginRight: 4, fontSize: 11 }} />
      {skill.always_inject ? (
        <Text type="secondary" style={{ fontSize: 11 }}>（无触发词，始终激活）</Text>
      ) : skill.triggers.length === 0 ? (
        <Text type="secondary" style={{ fontSize: 11 }}>（无触发词）</Text>
      ) : (
        <>
          {skill.triggers.slice(0, 5).map((t) => (
            <Tag key={t} style={{ fontSize: 11, marginBottom: 4 }}>
              {t}
            </Tag>
          ))}
          {skill.triggers.length > 5 && (
            <Text type="secondary" style={{ fontSize: 11 }}>
              +{skill.triggers.length - 5}
            </Text>
          )}
        </>
      )}
    </div>
  </Card>
);

// ─── SkillFormFields (shared) ─────────────────────────────────────────────────

const SkillFormFields: React.FC<{ showAdminToken?: boolean; nameReadonly?: boolean }> = ({
  showAdminToken = false,
  nameReadonly = false,
}) => (
  <>
    <Row gutter={16}>
      <Col span={12}>
        <Form.Item
          name="name"
          label="技能名称"
          rules={nameReadonly ? [] : [{ required: true, message: '请输入技能名称' }]}
          tooltip={nameReadonly ? undefined : '系统会自动转为 kebab-case（如 my-skill）'}
        >
          {nameReadonly ? (
            <Input disabled />
          ) : (
            <Input placeholder="my-skill-name" />
          )}
        </Form.Item>
      </Col>
      <Col span={6}>
        <Form.Item name="category" label="类别">
          <Select>
            <Option value="general">通用</Option>
            <Option value="engineering">数据加工</Option>
            <Option value="analytics">数据分析</Option>
          </Select>
        </Form.Item>
      </Col>
      <Col span={6}>
        <Form.Item name="priority" label="优先级">
          <Select>
            <Option value="high">高</Option>
            <Option value="medium">中</Option>
            <Option value="low">低</Option>
          </Select>
        </Form.Item>
      </Col>
    </Row>

    <Form.Item
      name="description"
      label="描述"
      rules={[{ required: true, message: '请输入描述' }]}
      tooltip="一行简要描述，≤120字"
    >
      <Input placeholder="简要描述此技能的作用" maxLength={120} showCount />
    </Form.Item>

    <Form.Item
      name="triggers"
      label="触发关键词"
      rules={[{ required: true, message: '请输入至少一个触发词' }]}
      tooltip="多个关键词用逗号或换行分隔，用户消息含这些词时技能自动激活"
    >
      <TextArea
        rows={2}
        placeholder="关键词1, 关键词2, keyword3&#10;（逗号或换行分隔）"
      />
    </Form.Item>

    <Form.Item
      name="content"
      label="技能内容 (Markdown)"
      rules={[{ required: true, message: '请输入技能内容' }, { min: 10, message: '至少10个字符' }]}
      tooltip="技能激活后注入系统提示词的 Markdown 内容"
    >
      <TextArea
        rows={12}
        placeholder={`# 技能标题\n\n## 角色定义\n你是...\n\n## 核心行为\n1. ...\n2. ...\n\n## 输出格式\n...`}
        style={{ fontFamily: 'monospace', fontSize: 13 }}
      />
    </Form.Item>

    {showAdminToken && (
      <Form.Item
        name="adminToken"
        label="管理员 Token"
        rules={[{ required: true, message: '请输入管理员 Token' }]}
        tooltip="项目技能需要管理员权限（X-Admin-Token）"
      >
        <Input.Password placeholder="ADMIN_SECRET_TOKEN" />
      </Form.Item>
    )}
  </>
);

// ─── Main Skills page ─────────────────────────────────────────────────────────

const Skills: React.FC = () => {
  const [mdSkills, setMdSkills] = useState<SkillMD[]>([]);
  const [userSkills, setUserSkills] = useState<SkillMD[]>([]);
  const [projectSkills, setProjectSkills] = useState<SkillMD[]>([]);
  const [loadingMd, setLoadingMd] = useState(false);
  const [loadingUser, setLoadingUser] = useState(false);
  const [loadingProject, setLoadingProject] = useState(false);

  // Admin token
  const [savedAdminToken, setSavedAdminToken] = useState<string>(
    () => sessionStorage.getItem('admin_token') || ''
  );

  // Detail drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [viewSkill, setViewSkill] = useState<SkillMD | null>(null);

  // Create user skill
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [creatingUser, setCreatingUser] = useState(false);
  const [userForm] = Form.useForm();

  // Edit user skill
  const [editUserOpen, setEditUserOpen] = useState(false);
  const [editingUser, setEditingUser] = useState(false);
  const [editUserSkill, setEditUserSkill] = useState<SkillMD | null>(null);
  const [editUserForm] = Form.useForm();

  // Create project skill
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectForm] = Form.useForm();

  // Edit project skill
  const [editProjectOpen, setEditProjectOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(false);
  const [editProjectSkill, setEditProjectSkill] = useState<SkillMD | null>(null);
  const [editProjectForm] = Form.useForm();

  // Promote user skill to project
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [promoteSkill, setPromoteSkill] = useState<SkillMD | null>(null);
  const [promoteForm] = Form.useForm();

  // Skill trigger test panel
  const [testMessage, setTestMessage] = useState('');
  const [testResult, setTestResult] = useState<any>(null);
  const [testLoading, setTestLoading] = useState(false);

  // ── Data loaders ──────────────────────────────────────────────────────────

  const loadMdSkills = useCallback(async () => {
    setLoadingMd(true);
    try {
      const data = await skillApi.getMdSkills();
      setMdSkills(Array.isArray(data) ? data : []);
    } catch (err: any) {
      if (!err?.message?.includes('404')) {
        message.error('加载系统技能失败: ' + (err?.message || ''));
      }
      setMdSkills([]);
    } finally {
      setLoadingMd(false);
    }
  }, []);

  const loadUserSkills = useCallback(async () => {
    setLoadingUser(true);
    try {
      const data = await skillApi.getUserSkills();
      setUserSkills(
        (Array.isArray(data) ? data : []).map((s: any) => ({
          name: s.name || s.filename?.replace('.md', '') || '',
          version: s.version || '1.0',
          description: s.description || '',
          triggers: s.triggers || [],
          category: s.category || 'general',
          priority: s.priority || 'medium',
          content: s.content || '',
          filepath: s.filepath || '',
          is_user_defined: true,
          tier: 'user',
        }))
      );
    } catch (err: any) {
      if (!err?.message?.includes('404')) {
        message.error('加载用户自定义技能失败: ' + (err?.message || ''));
      }
      setUserSkills([]);
    } finally {
      setLoadingUser(false);
    }
  }, []);

  const loadProjectSkills = useCallback(async () => {
    setLoadingProject(true);
    try {
      const data = await skillApi.getProjectSkills();
      setProjectSkills(
        (Array.isArray(data) ? data : []).map((s: any) => ({ ...s, tier: 'project' }))
      );
    } catch (err: any) {
      if (!err?.message?.includes('404')) {
        message.error('加载项目技能失败: ' + (err?.message || ''));
      }
      setProjectSkills([]);
    } finally {
      setLoadingProject(false);
    }
  }, []);

  useEffect(() => {
    loadMdSkills();
    loadUserSkills();
    loadProjectSkills();
  }, [loadMdSkills, loadUserSkills, loadProjectSkills]);

  // ── Helpers ───────────────────────────────────────────────────────────────

  const parseTriggers = (raw: string): string[] =>
    raw.split(/[,，\n]+/).map((t: string) => t.trim()).filter(Boolean);

  const handleView = (skill: SkillMD) => {
    setViewSkill(skill);
    setDrawerOpen(true);
  };

  const getAdminToken = (formValues: any): string => {
    const token = formValues.adminToken || savedAdminToken;
    if (token) {
      setSavedAdminToken(token);
      sessionStorage.setItem('admin_token', token);
    }
    return token;
  };

  // ── Skill trigger test ────────────────────────────────────────────────────

  const handleTestTrigger = async () => {
    if (!testMessage.trim()) {
      message.warning('请输入测试消息');
      return;
    }
    setTestLoading(true);
    try {
      const result = await (skillApi as any).previewSkillTrigger(testMessage);
      setTestResult(result);
    } catch (err: any) {
      message.error('触发测试失败: ' + (err?.message || ''));
    } finally {
      setTestLoading(false);
    }
  };

  // ── User skill CRUD ───────────────────────────────────────────────────────

  const handleDeleteUserSkill = async (skill: SkillMD) => {
    try {
      await skillApi.deleteUserSkill(skill.name);
      message.success(`技能 "${skill.name}" 已删除`);
      loadUserSkills();
      loadMdSkills();
    } catch (err: any) {
      message.error('删除失败: ' + err.message);
    }
  };

  const handleCreateUserSkill = async () => {
    try {
      const values = await userForm.validateFields();
      setCreatingUser(true);
      await skillApi.createUserSkill({
        name: values.name,
        description: values.description,
        triggers: parseTriggers(values.triggers || ''),
        category: values.category,
        priority: values.priority,
        content: values.content,
      });
      message.success(`技能 "${values.name}" 已创建并即时热加载`);
      setCreateUserOpen(false);
      userForm.resetFields();
      loadUserSkills();
      loadMdSkills();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error('创建失败: ' + (err?.message || String(err)));
    } finally {
      setCreatingUser(false);
    }
  };

  const handleOpenEditUser = (skill: SkillMD) => {
    setEditUserSkill(skill);
    editUserForm.setFieldsValue({
      name: skill.name,
      description: skill.description,
      triggers: skill.triggers.join(', '),
      category: skill.category,
      priority: skill.priority,
      content: skill.content,
    });
    setEditUserOpen(true);
  };

  const handleEditUserSkill = async () => {
    if (!editUserSkill) return;
    try {
      const values = await editUserForm.validateFields();
      setEditingUser(true);
      await (skillApi as any).updateUserSkill(editUserSkill.name, {
        description: values.description,
        triggers: parseTriggers(values.triggers || ''),
        category: values.category,
        priority: values.priority,
        content: values.content,
      });
      message.success(`技能 "${editUserSkill.name}" 已更新`);
      setEditUserOpen(false);
      editUserForm.resetFields();
      setEditUserSkill(null);
      loadUserSkills();
      loadMdSkills();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error('更新失败: ' + (err?.message || String(err)));
    } finally {
      setEditingUser(false);
    }
  };

  // ── Promote user skill ────────────────────────────────────────────────────

  const handleOpenPromote = (skill: SkillMD) => {
    setPromoteSkill(skill);
    promoteForm.setFieldsValue({ adminToken: savedAdminToken });
    setPromoteOpen(true);
  };

  const handlePromoteSkill = async () => {
    if (!promoteSkill) return;
    try {
      const values = await promoteForm.validateFields();
      setPromoting(true);
      const adminToken = getAdminToken(values);
      await skillApi.createProjectSkill(
        {
          name: promoteSkill.name + '-promoted',
          description: promoteSkill.description,
          triggers: promoteSkill.triggers,
          category: promoteSkill.category,
          priority: promoteSkill.priority,
          content: promoteSkill.content,
        },
        adminToken
      );
      message.success(`技能 "${promoteSkill.name}" 已晋升为项目技能`);
      setPromoteOpen(false);
      promoteForm.resetFields();
      setPromoteSkill(null);
      loadProjectSkills();
      loadMdSkills();
    } catch (err: any) {
      if (err?.errorFields) return;
      const status = err?.response?.status;
      if (status === 403 || status === 401) {
        message.error('管理员 Token 错误，请检查后重试');
      } else if (status === 409) {
        message.error('项目技能中已存在同名技能，请先修改名称再晋升');
      } else {
        message.error('晋升失败: ' + (err?.message || String(err)));
      }
    } finally {
      setPromoting(false);
    }
  };

  // ── Project skill CRUD ────────────────────────────────────────────────────

  const handleCreateProjectSkill = async () => {
    try {
      const values = await projectForm.validateFields();
      setCreatingProject(true);
      const adminToken = getAdminToken(values);
      await skillApi.createProjectSkill(
        {
          name: values.name,
          description: values.description,
          triggers: parseTriggers(values.triggers || ''),
          category: values.category,
          priority: values.priority,
          content: values.content,
        },
        adminToken
      );
      message.success(`项目技能 "${values.name}" 已创建`);
      setCreateProjectOpen(false);
      projectForm.resetFields();
      loadProjectSkills();
      loadMdSkills();
    } catch (err: any) {
      if (err?.errorFields) return;
      const status = err?.response?.status;
      if (status === 403 || status === 401) {
        message.error('管理员 Token 错误，请检查后重试');
      } else {
        message.error('创建失败: ' + (err?.message || String(err)));
      }
    } finally {
      setCreatingProject(false);
    }
  };

  const handleOpenEditProject = (skill: SkillMD) => {
    setEditProjectSkill(skill);
    editProjectForm.setFieldsValue({
      description: skill.description,
      triggers: skill.triggers.join(', '),
      category: skill.category,
      priority: skill.priority,
      content: skill.content,
      adminToken: savedAdminToken,
    });
    setEditProjectOpen(true);
  };

  const handleEditProjectSkill = async () => {
    if (!editProjectSkill) return;
    try {
      const values = await editProjectForm.validateFields();
      setEditingProject(true);
      const adminToken = getAdminToken(values);
      await skillApi.updateProjectSkill(
        editProjectSkill.name,
        {
          description: values.description,
          triggers: parseTriggers(values.triggers || ''),
          category: values.category,
          priority: values.priority,
          content: values.content,
        },
        adminToken
      );
      message.success(`项目技能 "${editProjectSkill.name}" 已更新`);
      setEditProjectOpen(false);
      editProjectForm.resetFields();
      setEditProjectSkill(null);
      loadProjectSkills();
      loadMdSkills();
    } catch (err: any) {
      if (err?.errorFields) return;
      const status = err?.response?.status;
      if (status === 403 || status === 401) {
        message.error('管理员 Token 错误，请检查后重试');
      } else {
        message.error('更新失败: ' + (err?.message || String(err)));
      }
    } finally {
      setEditingProject(false);
    }
  };

  const handleDeleteProjectSkill = async (skill: SkillMD) => {
    const token = savedAdminToken;
    if (!token) {
      message.warning('请先在新建/编辑对话框中输入管理员 Token 以缓存凭证');
      return;
    }
    try {
      await skillApi.deleteProjectSkill(skill.name, token);
      message.success(`项目技能 "${skill.name}" 已删除`);
      loadProjectSkills();
      loadMdSkills();
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 403 || status === 401) {
        message.error('管理员 Token 错误，请检查后重试');
      } else {
        message.error('删除失败: ' + (err?.message || String(err)));
      }
    }
  };

  // ── Derived ───────────────────────────────────────────────────────────────

  const systemSkills = mdSkills.filter((s) => !s.is_user_defined && s.tier === 'system');

  // ── Table columns ─────────────────────────────────────────────────────────

  const projectColumns = [
    {
      title: '技能名称',
      dataIndex: 'name',
      key: 'name',
      render: (text: string) => <Text code style={{ fontSize: 13 }}>{text}</Text>,
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '类别',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (cat: string) => (
        <Tag color={CATEGORY_COLOR[cat] || 'default'}>{CATEGORY_LABEL[cat] || cat}</Tag>
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 70,
      render: (v: string) => <Text type="secondary">v{v}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: any, record: SkillMD) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => handleView(record)}>查看</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleOpenEditProject(record)}>编辑</Button>
          <Popconfirm
            title={`确定删除项目技能 "${record.name}" 吗？`}
            description="需要已缓存的管理员 Token，删除后不可恢复。"
            onConfirm={() => handleDeleteProjectSkill(record)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const userColumns = [
    {
      title: '技能名称',
      dataIndex: 'name',
      key: 'name',
      render: (text: string) => <Text code style={{ fontSize: 13 }}>{text}</Text>,
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '类别',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (cat: string) => (
        <Tag color={CATEGORY_COLOR[cat] || 'default'}>{CATEGORY_LABEL[cat] || cat}</Tag>
      ),
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 80,
      render: (p: string) => (
        <Tag color={PRIORITY_COLOR[p] || 'default'}>{PRIORITY_LABEL[p] || p}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: any, record: SkillMD) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => handleView(record)}>查看</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleOpenEditUser(record)}>编辑</Button>
          <Tooltip title="晋升为项目级技能（需管理员 Token）">
            <Button size="small" icon={<RiseOutlined />} onClick={() => handleOpenPromote(record)} />
          </Tooltip>
          <Popconfirm
            title={`确定删除 "${record.name}" 吗？`}
            onConfirm={() => handleDeleteUserSkill(record)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── Skill trigger test panel ──────────────────────────────────────────────

  const triggerTestPanel = (
    <Collapse ghost style={{ marginBottom: 16 }}>
      <Panel
        key="trigger-test"
        header={
          <Space>
            <ThunderboltOutlined style={{ color: '#faad14' }} />
            <Text strong>触发测试</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>输入消息，预览哪些技能会被激活</Text>
          </Space>
        }
      >
        <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
          <Input
            placeholder="输入一条用户消息，例如：帮我分析用户留存率..."
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            onPressEnter={handleTestTrigger}
            prefix={<SearchOutlined style={{ color: '#8c8c8c' }} />}
          />
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={testLoading}
            onClick={handleTestTrigger}
          >
            测试
          </Button>
        </Space.Compact>

        {testResult && (
          <Card size="small" style={{ background: '#fafafa' }}>
            <Row gutter={16}>
              <Col span={12}>
                <Text strong style={{ fontSize: 12, color: '#8c8c8c' }}>触发的技能：</Text>
                {Object.entries(testResult.triggered || {}).map(([tier, skills]: [string, any]) =>
                  skills.length > 0 ? (
                    <div key={tier} style={{ marginTop: 4 }}>
                      <Tag color={TIER_COLOR[tier] || 'default'} style={{ fontSize: 11 }}>
                        {TIER_LABEL[tier] || tier}
                      </Tag>
                      {skills.map((s: any) => (
                        <Tag key={s.name} style={{ fontSize: 11 }}>{s.name}</Tag>
                      ))}
                    </div>
                  ) : null
                )}
                {testResult.always_inject?.length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    <Tag color="volcano" style={{ fontSize: 11 }}>始终注入</Tag>
                    {testResult.always_inject.map((s: any) => (
                      <Tag key={s.name} style={{ fontSize: 11 }}>{s.name}</Tag>
                    ))}
                  </div>
                )}
                {Object.values(testResult.triggered || {}).every((v: any) => v.length === 0) &&
                  !testResult.always_inject?.length && (
                    <Text type="secondary" style={{ fontSize: 12 }}>无触发（仅 base skills 生效）</Text>
                  )}
              </Col>
              <Col span={12}>
                <Text strong style={{ fontSize: 12, color: '#8c8c8c' }}>
                  预估注入字符数：<Text style={{ color: testResult.total_chars > 6000 ? 'orange' : 'inherit' }}>
                    {testResult.total_chars}
                  </Text>
                  {testResult.total_chars > 8000 && (
                    <Tag color="orange" style={{ marginLeft: 4, fontSize: 11 }}>已降级为摘要模式</Tag>
                  )}
                </Text>
              </Col>
            </Row>
          </Card>
        )}
      </Panel>
    </Collapse>
  );

  // ── Tab definitions ───────────────────────────────────────────────────────

  const tabItems = [
    {
      key: 'system',
      label: (
        <span>
          <BookOutlined /> 系统技能
          <Badge count={systemSkills.length} style={{ marginLeft: 6, backgroundColor: '#722ed1' }} size="small" />
        </span>
      ),
      children: (
        <Spin spinning={loadingMd}>
          <Alert
            type="info"
            showIcon
            icon={<LockOutlined />}
            message="系统技能（只读）"
            description="由开发者通过 VS Code / Claude Code 管理，前端用户只能查看。标记「始终注入」的技能在每次对话中自动激活，无需触发词。"
            style={{ marginBottom: 16 }}
          />
          {systemSkills.length === 0 ? (
            <Empty description="暂无系统技能" />
          ) : (
            <Row gutter={[16, 16]}>
              {systemSkills.map((skill) => (
                <Col key={skill.name} xs={24} sm={12} lg={8} xl={6}>
                  <SkillCard skill={{ ...skill, is_readonly: true, tier: 'system' }} onView={handleView} />
                </Col>
              ))}
            </Row>
          )}
        </Spin>
      ),
    },
    {
      key: 'project',
      label: (
        <span>
          <ProjectOutlined /> 项目技能
          <Badge count={projectSkills.length} style={{ marginLeft: 6, backgroundColor: '#1677ff' }} size="small" />
        </span>
      ),
      children: (
        <Spin spinning={loadingProject}>
          <Alert
            type="warning"
            showIcon
            message="项目技能（管理员可编辑）"
            description="通过 REST API 管理，需要管理员 Token（X-Admin-Token）。项目技能可由管理员根据业务需求定制，作用范围覆盖所有用户。"
            style={{ marginBottom: 16 }}
          />
          <div style={{ marginBottom: 12, textAlign: 'right' }}>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                projectForm.setFieldValue('adminToken', savedAdminToken);
                setCreateProjectOpen(true);
              }}
            >
              新建项目技能
            </Button>
          </div>
          {projectSkills.length === 0 ? (
            <Empty description="暂无项目技能，点击「新建项目技能」创建" />
          ) : (
            <Table
              columns={projectColumns}
              dataSource={projectSkills}
              rowKey="name"
              locale={{ emptyText: <Empty description="暂无项目技能" /> }}
              pagination={{ pageSize: 10 }}
              size="small"
            />
          )}
        </Spin>
      ),
    },
    {
      key: 'user',
      label: (
        <span>
          <UserOutlined /> 我的技能
          <Badge count={userSkills.length} style={{ marginLeft: 6, backgroundColor: '#52c41a' }} size="small" />
        </span>
      ),
      children: (
        <Spin spinning={loadingUser}>
          <Table
            columns={userColumns}
            dataSource={userSkills}
            rowKey="name"
            locale={{ emptyText: <Empty description="暂无用户自定义技能，点击「新建技能」创建" /> }}
            pagination={{ pageSize: 10 }}
            size="small"
          />
        </Spin>
      ),
    },
  ];

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <>
      <Card
        title={
          <Space>
            <FileMarkdownOutlined />
            <span>技能仪表盘</span>
            <Text type="secondary" style={{ fontSize: 13, fontWeight: 400 }}>
              SKILL.md 热加载系统 — 三层架构 · 触发词驱动
            </Text>
          </Space>
        }
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => { loadMdSkills(); loadUserSkills(); loadProjectSkills(); }}
            >
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateUserOpen(true)}>
              新建技能
            </Button>
          </Space>
        }
        style={{ marginBottom: 0 }}
      >
        {triggerTestPanel}
        <Tabs items={tabItems} defaultActiveKey="system" />
      </Card>

      {/* ── Detail Drawer ─────────────────────────────────────────────────── */}
      <Drawer
        title={
          <Space>
            <FileMarkdownOutlined />
            <span style={{ fontFamily: 'monospace' }}>{viewSkill?.name}</span>
            {viewSkill?.tier && (
              <Tag color={TIER_COLOR[viewSkill.tier] || 'default'}>
                {TIER_LABEL[viewSkill.tier] || viewSkill.tier}
              </Tag>
            )}
            {viewSkill && (
              <Tag color={CATEGORY_COLOR[viewSkill.category] || 'default'}>
                {CATEGORY_LABEL[viewSkill.category] || viewSkill.category}
              </Tag>
            )}
            {viewSkill?.always_inject && (
              <Tag icon={<SafetyOutlined />} color="volcano">始终注入</Tag>
            )}
            {viewSkill?.is_readonly && (
              <Tooltip title="系统内置技能，前端只读">
                <Tag icon={<LockOutlined />} color="default">只读</Tag>
              </Tooltip>
            )}
          </Space>
        }
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={640}
        extra={
          viewSkill && (
            <Space wrap>
              {viewSkill.triggers.map((t) => <Tag key={t}>{t}</Tag>)}
            </Space>
          )
        }
      >
        {viewSkill && (
          <>
            <Card size="small" style={{ marginBottom: 16, background: '#fafafa' }}>
              <Space wrap>
                <Text type="secondary">描述:</Text>
                <Text>{viewSkill.description}</Text>
                <Text type="secondary">版本:</Text>
                <Text>v{viewSkill.version}</Text>
                <Text type="secondary">优先级:</Text>
                <Tag color={PRIORITY_COLOR[viewSkill.priority] || 'default'}>
                  {PRIORITY_LABEL[viewSkill.priority] || viewSkill.priority}
                </Tag>
              </Space>
            </Card>
            <pre
              style={{
                background: '#1e1e1e', color: '#d4d4d4', padding: '16px',
                borderRadius: '6px', fontSize: 13, lineHeight: 1.6,
                overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}
            >
              {viewSkill.content || '（内容为空）'}
            </pre>
          </>
        )}
      </Drawer>

      {/* ── Create user skill ─────────────────────────────────────────────── */}
      <Modal
        title={<Space><PlusOutlined />新建用户自定义技能</Space>}
        open={createUserOpen}
        onOk={handleCreateUserSkill}
        onCancel={() => { setCreateUserOpen(false); userForm.resetFields(); }}
        okText="创建并热加载"
        cancelText="取消"
        confirmLoading={creatingUser}
        width={700}
        destroyOnClose
      >
        <Form form={userForm} layout="vertical" initialValues={{ category: 'general', priority: 'medium' }}>
          <SkillFormFields />
        </Form>
      </Modal>

      {/* ── Edit user skill ───────────────────────────────────────────────── */}
      <Modal
        title={
          <Space>
            <EditOutlined />
            编辑我的技能: <Text code>{editUserSkill?.name}</Text>
          </Space>
        }
        open={editUserOpen}
        onOk={handleEditUserSkill}
        onCancel={() => { setEditUserOpen(false); editUserForm.resetFields(); setEditUserSkill(null); }}
        okText="保存更新"
        cancelText="取消"
        confirmLoading={editingUser}
        width={700}
        destroyOnClose
      >
        <Form form={editUserForm} layout="vertical" initialValues={{ category: 'general', priority: 'medium' }}>
          <SkillFormFields nameReadonly={true} />
        </Form>
      </Modal>

      {/* ── Promote user skill ────────────────────────────────────────────── */}
      <Modal
        title={
          <Space>
            <RiseOutlined />
            晋升为项目技能: <Text code>{promoteSkill?.name}</Text>
          </Space>
        }
        open={promoteOpen}
        onOk={handlePromoteSkill}
        onCancel={() => { setPromoteOpen(false); promoteForm.resetFields(); setPromoteSkill(null); }}
        okText="确认晋升"
        cancelText="取消"
        confirmLoading={promoting}
        width={480}
        destroyOnClose
      >
        <Alert
          type="info"
          style={{ marginBottom: 16 }}
          message="晋升说明"
          description={
            <>
              将你的技能 <Text code>{promoteSkill?.name}</Text> 复制为项目级技能，
              名称自动追加 <Text code>-promoted</Text> 后缀以避免冲突。
              晋升后可在「项目技能」标签页中查看和管理。
            </>
          }
        />
        <Form form={promoteForm} layout="vertical">
          <Form.Item
            name="adminToken"
            label="管理员 Token"
            rules={[{ required: true, message: '请输入管理员 Token' }]}
            tooltip="晋升需要管理员权限（X-Admin-Token），本次会话内自动缓存"
          >
            <Input.Password placeholder="ADMIN_SECRET_TOKEN" />
          </Form.Item>
        </Form>
      </Modal>

      {/* ── Create project skill ──────────────────────────────────────────── */}
      <Modal
        title={<Space><ProjectOutlined />新建项目技能（管理员）</Space>}
        open={createProjectOpen}
        onOk={handleCreateProjectSkill}
        onCancel={() => { setCreateProjectOpen(false); projectForm.resetFields(); }}
        okText="创建项目技能"
        cancelText="取消"
        confirmLoading={creatingProject}
        width={700}
        destroyOnClose
      >
        <Form form={projectForm} layout="vertical" initialValues={{ category: 'general', priority: 'medium' }}>
          <SkillFormFields showAdminToken={true} />
        </Form>
      </Modal>

      {/* ── Edit project skill ────────────────────────────────────────────── */}
      <Modal
        title={
          <Space>
            <EditOutlined />
            编辑项目技能: <Text code>{editProjectSkill?.name}</Text>
          </Space>
        }
        open={editProjectOpen}
        onOk={handleEditProjectSkill}
        onCancel={() => { setEditProjectOpen(false); editProjectForm.resetFields(); setEditProjectSkill(null); }}
        okText="保存更新"
        cancelText="取消"
        confirmLoading={editingProject}
        width={700}
        destroyOnClose
      >
        <Form form={editProjectForm} layout="vertical" initialValues={{ category: 'general', priority: 'medium' }}>
          <Form.Item label="技能名称（不可更改）">
            <Text code>{editProjectSkill?.name}</Text>
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="category" label="类别">
                <Select>
                  <Option value="general">通用</Option>
                  <Option value="engineering">数据加工</Option>
                  <Option value="analytics">数据分析</Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="priority" label="优先级">
                <Select>
                  <Option value="high">高</Option>
                  <Option value="medium">中</Option>
                  <Option value="low">低</Option>
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="description" label="描述" rules={[{ required: true }]}>
            <Input maxLength={120} showCount />
          </Form.Item>
          <Form.Item name="triggers" label="触发关键词" rules={[{ required: true }]}>
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item name="content" label="技能内容 (Markdown)" rules={[{ required: true }, { min: 10 }]}>
            <TextArea rows={12} style={{ fontFamily: 'monospace', fontSize: 13 }} />
          </Form.Item>
          <Form.Item
            name="adminToken"
            label="管理员 Token"
            rules={[{ required: true, message: '请输入管理员 Token' }]}
          >
            <Input.Password placeholder="ADMIN_SECRET_TOKEN" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
};

export default Skills;
