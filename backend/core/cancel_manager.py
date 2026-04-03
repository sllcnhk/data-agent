"""
Conversation stream cancellation manager.

Each active conversation gets a lazily-created asyncio.Event.
Calling request_cancel() sets it; the AgenticLoop checks it via
_cancellable_await() using asyncio.wait(FIRST_COMPLETED).
"""
import asyncio
from typing import Dict


class ConversationCancelManager:
    def __init__(self) -> None:
        self._events: Dict[str, asyncio.Event] = {}

    def get_event(self, conv_id: str) -> asyncio.Event:
        """Return (creating if necessary) the cancel Event for conv_id."""
        if conv_id not in self._events:
            self._events[conv_id] = asyncio.Event()
        return self._events[conv_id]

    def request_cancel(self, conv_id: str) -> None:
        """Signal cancellation for the given conversation."""
        self.get_event(conv_id).set()

    def should_cancel(self, conv_id: str) -> bool:
        """True if a cancel has been requested but not yet cleared."""
        if conv_id not in self._events:
            return False
        return self._events[conv_id].is_set()

    def clear(self, conv_id: str) -> None:
        """Reset cancel state at the start of each new message."""
        if conv_id in self._events:
            self._events[conv_id].clear()


# Module-level singleton — import and use directly.
cancel_manager = ConversationCancelManager()
