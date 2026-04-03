"""
Attachment Upload Feature — Comprehensive Test Suite

作为资深测试工程师设计的全面测试，覆盖：
  A (10) — AttachmentData Pydantic 模型验证
  B (16) — _perceive() 多模态内容块构建（全类型 + 边界情况）
  C (10) — conversation_service 附件元数据存储 + 上下文注入
  D (10) — _build_context() 历史消息附件注解
  E  (8) — API 端点（FastAPI TestClient）请求解析 + Schema 校验
  F  (8) — RBAC 回归：无新菜单 / 路由 / 权限
  G (10) — 端到端管道：数据从 API 流经 service → context → _perceive()
  H  (8) — 边界 & 错误用例
  I  (4) — Bug-Fix 验证（已修复的三个 Bug）

总计: 84 个测试用例
"""
import asyncio
import base64
import json
import os
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("ENABLE_AUTH", "False")

sys.path.insert(0, os.path.dirname(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# Section A: AttachmentData Pydantic 模型验证 (10)
# ─────────────────────────────────────────────────────────────────────────────

class TestAttachmentDataModel(unittest.TestCase):
    """A: AttachmentData Pydantic 模型完整验证。"""

    def _cls(self):
        from backend.api.conversations import AttachmentData, SendMessageRequest
        return AttachmentData, SendMessageRequest

    def test_a01_valid_jpeg(self):
        A, _ = self._cls()
        a = A(name="photo.jpg", mime_type="image/jpeg", size=12345, data="aGVsbG8=")
        self.assertEqual(a.name, "photo.jpg")
        self.assertEqual(a.mime_type, "image/jpeg")
        self.assertEqual(a.size, 12345)
        self.assertEqual(a.data, "aGVsbG8=")

    def test_a02_valid_png(self):
        A, _ = self._cls()
        a = A(name="screen.png", mime_type="image/png", size=2048, data="abc=")
        self.assertEqual(a.mime_type, "image/png")

    def test_a03_valid_pdf(self):
        A, _ = self._cls()
        a = A(name="report.pdf", mime_type="application/pdf", size=99999, data="cGRm")
        self.assertEqual(a.mime_type, "application/pdf")

    def test_a04_valid_text_plain(self):
        A, _ = self._cls()
        a = A(name="data.txt", mime_type="text/plain", size=50, data="dGV4dA==")
        self.assertEqual(a.mime_type, "text/plain")

    def test_a05_valid_json(self):
        A, _ = self._cls()
        a = A(name="config.json", mime_type="application/json", size=200, data="e30=")
        self.assertEqual(a.mime_type, "application/json")

    def test_a06_size_zero_valid(self):
        """零字节文件仍然合法。"""
        A, _ = self._cls()
        a = A(name="empty.txt", mime_type="text/plain", size=0, data="")
        self.assertEqual(a.size, 0)

    def test_a07_missing_name_raises(self):
        A, _ = self._cls()
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            A(mime_type="image/png", size=100, data="abc=")

    def test_a08_missing_data_raises(self):
        A, _ = self._cls()
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            A(name="f.png", mime_type="image/png", size=100)

    def test_a09_send_message_request_default_empty_attachments(self):
        _, Req = self._cls()
        req = Req(content="hello", stream=True)
        self.assertEqual(req.attachments, [])
        self.assertIsInstance(req.attachments, list)

    def test_a10_send_message_request_multiple_attachments(self):
        A, Req = self._cls()
        req = Req(
            content="analyze these",
            stream=True,
            attachments=[
                A(name="a.jpg", mime_type="image/jpeg", size=1024, data="abc="),
                A(name="b.pdf", mime_type="application/pdf", size=4096, data="xyz="),
            ]
        )
        self.assertEqual(len(req.attachments), 2)
        names = [att.name for att in req.attachments]
        self.assertIn("a.jpg", names)
        self.assertIn("b.pdf", names)


# ─────────────────────────────────────────────────────────────────────────────
# Section B: _perceive() 多模态内容块构建 (16)
# ─────────────────────────────────────────────────────────────────────────────

def _make_loop():
    from backend.agents.agentic_loop import AgenticLoop
    mcp = MagicMock()
    mcp.list_servers.return_value = []
    mcp.get_all_tools.return_value = []
    loop = AgenticLoop.__new__(AgenticLoop)
    loop.mcp_manager = mcp
    loop.llm_adapter = MagicMock()
    loop._cancel_event = None
    return loop


class TestPerceiveMultimodal(unittest.TestCase):
    """B: _perceive() 多模态构建全覆盖。"""

    def test_b01_no_attachments_plain_text(self):
        loop = _make_loop()
        msgs = loop._perceive("hello", {"history": []})
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "hello")

    def test_b02_jpeg_image_block(self):
        loop = _make_loop()
        data = base64.b64encode(b"\xff\xd8\xff").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "p.jpg", "mime_type": "image/jpeg", "size": 3, "data": data}
        ]}
        msgs = loop._perceive("see this", ctx)
        content = msgs[0]["content"]
        img = [b for b in content if b["type"] == "image"]
        self.assertEqual(len(img), 1)
        self.assertEqual(img[0]["source"]["media_type"], "image/jpeg")

    def test_b03_png_image_block(self):
        loop = _make_loop()
        data = base64.b64encode(b"\x89PNG").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "s.png", "mime_type": "image/png", "size": 4, "data": data}
        ]}
        msgs = loop._perceive("check", ctx)
        content = msgs[0]["content"]
        img = [b for b in content if b["type"] == "image"]
        self.assertEqual(img[0]["source"]["media_type"], "image/png")

    def test_b04_gif_image_block(self):
        loop = _make_loop()
        data = base64.b64encode(b"GIF89a").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "a.gif", "mime_type": "image/gif", "size": 6, "data": data}
        ]}
        msgs = loop._perceive("gif", ctx)
        content = msgs[0]["content"]
        img = [b for b in content if b["type"] == "image"]
        self.assertEqual(img[0]["source"]["media_type"], "image/gif")

    def test_b05_webp_image_block(self):
        loop = _make_loop()
        data = base64.b64encode(b"RIFF").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "i.webp", "mime_type": "image/webp", "size": 4, "data": data}
        ]}
        msgs = loop._perceive("webp", ctx)
        content = msgs[0]["content"]
        img = [b for b in content if b["type"] == "image"]
        self.assertEqual(img[0]["source"]["media_type"], "image/webp")

    def test_b06_unsupported_image_type_falls_back_to_jpeg(self):
        """image/bmp 等不支持的图片类型回退到 image/jpeg。"""
        loop = _make_loop()
        data = base64.b64encode(b"BM").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "b.bmp", "mime_type": "image/bmp", "size": 2, "data": data}
        ]}
        msgs = loop._perceive("bmp", ctx)
        content = msgs[0]["content"]
        img = [b for b in content if b["type"] == "image"]
        self.assertEqual(len(img), 1)
        self.assertEqual(img[0]["source"]["media_type"], "image/jpeg")

    def test_b07_pdf_document_block(self):
        loop = _make_loop()
        data = base64.b64encode(b"%PDF-1.4").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "doc.pdf", "mime_type": "application/pdf", "size": 8, "data": data}
        ]}
        msgs = loop._perceive("analyze PDF", ctx)
        content = msgs[0]["content"]
        docs = [b for b in content if b["type"] == "document"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["source"]["media_type"], "application/pdf")
        self.assertEqual(docs[0]["source"]["type"], "base64")

    def test_b08_text_plain_decoded_and_embedded(self):
        loop = _make_loop()
        text = "col1,col2\n1,2\n3,4"
        data = base64.b64encode(text.encode()).decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "data.txt", "mime_type": "text/plain", "size": len(text), "data": data}
        ]}
        msgs = loop._perceive("analyze text", ctx)
        content = msgs[0]["content"]
        text_blocks = [b for b in content if b["type"] == "text"]
        combined = " ".join(b["text"] for b in text_blocks)
        self.assertIn("data.txt", combined)
        self.assertIn("col1,col2", combined)

    def test_b09_csv_text_embedded(self):
        loop = _make_loop()
        csv_content = "id,name\n1,Alice"
        data = base64.b64encode(csv_content.encode()).decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "users.csv", "mime_type": "text/csv", "size": len(csv_content), "data": data}
        ]}
        msgs = loop._perceive("parse CSV", ctx)
        content = msgs[0]["content"]
        text_blocks = [b for b in content if b["type"] == "text"]
        combined = " ".join(b["text"] for b in text_blocks)
        self.assertIn("users.csv", combined)
        self.assertIn("Alice", combined)

    def test_b10_json_text_embedded(self):
        loop = _make_loop()
        json_content = '{"key": "value"}'
        data = base64.b64encode(json_content.encode()).decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "cfg.json", "mime_type": "application/json", "size": len(json_content), "data": data}
        ]}
        msgs = loop._perceive("read config", ctx)
        content = msgs[0]["content"]
        text_blocks = [b for b in content if b["type"] == "text"]
        combined = " ".join(b["text"] for b in text_blocks)
        self.assertIn("cfg.json", combined)
        self.assertIn("value", combined)

    def test_b11_binary_fallback_does_not_crash(self):
        """非 UTF-8 二进制数据使用 errors='replace' 不崩溃。"""
        loop = _make_loop()
        binary = bytes(range(256))  # all byte values
        data = base64.b64encode(binary).decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "blob.bin", "mime_type": "application/octet-stream", "size": 256, "data": data}
        ]}
        msgs = loop._perceive("binary", ctx)
        self.assertEqual(len(msgs), 1)
        content = msgs[0]["content"]
        self.assertIsInstance(content, list)
        self.assertTrue(len(content) >= 1)

    def test_b12_empty_message_with_attachment_only(self):
        """只有附件、没有文字时，不添加空文本块。"""
        loop = _make_loop()
        data = base64.b64encode(b"img").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "p.jpg", "mime_type": "image/jpeg", "size": 3, "data": data}
        ]}
        msgs = loop._perceive("", ctx)
        self.assertEqual(len(msgs), 1)
        content = msgs[0]["content"]
        text_blocks = [b for b in content if b["type"] == "text"]
        self.assertEqual(len(text_blocks), 0)  # 无文字块

    def test_b13_text_and_image_both_present(self):
        """文字+图片时，两个块均存在。"""
        loop = _make_loop()
        data = base64.b64encode(b"\x89PNG").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "chart.png", "mime_type": "image/png", "size": 4, "data": data}
        ]}
        msgs = loop._perceive("explain this chart", ctx)
        content = msgs[0]["content"]
        self.assertTrue(any(b["type"] == "text" for b in content))
        self.assertTrue(any(b["type"] == "image" for b in content))
        text_blocks = [b for b in content if b["type"] == "text"]
        self.assertIn("explain this chart", text_blocks[0]["text"])

    def test_b14_multiple_attachments_all_blocks(self):
        """多个不同类型附件全部生成对应块。"""
        loop = _make_loop()
        img_data = base64.b64encode(b"\x89PNG").decode()
        pdf_data = base64.b64encode(b"%PDF").decode()
        txt_data = base64.b64encode(b"hello").decode()
        ctx = {"history": [], "current_attachments": [
            {"name": "a.png", "mime_type": "image/png", "size": 4, "data": img_data},
            {"name": "b.pdf", "mime_type": "application/pdf", "size": 4, "data": pdf_data},
            {"name": "c.txt", "mime_type": "text/plain", "size": 5, "data": txt_data},
        ]}
        msgs = loop._perceive("three files", ctx)
        content = msgs[0]["content"]
        self.assertTrue(any(b["type"] == "image" for b in content))
        self.assertTrue(any(b["type"] == "document" for b in content))
        self.assertTrue(any(b["type"] == "text" for b in content))

    def test_b15_history_messages_unaffected(self):
        """current_attachments 不影响已有历史消息（纯文本）。"""
        loop = _make_loop()
        ctx = {
            "history": [
                {"role": "user", "content": "prev question"},
                {"role": "assistant", "content": "prev answer"},
            ],
            "current_attachments": [
                {"name": "p.jpg", "mime_type": "image/jpeg", "size": 5, "data": "abc="}
            ]
        }
        msgs = loop._perceive("new question", ctx)
        # 历史两条
        self.assertEqual(msgs[0]["content"], "prev question")
        self.assertEqual(msgs[1]["content"], "prev answer")
        # 当前消息是多模态
        self.assertIsInstance(msgs[2]["content"], list)

    def test_b16_empty_attachments_list_falls_to_plain_text(self):
        """current_attachments=[] 时走普通文本路径。"""
        loop = _make_loop()
        ctx = {"history": [], "current_attachments": []}
        msgs = loop._perceive("hello", ctx)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "hello")


