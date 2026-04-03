import React from 'react';
import { Card, Button, Space, message } from 'antd';
import { logger } from '../services/logger';

const LogTest: React.FC = () => {
  const testLog = (level: 'INFO' | 'WARN' | 'ERROR', category: 'API' | 'NETWORK' | 'ERROR' | 'USER') => {
    switch (level) {
      case 'INFO':
        logger.info(category, `这是一条${category}的INFO日志`, {
          test: true,
          timestamp: new Date().toISOString(),
        });
        break;
      case 'WARN':
        logger.warn(category, `这是一条${category}的WARN日志`, {
          test: true,
          timestamp: new Date().toISOString(),
        });
        break;
      case 'ERROR':
        logger.error(category, `这是一条${category}的ERROR日志`, {
          test: true,
          timestamp: new Date().toISOString(),
          error: new Error('测试错误'),
        });
        break;
    }
    message.success(`已记录一条${level}级别日志`);
  };

  return (
    <Card title="日志功能测试" style={{ margin: 24 }}>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <div>
          <h3>测试API日志</h3>
          <Space>
            <Button onClick={() => testLog('INFO', 'API')}>API INFO</Button>
            <Button onClick={() => testLog('WARN', 'API')}>API WARN</Button>
            <Button onClick={() => testLog('ERROR', 'API')}>API ERROR</Button>
          </Space>
        </div>

        <div>
          <h3>测试网络日志</h3>
          <Space>
            <Button onClick={() => testLog('INFO', 'NETWORK')}>NETWORK INFO</Button>
            <Button onClick={() => testLog('WARN', 'NETWORK')}>NETWORK WARN</Button>
            <Button onClick={() => testLog('ERROR', 'NETWORK')}>NETWORK ERROR</Button>
          </Space>
        </div>

        <div>
          <h3>测试错误日志</h3>
          <Space>
            <Button onClick={() => testLog('INFO', 'ERROR')}>ERROR INFO</Button>
            <Button onClick={() => testLog('WARN', 'ERROR')}>ERROR WARN</Button>
            <Button onClick={() => testLog('ERROR', 'ERROR')}>ERROR ERROR</Button>
          </Space>
        </div>

        <div>
          <h3>测试用户日志</h3>
          <Space>
            <Button onClick={() => testLog('INFO', 'USER')}>USER INFO</Button>
            <Button onClick={() => testLog('WARN', 'USER')}>USER WARN</Button>
            <Button onClick={() => testLog('ERROR', 'USER')}>USER ERROR</Button>
          </Space>
        </div>

        <div>
          <h3>功能操作</h3>
          <Space>
            <Button
              type="primary"
              onClick={() => {
                const logs = logger.getLogs();
                message.success(`当前共有 ${logs.length} 条日志`);
              }}
            >
              获取日志数量
            </Button>
            <Button
              onClick={() => {
                logger.clearLogs();
                message.success('日志已清空');
              }}
            >
              清空日志
            </Button>
          </Space>
        </div>
      </Space>
    </Card>
  );
};

export default LogTest;
