#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_model_memory.py
====================
验证 "记住最后选择模型" + "消息显示模型信息" 两个功能。

Section A — UpdateConversationRequest 支持 model_key 字段
Section B — 发消息时持久化 model_key 到 conversation.current_model
Section C — 消息的 model 字段随 conversation.current_model 变化
Section D — frontend 逻辑验证（静态代码扫描）

运行：/d/ProgramData/Anaconda3/envs/dataagent/python.exe -X utf8 test_model_memory.py
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    results.append((name, condition))
    msg = f"  {status} {name}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    return condition


# ─────────────────────────────────────────────────────────────────────────────
# Section A — UpdateConversationRequest 新增 model_key 字段
# ─────────────────────────────────────────────────────────────────────────────

def test_section_a():
    print("\n=== Section A: UpdateConversationRequest 支持 model_key ===")

    from backend.api.conversations import UpdateConversationRequest

    # A1: model_key 字段存在且默认 None
    req = UpdateConversationRequest()
    check("A1 model_key 字段存在", hasattr(req, 'model_key'))
    check("A1 model_key 默认为 None", req.model_key is None)

    # A2: 可正常赋值
    req2 = UpdateConversationRequest(model_key="qianwen")
    check("A2 model_key 可赋值", req2.model_key == "qianwen")

    # A3: 与现有字段无冲突
    req3 = UpdateConversationRequest(title="新标题", model_key="claude")
    check("A3 title + model_key 共存", req3.title == "新标题" and req3.model_key == "claude")

    # A4: update_conversation handler 将 model_key 映射到 current_model
    # 通过检查源码确认
    conv_path = os.path.join(os.path.dirname(__file__), "backend", "api", "conversations.py")
    src = open(conv_path, encoding="utf-8").read()
    check("A4 handler 将 model_key 写入 current_model",
          'updates["current_model"] = request.model_key' in src)


# ─────────────────────────────────────────────────────────────────────────────
# Section B — 发消息时持久化 model_key 到 conversation.current_model
# ─────────────────────────────────────────────────────────────────────────────

def test_section_b():
    print("\n=== Section B: 发消息时持久化模型变更 ===")

    conv_path = os.path.join(os.path.dirname(__file__), "backend", "api", "conversations.py")
    src = open(conv_path, encoding="utf-8").read()

    # B1: 代码中存在对 conversation.current_model 的赋值（持久化逻辑）
    check("B1 发消息 handler 包含 current_model 赋值",
          "conversation.current_model = model_key" in src)

    # B2: 只在 model_key 变化时才 commit
    check("B2 变更条件：request.model_key != conversation.current_model",
          "request.model_key != conversation.current_model" in src)

    # B3: 变更后调用 db.commit() + db.refresh()
    # 找到持久化块的上下文
    idx = src.find("conversation.current_model = model_key")
    snippet = src[max(0, idx - 50): idx + 200]
    check("B3 变更后 db.commit()", "db.commit()" in snippet)
    check("B3 变更后 db.refresh()", "db.refresh(conversation)" in snippet)

    # B4: 发消息逻辑复用 ConversationService.update_conversation 的 **kwargs 机制
    # 确认 update_conversation 支持 current_model 更新
    from backend.services.conversation_service import ConversationService
    from unittest.mock import MagicMock
    import inspect

    src_svc = inspect.getsource(ConversationService.update_conversation)
    check("B4 update_conversation 用 setattr 更新任意字段",
          "setattr(conversation, key, value)" in src_svc)


# ─────────────────────────────────────────────────────────────────────────────
# Section C — 消息的 model 字段正确跟随 conversation.current_model
# ─────────────────────────────────────────────────────────────────────────────

def test_section_c():
    print("\n=== Section C: add_message 的 model 字段跟随 current_model ===")

    import inspect
    from backend.services.conversation_service import ConversationService

    src = inspect.getsource(ConversationService.add_message)

    # C1: add_message 从 conversation.current_model 取 model
    check("C1 add_message 从 current_model 取 model",
          "conversation.current_model" in src and "model=" in src)

    # C2: Message.to_dict() 含 model 字段（源码检查）
    from backend.models.conversation import Message
    import inspect as ins
    src_dict = ins.getsource(Message.to_dict)
    check("C2 Message.to_dict() 包含 model 字段", '"model"' in src_dict or "'model'" in src_dict)

    # C3: send_message_stream 的 assistant_message 事件包含 to_dict() 数据
    from backend.services.conversation_service import ConversationService as CS
    src_stream = inspect.getsource(CS.send_message_stream)
    check("C3 assistant_message 事件携带 to_dict() 数据",
          "assistant_message.to_dict()" in src_stream)


