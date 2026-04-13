"""
notify_service.py
=================
多渠道通知发送服务。

支持渠道：
  - email   — SMTP HTML 邮件（含报告链接 + 可选 PDF 附件）
  - wecom   — 企业微信 Webhook（图文消息）
  - feishu  — 飞书 Webhook（消息卡片）
  - webhook — 通用 HTTP Webhook（POST JSON）

调用方式：
    svc = NotifyService(db)
    summary = await svc.send_all(channels, report, scheduled_report_id, run_log_id)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class NotifyService:
    """多渠道通知服务"""

    def __init__(self, db):
        self.db = db

    # ─────────────────────────────────────────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────────────────────────────────────────

    async def send_all(
        self,
        channels: List[Dict[str, Any]],
        report,
        scheduled_report_id: Optional[str] = None,
        run_log_id: Optional[str] = None,
        is_test: bool = False,
    ) -> Dict[str, Any]:
        """
        向所有渠道发送通知。

        Returns:
            summary dict: {"email": "success", "wecom": "failed: xxx", "total": 2, "ok": 1}
        """
        summary: Dict[str, Any] = {"total": len(channels), "ok": 0}
        for ch in channels:
            channel_type = ch.get("type", "")
            try:
                await self.send_one(
                    channel=ch,
                    report=report,
                    scheduled_report_id=scheduled_report_id,
                    run_log_id=run_log_id,
                    is_test=is_test,
                )
                summary[channel_type] = "success"
                summary["ok"] = summary.get("ok", 0) + 1
            except Exception as e:
                err_msg = f"failed: {e}"
                summary[channel_type] = err_msg
                logger.warning("[Notify] Channel %s failed: %s", channel_type, e)

        return summary

    async def send_one(
        self,
        channel: Dict[str, Any],
        report,
        scheduled_report_id: Optional[str] = None,
        run_log_id: Optional[str] = None,
        is_test: bool = False,
    ) -> None:
        """
        向单个渠道发送通知，并写 NotificationLog（除非 is_test=True）。
        """
        channel_type = channel.get("type", "")
        recipient = ""

        try:
            if channel_type == "email":
                recipient = ", ".join(channel.get("to", []))
                await self._send_email(channel, report, is_test)

            elif channel_type == "wecom":
                recipient = channel.get("webhook_url", "")[:80]
                await self._send_wecom(channel, report, is_test)

            elif channel_type == "feishu":
                recipient = channel.get("webhook_url", "")[:80]
                await self._send_feishu(channel, report, is_test)

            elif channel_type == "webhook":
                recipient = channel.get("url", "")[:80]
                await self._send_webhook(channel, report, is_test)

            else:
                raise ValueError(f"未知的渠道类型: {channel_type}")

            if not is_test:
                self._write_log(
                    channel_type=channel_type,
                    recipient=recipient,
                    status="success",
                    scheduled_report_id=scheduled_report_id,
                    run_log_id=run_log_id,
                )

        except Exception as e:
            if not is_test:
                self._write_log(
                    channel_type=channel_type,
                    recipient=recipient,
                    status="failed",
                    error=str(e),
                    scheduled_report_id=scheduled_report_id,
                    run_log_id=run_log_id,
                )
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # 渠道实现
    # ─────────────────────────────────────────────────────────────────────────

    async def _send_email(self, channel: Dict, report, is_test: bool) -> None:
        """发送 HTML 邮件（SMTP）。"""
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        from backend.config.settings import settings

        smtp_host = getattr(settings, "smtp_host", "") or ""
        smtp_port = int(getattr(settings, "smtp_port", 587))
        smtp_user = getattr(settings, "smtp_user", "") or ""
        smtp_pass = getattr(settings, "smtp_pass", "") or ""
        smtp_from = getattr(settings, "smtp_from", smtp_user) or smtp_user

        if not smtp_host:
            raise RuntimeError("SMTP 未配置（SMTP_HOST 为空）")

        recipients: List[str] = channel.get("to", [])
        if not recipients:
            raise ValueError("邮件渠道未指定收件人（to 字段为空）")

        report_name = getattr(report, "name", "数据报告")
        report_url = _build_report_url(report)
        summary = getattr(report, "llm_summary", "") or ""
        date_str = datetime.now().strftime("%Y-%m-%d")

        subject_tpl = channel.get("subject_tpl", "{{name}} — {{date}}")
        subject = subject_tpl.replace("{{name}}", report_name).replace("{{date}}", date_str)
        if is_test:
            subject = "[测试] " + subject

        html_body = _render_email_html(report_name, report_url, summary, is_test)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            if smtp_port in (587, 465):
                server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, recipients, msg.as_string())

        logger.info("[Notify] Email sent to %s", recipients)

    async def _send_wecom(self, channel: Dict, report, is_test: bool) -> None:
        """发送企业微信 Webhook 图文消息。"""
        import urllib.request

        webhook_url = channel.get("webhook_url", "")
        if not webhook_url:
            raise ValueError("企微渠道未配置 webhook_url")

        report_name = getattr(report, "name", "数据报告")
        report_url = _build_report_url(report)
        summary = getattr(report, "llm_summary", "") or "点击查看详细报表数据。"

        payload = {
            "msgtype": "news",
            "news": {
                "articles": [
                    {
                        "title": ("[测试] " if is_test else "") + report_name,
                        "description": (summary[:120] + "...") if len(summary) > 120 else summary,
                        "url": report_url,
                        "picurl": "",
                    }
                ]
            },
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if result.get("errcode", 0) != 0:
            raise RuntimeError(f"企微返回错误: {result}")
        logger.info("[Notify] WeChat Work message sent.")

    async def _send_feishu(self, channel: Dict, report, is_test: bool) -> None:
        """发送飞书 Webhook 消息卡片。"""
        import urllib.request

        webhook_url = channel.get("webhook_url", "")
        if not webhook_url:
            raise ValueError("飞书渠道未配置 webhook_url")

        report_name = getattr(report, "name", "数据报告")
        report_url = _build_report_url(report)
        summary = getattr(report, "llm_summary", "") or "点击查看详细报表数据。"

        title = ("[测试] " if is_test else "") + report_name
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": summary[:300] if summary else "数据报告已生成",
                        },
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "查看报告"},
                                "url": report_url,
                                "type": "default",
                            }
                        ],
                    },
                ],
            },
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if result.get("StatusCode", 0) not in (0, 200) and result.get("code", 0) not in (0, 200):
            raise RuntimeError(f"飞书返回错误: {result}")
        logger.info("[Notify] Feishu message sent.")

    async def _send_webhook(self, channel: Dict, report, is_test: bool) -> None:
        """发送通用 Webhook。"""
        import urllib.request

        url = channel.get("url", "")
        if not url:
            raise ValueError("webhook 渠道未配置 url")

        method = channel.get("method", "POST").upper()
        report_name = getattr(report, "name", "数据报告")
        report_url = _build_report_url(report)
        summary = getattr(report, "llm_summary", "") or ""

        payload = {
            "report_id": str(getattr(report, "id", "")),
            "name": report_name,
            "html_url": report_url,
            "summary": summary[:500] if summary else "",
            "timestamp": datetime.utcnow().isoformat(),
            "is_test": is_test,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data if method == "POST" else None,
            headers={"Content-Type": "application/json"}, method=method
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.getcode()
        if status not in (200, 201, 202, 204):
            raise RuntimeError(f"Webhook 返回非 2xx 状态: {status}")
        logger.info("[Notify] Webhook sent to %s (status %s)", url, status)

    # ─────────────────────────────────────────────────────────────────────────
    # 日志写入
    # ─────────────────────────────────────────────────────────────────────────

    def _write_log(
        self,
        channel_type: str,
        recipient: str,
        status: str,
        error: Optional[str] = None,
        scheduled_report_id: Optional[str] = None,
        run_log_id: Optional[str] = None,
    ) -> None:
        try:
            from backend.models.notification_log import NotificationLog

            log = NotificationLog(
                scheduled_report_id=(
                    uuid.UUID(scheduled_report_id) if scheduled_report_id else None
                ),
                run_log_id=uuid.UUID(run_log_id) if run_log_id else None,
                channel_type=channel_type,
                recipient=recipient,
                status=status,
                error=error,
            )
            self.db.add(log)
            self.db.commit()
        except Exception as e:
            logger.warning("[Notify] Failed to write notification log: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────

def _build_report_url(report) -> str:
    """构建报告的公开访问 URL（使用 refresh_token）。"""
    import os

    host = os.environ.get("PUBLIC_HOST", "http://localhost:8000")
    rid = str(getattr(report, "id", ""))
    token = getattr(report, "refresh_token", "") or ""
    if rid and token:
        return f"{host}/api/v1/reports/{rid}/html?token={token}"
    return host


def _render_email_html(name: str, url: str, summary: str, is_test: bool) -> str:
    """渲染 HTML 邮件正文。"""
    test_banner = '<div style="background:#fffbe6;border:1px solid #ffe58f;padding:8px 16px;margin-bottom:16px;border-radius:4px;">⚠️ 这是一封测试邮件</div>' if is_test else ""
    summary_html = (
        f'<p style="color:#444;line-height:1.7;">{summary[:600]}</p>'
        if summary
        else '<p style="color:#999;">（本报告暂无 AI 总结）</p>'
    )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
  {test_banner}
  <h2 style="color:#1677ff;">📊 {name}</h2>
  {summary_html}
  <div style="margin-top:24px;">
    <a href="{url}" style="background:#1677ff;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:14px;">
      查看报告 →
    </a>
  </div>
  <p style="margin-top:32px;font-size:12px;color:#999;">
    由 Data Agent 自动发送 · <a href="{url}" style="color:#999;">{url}</a>
  </p>
</body>
</html>"""
