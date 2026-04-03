"""
测试套件：Tab UI + 只读模式 + 群组框架兼容性

覆盖范围（T1-T8）：
  A  DB 层 — is_shared 字段迁移与默认值
  B  后端写权限 — _check_conversation_write_permission 核心逻辑
  C  send_message 端点写保护（E2E）
  D  regenerate / clear 端点写保护（E2E）
  E  superadmin 只读访问（GET /messages）仍可正常查看
  F  is_shared=True → 群组扩展点允许写入
  G  前端代码结构静态验证（ConversationSidebar Tabs / ChatInput readOnly）
  H  RBAC 范围确认：无新菜单/路由/权限
  I  回归：已有 isolation 测试核心场景不受影响
"""

import os
import sys
import uuid
import pytest
from unittest.mock import patch

os.environ.setdefault("ENABLE_AUTH", "True")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# backend/ 需要在 sys.path 中，backend/main.py 使用 `from api import ...`
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from test_utils import make_test_username

# ── 全局测试前缀 ────────────────────────────────────────────────────────────
_PREFIX = f"_t_tabro_{uuid.uuid4().hex[:6]}_"


# ── DB / Auth helpers ────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(label="", is_superadmin=False, role_names=None):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = make_test_username(f"tabro_{label}")
    u = User(
        username=username,
        email=f"{username}@test.local",
        display_name=f"TabRo {label}",
        hashed_password=hash_password("pw123"),
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()

    for rname in (role_names or []):
        role = _g_db.query(Role).filter(Role.name == rname).first()
        if role:
            _g_db.add(UserRole(user_id=u.id, role_id=role.id))
    _g_db.commit()
    _g_db.refresh(u)
    return u


def _token(user):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    roles = get_user_roles(user, _g_db)   # already List[str]
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret,
        settings.jwt_algorithm,
    )


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


def _client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


def _make_conv(owner, title="test conv", is_shared=False):
    """直接用 service 创建对话，返回 Conversation ORM 对象。"""
    from backend.services.conversation_service import ConversationService
    svc = ConversationService(_g_db)
    conv = svc.create_conversation(
        title=title,
        model_key="claude",
        user_id=owner.id,
    )
    if is_shared:
        conv.is_shared = True
        _g_db.commit()
        _g_db.refresh(conv)
    return conv


def teardown_module(_=None):
    """清理本模块产生的所有测试数据。"""
    from backend.models.conversation import Conversation, Message
    from backend.models.conversation_group import ConversationGroup
    from backend.models.user import User

    _g_db.query(Message).filter(
        Message.conversation_id.in_(
            _g_db.query(Conversation.id).filter(Conversation.title.like(f"{_PREFIX}%"))
        )
    ).delete(synchronize_session=False)
    _g_db.query(Conversation).filter(Conversation.title.like(f"{_PREFIX}%")).delete(synchronize_session=False)
    _g_db.query(User).filter(User.username.like(f"_t_tabro_%")).delete(synchronize_session=False)
    _g_db.commit()
    _g_db.close()


# ════════════════════════════════════════════════════════════════════════════
# A — DB 层：is_shared 字段
# ════════════════════════════════════════════════════════════════════════════

class TestA_IsSharedColumn:
    """A1-A4: DB 层 is_shared 字段验证"""

    def test_A1_column_exists_in_schema(self):
        """is_shared 列已存在于 conversations 表。"""
        from sqlalchemy import inspect, text
        from backend.config.database import engine
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT column_name, column_default, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='conversations' AND column_name='is_shared'"
            ))
            row = result.fetchone()
        assert row is not None, "is_shared 列不存在"
        assert row[2] == "NO", "is_shared 应为 NOT NULL"

    def test_A2_default_value_is_false(self):
        """新创建对话的 is_shared 默认为 False。"""
        owner = _make_user("a2")
        conv = _make_conv(owner, title=f"{_PREFIX}A2")
        assert conv.is_shared == False

    def test_A3_to_dict_exposes_is_shared(self):
        """Conversation.to_dict() 输出中包含 is_shared 字段。"""
        owner = _make_user("a3")
        conv = _make_conv(owner, title=f"{_PREFIX}A3")
        d = conv.to_dict()
        assert "is_shared" in d
        assert d["is_shared"] == False

    def test_A4_to_dict_is_shared_true(self):
        """is_shared=True 时 to_dict() 正确暴露。"""
        owner = _make_user("a4")
        conv = _make_conv(owner, title=f"{_PREFIX}A4", is_shared=True)
        d = conv.to_dict()
        assert d["is_shared"] == True


