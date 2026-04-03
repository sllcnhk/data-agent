import React, { useEffect, useState } from 'react';
import {
  Card,
  Table,
  Button,
  Modal,
  Form,
  Input,
  Switch,
  message,
  Space,
  Tag,
  Popconfirm,
  Tooltip,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { llmConfigApi } from '../services/chatApi';
import type { LLMConfig } from '../store/useChatStore';

const ModelConfig: React.FC = () => {
  const [configs, setConfigs] = useState<LLMConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingConfig, setEditingConfig] = useState<LLMConfig | null>(null);
  const [form] = Form.useForm();

  useEffect(() => {
    loadConfigs();
  }, []);

  const loadConfigs = async () => {
    setLoading(true);
    try {
      const res = await llmConfigApi.listConfigs(false);
      if (res.success) {
        setConfigs(res.data);
      }
    } catch (error: any) {
      message.error('加载配置失败: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = () => {
    setEditingConfig(null);
    form.resetFields();
    setModalVisible(true);
  };

  const handleEdit = async (config: LLMConfig) => {
    // 获取包含敏感信息的完整配置
    try {
      const res = await llmConfigApi.getConfig(config.model_key, true);
      if (res.success) {
        setEditingConfig(res.data);
        form.setFieldsValue(res.data);
        setModalVisible(true);
      }
    } catch (error: any) {
      message.error('加载配置失败: ' + error.message);
    }
  };

  const handleDelete = async (modelKey: string) => {
    try {
      await llmConfigApi.deleteConfig(modelKey);
      message.success('删除成功');
      loadConfigs();
    } catch (error: any) {
      message.error('删除失败: ' + error.message);
    }
  };

  const handleTest = async (modelKey: string) => {
    message.loading({ content: '测试中...', key: 'test' });
    try {
      const res = await llmConfigApi.testConfig(modelKey);
      if (res.success) {
        message.success({ content: '测试成功!', key: 'test' });
        Modal.info({
          title: '测试响应',
          content: res.test_response,
        });
      } else {
        message.error({ content: res.message, key: 'test' });
      }
    } catch (error: any) {
      message.error({ content: '测试失败: ' + error.message, key: 'test' });
    }
  };

  const handleInitDefaults = async () => {
    try {
      const res = await llmConfigApi.initDefaults(false);
      if (res.success) {
        message.success(res.message);
        loadConfigs();
      }
    } catch (error: any) {
      message.error('初始化失败: ' + error.message);
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      if (editingConfig) {
        // 更新
        await llmConfigApi.updateConfig(editingConfig.model_key, values);
        message.success('更新成功');
      } else {
        // 创建
        await llmConfigApi.createConfig(values);
        message.success('创建成功');
      }

      setModalVisible(false);
      loadConfigs();
    } catch (error: any) {
      if (error.errorFields) {
        // 表单验证错误
        return;
      }
      message.error('保存失败: ' + error.message);
    }
  };

  const columns = [
    {
      title: '模型',
      key: 'model',
      render: (_: any, record: LLMConfig) => (
        <Space>
          <span style={{ fontSize: 18 }}>{record.icon || '🤖'}</span>
          <div>
            <div style={{ fontWeight: 500 }}>{record.model_name}</div>
            <div style={{ fontSize: 12, color: '#999' }}>{record.model_key}</div>
          </div>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'model_type',
      key: 'model_type',
    },
    {
      title: 'API地址',
      dataIndex: 'api_base_url',
      key: 'api_base_url',
      ellipsis: true,
    },
    {
      title: '状态',
      key: 'status',
      render: (_: any, record: LLMConfig) => (
        <Space>
          {record.is_enabled ? (
            <Tag icon={<CheckCircleOutlined />} color="success">
              启用
            </Tag>
          ) : (
            <Tag icon={<CloseCircleOutlined />} color="default">
              禁用
            </Tag>
          )}
          {record.is_default && (
            <Tag color="blue">默认</Tag>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: any, record: LLMConfig) => (
        <Space>
          <Tooltip title="测试连接">
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={() => handleTest(record.model_key)}
            />
          </Tooltip>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确定删除这个配置吗?"
            onConfirm={() => handleDelete(record.model_key)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" icon={<DeleteOutlined />} danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Card
        title="大语言模型配置"
        extra={
          <Space>
            <Button onClick={handleInitDefaults}>
              初始化默认配置
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
              新增配置
            </Button>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={configs}
          rowKey="model_key"
          loading={loading}
        />
      </Card>

      {/* 编辑/新增模态框 */}
      <Modal
        title={editingConfig ? '编辑模型配置' : '新增模型配置'}
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        width={600}
        okText="保存"
        cancelText="取消"
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            temperature: '0.7',
            max_tokens: '4096',
            is_enabled: true,
            is_default: false,
          }}
        >
          <Form.Item
            label="模型标识"
            name="model_key"
            rules={[{ required: true, message: '请输入模型标识' }]}
          >
            <Input
              placeholder="例如: claude, gemini, qianwen, doubao"
              disabled={!!editingConfig}
            />
          </Form.Item>

          <Form.Item
            label="模型名称"
            name="model_name"
            rules={[{ required: true, message: '请输入模型名称' }]}
          >
            <Input placeholder="例如: Claude Code" />
          </Form.Item>

          <Form.Item
            label="模型类型"
            name="model_type"
            rules={[{ required: true, message: '请输入模型类型' }]}
          >
            <Input placeholder="对应adapter类型,如: claude, gemini, qianwen, doubao" />
          </Form.Item>

          <Form.Item label="API地址" name="api_base_url">
            <Input placeholder="https://api.example.com" />
          </Form.Item>

          <Form.Item label="API密钥" name="api_key">
            <Input.Password placeholder="输入API密钥" />
          </Form.Item>

          <Form.Item label="API密钥2(可选)" name="api_secret">
            <Input.Password placeholder="部分模型需要" />
          </Form.Item>

          <Form.Item label="默认模型" name="default_model">
            <Input placeholder="例如: claude-3-5-sonnet-20240620" />
          </Form.Item>

          <Form.Item label="温度参数" name="temperature">
            <Input placeholder="0.7" />
          </Form.Item>

          <Form.Item label="最大Token数" name="max_tokens">
            <Input placeholder="4096" />
          </Form.Item>

          <Form.Item label="图标" name="icon">
            <Input placeholder="🤖" />
          </Form.Item>

          <Form.Item label="描述" name="description">
            <Input.TextArea rows={2} placeholder="模型描述" />
          </Form.Item>

          <Form.Item label="启用" name="is_enabled" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item label="设为默认" name="is_default" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default ModelConfig;
