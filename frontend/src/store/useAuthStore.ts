/**
 * Auth Store — 用户认证状态管理
 *
 * access_token: 存在 Zustand store（内存），不持久化到 localStorage（防 XSS）
 * refresh_token: 由后端写入 httpOnly Cookie，前端无法读取，请求时自动携带
 *
 * ENABLE_AUTH=false 时，后端返回 AnonymousUser（username='default', id='default'），
 * 前端无需 token 也能正常使用。
 */
import { create } from 'zustand';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';

export interface UserInfo {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  auth_source: string;
  is_superadmin: boolean;
  roles: string[];
  permissions: string[];
}

/** 是否为匿名用户（ENABLE_AUTH=false 时后端返回的内置用户） */
const isAnonymousUser = (user: UserInfo): boolean =>
  user.id === 'default' && user.username === 'default';

interface AuthState {
  user: UserInfo | null;
  accessToken: string | null;
  isLoading: boolean;
  /** 初始化检查完成标志（防止 RequireAuth 在检查前闪烁跳转）*/
  authChecked: boolean;
  /** 检查是否有指定权限 */
  hasPermission: (perm: string) => boolean;
  /** 应用启动时检测认证状态（四条路径） */
  initAuth: () => Promise<void>;
  /** 登录 */
  login: (username: string, password: string) => Promise<void>;
  /** 登出 */
  logout: () => Promise<void>;
  /** 刷新 access_token（利用 httpOnly Cookie 中的 refresh_token）*/
  refreshToken: () => Promise<boolean>;
  /** 从后端加载当前用户信息（用于页面刷新后恢复 token）*/
  fetchMe: () => Promise<void>;
  /** 设置 access_token */
  setToken: (token: string) => void;
}