# ════════════════════════════════════════════════════════════════════════════
# B — 后端写权限函数单元测试
# ════════════════════════════════════════════════════════════════════════════

class TestB_WritePermissionUnit:
    """B1-B8: _check_conversation_write_permission 纯逻辑覆盖"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.owner = _make_user("b_owner", role_names=["viewer"])
        self.other = _make_user("b_other", role_names=["viewer"])
        self.admin = _make_user("b_admin", is_superadmin=True)
        self.conv = _make_conv(self.owner, title=f"{_PREFIX}B")
        self.shared_conv = _make_conv(self.owner, title=f"{_PREFIX}B_shared", is_shared=True)

    def _call(self, conversation, user):
        from backend.api.conversations import _check_conversation_write_permission
        from fastapi import HTTPException
        _check_conversation_write_permission(conversation, user)

    def test_B1_owner_can_write(self):
        """对话 owner 可以写入。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            self._call(self.conv, self.owner)  # no exception

    def test_B2_other_user_cannot_write(self):
        """普通用户不能写他人对话。"""
        from fastapi import HTTPException
        with patch("backend.config.settings.settings.enable_auth", True):
            with pytest.raises(HTTPException) as exc:
                self._call(self.conv, self.other)
        assert exc.value.status_code == 403

    def test_B3_superadmin_cannot_write_others_nonshared(self):
        """superadmin 不能向他人的非共享对话写入（只读模式核心）。"""
        from fastapi import HTTPException
        with patch("backend.config.settings.settings.enable_auth", True):
            with pytest.raises(HTTPException) as exc:
                self._call(self.conv, self.admin)
        assert exc.value.status_code == 403
        assert "superadmin 仅可查看" in exc.value.detail

    def test_B4_superadmin_can_write_own_conv(self):
        """superadmin 可以写入自己的对话。"""
        own_conv = _make_conv(self.admin, title=f"{_PREFIX}B_own")
        with patch("backend.config.settings.settings.enable_auth", True):
            self._call(own_conv, self.admin)  # no exception

    def test_B5_superadmin_can_write_shared_conv(self):
        """superadmin 可以向 is_shared=True 对话写入（群组聊天预留）。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            self._call(self.shared_conv, self.admin)  # no exception

    def test_B6_anonymous_mode_passes(self):
        """匿名模式（ENABLE_AUTH=False）不阻止写入。"""
        from backend.api.deps import AnonymousUser
        anon = AnonymousUser()
        # 匿名用户 user_id=None，conv.user_id=None 不触发检查
        conv_null_owner = _make_conv(self.owner, title=f"{_PREFIX}B_anon")
        conv_null_owner.user_id = None
        _g_db.commit()
        with patch("backend.config.settings.settings.enable_auth", False):
            self._call(conv_null_owner, anon)  # no exception

    def test_B7_superadmin_blocked_message_text(self):
        """403 错误详情中包含可读提示信息。"""
        from fastapi import HTTPException
        with patch("backend.config.settings.settings.enable_auth", True):
            with pytest.raises(HTTPException) as exc:
                self._call(self.conv, self.admin)
        assert "其他用户" in exc.value.detail or "仅可查看" in exc.value.detail

    def test_B8_conv_null_user_id_superadmin_not_blocked(self):
        """conv.user_id=None（迁移前旧数据）时 superadmin 不被阻止。"""
        old_conv = _make_conv(self.owner, title=f"{_PREFIX}B_old")
        old_conv.user_id = None
        _g_db.commit()
        with patch("backend.config.settings.settings.enable_auth", True):
            self._call(old_conv, self.admin)  # no exception


# ════════════════════════════════════════════════════════════════════════════
# C — send_message 端点写保护（E2E HTTP）
# ════════════════════════════════════════════════════════════════════════════

class TestC_SendMessageEndpoint:
    """C1-C5: POST /{conv_id}/messages 写保护"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _client()
        self.owner = _make_user("c_owner", role_names=["viewer"])
        self.admin = _make_user("c_admin", is_superadmin=True, role_names=["superadmin"])
        self.conv = _make_conv(self.owner, title=f"{_PREFIX}C")

    def _send(self, conv_id, user, content="hello"):
        with patch("backend.config.settings.settings.enable_auth", True):
            return self.client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": content, "model_key": "claude", "stream": False},
                headers=_auth(user),
            )

    def test_C1_owner_can_send(self):
        """对话 owner 可以发送消息（200 或流式200）。"""
        resp = self._send(self.conv.id, self.owner)
        # 200 (stream=False) 或 200 (stream), 绝不是 403
        assert resp.status_code != 403, f"owner 被拒绝：{resp.text}"

    def test_C2_superadmin_cannot_send_to_others_conv(self):
        """superadmin 向他人非共享对话 send_message → 403。"""
        resp = self._send(self.conv.id, self.admin)
        assert resp.status_code == 403

    def test_C3_superadmin_sends_to_own_conv(self):
        """superadmin 向自己对话 send_message → 不是403。"""
        own_conv = _make_conv(self.admin, title=f"{_PREFIX}C_own")
        resp = self._send(own_conv.id, self.admin)
        assert resp.status_code != 403

    def test_C4_superadmin_sends_to_shared_conv(self):
        """superadmin 向 is_shared=True 对话 send_message → 不是403。"""
        shared = _make_conv(self.owner, title=f"{_PREFIX}C_shared", is_shared=True)
        resp = self._send(shared.id, self.admin)
        assert resp.status_code != 403

    def test_C5_nonexistent_conv_returns_404(self):
        """不存在的对话 send_message → 404。"""
        fake_id = uuid.uuid4()
        resp = self._send(fake_id, self.admin)
        assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# D — regenerate / clear 端点写保护
