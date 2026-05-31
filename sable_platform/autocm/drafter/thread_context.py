"""Thread-context assembler (DESIGN §4 ``drafter/thread_context``).

SKELETON (full impl = C3.3). Pulls the last N=5 messages from ``relay_messages``
for multi-turn coherence. C3.1 fixes the seam + the N constant.
"""
from __future__ import annotations

from typing import List

from sqlalchemy.engine import Connection

# Multi-turn coherence window (DESIGN §4: "last N messages").
THREAD_CONTEXT_N = 5


def load_thread_context(
    conn: Connection, chat_row_id: int, *, n: int = THREAD_CONTEXT_N
) -> List[str]:
    """Return the last ``n`` message texts for a chat. SKELETON — C3.3 implements."""
    raise NotImplementedError("thread-context assembly lands in C3.3")


__all__ = ["THREAD_CONTEXT_N", "load_thread_context"]
