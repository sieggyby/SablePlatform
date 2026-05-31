"""SableRelay query helpers (PLAN §5.3 ``relay/db.py``).

Every helper here takes an already-open SQLAlchemy ``Connection`` and **reuses
SablePlatform's existing connection pool** — this module deliberately creates
NO engine of its own (no ``create_engine`` / ``get_sa_engine`` calls). The
caller (CLI command, listener loop, poller) owns connection lifecycle, exactly
like ``db/audit.py`` / ``db/cost.py``.

This is the strict layering boundary required by MEGAPLAN C2.1: relay handlers
(C2.2+) call these helpers and never embed raw SQL. All SQL lives here, behind
named, typed functions, parameterized via SQLAlchemy ``text()`` bind params.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _utc_now_iso() -> str:
    """UTC ISO-8601 ``...Z`` timestamp, matching the relay TEXT timestamp columns.

    Computed in Python and bound as a parameter (not ``strftime('now')``) so the
    lifecycle SQL is dialect-agnostic — Postgres has no ``strftime``.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# The relay member roles that exist (mirrors the 057 CHECK on
# relay_member_roles.role). Kept here so callers can validate role names
# against the same set the schema enforces without reaching into schema.py.
RELAY_MEMBER_ROLES = ("sable_operator", "client_team", "admin")


# ------------------------------------------------------------------
# X-handle resolution
# ------------------------------------------------------------------
def resolve_x_handle(conn: Connection, org_id: str) -> str | None:
    """Resolve the X handle the relay should treat as the org's source account.

    Per SableRelay/PLAN §6, ``relay_clients`` may carry an
    ``x_handle_override``; otherwise the canonical handle is ``orgs.twitter_handle``
    (Relay does not duplicate org identity — PLAN §7). This is the
    ``COALESCE(relay_clients.x_handle_override, orgs.twitter_handle)`` resolver.

    Returns the resolved handle, or ``None`` if the org has neither an override
    nor a configured ``orgs.twitter_handle`` (or no relay_clients row exists).
    """
    row = conn.execute(
        text(
            "SELECT COALESCE(rc.x_handle_override, o.twitter_handle) AS x_handle "
            "FROM relay_clients rc "
            "JOIN orgs o ON o.org_id = rc.org_id "
            "WHERE rc.org_id = :org_id"
        ),
        {"org_id": org_id},
    ).fetchone()
    if row is None:
        return None
    return row[0]


# ------------------------------------------------------------------
# Role gating
# ------------------------------------------------------------------
def list_member_roles(conn: Connection, member_id: int, org_id: str) -> list[str]:
    """Return the relay roles a member holds for a given org (may be empty)."""
    rows = conn.execute(
        text(
            "SELECT role FROM relay_member_roles "
            "WHERE member_id = :member_id AND org_id = :org_id "
            "ORDER BY role"
        ),
        {"member_id": member_id, "org_id": org_id},
    ).fetchall()
    return [r[0] for r in rows]


def member_has_role(
    conn: Connection,
    member_id: int,
    org_id: str,
    role: str,
) -> bool:
    """Role-gating helper over ``relay_member_roles``.

    Returns True iff ``member_id`` holds ``role`` for ``org_id``. ``admin``
    implicitly satisfies any role check (admin can bind chats, register
    operators, override any setting — PLAN §8). An unknown ``role`` argument
    raises ``ValueError`` so a typo can never silently grant/deny access.
    """
    if role not in RELAY_MEMBER_ROLES:
        raise ValueError(
            f"unknown relay role {role!r}; expected one of {RELAY_MEMBER_ROLES}"
        )
    held = set(list_member_roles(conn, member_id, org_id))
    if "admin" in held:
        return True
    return role in held


def is_relay_operator(conn: Connection, member_id: int, org_id: str) -> bool:
    """True iff the member may act as a Sable operator for the org.

    Either an explicit ``sable_operator`` grant or ``admin`` (which subsumes
    operator capability). This is the gate the quorum/amplify handlers (C2.3a)
    apply before recording an operator action.
    """
    return member_has_role(conn, member_id, org_id, "sable_operator")