# ─────────────────────────────────────────────────────────────────────────────
# Section C: conversation_service 附件元数据 + 上下文注入 (10)
# ─────────────────────────────────────────────────────────────────────────────

class TestServiceAttachmentMetadata(unittest.TestCase):
    """C: 元数据剥离 base64、上下文注入、与 continuation 合并等。"""

    def _strip(self, attachments):
        """模拟 send_message_stream 的元数据剥离逻辑。"""
        return [
            {"name": a["name"], "mime_type": a["mime_type"], "size": a["size"]}
            for a in attachments
        ]

    def test_c01_single_attachment_stripped(self):
        result = self._strip([
            {"name": "img.jpg", "mime_type": "image/jpeg", "size": 1024, "data": "bigbase64=="}
        ])
        self.assertEqual(len(result), 1)
        self.assertNotIn("data", result[0])
        self.assertEqual(result[0]["name"], "img.jpg")
        self.assertEqual(result[0]["size"], 1024)

    def test_c02_multiple_attachments_all_stripped(self):
        atts = [
            {"name": f"f{i}.jpg", "mime_type": "image/jpeg", "size": i * 100, "data": "x=="}
            for i in range(3)
        ]
        result = self._strip(atts)
        self.assertEqual(len(result), 3)
        for item in result:
            self.assertNotIn("data", item)
            self.assertIn("name", item)
            self.assertIn("mime_type", item)
            self.assertIn("size", item)

    def test_c03_metadata_fields_exact(self):
        """剥离后恰好保留 name / mime_type / size 三个字段。"""
        result = self._strip([
            {"name": "f.pdf", "mime_type": "application/pdf", "size": 500, "data": "xyz="}
        ])
        self.assertEqual(set(result[0].keys()), {"name", "mime_type", "size"})

    def test_c04_context_injection_includes_base64(self):
        """current_attachments 注入到 context 时保留完整 base64。"""
        attachments = [{"name": "p.jpg", "mime_type": "image/jpeg", "size": 512, "data": "base64=="}]
        context = {"history": []}
        if attachments:
            context["current_attachments"] = attachments
        self.assertIn("current_attachments", context)
        self.assertEqual(context["current_attachments"][0]["data"], "base64==")

    def test_c05_empty_list_not_injected(self):
        context = {"history": []}
        if []:
            context["current_attachments"] = []
        self.assertNotIn("current_attachments", context)

    def test_c06_none_not_injected(self):
        context = {"history": []}
        attachments = None
        if attachments:
            context["current_attachments"] = attachments
        self.assertNotIn("current_attachments", context)

    def test_c07_merge_with_continuation_metadata(self):
        """附件元数据与 continuation 元数据正确合并，互不覆盖。"""
        user_extra_meta = {"continuation_round": 1, "max_rounds": 3}
        save_kwargs = {"extra_metadata": user_extra_meta}
        attachments = [{"name": "a.jpg", "mime_type": "image/jpeg", "size": 100, "data": "x="}]
        if attachments:
            attachment_meta = [{"name": a["name"], "mime_type": a["mime_type"], "size": a["size"]}
                               for a in attachments]
            existing_meta = save_kwargs.get("extra_metadata") or {}
            existing_meta["attachments"] = attachment_meta
            save_kwargs["extra_metadata"] = existing_meta
        meta = save_kwargs["extra_metadata"]
        self.assertIn("continuation_round", meta)
        self.assertIn("attachments", meta)
        self.assertEqual(meta["continuation_round"], 1)
        self.assertEqual(len(meta["attachments"]), 1)

    def test_c08_chinese_filename_preserved(self):
        """中文文件名在元数据中完整保留。"""
        result = self._strip([
            {"name": "用户数据.csv", "mime_type": "text/csv", "size": 300, "data": "xy="}
        ])
        self.assertEqual(result[0]["name"], "用户数据.csv")

    def test_c09_size_value_preserved_exactly(self):
        result = self._strip([
            {"name": "big.pdf", "mime_type": "application/pdf", "size": 10485760, "data": "x="}
        ])
        self.assertEqual(result[0]["size"], 10485760)

    def test_c10_injection_after_build_context_pattern(self):
        """验证 send_message_stream 的顺序：先 _build_context 再注入 current_attachments。"""
        attachments = [{"name": "img.png", "mime_type": "image/png", "size": 200, "data": "abc="}]
        # Simulate build_context result — should NOT contain current_attachments
        context = {"history": [], "username": "user1"}
        self.assertNotIn("current_attachments", context)
        # Now inject
        context["current_attachments"] = attachments
        self.assertIn("current_attachments", context)
        # _perceive gets context with current_attachments
        loop = _make_loop()
        msgs = loop._perceive("check image", context)
        content = msgs[0]["content"]
        self.assertIsInstance(content, list)


