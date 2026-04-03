import React, { useState } from 'react';
import {
  Card,
  Table,
  Button,
  Space,
  Tag,
  Modal,
  Form,
  Select,
  Input,
  message,
  Popconfirm,
  Statistic,
  Row,
  Col,
  Tooltip,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  ReloadOutlined,
  EyeOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { agentApi } from '@/services/api';
import { useAsync } from '@/hooks/useApi';
import { Agent } from '@/types/api';

const { Option } = Select;

const Agents: React.FC = () => {
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [form] = Form.useForm();

  const { data: agents, loading, error, execute } = useAsync<Agent[]>(
    () => agentApi.getAgents(),
    true
  );

  const handleCreateAgent = async () => {
    try {
      const values = await form.validateFields();
      await agentApi.createAgent(values.agent_type, values.agent_id);
      message.success('Agent创建成功');
      setCreateModalVisible(false);
      form.resetFields();
      execute();
    } catch (err) {
      // 错误已在拦截器中处理
    }
  };

  const handleDeleteAgent = async (agentId: string) => {
    try {
      await agentApi.deleteAgent(agentId);
      message.success('Agent删除成功');
      execute();
    } catch (err) {
      // 错误已在拦截器中处理
    }
  };

  const handleViewDetails = async (agentId: string) => {
    try {
      const agent = await agentApi.getAgent(agentId);
      setSelectedAgent(agent);
      setDetailModalVisible(true);
    } catch (err) {
      message.error('获取Agent详情失败');
    }
  };

  const getStatusColor = (status: string) => {
    const colorMap: Record<string, string> = {
      idle: 'green',
      busy: 'blue',
      error: 'red',
      offline: 'default',
    };
    return colorMap[status] || 'default';
  };

  const getStatusText = (status: string) => {
    const statusMap: Record<string, string> = {
      idle: '空闲',
      busy: '忙碌',
      error: '错误',
      offline: '离线',
    };
    return statusMap[status] || status;
  };

  const columns = [
    {
      title: 'Agent ID',
      dataIndex: 'agent_id',
      key: 'agent_id',
      width: 200,
      render: (text: string) => (
        <span style={{ fontFamily: 'monospace' }}>{text}</span>
      ),
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 150,
    },
    {
      title: '类型',
      dataIndex: 'agent_type',
      key: 'agent_type',
      width: 120,
      render: (type: string) => (
        <Tag color="blue">{type}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={getStatusColor(status)}>
          {getStatusText(status)}
        </Tag>
      ),
    },
    {
      title: '已完成任务',
      dataIndex: 'completed_tasks',
      key: 'completed_tasks',
      width: 120,
      render: (count: number) => (
        <Statistic
          title=""
          value={count}
          valueStyle={{ fontSize: 16 }}
        />
      ),
    },
    {
      title: '失败任务',
      dataIndex: 'failed_tasks',
      key: 'failed_tasks',
      width: 120,
      render: (count: number) => (
        <Statistic
          title=""
          value={count}
          valueStyle={{ fontSize: 16, color: count > 0 ? '#cf1322' : '#3f8600' }}
        />
      ),
    },
    {
      title: '最后活跃',
      dataIndex: 'last_active_at',
      key: 'last_active_at',
      width: 180,
      render: (time: string) => new Date(time).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: any, record: Agent) => (
        <Space>
          <Tooltip title="查看详情">
            <Button
              type="link"
              icon={<EyeOutlined />}
              onClick={() => handleViewDetails(record.agent_id)}
            />
          </Tooltip>
          <Popconfirm
            title="确定要删除这个Agent吗？"
            onConfirm={() => handleDeleteAgent(record.agent_id)}
            okText="确定"
            cancelText="取消"
          >
            <Tooltip title="删除">
              <Button type="link" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const agentTypes = [
    { value: 'data_analyst', label: '数据分析师' },
    { value: 'sql_expert', label: 'SQL专家' },
    { value: 'chart_builder', label: '图表构建师' },
    { value: 'etl_engineer', label: 'ETL工程师' },
    { value: 'generalist', label: '通用Agent' },
  ];

  const totalAgents = agents?.length || 0;
  const idleAgents = agents?.filter((a) => a.status === 'idle').length || 0;
  const busyAgents = agents?.filter((a) => a.status === 'busy').length || 0;
  const errorAgents = agents?.filter((a) => a.status === 'error').length || 0;

  return (
    <>
      <div className="page-header">
        <Row gutter={16}>
          <Col span={6}>
            <Card>
              <Statistic
                title="总Agent数"
                value={totalAgents}
                prefix={<RobotOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="空闲Agent"
                value={idleAgents}
                valueStyle={{ color: '#3f8600' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="忙碌Agent"
                value={busyAgents}
                valueStyle={{ color: '#1890ff' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="错误Agent"
                value={errorAgents}
                valueStyle={{ color: '#cf1322' }}
              />
            </Card>
          </Col>
        </Row>
      </div>

      <Card
        title="Agent列表"
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={execute}
              loading={loading}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateModalVisible(true)}
            >
              创建Agent
            </Button>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={agents}
          rowKey="agent_id"
          loading={loading}
          pagination={{ pageSize: 10 }}
        />
      </Card>

      <Modal
        title="创建Agent"
        open={createModalVisible}
        onOk={handleCreateAgent}
        onCancel={() => {
          setCreateModalVisible(false);
          form.resetFields();
        }}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="agent_type"
            label="Agent类型"
            rules={[{ required: true, message: '请选择Agent类型' }]}
          >
            <Select placeholder="请选择Agent类型">
              {agentTypes.map((type) => (
                <Option key={type.value} value={type.value}>
                  {type.label}
                </Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="agent_id"
            label="Agent ID（可选）"
            tooltip="不指定将自动生成"
          >
            <Input placeholder="输入Agent ID" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Agent详情"
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={null}
        width={800}
      >
        {selectedAgent && (
          <div>
            <Row gutter={16}>
              <Col span={12}>
                <p><strong>Agent ID:</strong> {selectedAgent.agent_id}</p>
                <p><strong>名称:</strong> {selectedAgent.name}</p>
                <p><strong>类型:</strong> {selectedAgent.agent_type}</p>
                <p><strong>状态:</strong> {getStatusText(selectedAgent.status)}</p>
              </Col>
              <Col span={12}>
                <p><strong>版本:</strong> {selectedAgent.version}</p>
                <p><strong>创建时间:</strong> {new Date(selectedAgent.created_at).toLocaleString('zh-CN')}</p>
                <p><strong>最后活跃:</strong> {new Date(selectedAgent.last_active_at).toLocaleString('zh-CN')}</p>
              </Col>
            </Row>
            <div style={{ marginTop: 16 }}>
              <p><strong>描述:</strong></p>
              <p>{selectedAgent.description}</p>
            </div>
            <div style={{ marginTop: 16 }}>
              <p><strong>能力:</strong></p>
              <div>
                {selectedAgent.capabilities.map((capability, index) => (
                  <Tag key={index} color="blue">{capability}</Tag>
                ))}
              </div>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
};

export default Agents;