# ------------------------------------------------------------------
# Client lookups (read helpers reused by listener / poller / CLI)
# ------------------------------------------------------------------
def get_relay_client(conn: Connection, org_id: str) -> dict | None:
    """Return the ``relay_clients`` row for an org as a dict, or None."""
    row = conn.execute(
        text(
            "SELECT org_id, enabled, x_handle_override, polling_interval_seconds, "
            "last_polled_at, last_seen_x_id, last_error, config, created_at "
            "FROM relay_clients WHERE org_id = :org_id"
        ),
        {"org_id": org_id},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def list_enabled_clients(conn: Connection) -> list[str]:
    """Return org_ids of relay clients with ``enabled = 1`` (the poller's set)."""
    rows = conn.execute(
        text("SELECT org_id FROM relay_clients WHERE enabled = 1 ORDER BY org_id")
    ).fetchall()
    return [r[0] for r in rows]


# ------------------------------------------------------------------
# Member identity resolution
# ------------------------------------------------------------------
def resolve_member_id(
    conn: Connection,
    platform: str,
    external_user_id: str,
) -> int | None:
    """Resolve a ``relay_members.id`` from a (platform, external_user_id) identity.

    Returns None if the external identity is not linked to any member yet
    (auto-creation of an identity is a handler concern, not a read helper).
    """
    row = conn.execute(
        text(
            "SELECT member_id FROM relay_member_identities "
            "WHERE platform = :platform AND external_user_id = :external_user_id"
        ),
        {"platform": platform, "external_user_id": external_user_id},
    ).fetchone()
    if row is None:
        return None
    return row[0]


# ------------------------------------------------------------------
# §15.3 chat-binding lifecycle SQL (dialect-agnostic; called by binding.py
# inside one BEGIN IMMEDIATE — see relay/bot/binding.py + relay/bot/txn.py)
#
# These embed NO SQLite-only constructs: named ``:param`` bind params (not ``?``),
# a Python-computed ISO-Z timestamp bound as a param (not ``strftime``), and
# ``result.rowcount`` (not ``changes()``) — so they run unchanged on the live
# Postgres pool on the VPS. This keeps ALL relay SQL behind named db.py helpers
# (the C2.1 §5.3 layering boundary); handlers never embed raw SQL.
# ------------------------------------------------------------------
def select_active_binding(
    conn: Connection, platform: str, chat_id: str
) -> tuple | None:
    """Return ``(org_id, role)`` for the active binding at ``(platform, chat_id)``.

    ``None`` if there is no active binding (the idempotent no-op signal for the
    migrate/kick handlers).
    """
    return conn.execute(
        text(
            "SELECT org_id, role FROM relay_chat_bindings "
            "WHERE platform = :platform AND chat_id = :chat_id AND status = 'active'"
        ),
        {"platform": platform, "chat_id": chat_id},
    ).fetchone()


def mark_binding_migrated(
    conn: Connection, old_chat_id: str, new_chat_id: str
) -> None:
    """Flip the active telegram binding at ``old_chat_id`` to ``migrated``."""
    conn.execute(
        text(
            "UPDATE relay_chat_bindings "
            "SET status = 'migrated', superseded_by_chat_id = :new_chat_id, "
            "    last_seen_at = :now "
            "WHERE platform = 'telegram' AND chat_id = :old_chat_id "
            "  AND status = 'active'"
        ),
        {"new_chat_id": new_chat_id, "old_chat_id": old_chat_id, "now": _utc_now_iso()},
    )


def clone_active_binding(
    conn: Connection, org_id: str, new_chat_id: str, role: str
) -> None:
    """Insert a fresh ``active`` telegram binding at ``new_chat_id`` (same org/role)."""
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:org_id, 'telegram', :new_chat_id, :role, 'active')"
        ),
        {"org_id": org_id, "new_chat_id": new_chat_id, "role": role},
    )


def repoint_submissions_chat_id(
    conn: Connection, old_chat_id: str, new_chat_id: str
) -> int:
    """Re-point in-flight submissions' ``source_chat_id`` to the new chat id.

    Returns the number of submissions re-pointed (``result.rowcount``).
    """
    result = conn.execute(
        text(
            "UPDATE relay_submissions SET source_chat_id = :new_chat_id "
            "WHERE source_chat_id = :old_chat_id "
            "  AND status IN ('pending', 'ready_to_publish')"
        ),
        {"new_chat_id": new_chat_id, "old_chat_id": old_chat_id},
    )
    return int(result.rowcount or 0)


def flip_binding_kicked(
    conn: Connection, platform: str, chat_id: str, last_error: str
) -> None:
    """Flip the active binding at ``(platform, chat_id)`` to ``kicked``."""
    conn.execute(
        text(
            "UPDATE relay_chat_bindings "
            "SET status = 'kicked', last_error = :last_error, last_seen_at = :now "
            "WHERE platform = :platform AND chat_id = :chat_id AND status = 'active'"
        ),
        {
            "last_error": last_error,
            "now": _utc_now_iso(),
            "platform": platform,
            "chat_id": chat_id,
        },
    )


def expire_pending_submissions(conn: Connection, chat_id: str) -> int:
    """Expire this chat's ``pending`` submissions. Returns the count expired."""
    result = conn.execute(
        text(
            "UPDATE relay_submissions "
            "SET status = 'expired', resolved_at = :now "
            "WHERE source_chat_id = :chat_id AND status = 'pending'"
        ),
        {"now": _utc_now_iso(), "chat_id": chat_id},
    )
    return int(result.rowcount or 0)


def kill_inflight_jobs(conn: Connection, platform: str, chat_id: str) -> int:
    """Kill in-flight publication jobs for a kicked destination chat.

    Flips ``pending`` / ``retry`` / ``claimed`` jobs to ``dead`` (the only
    CHECK-allowed halted state in the LOCKED §3.1 set). Returns the count killed.
    """
    result = conn.execute(
        text(
            "UPDATE relay_publication_jobs "
            "SET state = 'dead', last_error = 'destination chat kicked the bot' "
            "WHERE destination_platform = :platform AND destination_chat_id = :chat_id "
            "  AND state IN ('pending', 'retry', 'claimed')"
        ),
        {"platform": platform, "chat_id": chat_id},
    )
    return int(result.rowcount or 0)