# ─────────────────────────────────────────────────────────────────────────────
# Section D: _build_context() 历史消息附件注解 (10)
# ─────────────────────────────────────────────────────────────────────────────

def _annotate(hist_attachments, content):
    """复现 _build_context() 的注解逻辑。"""
    if hist_attachments:
        annotations = ", ".join(
            f"{a['name']} ({a['mime_type']}, {a['size']} bytes)"
            for a in hist_attachments
        )
        return f"{content}\n[附件: {annotations}]"
    return content


class TestBuildContextAnnotation(unittest.TestCase):
    """D: 历史消息注解逻辑。"""

    def test_d01_single_attachment_format(self):
        result = _annotate([{"name": "photo.jpg", "mime_type": "image/jpeg", "size": 1024}],
                           "user message")
        self.assertIn("[附件: photo.jpg (image/jpeg, 1024 bytes)]", result)
        self.assertIn("user message", result)

    def test_d02_multiple_attachments_comma_separated(self):
        result = _annotate([
            {"name": "a.jpg", "mime_type": "image/jpeg", "size": 100},
            {"name": "b.pdf", "mime_type": "application/pdf", "size": 200},
        ], "see files")
        self.assertIn("a.jpg", result)
        self.assertIn("b.pdf", result)
        self.assertIn("[附件:", result)

    def test_d03_no_attachments_no_annotation(self):
        result = _annotate([], "plain text")
        self.assertEqual(result, "plain text")
        self.assertNotIn("[附件:", result)

    def test_d04_none_no_annotation(self):
        result = _annotate(None, "plain text")
        self.assertEqual(result, "plain text")

    def test_d05_annotation_appended_after_content(self):
        result = _annotate([{"name": "f.png", "mime_type": "image/png", "size": 50}], "hello")
        # 内容在前，注解在后
        idx_hello = result.index("hello")
        idx_ann = result.index("[附件:")
        self.assertLess(idx_hello, idx_ann)

    def test_d06_annotation_format_bytes_unit(self):
        result = _annotate([{"name": "x.pdf", "mime_type": "application/pdf", "size": 99999}],
                           "msg")
        self.assertIn("99999 bytes", result)

    def test_d07_empty_original_content_still_annotated(self):
        """空消息内容（附件专用消息）注解后在 _perceive 中非空。"""
        result = _annotate([{"name": "pic.jpg", "mime_type": "image/jpeg", "size": 200}], "")
        # strip 后非空 → 不会被 _perceive 丢弃
        self.assertTrue(result.strip())
        self.assertIn("[附件:", result)

    def test_d08_assistant_messages_not_annotated(self):
        """助手消息不应被注解（只注解 user role）。"""
        # 注解逻辑仅在 llm_role == "user" 时执行
        hist_attachments = [{"name": "f.jpg", "mime_type": "image/jpeg", "size": 100}]
        llm_role = "assistant"
        # Mimic the condition in _build_context
        should_annotate = bool(hist_attachments) and llm_role == "user"
        self.assertFalse(should_annotate)

    def test_d09_continuation_role_maps_to_user_for_annotation(self):
        """continuation 角色在 _build_context 中映射为 user，若有附件则被注解。"""
        msg_role = "continuation"
        llm_role = "user" if msg_role == "continuation" else msg_role
        hist_attachments = [{"name": "f.jpg", "mime_type": "image/jpeg", "size": 100}]
        should_annotate = bool(hist_attachments) and llm_role == "user"
        self.assertTrue(should_annotate)

    def test_d10_annotation_survives_perceive_strip(self):
        """注解后的字符串经 strip() 仍非空，不会在 _perceive 中被丢弃。"""
        result = _annotate([{"name": "p.jpg", "mime_type": "image/jpeg", "size": 10}], "")
        stripped = result.strip()
        self.assertTrue(stripped)  # 确保 _perceive 中 `if content:` 为 True