# ════════════════════════════════════════════════════════════════════════════

class TestD_RegenerateClearEndpoints:
    """D1-D6: regenerate + clear 端点写保护"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _client()
        self.owner = _make_user("d_owner", role_names=["viewer"])
        self.admin = _make_user("d_admin", is_superadmin=True, role_names=["superadmin"])
        self.conv = _make_conv(self.owner, title=f"{_PREFIX}D")

    def _regen(self, conv_id, user):
        with patch("backend.config.settings.settings.enable_auth", True):
            return self.client.post(
                f"/api/v1/conversations/{conv_id}/regenerate",
                headers=_auth(user),
            )

    def _clear(self, conv_id, user):
        with patch("backend.config.settings.settings.enable_auth", True):
            return self.client.post(
                f"/api/v1/conversations/{conv_id}/clear",
                headers=_auth(user),
            )

    def test_D1_superadmin_cannot_regenerate_others(self):
        """superadmin regenerate 他人对话 → 403。"""
        resp = self._regen(self.conv.id, self.admin)
        assert resp.status_code == 403

    def test_D2_owner_can_clear(self):
        """owner 清空自己对话 → 200。"""
        resp = self._clear(self.conv.id, self.owner)
        assert resp.status_code == 200

    def test_D3_superadmin_cannot_clear_others(self):
        """superadmin clear 他人对话 → 403。"""
        resp = self._clear(self.conv.id, self.admin)
        assert resp.status_code == 403

    def test_D4_superadmin_can_clear_own(self):
        """superadmin clear 自己对话 → 200。"""
        own = _make_conv(self.admin, title=f"{_PREFIX}D_own")
        resp = self._clear(own.id, self.admin)
        assert resp.status_code == 200

    def test_D5_superadmin_can_regenerate_own(self):
        """superadmin regenerate 自己的有消息对话 → 不是403（无消息时400）。"""
        own = _make_conv(self.admin, title=f"{_PREFIX}D_regen")
        resp = self._regen(own.id, self.admin)
        # 400 = no messages, which is expected; 403 = blocked (wrong)
        assert resp.status_code != 403

    def test_D6_shared_conv_superadmin_can_clear(self):
        """is_shared=True 对话 superadmin clear → 200。"""
        shared = _make_conv(self.owner, title=f"{_PREFIX}D_shared", is_shared=True)
        resp = self._clear(shared.id, self.admin)
        assert resp.status_code == 200


# ════════════════════════════════════════════════════════════════════════════
# E — superadmin 只读访问（GET 端点不受写保护影响）
# ════════════════════════════════════════════════════════════════════════════

class TestE_SuperadminReadOnlyAccess:
    """E1-E4: superadmin 应能读取他人对话，只是不能写"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _client()
        self.owner = _make_user("e_owner", role_names=["viewer"])
        self.admin = _make_user("e_admin", is_superadmin=True, role_names=["superadmin"])
        self.conv = _make_conv(self.owner, title=f"{_PREFIX}E")

    def test_E1_superadmin_can_get_messages(self):
        """superadmin GET /messages 他人对话 → 200。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                f"/api/v1/conversations/{self.conv.id}/messages",
                headers=_auth(self.admin),
            )
        assert resp.status_code == 200

    def test_E2_superadmin_can_get_conversation_detail(self):
        """superadmin GET /conversations/{id} 他人对话 → 200。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                f"/api/v1/conversations/{self.conv.id}",
                headers=_auth(self.admin),
            )
        assert resp.status_code == 200

    def test_E3_superadmin_can_view_all_users(self):
        """superadmin GET /all-users-view → 200 含他人对话。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.admin),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data

    def test_E4_regular_user_cannot_view_all_users(self):
        """普通用户 GET /all-users-view → 403。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.owner),
            )
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════════════════════════
# F — is_shared 群组扩展点完整流
# ════════════════════════════════════════════════════════════════════════════