def read_client_config(conn: Connection, org_id: str) -> str | None:
    """Return the raw ``relay_clients.config`` JSON string for ``org_id`` (or None)."""
    row = conn.execute(
        text("SELECT config FROM relay_clients WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).fetchone()
    if row is None:
        return None
    return row[0]


# ------------------------------------------------------------------
# Operator-chat provisioning (C2.7 HITL surface)
#
# The per-client operator chat is the active ``relay_chat_bindings`` row with
# ``role='operator'`` for an org (one per org+platform, enforced by the partial
# unique index ``relay_chat_bindings_unique_role`` WHERE status='active'). AutoCM
# (C3.5b review queue) posts review-queue messages here with inline buttons; the
# inline-button callbacks route back to AutoCM via C2.7's callback router.
# ------------------------------------------------------------------
def get_operator_chat(
    conn: Connection, org_id: str, platform: str = "telegram"
) -> str | None:
    """Return the active operator-chat ``chat_id`` for an org (or None).

    Resolves the ``relay_chat_bindings`` row with ``role='operator'`` and
    ``status='active'`` for ``(org_id, platform)``. ``None`` means the operator
    chat has not been provisioned yet — the caller should provision it (or, for
    AutoCM, surface a config error rather than silently dropping the HITL queue).
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram' or 'discord'"
        )
    row = conn.execute(
        text(
            "SELECT chat_id FROM relay_chat_bindings "
            "WHERE org_id = :org_id AND platform = :platform "
            "  AND role = 'operator' AND status = 'active'"
        ),
        {"org_id": org_id, "platform": platform},
    ).fetchone()
    if row is None:
        return None
    return row[0]


def provision_operator_chat(
    conn: Connection,
    org_id: str,
    chat_id: str,
    *,
    platform: str = "telegram",
    title: str | None = None,
) -> str:
    """Provision (idempotently) the per-client operator chat for an org.

    Ensures both (1) a ``relay_chats`` chat-id surface row and (2) an active
    ``relay_chat_bindings`` row with ``role='operator'`` exist for
    ``(org_id, platform, chat_id)``. Returns the operator ``chat_id``.

    Idempotent: re-provisioning the SAME chat_id is a no-op that still returns
    it. Provisioning a DIFFERENT operator chat for an org that already has one
    re-points the binding (the old operator binding is flipped to ``disabled``
    and a fresh ``active`` one inserted) so the partial unique index
    ``relay_chat_bindings_unique_role`` (one active operator binding per
    org+platform) is never violated.

    **No external API call here** — provisioning is a pure DB operation. The
    caller (CLI / deploy step) runs this inside one ``BEGIN IMMEDIATE`` so the
    chat-surface insert + binding flip + binding insert are atomic.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram' or 'discord'"
        )
    # (1) Chat-id surface row (relay_chats) — idempotent on (platform, chat_id).
    upsert_chat(conn, org_id, chat_id, platform=platform, title=title)

    # (2) Operator binding. If an active operator binding already points at this
    # exact chat_id, nothing to do. Otherwise disable any existing active
    # operator binding for this org+platform and insert the new one.
    existing = conn.execute(
        text(
            "SELECT chat_id FROM relay_chat_bindings "
            "WHERE org_id = :org_id AND platform = :platform "
            "  AND role = 'operator' AND status = 'active'"
        ),
        {"org_id": org_id, "platform": platform},
    ).fetchone()
    if existing is not None and existing[0] == chat_id:
        return chat_id
    if existing is not None:
        conn.execute(
            text(
                "UPDATE relay_chat_bindings SET status = 'disabled', last_seen_at = :now "
                "WHERE org_id = :org_id AND platform = :platform "
                "  AND role = 'operator' AND status = 'active'"
            ),
            {"now": _utc_now_iso(), "org_id": org_id, "platform": platform},
        )
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:org_id, :platform, :chat_id, 'operator', 'active')"
        ),
        {"org_id": org_id, "platform": platform, "chat_id": chat_id},
    )
    return chat_id


def upsert_chat(
    conn: Connection,
    org_id: str,
    chat_id: str,
    *,
    platform: str = "telegram",
    title: str | None = None,
) -> int:
    """Ensure a ``relay_chats`` chat-id surface row exists; return its ``id``.

    Idempotent on the ``relay_chats_unique`` (platform, chat_id) index — a
    repeated call returns the existing row id without inserting. ``relay_chats``
    is the FK target for ``relay_messages.chat_id`` and
    ``autocm_drafts.source_chat_id`` (C1.1 AutoCM→Relay FK reconciliation).
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram' or 'discord'"
        )
    row = conn.execute(
        text(
            "SELECT id FROM relay_chats WHERE platform = :platform AND chat_id = :chat_id"
        ),
        {"platform": platform, "chat_id": chat_id},
    ).fetchone()
    if row is not None:
        return int(row[0])
    row = conn.execute(
        text(
            "INSERT INTO relay_chats (org_id, platform, chat_id, title) "
            "VALUES (:org_id, :platform, :chat_id, :title) "
            "RETURNING id"
        ),
        {"org_id": org_id, "platform": platform, "chat_id": chat_id, "title": title},
    ).fetchone()
    return int(row[0])


# ------------------------------------------------------------------
# Inbound-message persistence (C1.1 relay_messages corpus; C2.7 dispatch)
#
# Every inbound TG/Discord message the listener routes is persisted PER MESSAGE
# into relay_messages (the digest volume + member-activity corpus, C3.7), and
# the persisted row id is the FK target for autocm_drafts.source_message_id
# (C3.0). The registry (C2.7) calls this inside the same BEGIN IMMEDIATE that
# claims the dedupe row, BEFORE invoking the in-process AutoCM consumer.
# ------------------------------------------------------------------
def persist_inbound_message(
    conn: Connection,
    *,
    org_id: str,
    chat_row_id: int,
    platform: str,
    external_message_id: str,
    external_user_id: str | None = None,
    member_id: int | None = None,
    text_body: str | None = None,
    reply_to_external_message_id: str | None = None,
    with_inserted_flag: bool = False,
) -> int | tuple[int, bool]:
    """Persist one inbound message into ``relay_messages``; return its row id.

    Idempotent on the ``relay_messages_unique`` (platform, chat_id,
    external_message_id) index — a redelivered message returns the EXISTING row
    id rather than inserting a duplicate (the dedupe gate normally prevents this,
    but the unique index is the durable backstop). ``chat_row_id`` is the
    ``relay_chats.id`` (NOT the external chat id) — resolve it via
    :func:`upsert_chat` first.

    By default returns just the ``int`` row id. With ``with_inserted_flag=True``
    returns ``(row_id, inserted)`` where ``inserted`` is ``True`` iff a NEW row
    was written (``False`` if the existing row was returned). The registry uses
    this to enforce exactly-once-per-message consumer dispatch: a redelivered
    message that arrives under a DIFFERENT ``update_id`` (an edited TG message
    carries a new ``update_id`` for the same ``message_id``; a long-poll offset
    reset can re-deliver) passes the ``(platform, update_id)`` dedupe gate but
    must NOT re-run the AutoCM pipeline on a message that already has a row.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram' or 'discord'"
        )
    existing = conn.execute(
        text(
            "SELECT id FROM relay_messages "
            "WHERE platform = :platform AND chat_id = :chat_id "
            "  AND external_message_id = :emi"
        ),
        {"platform": platform, "chat_id": chat_row_id, "emi": external_message_id},
    ).fetchone()
    if existing is not None:
        return (int(existing[0]), False) if with_inserted_flag else int(existing[0])
    row = conn.execute(
        text(
            "INSERT INTO relay_messages "
            "(org_id, chat_id, member_id, platform, external_message_id, "
            " external_user_id, text, reply_to_external_message_id) "
            "VALUES (:org_id, :chat_id, :member_id, :platform, :emi, "
            "        :euid, :text, :reply_to) "
            "RETURNING id"
        ),
        {
            "org_id": org_id,
            "chat_id": chat_row_id,
            "member_id": member_id,
            "platform": platform,
            "emi": external_message_id,
            "euid": external_user_id,
            "text": text_body,
            "reply_to": reply_to_external_message_id,
        },
    ).fetchone()
    return (int(row[0]), True) if with_inserted_flag else int(row[0])


