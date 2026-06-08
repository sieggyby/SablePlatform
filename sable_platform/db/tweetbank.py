"""Tweet Assist tweetbank CRUD (migration 074).

The curated store of ready-to-post original tweets, keyed per managed account with a
shared per-org GLOBAL pool (``account_handle IS NULL``). Humans submit -> ``approved``
directly; the P4 AI suggester writes ``source='ai' status='pending'`` for an approver to
clear. CONTENT, not a cost surface -- no function here selects or stores a cost column.

The ``used`` status is an ADVISORY soft-claim (mark-used on Compose so an idea is not
double-posted across operators), NOT a hard lock. Writers require an ``immediate_txn``;
reads are transaction-free. Grant/approval gating lives in the caller (SableWeb) -- these
helpers are org-scoped data access, not the authorization boundary.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text as _sa_text
from sqlalchemy.engine import Connection

_COLS = (
    "id, client_org, account_handle, text, register_band, topic_tags, author, "
    "source, status, created_at, used_at, used_by"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_tags(topic_tags) -> str:
    """A JSON-array string. Accepts an already-encoded string or a list (fail-safe [])."""
    if isinstance(topic_tags, str):
        return topic_tags or "[]"
    try:
        return json.dumps([str(t) for t in (topic_tags or [])])
    except (TypeError, ValueError):
        return "[]"


def submit_entry(
    conn: Connection,
    *,
    client_org: str,
    text: str,
    account_handle: str | None = None,
    register_band: str | None = None,
    topic_tags=None,
    author: str | None = None,
    source: str = "human",
    status: str = "approved",
    now: str | None = None,
) -> int:
    """Insert a tweetbank entry; return the new id. The caller MUST be in an immediate_txn.

    A human submission defaults to ``source='human' status='approved'`` (lands in the bank
    directly); the P4 AI path passes ``source='ai' status='pending'``. ``account_handle``
    None = the per-org GLOBAL pool. NO cost is stored.
    """
    row = conn.execute(
        _sa_text(
            "INSERT INTO tweetbank_entries "
            "  (client_org, account_handle, text, register_band, topic_tags, author, source, status, created_at) "
            "VALUES (:org, :acc, :text, :band, :tags, :author, :source, :status, :now) "
            "RETURNING id"
        ),
        {
            "org": client_org,
            "acc": account_handle,
            "text": text,
            "band": register_band,
            "tags": _norm_tags(topic_tags),
            "author": author,
            "source": source,
            "status": status,
            "now": now or _utc_now_iso(),
        },
    ).fetchone()
    return int(row[0]) if row is not None else 0


def get_entry(conn: Connection, entry_id: int) -> dict | None:
    """Read one entry by id (read-only). Used by the caller to resolve an entry's org for
    the cross-org IDOR guard before a mark-used / approve / reject."""
    row = conn.execute(
        _sa_text(f"SELECT {_COLS} FROM tweetbank_entries WHERE id = :id"),
        {"id": int(entry_id)},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def list_bank(
    conn: Connection,
    client_org: str,
    account_handles=(),
    *,
    include_global: bool = True,
    statuses=("approved", "used"),
    limit: int = 200,
) -> list[dict]:
    """The operator's bank view: entries for ``client_org`` whose ``account_handle`` is one
    of ``account_handles`` (the operator's GRANTED accounts) OR global (NULL, when
    ``include_global``), in the given ``statuses``. Read-only, ORG-SCOPED. ``used`` rows
    sort last (advisory — still visible, just depressed). NO cost column is selected.

    Returns ``[]`` when the operator has no granted accounts AND global is excluded (a
    fail-closed empty view, never the whole table).
    """
    handles = [h for h in (account_handles or []) if h]
    statuses = [s for s in (statuses or []) if s]
    if not statuses:
        return []
    parts: list[str] = []
    params: dict = {"org": client_org, "limit": int(limit)}
    if handles:
        ph = ", ".join(f":h{i}" for i in range(len(handles)))
        params.update({f"h{i}": h for i, h in enumerate(handles)})
        parts.append(f"account_handle IN ({ph})")
    if include_global:
        parts.append("account_handle IS NULL")
    if not parts:
        return []  # no granted accounts, global excluded -> nothing (fail-closed)
    st_ph = ", ".join(f":st{i}" for i in range(len(statuses)))
    params.update({f"st{i}": s for i, s in enumerate(statuses)})
    acct_clause = "(" + " OR ".join(parts) + ")"
    rows = conn.execute(
        _sa_text(
            f"SELECT {_COLS} FROM tweetbank_entries "
            f"WHERE client_org = :org AND status IN ({st_ph}) AND {acct_clause} "
            "ORDER BY CASE WHEN status = 'used' THEN 1 ELSE 0 END, created_at DESC, id DESC "
            "LIMIT :limit"
        ),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def list_pending(conn: Connection, client_org: str, *, limit: int = 100) -> list[dict]:
    """The P4 approval queue: ALL ``status='pending'`` entries for ``client_org`` (across
    every account + global). Read-only, ORG-SCOPED. Approvers judge org-wide, so this is
    NOT account-filtered — the approver-only gate + the org wall live in the caller
    (SableWeb). Newest first. NO cost column."""
    rows = conn.execute(
        _sa_text(
            f"SELECT {_COLS} FROM tweetbank_entries "
            "WHERE client_org = :org AND status = 'pending' "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ),
        {"org": client_org, "limit": int(limit)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def mark_used(conn: Connection, entry_id: int, used_by: str | None, *, now: str | None = None) -> bool:
    """Advisory soft-claim: flip an APPROVED entry to ``used`` (records used_at/used_by) so
    it is not double-posted. Idempotent + safe: only an ``approved`` row flips (returns
    False if already used / pending / rejected / absent). The caller MUST be in an
    immediate_txn."""
    res = conn.execute(
        _sa_text(
            "UPDATE tweetbank_entries SET status = 'used', used_at = :now, used_by = :by "
            "WHERE id = :id AND status = 'approved'"
        ),
        {"now": now or _utc_now_iso(), "by": used_by, "id": int(entry_id)},
    )
    return (res.rowcount or 0) > 0


def set_status(conn: Connection, entry_id: int, status: str) -> bool:
    """P4 approve/reject: flip a ``pending`` entry to ``approved`` or ``rejected``. Only a
    pending row flips (returns False otherwise) so an already-used/approved entry can't be
    re-judged. The caller MUST be in an immediate_txn."""
    if status not in ("approved", "rejected"):
        raise ValueError(f"set_status only approves/rejects, got {status!r}")
    res = conn.execute(
        _sa_text(
            "UPDATE tweetbank_entries SET status = :status WHERE id = :id AND status = 'pending'"
        ),
        {"status": status, "id": int(entry_id)},
    )
    return (res.rowcount or 0) > 0


def add_ai_suggestions(conn: Connection, *, client_org: str, entries, now: str | None = None) -> int:
    """P4 bulk insert: write a batch of AI-proposed entries as ``source='ai'
    status='pending'`` for the approval queue. Each entry is a dict
    ``{text, account_handle?, register_band?, topic_tags?}``. Skips blank text. Returns the
    count inserted. The caller MUST be in an immediate_txn."""
    now = now or _utc_now_iso()
    count = 0
    for e in entries or []:
        body = str((e or {}).get("text") or "").strip()
        if not body:
            continue
        submit_entry(
            conn,
            client_org=client_org,
            text=body,
            account_handle=(e.get("account_handle") or None),
            register_band=(e.get("register_band") or None),
            topic_tags=e.get("topic_tags"),
            author=(e.get("author") or "ai"),
            source="ai",
            status="pending",
            now=now,
        )
        count += 1
    return count
