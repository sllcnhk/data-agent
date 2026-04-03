import React from 'react';
import {
  Row,
  Col,
  Card,
  Statistic,
  Space,
  Progress,
  Table,
  Tag,
  Alert,
  Button,
} from 'antd';
import {
  RobotOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  ThunderboltOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { agentApi, systemApi } from '@/services/api';
import { useAsync, useIntervalAsync } from '@/hooks/useApi';
import { Agent, SystemHealth } from '@/types/api';
import ChartComponent from '@/components/ChartComponent';
import { useNavigate } from 'react-router-dom';

const Dashboard: React.FC = () => {
  const navigate = useNavigate();

  const { data: agents, execute: refreshAgents } = useAsync<Agent[]>(
    () => agentApi.getAgents(),
    true
  );

  const { data: health, refresh: refreshHealth } = useIntervalAsync<SystemHealth>(
    () => systemApi.healthCheck(),
    5000
  );

  const totalAgents = agents?.length || 0;
  const idleAgents = agents?.filter((a) => a.status === 'idle').length || 0;
  const busyAgents = agents?.filter((a) => a.status === 'busy').length || 0;
  const errorAgents = agents?.filter((a) => a.status === 'error').length || 0;
  const completedTasks = agents?.reduce((sum, a) => sum + a.completed_tasks, 0) || 0;
  const failedTasks = agents?.reduce((sum, a) => sum + a.failed_tasks, 0) || 0;

  const successRate =
    completedTasks + failedTasks > 0
      ? (completedTasks / (completedTasks + failedTasks)) * 100
      : 100;

  const agentColumns = [
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
      title: '类型',
      dataIndex: 'agent_type',
      key: 'agent_type',
      width: 120,
      render: (type: string) => <Tag color="blue">{type}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
        const colorMap: Record<string, string> = {
          idle: 'success',
          busy: 'processing',
          error: 'error',
          offline: 'default',
        };
        return <Tag color={colorMap[status] || 'default'}>{status}</Tag>;
      },
    },
    {
      title: '已完成',
      dataIndex: 'completed_tasks',
      key: 'completed_tasks',
      width: 100,
    },
    {
      title: '失败',
      dataIndex: 'failed_tasks',
      key: 'failed_tasks',
      width: 100,
      render: (count: number) => (
        <span style={{ color: count > 0 ? '#ff4d4f' : '#52c41a' }}>
          {count}
        </span>
      ),
    },
  ];

  const chartData = agents?.map((agent) => ({
    name: agent.agent_id.split('-')[0],
    completed: agent.completed_tasks,
    failed: agent.failed_tasks,
  })) || [];

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
                valueStyle={{ color: '#1890ff' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="活跃Agent"
                value={idleAgents + busyAgents}
                prefix={<CheckCircleOutlined />}
                valueStyle={{ color: '#52c41a' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="等待中任务"
                value={health?.queue_size || 0}
                prefix={<ClockCircleOutlined />}
                valueStyle={{ color: '#faad14' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="错误Agent"
                value={errorAgents}
                prefix={<ExclamationCircleOutlined />}
                valueStyle={{ color: errorAgents > 0 ? '#ff4d4f' : '#52c41a' }}
              />
            </Card>
          </Col>
        </Row>
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={16}>
          <Card title="任务执行统计">
            <ChartComponent
              type="bar"
              data={chartData}
              xKey="name"
              yKey="completed"
              title="各Agent完成任务数"
              height={300}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card title="系统状态">
            <Space direction="vertical" style={{ width: '100%' }} size="large">
              <div>
                <p>系统状态</p>
                <Tag
                  color={health?.status === 'healthy' ? 'green' : 'red'}
                  style={{ fontSize: 14 }}
                >
                  {health?.status === 'healthy' ? '健康' : '异常'}
                </Tag>
              </div>

              <div>
                <p>任务成功率</p>
                <Progress
                  type="circle"
                  percent={Math.round(successRate)}
                  status={successRate > 90 ? 'success' : 'exception'}
                />
              </div>

              <div>
                <p>活跃Worker数</p>
                <Statistic
                  value={health?.active_workers || 0}
                  prefix={<ThunderboltOutlined />}
                />
              </div>
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={24}>
          <Card
            title="Agent状态一览"
            extra={
              <Space>
                <Tag color="blue">总数: {totalAgents}</Tag>
                <Tag color="green">空闲: {idleAgents}</Tag>
                <Tag color="orange">忙碌: {busyAgents}</Tag>
                <Tag color="red">错误: {errorAgents}</Tag>
              </Space>
            }
          >
            {errorAgents > 0 && (
              <Alert
                message="警告"
                description={
                  <Space>
                    <span>{`系统中有 ${errorAgents} 个Agent处于错误状态，请检查系统日志。`}</span>
                    <Button
                      type="link"
                      icon={<FileTextOutlined />}
                      onClick={() => navigate('/logs')}
                    >
                      查看详细日志
                    </Button>
                  </Space>
                }
                type="warning"
                showIcon
                closable
                style={{ marginBottom: 16 }}
              />
            )}
            <Table
              columns={agentColumns}
              dataSource={agents}
              rowKey="agent_id"
              pagination={{ pageSize: 5 }}
              size="small"
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={24}>
          <Card title="Agent类型分布">
            <Row gutter={16}>
              {Object.entries(health?.agent_types || {}).map(([type, count]) => (
                <Col span={6} key={type}>
                  <Card size="small">
                    <Statistic
                      title={type}
                      value={count}
                      valueStyle={{ fontSize: 24 }}
                    />
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
      </Row>
    </>
  );
};

export default Dashboard;