# ------------------------------------------------------------------
# C2.4 feed: tweet hydration cache (relay_tweets upsert)
#
# §15.1: the hydrated SocialData payload is upserted into relay_tweets and the
# hydrated x_id is the canonical id (never the URL). The poller (Flow A) and the
# submission/flag-reply path both land their tweets here. Idempotent on the
# ``relay_tweets.x_id`` UNIQUE constraint — a re-hydrate refreshes the cached
# text/media/raw without inserting a duplicate row.
# ------------------------------------------------------------------
def upsert_tweet(
    conn: Connection,
    *,
    x_id: str,
    x_author_handle: str,
    x_author_id: str | None = None,
    text_body: str | None = None,
    media_urls_json: str = "[]",
    is_reply: bool = False,
    in_reply_to_x_id: str | None = None,
    conversation_x_id: str | None = None,
    raw_json: str | None = None,
) -> int:
    """Upsert a hydrated tweet into ``relay_tweets``; return its row ``id``.

    Idempotent on the ``x_id`` UNIQUE index: a repeat call UPDATEs the cached
    fields (text/media/raw refresh on re-hydrate) and returns the existing row
    id. ``x_id`` is the hydrated canonical id (§15.1 — NEVER the URL).
    """
    existing = conn.execute(
        text("SELECT id FROM relay_tweets WHERE x_id = :x_id"),
        {"x_id": str(x_id)},
    ).fetchone()
    params = {
        "x_id": str(x_id),
        "x_author_handle": x_author_handle,
        "x_author_id": x_author_id,
        "text": text_body,
        "media_urls": media_urls_json,
        "is_reply": 1 if is_reply else 0,
        "in_reply_to_x_id": in_reply_to_x_id,
        "conversation_x_id": conversation_x_id,
        "raw": raw_json,
    }
    if existing is not None:
        conn.execute(
            text(
                "UPDATE relay_tweets SET "
                "  x_author_handle = :x_author_handle, "
                "  x_author_id = :x_author_id, "
                "  text = :text, "
                "  media_urls = :media_urls, "
                "  is_reply = :is_reply, "
                "  in_reply_to_x_id = :in_reply_to_x_id, "
                "  conversation_x_id = :conversation_x_id, "
                "  fetched_at = :now, "
                "  raw = :raw "
                "WHERE x_id = :x_id"
            ),
            {**params, "now": _utc_now_iso()},
        )
        return int(existing[0])
    row = conn.execute(
        text(
            "INSERT INTO relay_tweets "
            "(x_id, x_author_id, x_author_handle, text, media_urls, is_reply, "
            " in_reply_to_x_id, conversation_x_id, raw) "
            "VALUES (:x_id, :x_author_id, :x_author_handle, :text, :media_urls, "
            "        :is_reply, :in_reply_to_x_id, :conversation_x_id, :raw) "
            "RETURNING id"
        ),
        params,
    ).fetchone()
    return int(row[0])


