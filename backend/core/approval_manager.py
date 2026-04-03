"""
Approval Manager — Human-in-the-Loop approval queue.

Shared singleton used by:
  - backend.agents.etl_agent   (create + wait)
  - backend.api.approvals      (approve / reject REST endpoints)
  - frontend SSE stream        (approval_required event → ApprovalModal)

Flow:
  1. ETLAgenticLoop detects dangerous SQL in a tool_call
  2. Calls approval_manager.create_approval(data) → approval_id
  3. Yields  ``approval_required`` event with approval_id over SSE
  4. Awaits  approval_manager.wait_for_decision(approval_id, timeout=60)
  5. Frontend shows ApprovalModal; user clicks Approve or Reject
  6. Frontend calls POST /api/v1/approvals/{id}/approve  or  /reject
  7. REST handler calls approval_manager.approve() / .reject()
  8. asyncio.Event is set → wait_for_decision() returns
  9. ETLAgenticLoop continues or aborts the tool call
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 180.0  # seconds before auto-reject（3 分钟，为长 LLM 响应后留足交互时间）


@dataclass
class ApprovalEntry:
    """A single pending (or resolved) approval request."""
    approval_id: str
    status: str  # "pending" | "approved" | "rejected" | "timeout"
    data: Dict[str, Any]
    created_at: str
    resolved_at: Optional[str] = None
    reject_reason: Optional[str] = None
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "status": self.status,
            "data": self.data,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "reject_reason": self.reject_reason,
        }


class ApprovalManager:
    """
    In-memory approval queue with asyncio.Event-based waiting.

    Handles two approval modes:
      1. Per-operation (ETL dangerous SQL) — individual approval_id per tool call
      2. Session-level (file write)        — one approval unlocks all writes for
                                             the entire conversation session

    Thread-safety note: asyncio.Event is not thread-safe across event-loop
    threads.  All callers must run in the same asyncio event loop (FastAPI's
    default single-threaded loop satisfies this).
    """

    def __init__(self) -> None:
        self._approvals: Dict[str, ApprovalEntry] = {}
        # conversation_id → set of granted capabilities ("file_write", ...)
        self._session_grants: Dict[str, set] = {}

    # ------------------------------------------------------------------ #
    # Producer side (called by ETLAgenticLoop)                            #
    # ------------------------------------------------------------------ #

    def create_approval(self, data: Dict[str, Any]) -> str:
        """
        Register a new pending approval.

        Args:
            data: Arbitrary payload shown to the user.
                  Typically: {tool, sql, warnings, message}

        Returns:
            A new approval_id (UUID string).
        """
        aid = str(uuid.uuid4())
        entry = ApprovalEntry(
            approval_id=aid,
            status="pending",
            data=data,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._approvals[aid] = entry
        logger.info(
            "[ApprovalManager] Created approval %s tool=%s",
            aid[:8],
            data.get("tool", "?"),
        )
        return aid

    async def wait_for_decision(
        self,
        approval_id: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> bool:
        """
        Suspend the calling coroutine until the approval is resolved.

        Returns True  → approved (tool call should proceed)
        Returns False → rejected or timed-out (tool call should be aborted)
        """
        entry = self._approvals.get(approval_id)
        if not entry:
            logger.warning("[ApprovalManager] Unknown approval_id %s", approval_id)
            return False

        try:
            await asyncio.wait_for(entry._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            entry.status = "timeout"
            entry.resolved_at = datetime.now(timezone.utc).isoformat()
            logger.warning("[ApprovalManager] Approval %s timed out", approval_id[:8])
            return False

        return entry.status == "approved"

    # ------------------------------------------------------------------ #
    # Consumer side (called by REST API layer)                            #
    # ------------------------------------------------------------------ #

    def approve(self, approval_id: str) -> None:
        """Approve the pending operation and wake the waiting coroutine."""
        entry = self._require_pending(approval_id)
        entry.status = "approved"
        entry.resolved_at = datetime.now(timezone.utc).isoformat()
        entry._event.set()
        logger.info("[ApprovalManager] Approved %s", approval_id[:8])

    def reject(self, approval_id: str, reason: str = "") -> None:
        """Reject the pending operation and wake the waiting coroutine."""
        entry = self._require_pending(approval_id)
        entry.status = "rejected"
        entry.reject_reason = reason
        entry.resolved_at = datetime.now(timezone.utc).isoformat()
        entry._event.set()
        logger.info("[ApprovalManager] Rejected %s: %s", approval_id[:8], reason)

    # ------------------------------------------------------------------ #
    # Query helpers                                                       #
    # ------------------------------------------------------------------ #

    def get(self, approval_id: str) -> Optional[ApprovalEntry]:
        """Return the entry or None if not found."""
        return self._approvals.get(approval_id)

    def list_pending(self) -> List[Dict[str, Any]]:
        """Return all entries whose status is 'pending'."""
        return [
            e.to_dict()
            for e in self._approvals.values()
            if e.status == "pending"
        ]

    def list_all(self) -> List[Dict[str, Any]]:
        """Return all entries (for debugging / admin views)."""
        return [e.to_dict() for e in self._approvals.values()]

    # ------------------------------------------------------------------ #
    # Session-level grants (file write, etc.)                            #
    # ------------------------------------------------------------------ #

    def is_file_write_granted(self, conversation_id: str) -> bool:
        """Return True if conversation already has file-write session grant."""
        return "file_write" in self._session_grants.get(conversation_id, set())

    def grant_file_write(self, conversation_id: str) -> None:
        """Grant file-write capability for the entire conversation session."""
        if conversation_id not in self._session_grants:
            self._session_grants[conversation_id] = set()
        self._session_grants[conversation_id].add("file_write")
        logger.info(
            "[ApprovalManager] Session file_write granted for conversation %s",
            conversation_id[:8] if len(conversation_id) >= 8 else conversation_id,
        )

    def revoke_session_grants(self, conversation_id: str) -> None:
        """Revoke all session grants for a conversation (e.g., on session end)."""
        self._session_grants.pop(conversation_id, None)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_pending(self, approval_id: str) -> ApprovalEntry:
        entry = self._approvals.get(approval_id)
        if not entry:
            raise KeyError(f"Approval {approval_id!r} not found")
        if entry.status != "pending":
            raise ValueError(
                f"Approval {approval_id!r} is already in status {entry.status!r}"
            )
        return entry


# ──────────────────────────────────────────────────────────────────────── #
# Module-level singleton — import this from anywhere in the application   #
# ──────────────────────────────────────────────────────────────────────── #
approval_manager = ApprovalManager()