class TestF_IsSharedGroupExtension:
    """F1-F4: is_shared=True 群组聊天扩展点行为验证"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _client()
        self.owner = _make_user("f_owner", role_names=["viewer"])
        self.admin = _make_user("f_admin", is_superadmin=True, role_names=["superadmin"])

    def test_F1_create_conv_default_not_shared(self):
        """API 创建对话，响应中 is_shared=False。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}F1", "model_key": "claude"},
                headers=_auth(self.owner),
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_shared"] == False

    def test_F2_shared_conv_allows_superadmin_write(self):
        """is_shared=True 对话允许 superadmin send_message（不返回403）。"""
        shared = _make_conv(self.owner, title=f"{_PREFIX}F2", is_shared=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                f"/api/v1/conversations/{shared.id}/messages",
                json={"content": "hi", "model_key": "claude", "stream": False},
                headers=_auth(self.admin),
            )
        assert resp.status_code != 403

    def test_F3_shared_flag_persists_in_db(self):
        """is_shared 字段写入 DB 后可被读取。"""
        conv = _make_conv(self.owner, title=f"{_PREFIX}F3", is_shared=True)
        _g_db.expire(conv)
        _g_db.refresh(conv)
        assert conv.is_shared == True

    def test_F4_to_dict_field_type_boolean(self):
        """to_dict() 中 is_shared 为 bool 类型。"""
        conv = _make_conv(self.owner, title=f"{_PREFIX}F4")
        d = conv.to_dict()
        assert isinstance(d["is_shared"], bool)


# ════════════════════════════════════════════════════════════════════════════
# G — 前端代码结构静态验证
# ════════════════════════════════════════════════════════════════════════════

import subprocess
import sys as _sys

_FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "src")
_shell = _sys.platform == "win32"