def get_tweet_by_x_id(conn: Connection, x_id: str) -> dict | None:
    """Return the ``relay_tweets`` row for a hydrated ``x_id`` as a dict, or None."""
    row = conn.execute(
        text(
            "SELECT id, x_id, x_author_id, x_author_handle, text, media_urls, "
            "       is_reply, in_reply_to_x_id, conversation_x_id, fetched_at, raw "
            "FROM relay_tweets WHERE x_id = :x_id"
        ),
        {"x_id": str(x_id)},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def get_tweet_by_row_id(conn: Connection, tweet_row_id: int) -> dict | None:
    """Return the ``relay_tweets`` row for a ``relay_tweets.id`` as a dict, or None.

    ``relay_publication_jobs.tweet_id`` is the ``relay_tweets.id`` (NOT the X id),
    so the publisher resolves the tweet payload it sends via this helper.
    """
    row = conn.execute(
        text(
            "SELECT id, x_id, x_author_id, x_author_handle, text, media_urls, "
            "       is_reply, in_reply_to_x_id, conversation_x_id, fetched_at, raw "
            "FROM relay_tweets WHERE id = :id"
        ),
        {"id": int(tweet_row_id)},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


# ------------------------------------------------------------------
# C2.4 feed: poller cursor (relay_clients.last_polled_at / last_seen_x_id)
# ------------------------------------------------------------------
def list_enabled_clients_for_poll(conn: Connection) -> list[dict]:
    """Return the poller's per-enabled-client rows (Flow A working set).

    Each dict carries the columns the poller needs without cracking JSON every
    tick: ``org_id``, ``polling_interval_seconds``, ``last_polled_at``,
    ``last_seen_x_id`` (the ``since_id`` cursor), and ``config`` (cracked only if
    a per-org override is needed). Ordered by ``org_id`` for deterministic loop
    order (the budget-gated skip test asserts a stable two-org pass).
    """
    rows = conn.execute(
        text(
            "SELECT org_id, polling_interval_seconds, last_polled_at, "
            "       last_seen_x_id, config "
            "FROM relay_clients WHERE enabled = 1 ORDER BY org_id"
        )
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def list_active_destination_bindings(conn: Connection, org_id: str) -> list[dict]:
    """Return the org's active broadcast/community destination bindings (Flow A).

    Flow A fans an auto-broadcast out to every active ``broadcast`` /
    ``community`` binding (PLAN §3.1 line 190: "each active broadcast/community
    binding in (discord, telegram)"). Operator/shared chats are NOT broadcast
    destinations. Returns ``{platform, chat_id, role}`` dicts.
    """
    rows = conn.execute(
        text(
            "SELECT platform, chat_id, role FROM relay_chat_bindings "
            "WHERE org_id = :org_id AND status = 'active' "
            "  AND role IN ('broadcast','community') "
            "ORDER BY platform, chat_id"
        ),
        {"org_id": org_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def update_poll_cursor(
    conn: Connection,
    org_id: str,
    *,
    last_seen_x_id: str | None = None,
    last_error: str | None = None,
) -> None:
    """Stamp ``last_polled_at`` (now) and optionally advance ``last_seen_x_id``.

    ``last_seen_x_id`` is the Flow A ``since_id`` cursor — advanced to the newest
    tweet id seen this poll so the next tick dedupes. ``last_error`` records the
    last poll error (cleared to NULL on a clean poll by passing ``None``).
    """
    conn.execute(
        text(
            "UPDATE relay_clients SET "
            "  last_polled_at = :now, "
            "  last_seen_x_id = COALESCE(:last_seen_x_id, last_seen_x_id), "
            "  last_error = :last_error "
            "WHERE org_id = :org_id"
        ),
        {
            "now": _utc_now_iso(),
            "last_seen_x_id": last_seen_x_id,
            "last_error": last_error,
            "org_id": org_id,
        },
    )


# ------------------------------------------------------------------
# C2.4 feed: outbox publisher (claim → send OUTSIDE txn → record → done)
#
# The §3.1 publish-exactly-once state machine. ``claim_due_job`` and the
# state-transition helpers each run inside ONE immediate_txn driven by the
# publisher; the external send happens BETWEEN them, never inside a txn.
# ------------------------------------------------------------------
def enqueue_publication_job(
    conn: Connection,
    *,
    org_id: str,
    tweet_id: int,
    destination_platform: str,
    destination_chat_id: str,
    submission_id: int | None = None,
) -> int | None:
    """Insert a ``pending`` publication job (idempotent on the dedupe index).

    The partial unique index ``relay_publication_jobs_dedupe`` over
    ``(org_id, tweet_id, destination_platform, destination_chat_id)`` WHERE state
    IN ('pending','claimed','done') means a second enqueue for an already
    in-flight/published (org, tweet, dest) is a no-op. Returns the new row id, or
    ``None`` if a live duplicate already exists (the enqueue was skipped).
    """
    if destination_platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown destination platform {destination_platform!r}; "
            "expected 'telegram' or 'discord'"
        )
    existing = conn.execute(
        text(
            "SELECT id FROM relay_publication_jobs "
            "WHERE org_id = :org_id AND tweet_id = :tweet_id "
            "  AND destination_platform = :dp AND destination_chat_id = :dc "
            "  AND state IN ('pending','claimed','done')"
        ),
        {
            "org_id": org_id,
            "tweet_id": tweet_id,
            "dp": destination_platform,
            "dc": destination_chat_id,
        },
    ).fetchone()
    if existing is not None:
        return None
    row = conn.execute(
        text(
            "INSERT INTO relay_publication_jobs "
            "(org_id, submission_id, tweet_id, destination_platform, "
            " destination_chat_id, state, next_attempt_at) "
            "VALUES (:org_id, :submission_id, :tweet_id, :dp, :dc, 'pending', :now) "
            "RETURNING id"
        ),
        {
            "org_id": org_id,
            "submission_id": submission_id,
            "tweet_id": tweet_id,
            "dp": destination_platform,
            "dc": destination_chat_id,
            "now": _utc_now_iso(),
        },
    ).fetchone()
    return int(row[0])


def claim_due_job(conn: Connection, worker: str) -> dict | None:
    """Atomically claim ONE due publication job (§3.1 publisher claim).

    Selects the oldest ``pending`` OR ``retry`` job whose ``next_attempt_at`` is
    due (``<=`` now), flips it to ``claimed`` with ``claimed_by``/``claimed_at``,
    and returns it as a dict (or ``None`` when nothing is due). MUST be called
    inside an ``immediate_txn`` so the SELECT+UPDATE is atomic against other
    workers (SQLite serializes writers; Postgres runs SERIALIZABLE).

    Returns the full job dict so the caller can send WITHOUT re-reading the row.
    """
    now = _utc_now_iso()
    candidate = conn.execute(
        text(
            "SELECT id FROM relay_publication_jobs "
            "WHERE state IN ('pending','retry') AND next_attempt_at <= :now "
            "ORDER BY next_attempt_at, id LIMIT 1"
        ),
        {"now": now},
    ).fetchone()
    if candidate is None:
        return None
    job_id = int(candidate[0])
    conn.execute(
        text(
            "UPDATE relay_publication_jobs SET "
            "  state = 'claimed', claimed_by = :worker, claimed_at = :now "
            "WHERE id = :id"
        ),
        {"worker": worker, "now": now, "id": job_id},
    )
    row = conn.execute(
        text(
            "SELECT id, org_id, submission_id, tweet_id, destination_platform, "
            "       destination_chat_id, state, attempts, claimed_by, claimed_at, "
            "       next_attempt_at, last_error, created_at "
            "FROM relay_publication_jobs WHERE id = :id"
        ),
        {"id": job_id},
    ).fetchone()
    return dict(row._mapping)


def mark_job_done(conn: Connection, job_id: int) -> None:
    """Flip a claimed job to ``done`` (terminal success).

    State-guarded (``AND state = 'claimed'``) to mirror :func:`reset_stuck_claim`
    and :func:`reconcile_claim_done`. The publisher always reaches this from a
    ``claimed`` job it just claimed, so the guard is a no-op on the happy path; it
    only suppresses the dead→done resurrection in the kick-race (Publisher A
    claims+sends+stalls, the binding is kicked → ``kill_inflight_jobs`` flips A's
    job to ``dead``, A resumes into step-3). The message WAS sent before the kick
    (DB-exactly-once is unaffected — ``record_publication``'s ON CONFLICT collapse
    still holds), but the guard preserves the ``dead`` audit/halt state the kick
    handler set instead of overwriting it.
    """
    conn.execute(
        text(
            "UPDATE relay_publication_jobs SET state = 'done' "
            "WHERE id = :id AND state = 'claimed'"
        ),
        {"id": job_id},
    )


def mark_job_retry(
    conn: Connection,
    job_id: int,
    *,
    retry_after_seconds: float,
    last_error: str,
    now: datetime | None = None,
) -> None:
    """Flip a job to ``retry`` with backoff (§3.1 ratelimit/retryable path).

    Sets ``next_attempt_at = now + retry_after_seconds`` (computed in Python so
    the arithmetic is dialect-agnostic — no ``datetime('now','+N seconds')``),
    increments ``attempts``, and records ``last_error``. ``'retry'`` is in the
    LOCKED §3.1 CHECK set so this write succeeds.
    """
    base = now or datetime.now(timezone.utc)
    next_at = (base + timedelta(seconds=retry_after_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        text(
            "UPDATE relay_publication_jobs SET "
            "  state = 'retry', next_attempt_at = :next_at, "
            "  attempts = attempts + 1, last_error = :last_error "
            "WHERE id = :id"
        ),
        {"next_at": next_at, "last_error": last_error, "id": job_id},
    )


def mark_job_dead(conn: Connection, job_id: int, *, last_error: str) -> None:
    """Flip a job to ``dead`` (terminal failure — fatal or attempts exhausted)."""
    conn.execute(
        text(
            "UPDATE relay_publication_jobs SET state = 'dead', "
            "  attempts = attempts + 1, last_error = :last_error WHERE id = :id"
        ),
        {"last_error": last_error, "id": job_id},
    )


def record_publication(
    conn: Connection,
    *,
    org_id: str,
    tweet_id: int,
    destination_platform: str,
    destination_chat_id: str,
    destination_message_id: str,
    submission_id: int | None = None,
) -> bool:
    """Insert a ``relay_publications`` row ON CONFLICT DO NOTHING (§3.1).

    The unique index ``relay_publications_unique`` over
    ``(org_id, tweet_id, destination_platform, destination_chat_id)`` enforces
    DB-exactly-once: a second insert for the same (org, tweet, dest) is a no-op.
    Returns ``True`` iff a NEW publication row was written (``False`` on
    conflict — reconciliation/retry collapse to the existing row).

    Dialect-agnostic: instead of ``INSERT ... ON CONFLICT`` (whose target syntax
    differs subtly across SQLite/Postgres), we do a guarded existence check
    inside the caller's ``immediate_txn`` — the write lock makes the
    check-then-insert atomic (no second writer can slip a duplicate in).
    """
    existing = conn.execute(
        text(
            "SELECT id FROM relay_publications "
            "WHERE org_id = :org_id AND tweet_id = :tweet_id "
            "  AND destination_platform = :dp AND destination_chat_id = :dc"
        ),
        {
            "org_id": org_id,
            "tweet_id": tweet_id,
            "dp": destination_platform,
            "dc": destination_chat_id,
        },
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        text(
            "INSERT INTO relay_publications "
            "(org_id, submission_id, tweet_id, destination_platform, "
            " destination_chat_id, destination_message_id) "
            "VALUES (:org_id, :submission_id, :tweet_id, :dp, :dc, :dmi)"
        ),
        {
            "org_id": org_id,
            "submission_id": submission_id,
            "tweet_id": tweet_id,
            "dp": destination_platform,
            "dc": destination_chat_id,
            "dmi": destination_message_id,
        },
    )
    return True


def find_publication_by_message(
    conn: Connection,
    *,
    destination_platform: str,
    destination_chat_id: str,
    destination_message_id: str,
) -> dict | None:
    """Find an existing publication by its external message id (reconciliation).

    The §3.2 reconciliation pass calls this with the external_message_id its
    best-effort orphan search found, to decide whether the publication is already
    recorded. ``None`` means no DB record exists for that external message yet.
    """
    row = conn.execute(
        text(
            "SELECT id, org_id, tweet_id FROM relay_publications "
            "WHERE destination_platform = :dp AND destination_chat_id = :dc "
            "  AND destination_message_id = :dmi"
        ),
        {
            "dp": destination_platform,
            "dc": destination_chat_id,
            "dmi": destination_message_id,
        },
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


# ------------------------------------------------------------------
# C2.4 sweeper: stuck-claim reset + reconciliation candidate selection
# ------------------------------------------------------------------
def list_stuck_claims(conn: Connection, *, older_than_seconds: int) -> list[dict]:
    """Return ``claimed`` jobs whose ``claimed_at`` is older than the threshold.

    Used by BOTH the stuck-claim reset (>5min, §3.1) and the reconciliation pass
    (>60s, §3.2). The caller passes the window. Rows with a NULL ``claimed_at``
    are conservatively included (a claim with no timestamp is anomalous and
    should be recovered). Returns the full job dicts so reconciliation can run
    its best-effort external-message search.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        text(
            "SELECT id, org_id, submission_id, tweet_id, destination_platform, "
            "       destination_chat_id, state, attempts, claimed_by, claimed_at, "
            "       next_attempt_at, last_error, created_at "
            "FROM relay_publication_jobs "
            "WHERE state = 'claimed' "
            "  AND (claimed_at IS NULL OR claimed_at <= :cutoff) "
            "ORDER BY id"
        ),
        {"cutoff": cutoff},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def reset_stuck_claim(conn: Connection, job_id: int) -> None:
    """Recycle a stuck ``claimed`` job back to ``retry`` (due now), +1 attempt.

    §3.1 stuck-claim sweeper: a ``claimed`` row older than 5min is treated as an
    orphaned claim (worker crashed) and reset so it gets re-claimed. We reset to
    ``retry`` (not ``pending``) because the claim selector includes both and
    ``retry`` is the semantically-correct "re-attempt" state.
    """
    conn.execute(
        text(
            "UPDATE relay_publication_jobs SET "
            "  state = 'retry', next_attempt_at = :now, claimed_by = NULL, "
            "  claimed_at = NULL, attempts = attempts + 1, "
            "  last_error = 'stuck claim reset by sweeper' "
            "WHERE id = :id AND state = 'claimed'"
        ),
        {"now": _utc_now_iso(), "id": job_id},
    )


def kill_stuck_claim(conn: Connection, job_id: int, *, last_error: str) -> None:
    """Terminate a stuck ``claimed`` job to ``dead`` (recycle attempts exhausted).

    §15.6 "Quorum reached but publish loops failed → after N attempts,
    state=dead, alert admin" — upholds the same attempts→dead contract the
    publisher's own send-failure path enforces, but on the SWEEPER recycle path
    (a worker that repeatedly crashes mid-send is always re-claimed and never
    reaches the publisher's exception handler). Guarded ``AND state = 'claimed'``
    so it only acts on a still-stuck claim. Increments ``attempts`` so the dead
    row's attempt count reflects the final (exhausting) attempt.
    """
    conn.execute(
        text(
            "UPDATE relay_publication_jobs SET state = 'dead', "
            "  attempts = attempts + 1, claimed_by = NULL, claimed_at = NULL, "
            "  last_error = :last_error "
            "WHERE id = :id AND state = 'claimed'"
        ),
        {"last_error": last_error, "id": job_id},
    )


def reconcile_claim_done(
    conn: Connection, job_id: int, *, destination_message_id: str
) -> None:
    """Reconciliation found the orphan external message: record + mark done (§3.2).

    Writes the publication row (ON CONFLICT DO NOTHING via :func:`record_publication`
    is the caller's responsibility; here we only flip the job) and flips the job
    to ``done``. Caller runs both inside one ``immediate_txn``.
    """
    conn.execute(
        text(
            "UPDATE relay_publication_jobs SET state = 'done' "
            "WHERE id = :id AND state = 'claimed'"
        ),
        {"id": job_id},
    )


# ------------------------------------------------------------------
# C2.4 sweeper: submission expiry (pending past expires_at)
# ------------------------------------------------------------------
def expire_overdue_submissions(conn: Connection) -> int:
    """Expire ``pending`` submissions whose ``expires_at`` has passed.

    Distinct from :func:`expire_pending_submissions` (which expires by chat_id on
    a kick): this is the time-based sweep over ALL orgs' pending submissions
    whose quorum window elapsed. Returns the count expired.
    """
    result = conn.execute(
        text(
            "UPDATE relay_submissions SET status = 'expired', resolved_at = :now "
            "WHERE status = 'pending' AND expires_at <= :now"
        ),
        {"now": _utc_now_iso()},
    )
    return int(result.rowcount or 0)


def reject_submission(conn: Connection, submission_id: int, *, reason: str) -> bool:
    """Mark a submission ``rejected`` (§15.1 tweet-deleted-before-publish).

    Guarded: only transitions a submission still in ``pending`` /
    ``ready_to_publish`` (a terminal submission is left alone). ``reason`` is
    appended to ``note`` so the source-chat notifier can echo the precise reason.
    Returns ``True`` iff a row transitioned.
    """
    result = conn.execute(
        text(
            "UPDATE relay_submissions SET "
            "  status = 'rejected', resolved_at = :now, "
            "  note = COALESCE(note || ' | ', '') || :reason "
            "WHERE id = :id AND status IN ('pending','ready_to_publish')"
        ),
        {"now": _utc_now_iso(), "reason": f"rejected: {reason}", "id": submission_id},
    )
    return int(result.rowcount or 0) > 0


def get_submission(conn: Connection, submission_id: int) -> dict | None:
    """Return a submission row as a dict (for the §15.6 source-chat notifier), or None.

    The publish-path rejection branch reads ``source_chat_id`` /
    ``source_message_id`` so it can echo the precise rejection reason to the
    submitter in the source chat (§15.6 "notify submitter in source chat").
    """
    row = conn.execute(
        text(
            "SELECT id, org_id, tweet_id, submitter_id, source_chat_id, "
            "       source_message_id, control_message_id, source_role, status "
            "FROM relay_submissions WHERE id = :id"
        ),
        {"id": int(submission_id)},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


# ------------------------------------------------------------------
# C2.4 retention GC (SableRelay §15.5 — all five windows owned here)
# ------------------------------------------------------------------
def gc_processed_updates(conn: Connection, *, older_than_days: int = 7) -> int:
    """GC ``relay_processed_updates`` rows older than N days post-``processed_at``.

    §15.5 / Open-Q #7: retain 7 days. Uses the ``relay_processed_updates_gc``
    index. Returns the count deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=older_than_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = conn.execute(
        text("DELETE FROM relay_processed_updates WHERE processed_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


def gc_publication_jobs(conn: Connection, *, older_than_days: int = 30) -> int:
    """GC terminal (``done``/``dead``) publication jobs older than N days (§15.5).

    Retain 30 days post-terminal-state. We GC by ``created_at`` (no
    ``terminal_at`` column exists; ``created_at`` is the conservative anchor — a
    job created >30d ago that is terminal is safely past the window). Live states
    (``pending``/``claimed``/``retry``) are NEVER GC'd. Returns the count deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=older_than_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = conn.execute(
        text(
            "DELETE FROM relay_publication_jobs "
            "WHERE state IN ('done','dead') AND created_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


def gc_reply_notifications(conn: Connection, *, older_than_days: int = 90) -> int:
    """GC ``relay_reply_notifications`` rows older than N days (§15.5, retain 90d).

    The member-facing inbox is bounded to 90 days; aggregate stats live
    elsewhere. GC by ``notified_at``. Returns the count deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=older_than_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = conn.execute(
        text("DELETE FROM relay_reply_notifications WHERE notified_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


def gc_tweets_raw_payload(conn: Connection, *, older_than_days: int = 30) -> int:
    """Null out ``relay_tweets.raw`` older than N days (§15.5, raw TTL 30d).

    Tweet text + media URLs are retained indefinitely (cache); only the bulky
    ``raw`` payload is TTL'd to limit footprint — so this is an UPDATE that nulls
    ``raw`` (NOT a row delete; deleting would orphan submissions/publications
    that FK to the tweet). GC by ``fetched_at``. Returns the count of rows whose
    ``raw`` was cleared (only rows that still had a non-NULL ``raw``).
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=older_than_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = conn.execute(
        text(
            "UPDATE relay_tweets SET raw = NULL "
            "WHERE raw IS NOT NULL AND fetched_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


def gc_messages(conn: Connection, *, older_than_days: int = 90) -> int:
    """GC ``relay_messages`` rows older than N days (§15.5 bounded window).

    §5.2 reversed the "never persist inbound" posture: relay now keeps a minimal
    inbound corpus, GC'd on a bounded window (the 90-day member-analytics window
    pinned in §15.5 for ``relay_messages``). GC by ``received_at`` via the
    ``relay_messages_gc`` index. Returns the count deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=older_than_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = conn.execute(
        text("DELETE FROM relay_messages WHERE received_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


def gc_orphan_chats(conn: Connection, *, messages_older_than_days: int = 90) -> int:
    """GC orphan ``relay_chats`` rows (§15.5 line 865 — owned by the C2.4 sweeper).

    A ``relay_chats`` row is reclaimed ONLY once it is fully unreferenced:
      * no ``relay_chat_bindings`` row references the chat (by platform + chat_id —
        bindings carry the external chat id, not ``relay_chats.id``), AND
      * no ``relay_messages`` row inside the 90-day messages window points at it
        (``relay_messages.chat_id`` FKs ``relay_chats.id``).

    Must run AFTER :func:`gc_messages` in the same pass so a chat whose last
    in-window message was just swept becomes eligible immediately. The
    ``messages_older_than_days`` cutoff mirrors the messages window so a chat is
    only an orphan when no message NEWER than the cutoff references it (older
    messages are already gone). Returns the count deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=messages_older_than_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = conn.execute(
        text(
            "DELETE FROM relay_chats WHERE id IN ("
            "  SELECT c.id FROM relay_chats c "
            "  WHERE NOT EXISTS ("
            "    SELECT 1 FROM relay_chat_bindings b "
            "    WHERE b.platform = c.platform AND b.chat_id = c.chat_id"
            "  ) AND NOT EXISTS ("
            "    SELECT 1 FROM relay_messages m "
            "    WHERE m.chat_id = c.id AND m.received_at >= :cutoff"
            "  )"
            ")"
        ),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


# ------------------------------------------------------------------
# C2.4 Flow D 4.6 reply follow-through tracking
#
# §10/§3.2/Appendix: poll conversation_id:{tweet_id}, match replies against
# relay_member_identities X user ids, write replied_at + replied_tweet_id.
# ------------------------------------------------------------------
def list_open_reply_notifications(
    conn: Connection, org_id: str, *, within_hours: int = 24
) -> list[dict]:
    """Return reply notifications still awaiting follow-through for an org.

    The 4.6 tracker only polls notifications that are (a) not yet ``replied_at``,
    (b) inside the 24h tracking window (``notified_at`` within ``within_hours``),
    joined to their opportunity to get the source ``tweet_id`` (the
    conversation_id to poll) and the notified member. Returns one dict per
    notification with the X identity to match against.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=within_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        text(
            "SELECT n.id AS notification_id, n.opportunity_id, n.member_id, "
            "       n.notified_at, o.tweet_id AS tweet_row_id, "
            "       t.x_id AS conversation_x_id "
            "FROM relay_reply_notifications n "
            "JOIN relay_reply_opportunities o ON o.id = n.opportunity_id "
            "JOIN relay_tweets t ON t.id = o.tweet_id "
            "WHERE o.org_id = :org_id "
            "  AND n.replied_at IS NULL "
            "  AND n.notified_at >= :cutoff "
            "ORDER BY n.id"
        ),
        {"org_id": org_id, "cutoff": cutoff},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def get_member_x_user_id(conn: Connection, member_id: int) -> str | None:
    """Return a member's linked X ``external_user_id`` (platform='x'), or None.

    4.6 matches replies by stable X user id (NOT handle — handles change, per the
    §10/Appendix note). ``None`` means the member has no linked X identity, so
    their reply cannot be detected.
    """
    row = conn.execute(
        text(
            "SELECT external_user_id FROM relay_member_identities "
            "WHERE member_id = :member_id AND platform = 'x'"
        ),
        {"member_id": member_id},
    ).fetchone()
    if row is None:
        return None
    return row[0]


def mark_reply_followed_through(
    conn: Connection,
    notification_id: int,
    *,
    replied_tweet_id: str,
) -> bool:
    """Record a detected follow-through reply (§4.6 ``replied_at`` write).

    Guarded: only writes if ``replied_at`` is still NULL (idempotent — a repeat
    detection of the same reply is a no-op). Stamps ``replied_at`` = now and
    ``replied_tweet_id`` = the matched reply's x_id. Returns ``True`` iff a row
    transitioned (a NEW follow-through was recorded).
    """
    result = conn.execute(
        text(
            "UPDATE relay_reply_notifications SET "
            "  replied_at = :now, replied_tweet_id = :replied_tweet_id "
            "WHERE id = :id AND replied_at IS NULL"
        ),
        {
            "now": _utc_now_iso(),
            "replied_tweet_id": replied_tweet_id,
            "id": notification_id,
        },
    )
    return int(result.rowcount or 0) > 0