// React 18 Strict Mode 会双调用 useEffect，导致 initAuth 并发执行两次。
// 两次并发 refresh 请求中，第二次因 refresh token 已被第一次旋转而 401，
// 进而清空 user 状态并跳转登录页（白屏）。
// 用 Promise 级别的去重保证全局只有一个 initAuth 在运行。
let _initAuthPromise: Promise<void> | null = null;

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  isLoading: false,
  authChecked: false,

  hasPermission: (perm: string) => {
    const { user } = get();
    if (!user) return false;
    if (user.is_superadmin) return true;
    return user.permissions.includes(perm);
  },

  setToken: (token: string) => {
    set({ accessToken: token });
  },

  /**
   * 应用启动时调用一次，检测认证模式并恢复 session：
   *
   * 路径 1: 有 access_token → /auth/me 验证 → 成功则恢复 user
   * 路径 2: 无 access_token → POST /auth/refresh（httpOnly Cookie）→ 成功则恢复 user
   * 路径 3: refresh 也失败 → GET /auth/me 无 token → 返回 200 说明 ENABLE_AUTH=false（匿名用户）
   * 路径 4: /auth/me 返回 401 或网络错误 → 认证已启用，需要登录
   *
   * 失败安全策略：任何无法确认 ENABLE_AUTH=false 的情况，
   * 一律设 auth_enabled='true'，RequireAuth 跳转到 /login。
   */
  initAuth: async () => {
    // 去重：若已有正在进行的 initAuth，直接返回同一个 Promise（避免 Strict Mode 双调用）
    if (_initAuthPromise !== null) return _initAuthPromise;

    _initAuthPromise = (async () => {
    set({ isLoading: true });
    const { accessToken } = get();

    const callMe = async (token: string | null) => {
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      return axios.get(`${API_BASE}/auth/me`, { headers, withCredentials: true });
    };

    /** 标记"认证已启用，用户需登录"（失败安全默认值） */
    const markAuthRequired = () => {
      localStorage.setItem('auth_enabled', 'true');
      set({ user: null, accessToken: null, isLoading: false, authChecked: true });
    };

    try {
      // ── 路径 1: 有 access_token ────────────────────────────────────────────
      if (accessToken) {
        try {
          const res = await callMe(accessToken);
          const userData: UserInfo = res.data;
          if (isAnonymousUser(userData)) {
            // ENABLE_AUTH=false，匿名模式
            localStorage.setItem('auth_enabled', 'false');
          } else {
            localStorage.setItem('auth_enabled', 'true');
          }
          set({ user: userData, isLoading: false, authChecked: true });
          return;
        } catch (err: any) {
          if (err?.response?.status !== 401) {
            // 非 401 错误（网络错误等）→ 失败安全
            markAuthRequired();
            return;
          }
          // token 过期，清空后继续走 refresh 流程
          set({ accessToken: null });
        }
      }

      // ── 路径 2: 尝试 httpOnly Cookie 刷新 ────────────────────────────────
      try {
        const refreshRes = await axios.post(`${API_BASE}/auth/refresh`, {}, {
          withCredentials: true,
        });
        const newToken: string = refreshRes.data.access_token;
        set({ accessToken: newToken });
        const res = await callMe(newToken);
        const userData: UserInfo = res.data;
        localStorage.setItem('auth_enabled', 'true');
        set({ user: userData, isLoading: false, authChecked: true });
        return;
      } catch {
        // refresh 失败（无 Cookie / 已过期 / 网络错误），继续探测
      }

      // ── 路径 3 & 4: 无 token 探测 ENABLE_AUTH 状态 ───────────────────────
      try {
        const res = await callMe(null);
        const userData: UserInfo = res.data;

        if (isAnonymousUser(userData)) {
          // 后端 ENABLE_AUTH=false，返回匿名用户 → 不需要登录
          localStorage.setItem('auth_enabled', 'false');
          set({ user: userData, isLoading: false, authChecked: true });
        } else {
          // 后端返回了真实用户（不常见）→ 认证已启用
          localStorage.setItem('auth_enabled', 'true');
          set({ user: userData, isLoading: false, authChecked: true });
        }
      } catch (err: any) {
        // 401 → ENABLE_AUTH=true，需要登录
        // 网络错误 → 无法确认，失败安全策略：要求登录
        markAuthRequired();
      }
    } catch {
      // 外层意外错误 → 失败安全
      markAuthRequired();
    }
    })().finally(() => {
      _initAuthPromise = null;
    });
    return _initAuthPromise;
  },

  login: async (username: string, password: string) => {
    set({ isLoading: true });
    try {
      const res = await axios.post(`${API_BASE}/auth/login`, { username, password }, {
        withCredentials: true,  // 允许服务器设置 httpOnly Cookie
      });
      const { access_token } = res.data;
      set({ accessToken: access_token });

      // 获取完整用户信息（含 permissions）
      const meRes = await axios.get(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${access_token}` },
        withCredentials: true,
      });
      localStorage.setItem('auth_enabled', 'true');
      set({ user: meRes.data, isLoading: false, authChecked: true });
    } catch (err) {
      set({ isLoading: false });
      throw err;
    }
  },

  logout: async () => {
    try {
      await axios.post(`${API_BASE}/auth/logout`, {}, { withCredentials: true });
    } catch {
      // 忽略登出错误
    }
    localStorage.setItem('auth_enabled', 'true');  // 登出后保持认证模式
    set({ user: null, accessToken: null });
  },

  refreshToken: async () => {
    try {
      const res = await axios.post(`${API_BASE}/auth/refresh`, {}, {
        withCredentials: true,
      });
      const { access_token } = res.data;
      set({ accessToken: access_token });
      return true;
    } catch {
      set({ user: null, accessToken: null });
      return false;
    }
  },

  fetchMe: async () => {
    const { accessToken } = get();
    if (!accessToken) return;
    try {
      const res = await axios.get(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        withCredentials: true,
      });
      set({ user: res.data });
    } catch {
      set({ user: null, accessToken: null });
    }
  },
}));
