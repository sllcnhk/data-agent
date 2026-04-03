/**
 * 前端日志服务
 * 提供详细的错误日志记录功能
 */
import axios, { AxiosRequestConfig, AxiosResponse, AxiosError } from 'axios';

export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR';
export type LogCategory = 'API' | 'NETWORK' | 'ERROR' | 'USER';

interface LogEntry {
  timestamp: string;
  level: LogLevel;
  category: LogCategory;
  message: string;
  details?: any;
  stack?: string;
}

class LoggerService {
  private logs: LogEntry[] = [];
  private maxLogs = 1000; // 最多保存1000条日志
  private logToFile = true;

  private formatTime(): string {
    const now = new Date();
    return now.toISOString();
  }

  private addLog(level: LogLevel, category: LogCategory, message: string, details?: any, stack?: string) {
    const logEntry: LogEntry = {
      timestamp: this.formatTime(),
      level,
      category,
      message,
      details: details ? JSON.stringify(details, null, 2) : undefined,
      stack,
    };

    this.logs.push(logEntry);

    // 保持日志数量在限制内
    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs);
    }

    // 输出到控制台
    const logMessage = `[${logEntry.timestamp}] [${level}] [${category}] ${message}`;
    if (details) {
      console.log(logMessage, details);
    } else {
      console.log(logMessage);
    }

    // 写入文件
    if (this.logToFile && typeof window !== 'undefined') {
      this.writeToFile(logEntry);
    }
  }

  private writeToFile(entry: LogEntry) {
    try {
      // 使用 localStorage 临时存储，然后可以导出
      const key = 'frontend_logs';
      const existingLogs = JSON.parse(localStorage.getItem(key) || '[]');
      existingLogs.push(entry);

      // 保持数量在限制内
      if (existingLogs.length > this.maxLogs) {
        existingLogs.splice(0, existingLogs.length - this.maxLogs);
      }

      localStorage.setItem(key, JSON.stringify(existingLogs));
    } catch (error) {
      console.error('Failed to write log to file:', error);
    }
  }

  debug(category: LogCategory, message: string, details?: any) {
    this.addLog('DEBUG', category, message, details);
  }

  info(category: LogCategory, message: string, details?: any) {
    this.addLog('INFO', category, message, details);
  }

  warn(category: LogCategory, message: string, details?: any) {
    this.addLog('WARN', category, message, details);
  }

  error(category: LogCategory, message: string, details?: any, stack?: string) {
    this.addLog('ERROR', category, message, details, stack);
  }

  // API 请求日志
  logApiRequest(config: AxiosRequestConfig) {
    this.info('API', '发送请求', {
      method: config.method?.toUpperCase(),
      url: config.url,
      baseURL: config.baseURL,
      headers: config.headers,
      data: config.data,
    });
  }

  // API 响应日志
  logApiResponse(response: AxiosResponse) {
    this.info('API', '收到响应', {
      status: response.status,
      statusText: response.statusText,
      url: response.config.url,
      data: response.data,
    });
  }

  // API 错误日志
  logApiError(error: AxiosError) {
    const errorDetails: any = {
      message: error.message,
    };

    if (error.response) {
      errorDetails.status = error.response.status;
      errorDetails.statusText = error.response.statusText;
      errorDetails.data = error.response.data;
      errorDetails.headers = error.response.headers;
    } else if (error.request) {
      errorDetails.request = error.request;
      errorDetails.description = '请求未收到响应，可能是网络问题或CORS问题';
    }

    this.error('API', `API请求失败: ${error.message}`, errorDetails, error.stack);
  }

  // 获取所有日志
  getLogs(): LogEntry[] {
    return [...this.logs];
  }

  // 从 localStorage 获取日志
  getLogsFromStorage(): LogEntry[] {
    try {
      const logs = localStorage.getItem('frontend_logs');
      return logs ? JSON.parse(logs) : [];
    } catch (error) {
      console.error('Failed to get logs from storage:', error);
      return [];
    }
  }

  // 清空日志
  clearLogs() {
    this.logs = [];
    localStorage.removeItem('frontend_logs');
  }

  // 导出日志
  exportLogs(): string {
    const logs = this.getLogsFromStorage();
    return JSON.stringify(logs, null, 2);
  }

  // 获取日志摘要
  getSummary() {
    const logs = this.getLogsFromStorage();
    const summary = {
      total: logs.length,
      error: logs.filter(l => l.level === 'ERROR').length,
      warn: logs.filter(l => l.level === 'WARN').length,
      info: logs.filter(l => l.level === 'INFO').length,
      debug: logs.filter(l => l.level === 'DEBUG').length,
      apiErrors: logs.filter(l => l.category === 'API' && l.level === 'ERROR').length,
      networkErrors: logs.filter(l => l.category === 'NETWORK' && l.level === 'ERROR').length,
      recentErrors: logs.filter(l => l.level === 'ERROR').slice(-10),
    };
    return summary;
  }
}

export const logger = new LoggerService();

// 监听未捕获的错误
if (typeof window !== 'undefined') {
  window.addEventListener('error', (event) => {
    logger.error('ERROR', `未捕获的错误: ${event.message}`, {
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    logger.error('ERROR', '未处理的Promise拒绝', {
      reason: event.reason,
    });
  });
}
