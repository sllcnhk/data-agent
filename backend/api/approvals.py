"""
Human-in-the-Loop Approvals REST API

ETL 工程师 Agent 在执行高危 SQL（DROP / TRUNCATE / DELETE / ALTER）前，
会暂停并通过 SSE 发出 ``approval_required`` 事件。前端弹出 ApprovalModal，
用户点击同意或拒绝后，调用此 API，Agent 随即继续或中止操作。

Endpoints
---------
GET  /api/v1/approvals/                        列出所有待审批请求
GET  /api/v1/approvals/{approval_id}           查询单个审批状态
POST /api/v1/approvals/{approval_id}/approve   批准操作
POST /api/v1/approvals/{approval_id}/reject    拒绝操作
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.approval_manager import approval_manager

router = APIRouter(prefix="/approvals", tags=["approvals"])


# ──────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────

class RejectRequest(BaseModel):
    reason: str = ""


# ──────────────────────────────────────────────────────────
# REST endpoints
# ──────────────────────────────────────────────────────────

@router.get("/", response_model=List[Dict[str, Any]])
async def list_pending_approvals():
    """列出所有待审批的操作（前端轮询或调试用）"""
    return approval_manager.list_pending()


@router.get("/{approval_id}", response_model=Dict[str, Any])
async def get_approval(approval_id: str):
    """获取单个审批的详细信息"""
    entry = approval_manager.get(approval_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")
    return entry.to_dict()


@router.post("/{approval_id}/approve", response_model=Dict[str, Any])
async def approve_action(approval_id: str):
    """
    批准操作。

    调用后，正在等待的 ETLAgenticLoop 协程将继续执行工具调用。
    """
    entry = approval_manager.get(approval_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")
    try:
        approval_manager.approve(approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "success": True,
        "approval_id": approval_id,
        "status": "approved",
    }


@router.post("/{approval_id}/reject", response_model=Dict[str, Any])
async def reject_action(approval_id: str, request: RejectRequest):
    """
    拒绝操作。

    调用后，ETLAgenticLoop 将跳过该危险操作并在 SSE 流中发出错误事件。
    """
    entry = approval_manager.get(approval_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")
    try:
        approval_manager.reject(approval_id, request.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "success": True,
        "approval_id": approval_id,
        "status": "rejected",
        "reason": request.reason,
    }
