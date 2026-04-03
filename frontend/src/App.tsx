import React, { useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Layout, Spin } from 'antd';
import AppLayout from './components/AppLayout';
import Chat from './pages/Chat';
import ModelConfig from './pages/ModelConfig';
import Dashboard from './pages/Dashboard';
import Agents from './pages/Agents';
import Tasks from './pages/Tasks';
import Skills from './pages/Skills';
import LogsPage from './pages/Logs';
import Login from './pages/Login';
import Users from './pages/Users';
import Roles from './pages/Roles';
import { useAuthStore } from '@/store/useAuthStore';

const { Content } = Layout;

/**
 * RequireAuth — 路由守卫
 *
 * 等待 initAuth() 完成（authChecked=true）后再做重定向判断，
 * 避免页面刷新时的登录页闪烁。
 *
 * ENABLE_AUTH=false → auth_enabled='false' → 放行
 * ENABLE_AUTH=true + 无有效 token → auth_enabled='true' + user=null → 跳转 /login
 */
const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, accessToken, authChecked } = useAuthStore();
  const location = useLocation();

  // 初始化未完成时显示加载态，防止闪烁
  if (!authChecked) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" tip="初始化中..." />
      </div>
    );
  }

  const authEnabled = localStorage.getItem('auth_enabled') === 'true';
  if (authEnabled && !accessToken && !user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
};

function App() {
  const { initAuth } = useAuthStore();

  useEffect(() => {
    // 应用启动时检测认证模式并恢复 user 信息
    initAuth();
  }, []);

  return (
    <Router>
      <Routes>
        {/* 登录页（无需认证）*/}
        <Route path="/login" element={<Login />} />

        {/* 聊天页面不使用 AppLayout（全屏布局）*/}
        <Route path="/" element={
          <RequireAuth>
            <Chat />
          </RequireAuth>
        } />
        <Route path="/chat" element={
          <RequireAuth>
            <Chat />
          </RequireAuth>
        } />

        {/* 其他页面使用 AppLayout */}
        <Route path="/*" element={
          <RequireAuth>
            <AppLayout>
              <Content style={{ padding: '24px', minHeight: 'calc(100vh - 64px)' }}>
                <Routes>
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/agents" element={<Agents />} />
                  <Route path="/tasks" element={<Tasks />} />
                  <Route path="/skills" element={<Skills />} />
                  <Route path="/model-config" element={<ModelConfig />} />
                  <Route path="/logs" element={<LogsPage />} />
                  <Route path="/users" element={<Users />} />
                  <Route path="/roles" element={<Roles />} />
                </Routes>
              </Content>
            </AppLayout>
          </RequireAuth>
        } />
      </Routes>
    </Router>
  );
}

export default App;