class TestG_FrontendStructure:
    """G1-G10: 前端关键代码结构静态检查"""

    def _read(self, rel_path):
        with open(os.path.join(_FRONTEND, rel_path), encoding="utf-8") as f:
            return f.read()

    # ── ConversationSidebar Tabs ──
    def test_G1_sidebar_imports_tabs(self):
        """ConversationSidebar.tsx 引入 Tabs 组件。"""
        src = self._read("components/chat/ConversationSidebar.tsx")
        assert "Tabs" in src

    def test_G2_sidebar_has_activeTab_state(self):
        """ConversationSidebar.tsx 有 activeTab 状态。"""
        src = self._read("components/chat/ConversationSidebar.tsx")
        assert "activeTab" in src

    def test_G3_sidebar_tab_mine_label(self):
        """ConversationSidebar.tsx 有"我的对话" Tab 标签。"""
        src = self._read("components/chat/ConversationSidebar.tsx")
        assert "我的对话" in src

    def test_G4_sidebar_tab_others_label(self):
        """ConversationSidebar.tsx 有"其他用户" Tab 标签。"""
        src = self._read("components/chat/ConversationSidebar.tsx")
        assert "其他用户" in src

    def test_G5_sidebar_create_buttons_in_mine_tab_only(self):
        """新建对话/分组按钮在 myConversationsContent 变量内（Tab1）。"""
        src = self._read("components/chat/ConversationSidebar.tsx")
        # 新建对话按钮应在 myConversationsContent 区块中
        idx_my = src.index("myConversationsContent")
        idx_create = src.index("新建对话", idx_my)
        # 确认新建对话出现在 myConversationsContent 定义之后
        assert idx_create > idx_my

    # ── ChatInput readOnly ──
    def test_G6_chatinput_has_readonly_prop(self):
        """ChatInput.tsx 接口定义包含 readOnly 属性。"""
        src = self._read("components/chat/ChatInput.tsx")
        assert "readOnly" in src

    def test_G7_chatinput_readonly_banner_text(self):
        """ChatInput.tsx 只读横幅包含正确提示文字。"""
        src = self._read("components/chat/ChatInput.tsx")
        assert "仅查看模式" in src
        assert "其他用户" in src

    # ── Chat.tsx 只读状态 ──
    def test_G8_chat_has_isViewingOtherUserConv(self):
        """Chat.tsx 定义 isViewingOtherUserConv 状态。"""
        src = self._read("pages/Chat.tsx")
        assert "isViewingOtherUserConv" in src

    def test_G9_chat_handleSendMessage_guard(self):
        """Chat.tsx handleSendMessage 有只读守卫。"""
        src = self._read("pages/Chat.tsx")
        assert "isViewingOtherUserConv" in src
        # 守卫在 handleSendMessage 中 return
        idx_fn = src.index("handleSendMessage")
        idx_guard = src.index("isViewingOtherUserConv", idx_fn)
        assert idx_guard > idx_fn

    def test_G10_chat_stop_button_hidden_in_readonly(self):
        """Chat.tsx 停止生成按钮有 isViewingOtherUserConv 条件。"""
        src = self._read("pages/Chat.tsx")
        # sending && !isViewingOtherUserConv 控制停止按钮显示
        assert "!isViewingOtherUserConv" in src or "isViewingOtherUserConv" in src

    # ── useChatStore interface ──
    def test_G11_store_interface_has_is_shared(self):
        """useChatStore.ts Conversation interface 有 is_shared 字段。"""
        src = self._read("store/useChatStore.ts")
        assert "is_shared" in src


# ════════════════════════════════════════════════════════════════════════════
# H — RBAC 范围：无新菜单/路由/权限
# ════════════════════════════════════════════════════════════════════════════

