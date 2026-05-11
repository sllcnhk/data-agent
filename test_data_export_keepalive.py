"""
test_data_export_keepalive — Task C 单测

覆盖 backend/services/export_clients/clickhouse.py 中新增的 Windows TCP keepalive
（SIO_KEEPALIVE_VALS via ioctl）平台分支与降级语义。

测试段：
  K1 — win32 平台 init_poolmanager 注册 _WinKeepAliveHTTPConnectionPool
  K2 — linux/darwin 平台不注册 win pool 类，仍走 socket_options
  K3 — _WinKeepAliveHTTPConnection.connect() 在 super().connect() 后调用 ioctl
  K4 — ioctl 抛异常时仅 warn，连接成功路径不受影响
  K5 — _get_keepalive_params 读取 env / 默认值
  K6 — CH_EXPORT_TCP_KEEPALIVE=0 全局关闭路径仍可用（不影响本任务的 Win 分支）
"""
import os
import socket
import sys
from unittest.mock import MagicMock, patch

import pytest


# 必须在 import 被测模块前清理 env 影响测试默认值
os.environ.pop("CH_EXPORT_TCP_KEEPIDLE", None)
os.environ.pop("CH_EXPORT_TCP_KEEPINTVL", None)
os.environ.pop("CH_EXPORT_TCP_KEEPCNT", None)


def _import_fresh_module():
    """重新导入 clickhouse 模块，便于 platform / env 变更后取到新行为"""
    import importlib

    import backend.services.export_clients.clickhouse as ch
    importlib.reload(ch)
    return ch


# ─────────────────────────────────────────────────────────────────────────────
# K1 — win32 平台分支
# ─────────────────────────────────────────────────────────────────────────────

class TestWin32PoolRegistration:
    def test_k1_win32_registers_win_pool_classes(self, monkeypatch):
        """win32 平台 init_poolmanager 后,pool_classes_by_scheme 含 win pool 类"""
        monkeypatch.setattr(sys, "platform", "win32")
        ch = _import_fresh_module()

        adapter = ch._TCPKeepAliveAdapter()
        # init_poolmanager 在 adapter __init__ 中已自动调用
        pcm = adapter.poolmanager.pool_classes_by_scheme
        assert pcm["http"] is ch._WinKeepAliveHTTPConnectionPool
        assert pcm["https"] is ch._WinKeepAliveHTTPSConnectionPool

    def test_k1b_win32_pool_uses_keepalive_connection(self, monkeypatch):
        """win pool 的 ConnectionCls 是我们的子类（确保 ioctl 钩点会被调用）"""
        monkeypatch.setattr(sys, "platform", "win32")
        ch = _import_fresh_module()

        assert ch._WinKeepAliveHTTPConnectionPool.ConnectionCls is ch._WinKeepAliveHTTPConnection
        assert ch._WinKeepAliveHTTPSConnectionPool.ConnectionCls is ch._WinKeepAliveHTTPSConnection


# ─────────────────────────────────────────────────────────────────────────────
# K2 — linux/darwin 平台不注册 win pool 类
# ─────────────────────────────────────────────────────────────────────────────

class TestNonWin32Behavior:
    def test_k2_linux_keeps_default_pool_classes(self, monkeypatch):
        """linux 平台 init_poolmanager 不替换 pool_classes_by_scheme 为 win pool"""
        monkeypatch.setattr(sys, "platform", "linux")
        # 模拟 Linux 上 TCP_KEEPIDLE 可用
        if not hasattr(socket, "TCP_KEEPIDLE"):
            monkeypatch.setattr(socket, "TCP_KEEPIDLE", 4, raising=False)
            monkeypatch.setattr(socket, "TCP_KEEPINTVL", 5, raising=False)
            monkeypatch.setattr(socket, "TCP_KEEPCNT", 6, raising=False)
        ch = _import_fresh_module()

        adapter = ch._TCPKeepAliveAdapter()
        pcm = adapter.poolmanager.pool_classes_by_scheme
        # pool_classes_by_scheme 不被替换为 win pool 类
        assert pcm["http"] is not ch._WinKeepAliveHTTPConnectionPool
        assert pcm["https"] is not ch._WinKeepAliveHTTPSConnectionPool

    def test_k2b_darwin_keeps_default_pool_classes(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        ch = _import_fresh_module()

        adapter = ch._TCPKeepAliveAdapter()
        pcm = adapter.poolmanager.pool_classes_by_scheme
        assert pcm["http"] is not ch._WinKeepAliveHTTPConnectionPool


# ─────────────────────────────────────────────────────────────────────────────
# K3 — _WinKeepAliveHTTPConnection.connect 调用 ioctl
# ─────────────────────────────────────────────────────────────────────────────

class TestWinConnectionIoctl:
    def test_k3_connect_calls_ioctl_with_keepalive_vals(self, monkeypatch):
        """connect() 后调用 sock.ioctl(SIO_KEEPALIVE_VALS, (1, idle_ms, intvl_ms))"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPIDLE", "30")
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPINTVL", "10")
        ch = _import_fresh_module()

        # 实例化时 urllib3 要求基本参数,但我们不真正建立连接,
        # 只调用 connect() 测试其行为 → mock super().connect() + self.sock
        mock_sock = MagicMock()
        with patch.object(
            ch.HTTPConnection, "connect",
            lambda self: setattr(self, "sock", mock_sock),
        ):
            conn = ch._WinKeepAliveHTTPConnection(host="example.com", port=8123)
            conn.connect()

        # 验证 ioctl 调用
        mock_sock.ioctl.assert_called_once()
        call_args = mock_sock.ioctl.call_args
        assert call_args[0][0] == socket.SIO_KEEPALIVE_VALS
        # (onoff, keepalive_time_ms, keepalive_interval_ms)
        assert call_args[0][1] == (1, 30000, 10000)

    def test_k3b_custom_env_propagates_to_ioctl(self, monkeypatch):
        """环境变量 KEEPIDLE/KEEPINTVL 应改变 ioctl 入参"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPIDLE", "60")
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPINTVL", "5")
        ch = _import_fresh_module()

        mock_sock = MagicMock()
        with patch.object(
            ch.HTTPConnection, "connect",
            lambda self: setattr(self, "sock", mock_sock),
        ):
            conn = ch._WinKeepAliveHTTPConnection(host="example.com", port=8123)
            conn.connect()

        assert mock_sock.ioctl.call_args[0][1] == (1, 60000, 5000)