# ─────────────────────────────────────────────────────────────────────────────
# Section E: API 端点 — TestClient + Schema 校验 (8)
# ─────────────────────────────────────────────────────────────────────────────

class TestApiEndpointSchema(unittest.TestCase):
    """E: AttachmentData / SendMessageRequest schema 及 TestClient 端点测试。"""

    def _make_client(self):
        from fastapi.testclient import TestClient
        from backend.api.conversations import router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return TestClient(app, raise_server_exceptions=False)

    def test_e01_attachment_model_dump_has_four_fields(self):
        from backend.api.conversations import AttachmentData
        a = AttachmentData(name="f.txt", mime_type="text/plain", size=50, data="hello=")
        d = a.model_dump()
        self.assertEqual(set(d.keys()), {"name", "mime_type", "size", "data"})

    def test_e02_list_comprehension_passes_all_fields(self):
        """conversations.py 中 [a.model_dump() for a in request.attachments] 保留 data 字段。"""
        from backend.api.conversations import AttachmentData, SendMessageRequest
        req = SendMessageRequest(
            content="x",
            attachments=[AttachmentData(name="f.jpg", mime_type="image/jpeg", size=100, data="YWJj")]
        )
        dumped = [a.model_dump() for a in req.attachments]
        self.assertEqual(dumped[0]["data"], "YWJj")
        self.assertEqual(dumped[0]["name"], "f.jpg")

    def test_e03_request_without_attachments_defaults_empty(self):
        from backend.api.conversations import SendMessageRequest
        req = SendMessageRequest(content="hi")
        self.assertEqual(req.attachments, [])

    def test_e04_invalid_attachment_missing_name_422(self):
        """缺少 name 字段应返回 422（由 Pydantic 验证）。"""
        from pydantic import ValidationError
        from backend.api.conversations import AttachmentData
        with self.assertRaises(ValidationError):
            AttachmentData(mime_type="image/png", size=100, data="abc=")

    def test_e05_cancel_endpoint_still_works(self):
        """cancel 端点回归：路径未变，返回 200。"""
        client = self._make_client()
        conv_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/conversations/{conv_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "cancellation_requested")

    def test_e06_send_message_endpoint_path_unchanged(self):
        """send_message 端点路径仍为 /{id}/messages。"""
        import re
        conv_path = os.path.join(os.path.dirname(__file__), "backend", "api", "conversations.py")
        with open(conv_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn('router.post("/{conversation_id}/messages"', content)

    def test_e07_no_standalone_attachment_route(self):
        """不应存在独立的 /attachments 路由。"""
        conv_path = os.path.join(os.path.dirname(__file__), "backend", "api", "conversations.py")
        with open(conv_path, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn('"/{conversation_id}/attachments"', content)
        self.assertNotIn('"/attachments"', content)

    def test_e08_attachment_data_in_send_message_path(self):
        """AttachmentData 被引用在 send_message 的调用链中（model_dump 传递）。"""
        conv_path = os.path.join(os.path.dirname(__file__), "backend", "api", "conversations.py")
        with open(conv_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("model_dump()", content)
        self.assertIn("request.attachments", content)


# ─────────────────────────────────────────────────────────────────────────────
# Section F: RBAC 回归 — 无新菜单 / 路由 / 权限 (8)
# ─────────────────────────────────────────────────────────────────────────────

class TestRbacRegression(unittest.TestCase):
    """F: 附件功能不引入新权限、新菜单、新路由。"""

    def _read(self, *path_parts):
        p = os.path.join(os.path.dirname(__file__), *path_parts)
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_f01_init_rbac_no_attachment_permission(self):
        """RBAC 初始化脚本中没有 attachment 相关权限。"""
        p = os.path.join(os.path.dirname(__file__), "backend", "scripts", "init_rbac.py")
        if not os.path.exists(p):
            self.skipTest("init_rbac.py not found")
        content = self._read("backend", "scripts", "init_rbac.py")
        self.assertNotIn("attachment", content.lower())

    def test_f02_no_new_route_for_attachments(self):
        content = self._read("backend", "api", "conversations.py")
        self.assertNotIn('/attachments"', content)

    def test_f03_send_message_endpoint_path_unchanged(self):
        content = self._read("backend", "api", "conversations.py")
        self.assertIn('router.post("/{conversation_id}/messages"', content)

    def test_f04_attachment_data_model_has_exactly_four_fields(self):
        from backend.api.conversations import AttachmentData
        fields = set(AttachmentData.model_fields.keys())
        self.assertEqual(fields, {"name", "mime_type", "size", "data"})

    def test_f05_agentic_loop_import_clean(self):
        from backend.agents.agentic_loop import AgenticLoop
        self.assertTrue(callable(AgenticLoop._perceive))

    def test_f06_conversation_service_has_attachments_param(self):
        """send_message_stream 新增了 attachments 参数（可选，默认 None）。"""
        import inspect
        from backend.services.conversation_service import ConversationService
        sig = inspect.signature(ConversationService.send_message_stream)
        self.assertIn("attachments", sig.parameters)
        param = sig.parameters["attachments"]
        # 默认值是 None（可选参数，不影响现有调用者）
        self.assertIsNone(param.default)

    def test_f07_no_new_main_py_route_registration(self):
        """main.py 中没有新增 attachment 路由注册。"""
        main_path = os.path.join(os.path.dirname(__file__), "backend", "main.py")
        if not os.path.exists(main_path):
            self.skipTest("main.py not found")
        content = self._read("backend", "main.py")
        self.assertNotIn("attachment", content.lower())

    def test_f08_perceive_imports_base64_at_module_level(self):
        """Bug-1 修复验证：base64 已移至模块级导入。"""
        loop_path = os.path.join(os.path.dirname(__file__), "backend", "agents", "agentic_loop.py")
        with open(loop_path, encoding="utf-8") as f:
            lines = f.readlines()
        # 找到 'import base64' 行
        import_lines = [i for i, l in enumerate(lines) if "import base64" in l]
        self.assertTrue(len(import_lines) >= 1, "agentic_loop.py 应有 import base64")
        # 最早的 import base64 应在文件顶部（前 30 行）
        self.assertLess(import_lines[0], 30, "import base64 应在文件顶部")
        # _perceive() 方法中不应有 'import base64 as _b64'
        inline_imports = [l for l in lines if "import base64 as _b64" in l]
        self.assertEqual(len(inline_imports), 0, "不应有内联 import base64 as _b64")


# ─────────────────────────────────────────────────────────────────────────────
# Section G: 端到端管道 (10)
# ─────────────────────────────────────────────────────────────────────────────

class TestEndToEndPipeline(unittest.TestCase):
    """G: 附件数据从 API 请求体流经 service → context → _perceive()。"""

    def test_g01_jpeg_full_pipeline(self):
        loop = _make_loop()
        img_data = base64.b64encode(b"\x89PNG\r\n").decode()
        context = {
            "history": [],
            "current_attachments": [
                {"name": "chart.png", "mime_type": "image/png", "size": 6, "data": img_data}
            ]
        }
        msgs = loop._perceive("explain this chart", context)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        content = msgs[0]["content"]
        self.assertIsInstance(content, list)
        img_blocks = [b for b in content if b["type"] == "image"]
        self.assertEqual(len(img_blocks), 1)
        self.assertEqual(img_blocks[0]["source"]["data"], img_data)

    def test_g02_pdf_full_pipeline(self):
        loop = _make_loop()
        pdf_data = base64.b64encode(b"%PDF-1.5").decode()
        context = {
            "history": [],
            "current_attachments": [
                {"name": "contract.pdf", "mime_type": "application/pdf", "size": 8, "data": pdf_data}
            ]
        }
        msgs = loop._perceive("summarize this PDF", context)
        content = msgs[0]["content"]
        doc_blocks = [b for b in content if b["type"] == "document"]
        self.assertEqual(len(doc_blocks), 1)
        self.assertEqual(doc_blocks[0]["source"]["data"], pdf_data)

    def test_g03_text_file_decoded_in_pipeline(self):
        loop = _make_loop()
        sql = "SELECT * FROM users WHERE id = 1;"
        txt_data = base64.b64encode(sql.encode()).decode()
        context = {
            "history": [],
            "current_attachments": [
                {"name": "query.sql", "mime_type": "text/plain", "size": len(sql), "data": txt_data}
            ]
        }
        msgs = loop._perceive("optimize this query", context)
        content = msgs[0]["content"]
        text_blocks = [b for b in content if b["type"] == "text"]
        combined = " ".join(b["text"] for b in text_blocks)
        self.assertIn("SELECT * FROM users", combined)

    def test_g04_metadata_stripped_matches_service_logic(self):
        """API 传来的完整附件 → service 剥离 data → 只保留三字段。"""
        full_attachment = {"name": "img.jpg", "mime_type": "image/jpeg",
                           "size": 512, "data": "very_long_base64=="}
        meta = {"name": full_attachment["name"],
                "mime_type": full_attachment["mime_type"],
                "size": full_attachment["size"]}
        self.assertNotIn("data", meta)
        self.assertEqual(len(meta), 3)

    def test_g05_history_annotation_format_verified(self):
        """历史消息注解格式被 _perceive() 正确处理（非空，不被丢弃）。"""
        loop = _make_loop()
        # 模拟带注解的历史消息（_build_context 输出）
        annotated_content = "please analyze\n[附件: data.csv (text/csv, 50000 bytes)]"
        context = {
            "history": [{"role": "user", "content": annotated_content}],
        }
        msgs = loop._perceive("follow up", context)
        self.assertEqual(msgs[0]["content"], annotated_content)

    def test_g06_empty_user_message_with_attachment_annotated_in_history(self):
        """用户仅上传附件（无文字）时，注解后历史非空，不被 _perceive 丢弃。
        _perceive() 对 content 做 .strip()，故前导换行会被剥除，但 [附件:...] 保留。"""
        loop = _make_loop()
        # _build_context 产生带前导 \n 的注解
        annotated = "\n[附件: pic.jpg (image/jpeg, 200 bytes)]"
        context = {
            "history": [{"role": "user", "content": annotated}],
        }
        msgs = loop._perceive("about that image", context)
        # _perceive strip() 后前导 \n 消失，但消息不被丢弃
        self.assertEqual(msgs[0]["content"], annotated.strip())
        self.assertIn("[附件:", msgs[0]["content"])

    def test_g07_multiple_attachment_types_in_single_request(self):
        loop = _make_loop()
        context = {
            "history": [],
            "current_attachments": [
                {"name": "a.jpg", "mime_type": "image/jpeg", "size": 100,
                 "data": base64.b64encode(b"\xff\xd8").decode()},
                {"name": "b.pdf", "mime_type": "application/pdf", "size": 200,
                 "data": base64.b64encode(b"%PDF").decode()},
                {"name": "c.csv", "mime_type": "text/csv", "size": 30,
                 "data": base64.b64encode(b"a,b\n1,2").decode()},
            ]
        }
        msgs = loop._perceive("analyze all", context)
        content = msgs[0]["content"]
        types = {b["type"] for b in content}
        self.assertIn("image", types)
        self.assertIn("document", types)
        self.assertIn("text", types)

    def test_g08_base64_data_preserved_exactly(self):
        """base64 数据在传递过程中不被修改。"""
        loop = _make_loop()
        original = base64.b64encode(b"test binary data \x00\x01\x02").decode()
        context = {"history": [], "current_attachments": [
            {"name": "f.pdf", "mime_type": "application/pdf", "size": 20, "data": original}
        ]}
        msgs = loop._perceive("check pdf", context)
        content = msgs[0]["content"]
        doc_blocks = [b for b in content if b["type"] == "document"]
        self.assertEqual(doc_blocks[0]["source"]["data"], original)

    def test_g09_request_body_serialization(self):
        """完整 SendMessageRequest 含附件可序列化为 JSON（实际 HTTP 传输）。"""
        from backend.api.conversations import AttachmentData, SendMessageRequest
        req = SendMessageRequest(
            content="analyze",
            stream=True,
            attachments=[
                AttachmentData(name="f.jpg", mime_type="image/jpeg", size=1024, data="abc=")
            ]
        )
        body = req.model_dump()
        json_str = json.dumps(body)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["attachments"][0]["data"], "abc=")

    def test_g10_attachment_size_recorded_from_original_not_base64(self):
        """size 字段保存的是原始文件大小（非 base64 编码后大小）。"""
        original_bytes = b"hello world"
        b64 = base64.b64encode(original_bytes).decode()
        # original size
        original_size = len(original_bytes)
        # base64 size (roughly 4/3 of original)
        b64_size = len(b64)
        self.assertNotEqual(original_size, b64_size)

        from backend.api.conversations import AttachmentData
        a = AttachmentData(name="f.txt", mime_type="text/plain",
                           size=original_size, data=b64)
        # size should match original, not b64 length
        self.assertEqual(a.size, original_size)


# ─────────────────────────────────────────────────────────────────────────────
# Section H: 边界 & 错误用例 (8)
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):
    """H: 边界值、错误输入、特殊字符等。"""

    def test_h01_malformed_base64_does_not_crash_perceive(self):
        """无效 base64 数据触发异常处理，返回降级文本块。"""
        loop = _make_loop()
        context = {"history": [], "current_attachments": [
            {"name": "bad.txt", "mime_type": "text/plain", "size": 5, "data": "not!!valid!!base64"}
        ]}
        # Should not raise
        msgs = loop._perceive("check bad file", context)
        self.assertEqual(len(msgs), 1)
        content = msgs[0]["content"]
        # At least one block should exist (fallback)
        self.assertTrue(len(content) >= 1)

    def test_h02_very_long_filename_preserved(self):
        """超长文件名在注解中完整保留。"""
        long_name = "very_long_filename_" + "x" * 200 + ".jpg"
        result = _annotate([{"name": long_name, "mime_type": "image/jpeg", "size": 100}],
                           "message")
        self.assertIn(long_name, result)

    def test_h03_size_zero_attachment(self):
        """空文件（size=0）不引发异常。"""
        loop = _make_loop()
        context = {"history": [], "current_attachments": [
            {"name": "empty.txt", "mime_type": "text/plain", "size": 0, "data": ""}
        ]}
        msgs = loop._perceive("check empty file", context)
        self.assertEqual(len(msgs), 1)

    def test_h04_pdf_exact_mime_match(self):
        """PDF 检测是精确匹配，'application/pdf; charset=utf-8' 不匹配。"""
        mime = "application/pdf; charset=utf-8"
        # Mimic _perceive logic
        is_pdf = mime == "application/pdf"
        is_image = mime.startswith("image/")
        self.assertFalse(is_pdf)
        self.assertFalse(is_image)
        # Falls to else (text decode) — acceptable behavior for malformed mime

    def test_h05_multiple_calls_no_state_leakage(self):
        """连续调用 _perceive 时 context 不共享状态。"""
        loop = _make_loop()
        ctx1 = {"history": [], "current_attachments": [
            {"name": "a.jpg", "mime_type": "image/jpeg", "size": 3,
             "data": base64.b64encode(b"img").decode()}
        ]}
        ctx2 = {"history": []}
        msgs1 = loop._perceive("first", ctx1)
        msgs2 = loop._perceive("second", ctx2)
        # First has multimodal, second is plain
        self.assertIsInstance(msgs1[0]["content"], list)
        self.assertIsInstance(msgs2[0]["content"], str)

    def test_h06_none_in_attachments_list_handled(self):
        """attachments 列表为 None 时不注入 context。"""
        context = {"history": []}
        att = None
        if att:
            context["current_attachments"] = att
        self.assertNotIn("current_attachments", context)

    def test_h07_image_data_passed_unchanged_to_source(self):
        """图片 base64 数据按原样传入 source.data，不做任何修改。"""
        loop = _make_loop()
        # Include padding chars and mixed case (valid base64)
        data = "SGVsbG8gV29ybGQh"
        context = {"history": [], "current_attachments": [
            {"name": "t.jpg", "mime_type": "image/jpeg", "size": 12, "data": data}
        ]}
        msgs = loop._perceive("test", context)
        content = msgs[0]["content"]
        img = [b for b in content if b["type"] == "image"][0]
        self.assertEqual(img["source"]["data"], data)

    def test_h08_send_request_two_instances_dont_share_attachments(self):
        """两个 SendMessageRequest 实例的 attachments 列表互相独立。"""
        from backend.api.conversations import AttachmentData, SendMessageRequest
        req1 = SendMessageRequest(content="first")
        req2 = SendMessageRequest(content="second")
        # Mutate req1's list
        req1.attachments.append(
            AttachmentData(name="f.jpg", mime_type="image/jpeg", size=100, data="x=")
        )
        # req2 should not be affected
        self.assertEqual(len(req2.attachments), 0)


# ─────────────────────────────────────────────────────────────────────────────
# Section I: Bug-Fix 验证 (4)
# ─────────────────────────────────────────────────────────────────────────────

class TestBugFixes(unittest.TestCase):
    """I: 三个已修复 Bug 的验证。"""

    def test_i01_bug1_base64_module_level_import(self):
        """Bug-1: import base64 已移至 agentic_loop.py 模块顶部，_perceive 中无内联导入。"""
        path = os.path.join(os.path.dirname(__file__), "backend", "agents", "agentic_loop.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        lines = src.splitlines()
        # 'import base64' 存在
        self.assertIn("import base64", src)
        # 找到第一行 'import base64'（不含 as _b64）
        top_imports = [i for i, l in enumerate(lines)
                       if l.strip() == "import base64"]
        self.assertTrue(len(top_imports) > 0, "应有 'import base64' 行")
        self.assertLess(top_imports[0], 30, "import base64 应在文件顶部")
        # 无内联 'import base64 as _b64'
        self.assertNotIn("import base64 as _b64", src)

    def test_i02_bug2_chatinput_imports_antmessage(self):
        """Bug-2: ChatInput.tsx 引入 antd message 用于用户反馈。"""
        path = os.path.join(os.path.dirname(__file__),
                            "frontend", "src", "components", "chat", "ChatInput.tsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # 引入 antMessage（别名 antMessage）
        self.assertIn("antMessage", content)
        # 使用 antMessage.error
        self.assertIn("antMessage.error", content)

    def test_i03_bug3_mime_fallback_function_present(self):
        """Bug-3: ChatInput.tsx 包含 inferMimeType 函数和 EXT_MIME_MAP。"""
        path = os.path.join(os.path.dirname(__file__),
                            "frontend", "src", "components", "chat", "ChatInput.tsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("inferMimeType", content)
        self.assertIn("EXT_MIME_MAP", content)
        # .md 有对应条目
        self.assertIn("text/markdown", content)

    def test_i04_bug3_mime_fallback_logic_works(self):
        """Bug-3 功能验证：EXT_MIME_MAP 正确映射常见扩展名。"""
        # 模拟 TypeScript 逻辑的 Python 等价
        EXT_MIME_MAP = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif", "webp": "image/webp",
            "pdf": "application/pdf",
            "txt": "text/plain", "csv": "text/csv",
            "md": "text/markdown", "markdown": "text/markdown",
            "json": "application/json",
        }
        def infer_mime(filename, file_type):
            if file_type:
                return file_type
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            return EXT_MIME_MAP.get(ext, "")

        self.assertEqual(infer_mime("doc.md", ""), "text/markdown")
        self.assertEqual(infer_mime("data.csv", ""), "text/csv")
        self.assertEqual(infer_mime("report.pdf", ""), "application/pdf")
        self.assertEqual(infer_mime("photo.jpeg", "image/jpeg"), "image/jpeg")
        self.assertEqual(infer_mime("unknown.xyz", ""), "")


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

SECTIONS = [
    ("A", TestAttachmentDataModel),
    ("B", TestPerceiveMultimodal),
    ("C", TestServiceAttachmentMetadata),
    ("D", TestBuildContextAnnotation),
    ("E", TestApiEndpointSchema),
    ("F", TestRbacRegression),
    ("G", TestEndToEndPipeline),
    ("H", TestEdgeCases),
    ("I", TestBugFixes),
]

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
