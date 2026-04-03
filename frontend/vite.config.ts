import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // 确保 SSE (text/event-stream) 响应不被 Vite 开发代理缓冲。
        // 不设置此项时，Vite/http-proxy 可能在 Node.js HTTP 响应缓冲区中
        // 积压所有 SSE 事件，直到上游连接关闭才一次性发给浏览器。
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            const ct = proxyRes.headers['content-type'] ?? '';
            if (ct.includes('text/event-stream')) {
              // 关闭 nginx 等反向代理的缓冲（X-Accel-Buffering 已由后端设置，
              // 这里额外确保 Vite 代理层不会覆盖该头）
              proxyRes.headers['x-accel-buffering'] = 'no';
              proxyRes.headers['cache-control'] = 'no-cache';
            }
          });
        },
      },
    },
  },
});