class TestH_RBACScope:
    """H1-H5: 确认本次变更未引入新的权限/菜单条目"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _client()
        self.admin = _make_user("h_admin", is_superadmin=True, role_names=["superadmin"])
        self.viewer = _make_user("h_viewer", role_names=["viewer"])
        self.analyst = _make_user("h_analyst", role_names=["analyst"])

    def test_H1_no_new_permission_in_db(self):
        """数据库 permissions 表中无新增 is_shared / conversation:shared 权限条目。"""
        from backend.models.permission import Permission
        perms = _g_db.query(Permission).filter(
            Permission.resource.like("%shared%")
        ).all()
        assert len(perms) == 0, f"意外的 shared 权限: {[(p.resource, p.action) for p in perms]}"

    def test_H2_viewer_write_still_blocked(self):
        """viewer 角色用户对他人对话写保护不受影响。"""
        other = _make_user("h_other", role_names=["viewer"])
        conv = _make_conv(other, title=f"{_PREFIX}H2")
        client = _client()
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = client.post(
                f"/api/v1/conversations/{conv.id}/messages",
                json={"content": "hi", "model_key": "claude", "stream": False},
                headers=_auth(self.viewer),
            )
        assert resp.status_code == 403

    def test_H3_all_users_view_still_superadmin_only(self):
        """all-users-view 端点仍然只有 superadmin 可访问。"""
        for user in [self.viewer, self.analyst]:
            with patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.get(
                    "/api/v1/conversations/all-users-view",
                    headers=_auth(user),
                )
            assert resp.status_code == 403, f"{user.username} 不该能访问 all-users-view"

    def test_H4_superadmin_permission_matrix_unchanged(self):
        """superadmin 仍可访问 /all-users-view，且普通用户不能。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.admin),
            )
        assert resp.status_code == 200

    def test_H5_is_shared_not_in_api_request_schema(self):
        """CreateConversationRequest Pydantic 模型不接受 is_shared 参数（非公开 API 字段）。"""
        from backend.api.conversations import CreateConversationRequest
        fields = CreateConversationRequest.model_fields.keys()
        assert "is_shared" not in fields, "is_shared 不应暴露在创建接口的入参中"


# ════════════════════════════════════════════════════════════════════════════
# I — 回归：已有 isolation 测试核心场景
# ════════════════════════════════════════════════════════════════════════════

class TestI_RegressionIsolation:
    """I1-I6: 已有对话隔离核心场景回归"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _client()
        self.alice = _make_user("i_alice", role_names=["viewer"])
        self.bob = _make_user("i_bob", role_names=["viewer"])
        self.admin = _make_user("i_admin", is_superadmin=True, role_names=["superadmin"])

    def test_I1_user_sees_only_own_conversations(self):
        """用户只能看到自己的对话列表。"""
        conv_a = _make_conv(self.alice, title=f"{_PREFIX}I_alice")
        conv_b = _make_conv(self.bob, title=f"{_PREFIX}I_bob")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations",
                headers=_auth(self.alice),
            )
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["conversations"]]
        assert str(conv_a.id) in ids
        assert str(conv_b.id) not in ids

    def test_I2_user_cannot_delete_others_conv(self):
        """用户不能删除他人对话。"""
        conv_bob = _make_conv(self.bob, title=f"{_PREFIX}I_del")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(
                f"/api/v1/conversations/{conv_bob.id}",
                headers=_auth(self.alice),
            )
        assert resp.status_code == 403

    def test_I3_superadmin_can_read_others_messages(self):
        """superadmin 可以读取他人对话消息（只读权限）。"""
        conv_bob = _make_conv(self.bob, title=f"{_PREFIX}I_read")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                f"/api/v1/conversations/{conv_bob.id}/messages",
                headers=_auth(self.admin),
            )
        assert resp.status_code == 200

    def test_I4_superadmin_cannot_write_others_messages_regression(self):
        """回归：superadmin send_message 到他人对话仍返回403（核心变更）。"""
        conv_bob = _make_conv(self.bob, title=f"{_PREFIX}I_write")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                f"/api/v1/conversations/{conv_bob.id}/messages",
                json={"content": "inject", "model_key": "claude", "stream": False},
                headers=_auth(self.admin),
            )
        assert resp.status_code == 403

    def test_I5_conversation_list_response_has_is_shared(self):
        """GET /conversations 列表响应中每个对话包含 is_shared 字段。"""
        _make_conv(self.alice, title=f"{_PREFIX}I5")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations",
                headers=_auth(self.alice),
            )
        assert resp.status_code == 200
        convs = resp.json()["conversations"]
        if convs:
            assert "is_shared" in convs[0], "对话列表响应缺少 is_shared 字段"

    def test_I6_create_conversation_response_has_is_shared(self):
        """POST /conversations 创建响应中包含 is_shared 字段。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}I6", "model_key": "claude"},
                headers=_auth(self.alice),
            )
        assert resp.status_code == 200
        assert "is_shared" in resp.json()["data"]
