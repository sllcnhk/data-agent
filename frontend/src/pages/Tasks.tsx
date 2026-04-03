import React, { useState } from 'react';
import {
  Card,
  Form,
  Input,
  Select,
  Button,
  Space,
  message,
  Table,
  Tag,
  Modal,
  Descriptions,
  Row,
  Col,
  Tooltip,
} from 'antd';
import {
  SendOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  EyeOutlined,
} from '@ant-design/icons';
import { taskApi, systemApi } from '@/services/api';
import { useAsync } from '@/hooks/useApi';
import { Task, TaskSubmitRequest, RoutingSuggestion } from '@/types/api';
import { useAgentStore } from '@/store/useAgentStore';

const { TextArea } = Input;
const { Option } = Select;

const Tasks: React.FC = () => {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<RoutingSuggestion[]>([]);

  const { agents, addTask } = useAgentStore();

  const handleSubmitTask = async (values: TaskSubmitRequest) => {
    setSubmitting(true);
    try {
      const response = await taskApi.submitTask(values);
      message.success('任务提交成功');
      form.resetFields();
      addTask({
        task_id: response.task_id,
        agent_type: response.agent_type,
        priority: response.priority,
        status: 'pending',
        input_data: values as any,
        created_at: new Date().toISOString(),
        retry_count: 0,
      });
    } catch (err) {
      // 错误已在拦截器中处理
    } finally {
      setSubmitting(false);
    }
  };

  const handleQueryChange = async (value: string) => {
    setQuery(value);
    if (value.trim()) {
      try {
        const routingSuggestions = await systemApi.getRoutingSuggestions(value);
        setSuggestions(routingSuggestions);
      } catch (err) {
        setSuggestions([]);
      }
    } else {
      setSuggestions([]);
    }
  };

  const getStatusIcon = (status: string) => {
    const iconMap: Record<string, React.ReactNode> = {
      pending: <ClockCircleOutlined style={{ color: '#999' }} />,
      running: <SyncOutlined spin style={{ color: '#1890ff' }} />,
      completed: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
      failed: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
      cancelled: <CloseCircleOutlined style={{ color: '#d9d9d9' }} />,
    };
    return iconMap[status] || <ClockCircleOutlined />;
  };

  const getStatusText = (status: string) => {
    const statusMap: Record<string, string> = {
      pending: '等待中',
      running: '运行中',
      completed: '已完成',
      failed: '已失败',
      cancelled: '已取消',
    };
    return statusMap[status] || status;
  };

  const getStatusColor = (status: string) => {
    const colorMap: Record<string, string> = {
      pending: 'default',
      running: 'blue',
      completed: 'green',
      failed: 'red',
      cancelled: 'default',
    };
    return colorMap[status] || 'default';
  };

  const getPriorityText = (priority: number) => {
    const priorityMap: Record<number, string> = {
      1: '低',
      2: '普通',
      3: '高',
      4: '紧急',
    };
    return priorityMap[priority] || '普通';
  };

  const columns = [
    {
      title: '任务ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 200,
      render: (text: string) => (
        <span style={{ fontFamily: 'monospace' }}>{text}</span>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => (
        <Space>
          {getStatusIcon(status)}
          <Tag color={getStatusColor(status)}>
            {getStatusText(status)}
          </Tag>
        </Space>
      ),
    },
    {
      title: 'Agent类型',
      dataIndex: 'agent_type',
      key: 'agent_type',
      width: 120,
      render: (type: string) => <Tag color="blue">{type}</Tag>,
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 100,
      render: (priority: number) => (
        <Tag color={priority >= 3 ? 'orange' : 'default'}>
          {getPriorityText(priority)}
        </Tag>
      ),
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (time: string) => new Date(time).toLocaleString('zh-CN'),
    },
    {
      title: '完成时间',
      dataIndex: 'completed_at',
      key: 'completed_at',
      width: 180,
      render: (time?: string) =>
        time ? new Date(time).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: any, record: Task) => (
        <Tooltip title="查看详情">
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => {
              setSelectedTask(record);
              setDetailModalVisible(true);
            }}
          />
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      <div className="page-header">
        <Row gutter={16}>
          <Col span={24}>
            <Card>
              <Form
                form={form}
                layout="vertical"
                onFinish={handleSubmitTask}
                initialValues={{
                  priority: 'normal',
                }}
              >
                <Form.Item
                  name="query"
                  label="任务描述"
                  rules={[{ required: true, message: '请输入任务描述' }]}
                >
                  <TextArea
                    rows={4}
                    placeholder="请描述您要执行的任务，例如：分析销售数据的趋势、生成SQL查询等..."
                    onChange={(e) => handleQueryChange(e.target.value)}
                  />
                </Form.Item>

                {suggestions.length > 0 && (
                  <Form.Item label="推荐Agent">
                    <Space wrap>
                      {suggestions.slice(0, 3).map((suggestion, index) => (
                        <Tag
                          key={index}
                          color="blue"
                          style={{ cursor: 'pointer' }}
                          onClick={() => {
                            // 可以根据建议自动填充表单
                          }}
                        >
                          {suggestion.agent_type} ({Math.round(suggestion.confidence * 100)}%)
                        </Tag>
                      ))}
                    </Space>
                  </Form.Item>
                )}

                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item name="priority" label="优先级">
                      <Select>
                        <Option value="low">低</Option>
                        <Option value="normal">普通</Option>
                        <Option value="high">高</Option>
                        <Option value="urgent">紧急</Option>
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label=" ">
                      <Button
                        type="primary"
                        htmlType="submit"
                        icon={<SendOutlined />}
                        loading={submitting}
                        size="large"
                        block
                      >
                        提交任务
                      </Button>
                    </Form.Item>
                  </Col>
                </Row>
              </Form>
            </Card>
          </Col>
        </Row>
      </div>

      <Card title="任务列表">
        <Table
          columns={columns}
          dataSource={agents
            .filter((a) => a.current_task)
            .map((a) => a.current_task!)
            .concat(
              agents
                .flatMap((a) => a.current_task ? [a.current_task] : [])
                .filter(Boolean)
            )
          }
          rowKey="task_id"
          pagination={{ pageSize: 10 }}
        />
      </Card>

      <Modal
        title="任务详情"
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={null}
        width={800}
      >
        {selectedTask && (
          <Descriptions bordered column={2}>
            <Descriptions.Item label="任务ID" span={2}>
              <span style={{ fontFamily: 'monospace' }}>{selectedTask.task_id}</span>
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Space>
                {getStatusIcon(selectedTask.status)}
                <Tag color={getStatusColor(selectedTask.status)}>
                  {getStatusText(selectedTask.status)}
                </Tag>
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="Agent类型">
              <Tag color="blue">{selectedTask.agent_type}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="优先级">
              <Tag color={selectedTask.priority >= 3 ? 'orange' : 'default'}>
                {getPriorityText(selectedTask.priority)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="重试次数">
              {selectedTask.retry_count}
            </Descriptions.Item>
            <Descriptions.Item label="提交时间" span={2}>
              {new Date(selectedTask.created_at).toLocaleString('zh-CN')}
            </Descriptions.Item>
            {selectedTask.started_at && (
              <Descriptions.Item label="开始时间" span={2}>
                {new Date(selectedTask.started_at).toLocaleString('zh-CN')}
              </Descriptions.Item>
            )}
            {selectedTask.completed_at && (
              <Descriptions.Item label="完成时间" span={2}>
                {new Date(selectedTask.completed_at).toLocaleString('zh-CN')}
              </Descriptions.Item>
            )}
            <Descriptions.Item label="输入数据" span={2}>
              <pre style={{ margin: 0, padding: 8, background: '#f5f5f5', borderRadius: 4 }}>
                {JSON.stringify(selectedTask.input_data, null, 2)}
              </pre>
            </Descriptions.Item>
            {selectedTask.error && (
              <Descriptions.Item label="错误信息" span={2}>
                <pre style={{ margin: 0, padding: 8, background: '#fff2f0', color: '#ff4d4f', borderRadius: 4 }}>
                  {selectedTask.error}
                </pre>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Modal>
    </>
  );
};

export default Tasks;
