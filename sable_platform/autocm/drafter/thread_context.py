"""Thread-context assembler (DESIGN §4 ``drafter/thread_context`` — C3.3).

Pulls the last N=5 messages from ``relay_messages`` for multi-turn coherence so the
bimodal NULO drafter can compose a reply that is aware of the immediately-preceding
conversation (CLASSIFIER §3 / the C3.4a injection surface names ``{thread_context}``
as "last-5 messages from OTHER members").

Two responsibilities, both deterministic and dependency-light:

  * :func:`load_thread_context` — the SQL read: the last ``n`` message texts for a
    chat, oldest-first (chronological), each tagged with a coarse author label
    (``self`` when the bot itself authored it, ``other`` otherwise — the bot's own
    member id is injectable). Mirrors ``sable_platform.autocm.db``'s contract:
    takes an already-open SA ``Connection`` (the caller owns lifecycle), creates no
    engine, binds Python-computed params, and embeds no business logic beyond the
    windowed read. Empty / no-text rows are skipped so a media-only message does not
    inject a blank turn.

  * :func:`truncate_thread_context` — the coherence-window TRUNCATION primitive:
    given an arbitrary list of prior turns, keep only the most-recent ``n`` (the
    TAIL — the closest context to the message being answered). This is the
    load-bearing N=5 cap that bounds the variable bytes that flow into the
    prompt-cached request (the cached persona system block is stable; only the
    truncated thread + the message vary).

The window is scoped to the chat over recency (``relay_messages`` carries no
first-class thread id — see ``autocm.db.member_replied_within`` for the same v1
recency-in-chat scoping), matching the LATENCY §2 cheap-context intent (no thread
reconstruction in v1).
"""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Multi-turn coherence window (DESIGN §4: "last N messages"). N=5 is LOCKED — it is
# the same window the C3.4a injection-hardening surface wraps ("last-5 messages from
# OTHER members"), so the two must not drift.
THREAD_CONTEXT_N = 5

#: coarse author labels prefixing each rendered turn.
SELF_LABEL = "self"
OTHER_LABEL = "other"


def truncate_thread_context(
    turns: List[str], *, n: int = THREAD_CONTEXT_N
) -> List[str]:
    """Keep only the most-recent ``n`` turns (the TAIL), preserving order.

    The N=5 coherence cap. ``turns`` is assumed chronological (oldest-first); the
    closest context to the message being answered is the END of the list, so the
    tail is retained. ``n <= 0`` yields an empty window (no context); a list shorter
    than ``n`` is returned unchanged. Pure, deterministic — the truncation that
    bounds the variable prompt bytes.
    """
    if n <= 0:
        return []
    if len(turns) <= n:
        return list(turns)
    return list(turns[-n:])


def load_thread_context(
    conn: Connection,
    chat_row_id: int,
    *,
    n: int = THREAD_CONTEXT_N,
    bot_member_id: Optional[int] = None,
    before_message_row_id: Optional[int] = None,
) -> List[str]:
    """Return the last ``n`` non-empty message turns for a chat, oldest-first.

    Each turn is rendered ``"<label>: <text>"`` where ``label`` is :data:`SELF_LABEL`
    when ``bot_member_id`` authored the row else :data:`OTHER_LABEL`. The read pulls
    the most-recent ``n`` rows (newest-first by ``id``, the monotonic insertion
    order — robust to equal ``received_at`` timestamps) then REVERSES to
    chronological order for the prompt. Rows with NULL/empty ``text`` (media-only)
    are excluded so they never inject a blank turn.

    ``before_message_row_id`` excludes the message currently being answered (and
    anything inserted after it) when the relay has already persisted the inbound
    message — so the bot's context is the conversation BEFORE its reply target, not
    including the target itself.

    Pure read: caller owns the ``Connection`` lifecycle; no engine is created here;
    params are bound (dialect-agnostic, runs unchanged on Postgres).
    """
    if n <= 0:
        return []

    params: dict = {"chat_id": chat_row_id, "lim": n}
    before_sql = ""
    if before_message_row_id is not None:
        before_sql = " AND id < :before_id"
        params["before_id"] = before_message_row_id

    rows = conn.execute(
        text(
            "SELECT member_id, text FROM relay_messages "
            "WHERE chat_id = :chat_id "
            "  AND text IS NOT NULL AND text <> '' "
            f"{before_sql} "
            "ORDER BY id DESC "
            "LIMIT :lim"
        ),
        params,
    ).fetchall()

    # rows are newest-first; reverse to chronological (oldest-first) for the prompt.
    turns: List[str] = []
    for row in reversed(rows):
        m = row._mapping
        body = (m["text"] or "").strip()
        if not body:
            continue
        label = (
            SELF_LABEL
            if bot_member_id is not None and m["member_id"] == bot_member_id
            else OTHER_LABEL
        )
        turns.append(f"{label}: {body}")

    # the LIMIT already bounds to n; truncate defensively so the N=5 cap is a single
    # enforced invariant regardless of how the read evolves.
    return truncate_thread_context(turns, n=n)


__all__ = [
    "THREAD_CONTEXT_N",
    "SELF_LABEL",
    "OTHER_LABEL",
    "truncate_thread_context",
    "load_thread_context",
]