# ─────────────────────────────────────────────────────────────────────────────
# K4 — ioctl 失败仅 warn,不影响连接
# ─────────────────────────────────────────────────────────────────────────────

class TestIoctlFailureGraceful:
    def test_k4_ioctl_exception_logged_not_raised(self, monkeypatch, caplog):
        """sock.ioctl 抛异常 → 仅记 warning,connect 整体仍返回成功"""
        import logging
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(sys, "platform", "win32")
        ch = _import_fresh_module()

        mock_sock = MagicMock()
        mock_sock.ioctl.side_effect = OSError("simulated ioctl failure")

        with patch.object(
            ch.HTTPConnection, "connect",
            lambda self: setattr(self, "sock", mock_sock),
        ):
            conn = ch._WinKeepAliveHTTPConnection(host="example.com", port=8123)
            conn.connect()  # 不应抛

        # 应记录 warning
        warned = [
            r for r in caplog.records
            if "SIO_KEEPALIVE_VALS" in r.getMessage()
        ]
        assert len(warned) >= 1, f"expected warning, got logs: {[r.getMessage() for r in caplog.records]}"

    def test_k4b_sock_none_skipped(self, monkeypatch):
        """super().connect() 之后 self.sock 仍为 None → 跳过 ioctl,不抛"""
        monkeypatch.setattr(sys, "platform", "win32")
        ch = _import_fresh_module()

        with patch.object(
            ch.HTTPConnection, "connect",
            lambda self: setattr(self, "sock", None),
        ):
            conn = ch._WinKeepAliveHTTPConnection(host="example.com", port=8123)
            # 不应抛任何异常
            conn.connect()


# ─────────────────────────────────────────────────────────────────────────────
# K5 — _get_keepalive_params env 读取
# ─────────────────────────────────────────────────────────────────────────────

class TestKeepaliveParams:
    def test_k5_defaults(self, monkeypatch):
        monkeypatch.delenv("CH_EXPORT_TCP_KEEPIDLE", raising=False)
        monkeypatch.delenv("CH_EXPORT_TCP_KEEPINTVL", raising=False)
        monkeypatch.delenv("CH_EXPORT_TCP_KEEPCNT", raising=False)
        ch = _import_fresh_module()
        assert ch._get_keepalive_params() == (30, 10, 6)

    def test_k5b_env_override(self, monkeypatch):
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPIDLE", "120")
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPINTVL", "20")
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPCNT", "9")
        ch = _import_fresh_module()
        assert ch._get_keepalive_params() == (120, 20, 9)


# ─────────────────────────────────────────────────────────────────────────────
# K6 — 全局关闭 keepalive 路径
# ─────────────────────────────────────────────────────────────────────────────

class TestGlobalDisable:
    def test_k6_keepalive_off_returns_plain_session(self, monkeypatch):
        """CH_EXPORT_TCP_KEEPALIVE=0 时 _build_keepalive_session 走 plain Session 路径"""
        monkeypatch.setenv("CH_EXPORT_TCP_KEEPALIVE", "0")
        ch = _import_fresh_module()

        # 模拟模块单例未初始化
        ch._export_session = None
        session = ch._get_export_session()
        assert session is not None
        # plain Session 没挂自定义 adapter（默认 HTTPAdapter）
        adapter = session.adapters.get("http://")
        assert not isinstance(adapter, ch._TCPKeepAliveAdapter)
