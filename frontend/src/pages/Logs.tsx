import React from 'react';
import { Card, Alert, Space, Typography } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';
import LogsViewer from '../components/LogsViewer';

const { Text } = Typography;

const LogsPage: React.FC = () => {
  return (
    <div>
      {/* 提示信息 */}
      <Alert
        message="日志查看器使用说明"
        description={
          <Space direction="vertical">
            <div>
              • <Text strong>刷新</Text>: 重新从浏览器存储加载最新日志
            </div>
            <div>
              • <Text strong>导出日志</Text>: 将日志保存为JSON文件，方便调试和问题追踪
            </div>
            <div>
              • <Text strong>筛选</Text>: 按级别、类别或关键词搜索特定日志
            </div>
            <div>
              • <Text strong>Network Error诊断</Text>: 查看API调用失败的具体原因，包括CORS错误、网络问题或后端服务未启动
            </div>
          </Space>
        }
        type="info"
        showIcon
        icon={<InfoCircleOutlined />}
        style={{ marginBottom: 16 }}
      />

      {/* 日志查看器 */}
      <LogsViewer />
    </div>
  );
};

export default LogsPage;