# ─────────────────────────────────────────────────────────────────────────────
# Section D — Frontend 静态代码扫描
# ─────────────────────────────────────────────────────────────────────────────

def test_section_d():
    print("\n=== Section D: Frontend 代码扫描 ===")

    base = os.path.dirname(os.path.abspath(__file__))
    frontend = os.path.join(base, "frontend", "src")

    def read(path):
        return open(os.path.join(frontend, path), encoding="utf-8").read()

    store_src = read("store/useChatStore.ts")
    chat_src = read("pages/Chat.tsx")
    messages_src = read("components/chat/ChatMessages.tsx")
    api_src = read("services/chatApi.ts")

    # D1: setLLMConfigs 不再无条件覆盖默认模型（检查有 localStorage 判断逻辑）
    check("D1 setLLMConfigs 优先使用 localStorage 中保存的模型",
          "localStorage.getItem('selectedModel')" in store_src
          and "currentConversation" in store_src)

    # D2: setCurrentConversation 同步 selectedModel 到 conversation.current_model
    check("D2 setCurrentConversation 同步 selectedModel",
          "convModel" in store_src and "current_model" in store_src)

    # D3: ModelSelector 绑定 handleModelChange（不再直接 setSelectedModel）
    check("D3 ModelSelector.onSelect 绑定 handleModelChange",
          "handleModelChange" in chat_src and "onSelect={handleModelChange}" in chat_src)

    # D4: handleModelChange 调用 updateConversation API
    check("D4 handleModelChange 调用 updateConversation",
          "conversationApi.updateConversation" in chat_src
          and "model_key: modelKey" in chat_src)

    # D5: handleModelChange 同步更新 store 中 current_model
    check("D5 handleModelChange 更新 store current_model",
          "updateConversation(currentConversation.id, { current_model: modelKey })" in chat_src)

    # D6: chatApi.updateConversation 签名包含 model_key
    check("D6 chatApi.updateConversation 签名含 model_key",
          "model_key?: string" in api_src)

    # D7: ChatMessages 引入 useChatStore 获取 llmConfigs
    check("D7 ChatMessages 从 store 取 llmConfigs",
          "useChatStore" in messages_src and "llmConfigs" in messages_src)

    # D8: ChatMessages 定义 getModelLabel 函数
    check("D8 ChatMessages 定义 getModelLabel",
          "getModelLabel" in messages_src)

    # D9: 模型标签仅对助手消息展示（!isUser 判断）
    check("D9 模型标签仅对助手消息展示",
          "!isUser" in messages_src and "getModelLabel" in messages_src)

    # D10: 模型标签有 tooltip（展示详细信息）
    check("D10 模型标签使用 Tooltip",
          "Tooltip" in messages_src and "tooltip" in messages_src)

    # D11: 切换对话时模型跟随对话（setCurrentConversation 内同步）
    check("D11 setCurrentConversation 同步 localStorage('selectedModel')",
          "localStorage.setItem('selectedModel', convModel)" in store_src)

    # D12: setLLMConfigs 当对话激活时不覆盖 selectedModel
    check("D12 setLLMConfigs 有 currentConversation early-return",
          "if (currentConversation)" in store_src and "return;" in store_src)


# ─────────────────────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  test_model_memory.py")
    print("  记住最后选择模型 + 消息展示模型信息 测试")
    print("=" * 60)

    test_section_a()
    test_section_b()
    test_section_c()
    test_section_d()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    total = len(results)
    print(f"  结果: {passed}/{total} 通过  ({failed} 失败)")
    print("=" * 60)

    if failed:
        print("\n失败项：")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
        sys.exit(1)
    else:
        print("  全部通过！")
        sys.exit(0)
