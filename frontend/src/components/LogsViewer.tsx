import React, { useState, useEffect } from 'react';
import { Card, Table, Button, Space, Tag, Input, Select, Typography, Divider, Modal, message } from 'antd';
import { DownloadOutlined, ReloadOutlined, ClearOutlined, EyeOutlined } from '@ant-design/icons';
import { logger, LogEntry } from '../services/logger';

const { Title, Text } = Typography;
const { Search } = Input;
const { Option } = Select;

const LogsViewer: React.FC = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [filteredLogs, setFilteredLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
  const [detailVisible, setDetailVisible] = useState(false);

  // 筛选条件
  const [levelFilter, setLevelFilter] = useState<string>('ALL');
  const [categoryFilter, setCategoryFilter] = useState<string>('ALL');
  const [searchText, setSearchText] = useState<string>('');

  // 加载日志
  const loadLogs = () => {
    setLoading(true);
    try {
      const allLogs = logger.getLogsFromStorage();
      setLogs(allLogs);
      setFilteredLogs(allLogs);
    } catch (error) {
      console.error('加载日志失败:', error);
      message.error('加载日志失败');
    } finally {
      setLoading(false);
    }
  };

  // 应用筛选
  useEffect(() => {
    let filtered = [...logs];

    // 按级别筛选
    if (levelFilter !== 'ALL') {
      filtered = filtered.filter(log => log.level === levelFilter);
    }

    // 按类别筛选
    if (categoryFilter !== 'ALL') {
      filtered = filtered.filter(log => log.category === categoryFilter);
    }

    // 按文本搜索
    if (searchText) {
      filtered = filtered.filter(log =>
        log.message.toLowerCase().includes(searchText.toLowerCase()) ||
        (log.details && log.details.toLowerCase().includes(searchText.toLowerCase()))
      );
    }

    setFilteredLogs(filtered);
  }, [logs, levelFilter, categoryFilter, searchText]);

  // 组件挂载时加载日志
  useEffect(() => {
    loadLogs();
  }, []);

  // 导出日志
  const exportLogs = () => {
    try {
      const logData = logger.exportLogs();
      const blob = new Blob([logData], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `frontend-logs-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      message.success('日志导出成功');
    } catch (error) {
      console.error('导出日志失败:', error);
      message.error('导出日志失败');
    }
  };

  // 清空日志
  const clearLogs = () => {
    Modal.confirm({
      title: '确认清空日志',
      content: '确定要清空所有日志吗？此操作不可恢复。',
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        logger.clearLogs();
        setLogs([]);
        setFilteredLogs([]);
        message.success('日志已清空');
      },
    });
  };

  // 获取日志级别颜色
  const getLevelColor = (level: string) => {
    switch (level) {
      case 'ERROR': return 'red';
      case 'WARN': return 'orange';
      case 'INFO': return 'blue';
      case 'DEBUG': return 'default';
      default: return 'default';
    }
  };

  // 获取类别颜色
  const getCategoryColor = (category: string) => {
    switch (category) {
      case 'API': return 'purple';
      case 'NETWORK': return 'cyan';
      case 'ERROR': return 'red';
      case 'USER': return 'green';
      default: return 'default';
    }
  };

  // 查看日志详情
  const viewLogDetail = (log: LogEntry) => {
    setSelectedLog(log);
    setDetailVisible(true);
  };

  // 表格列定义
  const columns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 180,
      render: (text: string) => new Date(text).toLocaleString('zh-CN'),
      sorter: (a: LogEntry, b: LogEntry) =>
        new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
      defaultSortOrder: 'descend' as const,
    },
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      width: 80,
      render: (level: string) => <Tag color={getLevelColor(level)}>{level}</Tag>,
      filters: [
        { text: 'ERROR', value: 'ERROR' },
        { text: 'WARN', value: 'WARN' },
        { text: 'INFO', value: 'INFO' },
        { text: 'DEBUG', value: 'DEBUG' },
      ],
      onFilter: (value: any, record: LogEntry) => record.level === value,
    },
    {
      title: '类别',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (category: string) => <Tag color={getCategoryColor(category)}>{category}</Tag>,
      filters: [
        { text: 'API', value: 'API' },
        { text: 'NETWORK', value: 'NETWORK' },
        { text: 'ERROR', value: 'ERROR' },
        { text: 'USER', value: 'USER' },
      ],
      onFilter: (value: any, record: LogEntry) => record.category === value,
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (text: string) => <Text>{text}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: any, record: LogEntry) => (
        <Button
          type="link"
          icon={<EyeOutlined />}
          onClick={() => viewLogDetail(record)}
        >
          详情
        </Button>
      ),
    },
  ];

  // 获取统计信息
  const getSummary = () => {
    return logger.getSummary();
  };

  const summary = getSummary();

  return (
    <div>
      <Title level={2}>日志查看器</Title>

      {/* 统计信息卡片 */}
      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Tag color="blue">总计: {summary.total}</Tag>
          <Tag color="red">错误: {summary.error}</Tag>
          <Tag color="orange">警告: {summary.warn}</Tag>
          <Tag color="purple">API错误: {summary.apiErrors}</Tag>
        </Space>
      </Card>

      {/* 筛选和操作栏 */}
      <Card style={{ marginBottom: 16 }}>
        <Space wrap style={{ marginBottom: 16 }}>
          <Search
            placeholder="搜索日志内容"
            allowClear
            style={{ width: 300 }}
            onSearch={setSearchText}
          />
          <Select
            value={levelFilter}
            onChange={setLevelFilter}
            style={{ width: 120 }}
          >
            <Option value="ALL">所有级别</Option>
            <Option value="ERROR">ERROR</Option>
            <Option value="WARN">WARN</Option>
            <Option value="INFO">INFO</Option>
            <Option value="DEBUG">DEBUG</Option>
          </Select>
          <Select
            value={categoryFilter}
            onChange={setCategoryFilter}
            style={{ width: 120 }}
          >
            <Option value="ALL">所有类别</Option>
            <Option value="API">API</Option>
            <Option value="NETWORK">NETWORK</Option>
            <Option value="ERROR">ERROR</Option>
            <Option value="USER">USER</Option>
          </Select>
        </Space>

        <Divider style={{ margin: '8px 0' }} />

        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={loadLogs}
            loading={loading}
          >
            刷新
          </Button>
          <Button
            icon={<DownloadOutlined />}
            onClick={exportLogs}
            type="primary"
          >
            导出日志
          </Button>
          <Button
            icon={<ClearOutlined />}
            onClick={clearLogs}
            danger
          >
            清空日志
          </Button>
        </Space>
      </Card>

      {/* 日志表格 */}
      <Card>
        <Table
          columns={columns}
          dataSource={filteredLogs}
          rowKey={(record) => `${record.timestamp}-${record.message}`}
          loading={loading}
          pagination={{
            total: filteredLogs.length,
            pageSize: 50,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 条日志`,
          }}
          scroll={{ x: true }}
        />
      </Card>

      {/* 日志详情模态框 */}
      <Modal
        title="日志详情"
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={null}
        width={800}
      >
        {selectedLog && (
          <div>
            <p><strong>时间:</strong> {new Date(selectedLog.timestamp).toLocaleString('zh-CN')}</p>
            <p><strong>级别:</strong> <Tag color={getLevelColor(selectedLog.level)}>{selectedLog.level}</Tag></p>
            <p><strong>类别:</strong> <Tag color={getCategoryColor(selectedLog.category)}>{selectedLog.category}</Tag></p>
            <p><strong>消息:</strong> {selectedLog.message}</p>
            {selectedLog.details && (
              <>
                <Divider />
                <p><strong>详细信息:</strong></p>
                <pre
                  style={{
                    background: '#f5f5f5',
                    padding: '12px',
                    borderRadius: '4px',
                    overflow: 'auto',
                    maxHeight: '400px',
                  }}
                >
                  {typeof selectedLog.details === 'string'
                    ? selectedLog.details
                    : JSON.stringify(JSON.parse(selectedLog.details), null, 2)}
                </pre>
              </>
            )}
            {selectedLog.stack && (
              <>
                <Divider />
                <p><strong>堆栈跟踪:</strong></p>
                <pre
                  style={{
                    background: '#fff2e8',
                    padding: '12px',
                    borderRadius: '4px',
                    overflow: 'auto',
                    maxHeight: '400px',
                    color: '#d46b08',
                  }}
                >
                  {selectedLog.stack}
                </pre>
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default LogsViewer;
