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
# C2.3a Flow B/C: submission create + quorum vote upsert + guarded transition
#
# These back the amplify (Flow B/C) + quorum (§3.1) handlers. Per the LOCKED
# C2.1 §5.3 layering boundary, the handlers embed NO raw SQL — every statement
# is a named, ``text()``-parameterized helper here. All are dialect-agnostic:
# named ``:param`` binds (not ``?``), a Python ISO-Z timestamp bound as a param
# (not ``strftime``), and ``result.rowcount`` (not ``changes()``) — so the
# guarded transition runs unchanged on the live Postgres pool. The caller drives
# every multi-statement sequence inside ONE ``immediate_txn`` (§3.1).
# ------------------------------------------------------------------
def auto_create_member_identity(
    conn: Connection,
    platform: str,
    external_user_id: str,
    *,
    handle: str | None = None,
) -> int:
    """Resolve OR auto-create the ``relay_members`` row for an external identity.

    §3.1 step 4: when a previously-unknown TG/Discord user reacts/submits, the
    handler auto-creates a ``relay_members`` + ``relay_member_identities`` row
    **for audit only** — auto-creation grants NO roles (authorization is always
    role-gated via ``relay_member_roles``; §8). Idempotent: if the identity
    already exists, its ``relay_members.id`` is returned (and ``handle`` is
    refreshed to the latest seen value — handles are display-only, §15.4). The
    caller MUST be inside an ``immediate_txn`` (this writes).

    Returns the resolved/created ``relay_members.id``.
    """
    if platform not in ("telegram", "discord", "x"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram', 'discord' or 'x'"
        )
    existing = conn.execute(
        text(
            "SELECT member_id FROM relay_member_identities "
            "WHERE platform = :platform AND external_user_id = :euid"
        ),
        {"platform": platform, "euid": str(external_user_id)},
    ).fetchone()
    if existing is not None:
        member_id = int(existing[0])
        # Refresh the display handle on next interaction (never grants perms).
        if handle is not None:
            conn.execute(
                text(
                    "UPDATE relay_member_identities SET handle = :handle "
                    "WHERE platform = :platform AND external_user_id = :euid"
                ),
                {"handle": handle, "platform": platform, "euid": str(external_user_id)},
            )
        return member_id
    row = conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES (:name) RETURNING id"),
        {"name": handle},
    ).fetchone()
    member_id = int(row[0])
    conn.execute(
        text(
            "INSERT INTO relay_member_identities "
            "(member_id, platform, external_user_id, handle, linked_at) "
            "VALUES (:member_id, :platform, :euid, :handle, :now)"
        ),
        {
            "member_id": member_id,
            "platform": platform,
            "euid": str(external_user_id),
            "handle": handle,
            "now": _utc_now_iso(),
        },
    )
    return member_id


def find_open_submission_for_tweet(
    conn: Connection, org_id: str, tweet_id: int
) -> dict | None:
    """Return the org's open (``pending``/``ready_to_publish``) submission for a tweet.

    Backs the §11 #2 one-pending-per-tweet MERGE: a duplicate ``/amplify`` of the
    same tweet resolves to the FIRST open submission rather than minting a second
    (the ``relay_submissions_one_pending_per_tweet`` partial unique index makes a
    second insert fail anyway; this lets the handler merge gracefully). ``None``
    if no open submission exists for ``(org_id, tweet_id)``.
    """
    row = conn.execute(
        text(
            "SELECT id, org_id, tweet_id, submitter_id, source_chat_id, "
            "       source_message_id, control_message_id, source_role, status, note "
            "FROM relay_submissions "
            "WHERE org_id = :org_id AND tweet_id = :tweet_id "
            "  AND status IN ('pending','ready_to_publish') "
            "ORDER BY id LIMIT 1"
        ),
        {"org_id": org_id, "tweet_id": int(tweet_id)},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def create_submission(
    conn: Connection,
    *,
    org_id: str,
    tweet_id: int,
    submitter_id: int,
    source_chat_id: str,
    source_message_id: str,
    source_role: str,
    expires_at: str,
    note: str | None = None,
    status: str = "pending",
    control_message_id: str | None = None,
) -> int:
    """Insert a ``relay_submissions`` row (Flow B pending / Flow C immediate).

    ``source_role`` is ``'operator'`` (Flow B quorum) or ``'shared'`` (Flow C
    immediate) — the CHECK enforces the set. ``status`` is ``'pending'`` for
    Flow B (awaits quorum) and the caller transitions Flow C straight through.
    ``expires_at`` is materialized at insert (the quorum window; §5.2) so the
    sweeper need not crack JSON. Returns the new ``relay_submissions.id``.

    The caller MUST be inside an ``immediate_txn`` and SHOULD first consult
    :func:`find_open_submission_for_tweet` to honor one-pending-per-tweet.
    """
    if source_role not in ("operator", "shared"):
        raise ValueError(
            f"unknown source_role {source_role!r}; expected 'operator' or 'shared'"
        )
    if status not in ("pending", "ready_to_publish"):
        raise ValueError(
            f"create_submission status must be 'pending' or 'ready_to_publish', got {status!r}"
        )
    row = conn.execute(
        text(
            "INSERT INTO relay_submissions "
            "(org_id, tweet_id, submitter_id, source_chat_id, source_message_id, "
            " control_message_id, source_role, note, status, expires_at) "
            "VALUES (:org_id, :tweet_id, :submitter_id, :source_chat_id, "
            "        :source_message_id, :control_message_id, :source_role, :note, "
            "        :status, :expires_at) "
            "RETURNING id"
        ),
        {
            "org_id": org_id,
            "tweet_id": int(tweet_id),
            "submitter_id": int(submitter_id),
            "source_chat_id": str(source_chat_id),
            "source_message_id": str(source_message_id),
            "control_message_id": control_message_id,
            "source_role": source_role,
            "note": note,
            "status": status,
            "expires_at": expires_at,
        },
    ).fetchone()
    return int(row[0])


def set_submission_control_message_id(
    conn: Connection, submission_id: int, control_message_id: str
) -> None:
    """Record the acknowledgment/control message id on a submission.

    Flow B posts a pending-acknowledgment ("📥 needs N more 📢") into the operator
    chat AFTER the submission row exists (the message id is only known once the
    external send returns — OUTSIDE the txn). This back-fills
    ``control_message_id`` so the §3.1 reaction handler can route a
    ``MessageReactionUpdated`` on that message to its submission via
    ``relay_submissions_control_lookup``. Called inside a follow-up
    ``immediate_txn`` after the send.
    """
    conn.execute(
        text(
            "UPDATE relay_submissions SET control_message_id = :cmid WHERE id = :id"
        ),
        {"cmid": str(control_message_id), "id": int(submission_id)},
    )


def find_submission_by_control(
    conn: Connection, source_chat_id: str, control_message_id: str
) -> dict | None:
    """Resolve a submission from a reaction's ``(chat_id, message_id)`` (§3.1 step 2).

    TG ``MessageReactionUpdated`` arrives keyed by ``(chat_id, message_id)``; this
    resolves it to the pending submission whose acknowledgment/control message it
    is, via ``relay_submissions_control_lookup``. ``None`` (the §3.1 step-2 "if
    unknown → COMMIT, return" no-op signal) if there is no matching submission.
    """
    row = conn.execute(
        text(
            "SELECT id, org_id, tweet_id, submitter_id, source_chat_id, "
            "       source_message_id, control_message_id, source_role, status, note "
            "FROM relay_submissions "
            "WHERE source_chat_id = :chat_id AND control_message_id = :cmid "
            "ORDER BY id LIMIT 1"
        ),
        {"chat_id": str(source_chat_id), "cmid": str(control_message_id)},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def upsert_submission_reaction(
    conn: Connection, submission_id: int, member_id: int, emoji: str
) -> None:
    """Record an operator's reaction vote (§3.1 step 6, emoji ADDED).

    Reactions are *current state*, not append-only (§5.2): the row is keyed
    ``(submission_id, member_id, emoji)`` so a repeat add is idempotent.
    ``INSERT OR IGNORE`` semantics via a guarded existence check so this is
    dialect-agnostic (no SQLite-only ``OR IGNORE``). The caller has already
    role-gated the member (§3.1 step 5 — non-operator reactions are NEVER
    recorded here, to keep the audit table clean per §8/§15.4).
    """
    existing = conn.execute(
        text(
            "SELECT 1 FROM relay_submission_reactions "
            "WHERE submission_id = :sid AND member_id = :mid AND emoji = :emoji"
        ),
        {"sid": int(submission_id), "mid": int(member_id), "emoji": emoji},
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        text(
            "INSERT INTO relay_submission_reactions "
            "(submission_id, member_id, emoji, reacted_at) "
            "VALUES (:sid, :mid, :emoji, :now)"
        ),
        {
            "sid": int(submission_id),
            "mid": int(member_id),
            "emoji": emoji,
            "now": _utc_now_iso(),
        },
    )


def delete_submission_reaction(
    conn: Connection, submission_id: int, member_id: int, emoji: str
) -> None:
    """Remove an operator's reaction vote (§3.1 step 6, emoji REMOVED).

    §5.2: removing the configured emoji removes the vote (reactions are current
    state). A no-op if the vote was not present.
    """
    conn.execute(
        text(
            "DELETE FROM relay_submission_reactions "
            "WHERE submission_id = :sid AND member_id = :mid AND emoji = :emoji"
        ),
        {"sid": int(submission_id), "mid": int(member_id), "emoji": emoji},
    )


def count_distinct_quorum_operators(
    conn: Connection, submission_id: int, org_id: str, emoji: str
) -> int:
    """Distinct CURRENT operators who voted the quorum emoji on a submission (§3.1 step 7).

    Counts distinct ``member_id``s that (a) have a ``relay_submission_reactions``
    row with the configured quorum ``emoji`` for this submission AND (b) currently
    hold ``role='sable_operator'`` (or ``admin``, which subsumes operator) for the
    org. Joining to ``relay_member_roles`` at count time means a member who lost
    operator role no longer counts — authorization is role-gated at the moment of
    tally (§8). The submitter, if an operator who reacted, is included (the
    "submitter counts as 1" rationale, §3 / threshold).
    """
    row = conn.execute(
        text(
            "SELECT COUNT(DISTINCT r.member_id) "
            "FROM relay_submission_reactions r "
            "JOIN relay_member_roles mr ON mr.member_id = r.member_id "
            "WHERE r.submission_id = :sid AND r.emoji = :emoji "
            "  AND mr.org_id = :org_id "
            "  AND mr.role IN ('sable_operator','admin')"
        ),
        {"sid": int(submission_id), "emoji": emoji, "org_id": org_id},
    ).fetchone()
    return int(row[0] or 0)


def count_distinct_quorum_operators_excluding(
    conn: Connection,
    submission_id: int,
    org_id: str,
    emoji: str,
    exclude_member_id: int,
) -> int:
    """Distinct quorum operators EXCLUDING one member (the ``min_other_operators`` gate).

    §3 / §135: ``min_other_operators`` (default null) adds a "≥N operators OTHER
    than the submitter" constraint ON TOP of ``quorum_threshold``. This counts the
    same role-gated distinct operators as :func:`count_distinct_quorum_operators`
    but excludes ``exclude_member_id`` (the submitter), so the handler can enforce
    the extra constraint without double-querying.
    """
    row = conn.execute(
        text(
            "SELECT COUNT(DISTINCT r.member_id) "
            "FROM relay_submission_reactions r "
            "JOIN relay_member_roles mr ON mr.member_id = r.member_id "
            "WHERE r.submission_id = :sid AND r.emoji = :emoji "
            "  AND mr.org_id = :org_id "
            "  AND mr.role IN ('sable_operator','admin') "
            "  AND r.member_id <> :exclude"
        ),
        {
            "sid": int(submission_id),
            "emoji": emoji,
            "org_id": org_id,
            "exclude": int(exclude_member_id),
        },
    ).fetchone()
    return int(row[0] or 0)


def transition_submission_ready(conn: Connection, submission_id: int) -> bool:
    """The §3.1 step-8 GUARDED transition: ``pending`` → ``ready_to_publish``.

    ``UPDATE relay_submissions SET status='ready_to_publish', resolved_at=now
    WHERE id=:id AND status='pending'`` — the ``AND status='pending'`` guard is
    the exactly-once primitive: only the FIRST writer transitions; a concurrent
    writer (SQLite serializes writers under the caller's ``BEGIN IMMEDIATE``;
    Postgres runs SERIALIZABLE) sees ``status != 'pending'`` and the UPDATE
    matches zero rows. Returns ``True`` iff THIS call performed the transition
    (``result.rowcount == 1``) — the caller enqueues the fan-out jobs ONLY when
    this returns ``True`` (so the outbox is enqueued exactly once, §3.1 step 8).
    """
    result = conn.execute(
        text(
            "UPDATE relay_submissions SET "
            "  status = 'ready_to_publish', resolved_at = :now "
            "WHERE id = :id AND status = 'pending'"
        ),
        {"now": _utc_now_iso(), "id": int(submission_id)},
    )
    return int(result.rowcount or 0) == 1


def mark_submission_published(conn: Connection, submission_id: int) -> bool:
    """Flip a ``ready_to_publish`` submission to ``published`` (post-fan-out marker).

    Flow C publishes immediately (single approval, no quorum) and marks the
    submission ``published`` once the fan-out jobs are enqueued. Guarded
    (``AND status='ready_to_publish'``) so it only advances a submission the
    caller just transitioned. Returns ``True`` iff a row advanced.
    """
    result = conn.execute(
        text(
            "UPDATE relay_submissions SET status = 'published', resolved_at = :now "
            "WHERE id = :id AND status = 'ready_to_publish'"
        ),
        {"now": _utc_now_iso(), "id": int(submission_id)},
    )
    return int(result.rowcount or 0) > 0


def list_active_publish_bindings(conn: Connection, org_id: str) -> list[dict]:
    """Active broadcast/community destinations for the §3.1 step-8 fan-out.

    The quorum/amplify fan-out enqueues one ``relay_publication_jobs`` row per
    active broadcast/community binding (PLAN §3.1 line 190: "each active
    broadcast/community binding in (discord, telegram)"). This is the same set
    Flow A's poller fans out to — :func:`list_active_destination_bindings` is the
    canonical query; this is a clearly-named alias for the quorum call site so the
    handler reads intent-first. Returns ``{platform, chat_id, role}`` dicts.
    """
    return list_active_destination_bindings(conn, org_id)


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


# ==================================================================
# C2.3b — Flow D v1 reply opportunities (flag_reply handler)
#
# §2 Flow D: an operator runs /flag-reply <url> [note] [target=@handle...] in the
# operator chat → records a relay_reply_opportunity, resolves the target set
# (explicit handle targets, else all opted-in members for the org per §11 #1),
# and inserts one relay_reply_notification per target so the listener can DM each
# one the compose deeplink. No external send happens here — these are pure DB
# writes inside the handler's immediate_txn; the listener does the DM fan-out
# OUTSIDE the txn from the returned target list.
# ==================================================================
def create_reply_opportunity(
    conn: Connection,
    *,
    org_id: str,
    tweet_id: int,
    flagger_id: int,
    origin: str = "explicit_command",
    note: str | None = None,
) -> int:
    """Insert a ``relay_reply_opportunities`` row; return its id.

    ``origin`` is one of the 057 CHECK set ``('explicit_command','reaction',
    'auto_mention')`` — Flow D v1 (``/flag-reply``) is always
    ``'explicit_command'`` (the v1.5 reaction path is DEFERRED, §10 Phase 5).
    The caller MUST be inside an ``immediate_txn`` (this writes).
    """
    if origin not in ("explicit_command", "reaction", "auto_mention"):
        raise ValueError(
            f"unknown reply-opportunity origin {origin!r}; expected one of "
            "('explicit_command','reaction','auto_mention')"
        )
    row = conn.execute(
        text(
            "INSERT INTO relay_reply_opportunities "
            "(org_id, tweet_id, flagger_id, origin, note) "
            "VALUES (:org_id, :tweet_id, :flagger_id, :origin, :note) "
            "RETURNING id"
        ),
        {
            "org_id": org_id,
            "tweet_id": int(tweet_id),
            "flagger_id": int(flagger_id),
            "origin": origin,
            "note": note,
        },
    ).fetchone()
    return int(row[0])


def list_optedin_members(conn: Connection, org_id: str) -> list[dict]:
    """Return members opted-in to reply pings for an org and NOT currently muted.

    §2 Flow D / §11 #1: the default fan-out is "all opted-in members for the org"
    — ``relay_member_preferences.replies_optin = 1`` AND not muted (``mute_until``
    NULL or in the past). A member's TG identity (the DM target) is joined in so
    the listener can DM them; a member with no TG identity is still returned
    (``tg_user_id`` NULL) so the opportunity is recorded against them even though
    the bot cannot DM them (the listener skips a NULL DM target). Returns
    ``{member_id, tg_user_id, handle}`` dicts ordered by ``member_id``.
    """
    now = _utc_now_iso()
    rows = conn.execute(
        text(
            "SELECT p.member_id AS member_id, "
            "       i.external_user_id AS tg_user_id, i.handle AS handle "
            "FROM relay_member_preferences p "
            "LEFT JOIN relay_member_identities i "
            "  ON i.member_id = p.member_id AND i.platform = 'telegram' "
            "WHERE p.org_id = :org_id AND p.replies_optin = 1 "
            "  AND (p.mute_until IS NULL OR p.mute_until <= :now) "
            "ORDER BY p.member_id"
        ),
        {"org_id": org_id, "now": now},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def resolve_members_by_telegram_handle(
    conn: Connection, handles: list[str]
) -> dict[str, int | None]:
    """Resolve TG ``@handle`` strings to ``relay_members.id`` (handle → member_id).

    Backs ``/flag-reply``'s optional ``target=@handle…`` (§11 #1: "resolves handles
    to relay_members.id via relay_member_identities"). Handles are presentation
    only and resolution is best-effort: a handle that matches no TG identity maps
    to ``None`` so the caller can report it as unresolved (it never silently grants
    a target). Matching is case-insensitive and tolerant of a leading ``@``. If a
    handle ambiguously matches multiple members it maps to ``None`` (the caller
    should report the ambiguity rather than guess — identity is ``member_id``, not
    a mutable handle, §15.4).
    """
    resolved: dict[str, int | None] = {}
    for raw in handles:
        key = raw.strip()
        if not key:
            continue
        bare = key.lstrip("@").lower()
        rows = conn.execute(
            text(
                "SELECT DISTINCT member_id FROM relay_member_identities "
                "WHERE platform = 'telegram' AND lower(handle) = :h"
            ),
            {"h": bare},
        ).fetchall()
        resolved[key] = int(rows[0][0]) if len(rows) == 1 else None
    return resolved


def insert_reply_notification(
    conn: Connection, opportunity_id: int, member_id: int
) -> int | None:
    """Insert a ``relay_reply_notifications`` row for (opportunity, member).

    Idempotent on the ``relay_reply_notifications_unique`` (opportunity_id,
    member_id) index — a member targeted twice for the same opportunity (e.g.
    listed explicitly AND opted-in) gets a single notification row. Returns the
    new notification id, or ``None`` if a row already existed (so the listener
    DMs each target exactly once). Also records the opportunity↔target junction
    (``relay_reply_opportunity_targets``) for audit/analytics. The caller MUST be
    inside an ``immediate_txn``.
    """
    # Junction row (idempotent on its composite PK) — the durable target record.
    exists_target = conn.execute(
        text(
            "SELECT 1 FROM relay_reply_opportunity_targets "
            "WHERE opportunity_id = :oid AND member_id = :mid"
        ),
        {"oid": int(opportunity_id), "mid": int(member_id)},
    ).fetchone()
    if exists_target is None:
        conn.execute(
            text(
                "INSERT INTO relay_reply_opportunity_targets (opportunity_id, member_id) "
                "VALUES (:oid, :mid)"
            ),
            {"oid": int(opportunity_id), "mid": int(member_id)},
        )
    existing = conn.execute(
        text(
            "SELECT id FROM relay_reply_notifications "
            "WHERE opportunity_id = :oid AND member_id = :mid"
        ),
        {"oid": int(opportunity_id), "mid": int(member_id)},
    ).fetchone()
    if existing is not None:
        return None
    row = conn.execute(
        text(
            "INSERT INTO relay_reply_notifications (opportunity_id, member_id, notified_at) "
            "VALUES (:oid, :mid, :now) RETURNING id"
        ),
        {"oid": int(opportunity_id), "mid": int(member_id), "now": _utc_now_iso()},
    ).fetchone()
    return int(row[0])


# ==================================================================
# C2.3b — reply-ping preferences (optin / optout / mute / whoami)
#
# §4 commands: /optin-replies, /optout-replies, /mute-replies <duration> live in
# a DM with the bot; /whoami in any chat. All keyed on (member_id, org_id) in
# relay_member_preferences (the 057 PK). A member is auto-created on first
# interaction (auto_create_member_identity); auto-creation grants NO role (§8) —
# preferences are independent of roles.
# ==================================================================
def upsert_member_preference(
    conn: Connection,
    member_id: int,
    org_id: str,
    *,
    replies_optin: bool | None = None,
    mute_until: str | None = ...,  # type: ignore[assignment]  # ... = "leave unchanged"
) -> None:
    """Upsert a ``relay_member_preferences`` row for (member, org).

    Only the fields explicitly passed are changed: ``replies_optin=None`` leaves
    the opt-in flag as-is; ``mute_until=...`` (the default sentinel) leaves the
    mute as-is, while ``mute_until=None`` CLEARS the mute and ``mute_until="<iso>"``
    sets it. The row is created on first write (default ``replies_optin=0``). The
    caller MUST be inside an ``immediate_txn``.
    """
    existing = conn.execute(
        text(
            "SELECT replies_optin, mute_until FROM relay_member_preferences "
            "WHERE member_id = :mid AND org_id = :org_id"
        ),
        {"mid": int(member_id), "org_id": org_id},
    ).fetchone()
    if existing is None:
        new_optin = 1 if replies_optin else 0
        new_mute = None if mute_until is ... else mute_until
        conn.execute(
            text(
                "INSERT INTO relay_member_preferences "
                "(member_id, org_id, replies_optin, mute_until, updated_at) "
                "VALUES (:mid, :org_id, :optin, :mute, :now)"
            ),
            {
                "mid": int(member_id),
                "org_id": org_id,
                "optin": new_optin,
                "mute": new_mute,
                "now": _utc_now_iso(),
            },
        )
        return
    cur_optin, cur_mute = int(existing[0]), existing[1]
    next_optin = cur_optin if replies_optin is None else (1 if replies_optin else 0)
    next_mute = cur_mute if mute_until is ... else mute_until
    conn.execute(
        text(
            "UPDATE relay_member_preferences SET "
            "  replies_optin = :optin, mute_until = :mute, updated_at = :now "
            "WHERE member_id = :mid AND org_id = :org_id"
        ),
        {
            "optin": next_optin,
            "mute": next_mute,
            "now": _utc_now_iso(),
            "mid": int(member_id),
            "org_id": org_id,
        },
    )


def get_member_preference(
    conn: Connection, member_id: int, org_id: str
) -> dict | None:
    """Return a member's reply-ping preference row for an org as a dict, or None."""
    row = conn.execute(
        text(
            "SELECT member_id, org_id, replies_optin, mute_until, updated_at "
            "FROM relay_member_preferences "
            "WHERE member_id = :mid AND org_id = :org_id"
        ),
        {"mid": int(member_id), "org_id": org_id},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def get_identity_handle(
    conn: Connection, member_id: int, platform: str = "telegram"
) -> str | None:
    """Return a member's stored ``handle`` for a platform identity (display-only)."""
    row = conn.execute(
        text(
            "SELECT handle FROM relay_member_identities "
            "WHERE member_id = :mid AND platform = :platform"
        ),
        {"mid": int(member_id), "platform": platform},
    ).fetchone()
    if row is None:
        return None
    return row[0]


def get_member_identity(
    conn: Connection, member_id: int, platform: str = "telegram"
) -> dict | None:
    """Return a member's ``(external_user_id, handle)`` for a platform, or None.

    The DM-target resolver for ``/flag-reply``'s explicit ``target=@handle…`` path:
    given a resolved ``member_id``, return the TG identity the listener DMs.
    Returns ``{external_user_id, handle}`` or ``None`` if the member has no
    identity on that platform.
    """
    row = conn.execute(
        text(
            "SELECT external_user_id, handle FROM relay_member_identities "
            "WHERE member_id = :mid AND platform = :platform"
        ),
        {"mid": int(member_id), "platform": platform},
    ).fetchone()
    if row is None:
        return None
    return {"external_user_id": row[0], "handle": row[1]}


# ==================================================================
# C2.3b — admin: register-operator + bind-chat
#
# §8: every member is provisioned by an existing admin/operator (no
# self-registration). /register-operator supports THREE resolution modes
# (numeric tg_user_id / self-claim-via-recent-DM / forwarded-message from.id) and
# NO bare-handle resolution (the Bot API exposes no getUserByUsername; handles are
# mutable, §8). /bind-chat binds the current chat as operator/shared/community/
# broadcast for a client (admin-gated). Both write an audit row IN the same
# immediate_txn via write_relay_audit (no commit — log_audit() in db/audit.py
# commits, which would break the handler's single-txn contract).
# ==================================================================
def write_relay_audit(
    conn: Connection,
    *,
    actor: str,
    action: str,
    org_id: str | None = None,
    entity_id: str | None = None,
    detail: dict | None = None,
) -> int:
    """Append a relay audit row to ``audit_log`` WITHOUT committing.

    ``db/audit.py``'s :func:`log_audit` calls ``conn.commit()`` internally, which
    would break a relay handler's single-``immediate_txn`` contract (committing
    mid-handler). This is the txn-safe sibling: it inserts the same
    ``audit_log`` row but leaves the transaction open for the caller's
    ``immediate_txn`` to commit atomically with the rest of the handler's writes.
    ``source`` is fixed to ``'relay'``. Returns the new row id.
    """
    import json as _json

    detail_json = _json.dumps(detail) if detail else None
    row = conn.execute(
        text(
            "INSERT INTO audit_log (actor, action, org_id, entity_id, detail_json, source) "
            "VALUES (:actor, :action, :org_id, :entity_id, :detail_json, 'relay') "
            "RETURNING id"
        ),
        {
            "actor": actor,
            "action": action,
            "org_id": org_id,
            "entity_id": entity_id,
            "detail_json": detail_json,
        },
    ).fetchone()
    return int(row[0])


def resolve_recent_telegram_identity(
    conn: Connection, handle: str, *, within_days: int = 7
) -> tuple[int | None, list[str]]:
    """Resolve a TG ``@handle`` to a member via RECENTLY-SEEN identities (mode 2).

    The §8 self-claim path: the target DMs the bot ``/whoami`` first (populating
    ``relay_member_identities``), then the admin runs ``/register-operator @handle``
    and the bot resolves the handle against TG identities LINKED within the last
    ``within_days`` days. Returns ``(member_id, candidate_external_ids)``:

      * exactly one recent match → ``(member_id, [external_user_id])``;
      * zero matches → ``(None, [])`` (the admin must use mode 1 or 2 first);
      * 2+ matches → ``(None, [external_user_id, …])`` so the caller can list the
        candidates and ask the admin to disambiguate with a numeric id (mode 1).

    This is NOT bare-handle resolution: it resolves ONLY against a recently-seen,
    self-claimed identity already in ``relay_member_identities`` (a handle that
    has never DMed the bot resolves to nothing). Matching is case-insensitive and
    tolerant of a leading ``@``.
    """
    bare = handle.strip().lstrip("@").lower()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=within_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        text(
            "SELECT member_id, external_user_id FROM relay_member_identities "
            "WHERE platform = 'telegram' AND lower(handle) = :h "
            "  AND linked_at >= :cutoff "
            "ORDER BY linked_at DESC"
        ),
        {"h": bare, "cutoff": cutoff},
    ).fetchall()
    if not rows:
        return None, []
    if len(rows) == 1:
        return int(rows[0][0]), [str(rows[0][1])]
    return None, [str(r[1]) for r in rows]


def grant_member_role(
    conn: Connection,
    member_id: int,
    org_id: str,
    role: str,
    *,
    granted_by: int | None = None,
) -> bool:
    """Grant ``role`` to a member for an org (idempotent). Returns True iff NEW.

    ``role`` is one of :data:`RELAY_MEMBER_ROLES`. Idempotent on the
    ``relay_member_roles`` (member_id, org_id, role) PK — re-granting an existing
    role is a no-op that returns ``False``. ``granted_by`` records the admin's
    ``relay_members.id`` for the chain-of-trust audit (§8). The caller MUST be
    inside an ``immediate_txn`` and MUST have already admin-gated the caller.
    """
    if role not in RELAY_MEMBER_ROLES:
        raise ValueError(
            f"unknown relay role {role!r}; expected one of {RELAY_MEMBER_ROLES}"
        )
    existing = conn.execute(
        text(
            "SELECT 1 FROM relay_member_roles "
            "WHERE member_id = :mid AND org_id = :org_id AND role = :role"
        ),
        {"mid": int(member_id), "org_id": org_id, "role": role},
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role, granted_by, granted_at) "
            "VALUES (:mid, :org_id, :role, :granted_by, :now)"
        ),
        {
            "mid": int(member_id),
            "org_id": org_id,
            "role": role,
            "granted_by": int(granted_by) if granted_by is not None else None,
            "now": _utc_now_iso(),
        },
    )
    return True


def relay_client_exists(conn: Connection, org_id: str) -> bool:
    """True iff a ``relay_clients`` row exists for ``org_id`` (bind-chat precondition)."""
    row = conn.execute(
        text("SELECT 1 FROM relay_clients WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).fetchone()
    return row is not None


def bind_chat(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    chat_id: str,
    role: str,
    title: str | None = None,
) -> int:
    """Bind the current chat as ``role`` for a client (admin ``/bind-chat``).

    ``role`` is one of the 057 ``relay_chat_bindings`` CHECK set
    ``('operator','shared','community','broadcast')``. Honors the two partial
    unique indexes (active per org+platform+role; active per platform+chat):

      * an existing active binding for the SAME (org, platform, role) pointing at a
        DIFFERENT chat is flipped to ``disabled`` (re-pointing the role), and
      * an existing active binding for the SAME (platform, chat) under a DIFFERENT
        role is flipped to ``disabled`` (a chat takes at most one active role)

    before the new active binding is inserted. Re-binding the exact same
    (org, platform, chat, role) is a no-op that returns the existing binding id.
    Also ensures the ``relay_chats`` chat-id surface row exists. The caller MUST
    be inside an ``immediate_txn`` and MUST have already admin-gated the caller.
    Returns the active binding id.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram' or 'discord'"
        )
    if role not in ("operator", "shared", "community", "broadcast"):
        raise ValueError(
            f"unknown binding role {role!r}; expected one of "
            "('operator','shared','community','broadcast')"
        )
    # Chat-id surface row (idempotent).
    upsert_chat(conn, org_id, chat_id, platform=platform, title=title)

    # Already bound to this exact role+chat? (idempotent return)
    same = conn.execute(
        text(
            "SELECT id FROM relay_chat_bindings "
            "WHERE org_id = :org_id AND platform = :platform AND chat_id = :chat_id "
            "  AND role = :role AND status = 'active'"
        ),
        {"org_id": org_id, "platform": platform, "chat_id": chat_id, "role": role},
    ).fetchone()
    if same is not None:
        return int(same[0])

    now = _utc_now_iso()
    # Flip any active binding for the SAME (org, platform, role) on another chat.
    conn.execute(
        text(
            "UPDATE relay_chat_bindings SET status = 'disabled', last_seen_at = :now "
            "WHERE org_id = :org_id AND platform = :platform AND role = :role "
            "  AND status = 'active'"
        ),
        {"now": now, "org_id": org_id, "platform": platform, "role": role},
    )
    # Flip any active binding for the SAME (platform, chat) under another role.
    conn.execute(
        text(
            "UPDATE relay_chat_bindings SET status = 'disabled', last_seen_at = :now "
            "WHERE platform = :platform AND chat_id = :chat_id AND status = 'active'"
        ),
        {"now": now, "platform": platform, "chat_id": chat_id},
    )
    row = conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:org_id, :platform, :chat_id, :role, 'active') "
            "RETURNING id"
        ),
        {"org_id": org_id, "platform": platform, "chat_id": chat_id, "role": role},
    ).fetchone()
    return int(row[0])


# ==================================================================
# C2.3c — /forget-me PII deletion + /link-x identity link
#
# §15.5: /forget-me "removes preferences and identity rows but keeps audit
# references via member_id (anonymized)". §8: /link-x adds platform='x' to an
# existing member; the (platform, external_user_id) PK enforces uniqueness
# mechanically, so a collision (the X identity already links a DIFFERENT member)
# is rejected with the §8 message and resolved only by an admin-only DB merge
# (no v1 self-serve merge UI). Both run inside the caller's immediate_txn; the
# audit row is written via write_relay_audit (txn-safe, no mid-handler commit).
# ==================================================================
def anonymize_member(conn: Connection, member_id: int) -> None:
    """Anonymize the ``relay_members`` row's PII fields, keeping the id (§15.5).

    The ``relay_members.id`` is the durable audit anchor (submissions, reactions,
    reply opportunities/notifications, audit rows all FK to it), so the row is
    NEVER deleted — only its PII-carrying ``display_name`` (which may hold the
    member's handle / real name) is cleared to ``NULL``. This is the
    "audit references via member_id (anonymized)" half of ``/forget-me``: the
    member_id keeps pointing at a now-nameless row. The caller MUST be inside an
    ``immediate_txn``.
    """
    conn.execute(
        text("UPDATE relay_members SET display_name = NULL WHERE id = :id"),
        {"id": int(member_id)},
    )


def delete_member_preferences(conn: Connection, member_id: int) -> int:
    """Delete ALL ``relay_member_preferences`` rows for a member (§15.5). Count deleted.

    Reply-ping preferences (opt-in state, mute windows) across every org the
    member had a preference for are PII-adjacent and removed wholesale on
    ``/forget-me``. Returns the number of preference rows deleted. The caller MUST
    be inside an ``immediate_txn``.
    """
    result = conn.execute(
        text("DELETE FROM relay_member_preferences WHERE member_id = :mid"),
        {"mid": int(member_id)},
    )
    return int(result.rowcount or 0)


def delete_member_identities(conn: Connection, member_id: int) -> int:
    """Delete ALL ``relay_member_identities`` rows for a member (§15.5). Count deleted.

    The identity rows carry the externally-identifying PII — the stable
    ``external_user_id`` per platform and the display ``handle`` — so they are
    removed on ``/forget-me`` (the member is no longer resolvable from any
    external id, and a future interaction would auto-create a FRESH member). Roles
    on ``relay_member_roles`` are intentionally NOT touched here (deleting an
    identity already strips the member's ability to be resolved/authorized — and
    a forgotten member keeping a dangling role row carries no PII). Returns the
    number of identity rows deleted. The caller MUST be inside an ``immediate_txn``.
    """
    result = conn.execute(
        text("DELETE FROM relay_member_identities WHERE member_id = :mid"),
        {"mid": int(member_id)},
    )
    return int(result.rowcount or 0)


def get_x_identity(conn: Connection, external_user_id: str) -> dict | None:
    """Return the ``relay_member_identities`` row for an X ``external_user_id``, or None.

    The ``(platform, external_user_id)`` PK means at most one row exists for a
    given X user id. ``/link-x`` consults this to detect the §8 collision (the X
    id already linked to a DIFFERENT member) before attempting the insert.
    Returns ``{member_id, external_user_id, handle}`` or ``None`` if unlinked.
    """
    row = conn.execute(
        text(
            "SELECT member_id, external_user_id, handle "
            "FROM relay_member_identities "
            "WHERE platform = 'x' AND external_user_id = :euid"
        ),
        {"euid": str(external_user_id)},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def link_x_identity(
    conn: Connection,
    member_id: int,
    x_user_id: str,
    *,
    handle: str | None = None,
) -> None:
    """Insert a ``platform='x'`` identity row for an existing member (§8 ``/link-x``).

    The caller MUST have already (a) verified the member exists and (b) checked
    via :func:`get_x_identity` that the X id is unlinked (or links to THIS member —
    an idempotent re-link refreshes the handle instead of inserting). This writes
    the new ``(platform='x', external_user_id)`` PK row. The caller MUST be inside
    an ``immediate_txn``.
    """
    conn.execute(
        text(
            "INSERT INTO relay_member_identities "
            "(member_id, platform, external_user_id, handle, linked_at) "
            "VALUES (:member_id, 'x', :euid, :handle, :now)"
        ),
        {
            "member_id": int(member_id),
            "euid": str(x_user_id),
            "handle": handle,
            "now": _utc_now_iso(),
        },
    )


def reassign_x_identity(
    conn: Connection,
    x_user_id: str,
    *,
    new_member_id: int,
    handle: str | None = None,
) -> bool:
    """Re-point an existing X identity to a different member (§8 admin-only merge).

    The §8 merge path: an admin resolves a ``/link-x`` collision by re-assigning
    the X id's ``member_id`` to the intended member (no v1 self-serve UI). Updates
    the ``(platform='x', external_user_id)`` row's ``member_id`` (and refreshes
    ``handle`` if supplied). Returns ``True`` iff a row was re-pointed (``False``
    if the X id was not linked at all — there is nothing to merge). The caller
    MUST be inside an ``immediate_txn`` and MUST have already admin-gated the
    caller.
    """
    if handle is not None:
        result = conn.execute(
            text(
                "UPDATE relay_member_identities "
                "SET member_id = :mid, handle = :handle "
                "WHERE platform = 'x' AND external_user_id = :euid"
            ),
            {"mid": int(new_member_id), "handle": handle, "euid": str(x_user_id)},
        )
    else:
        result = conn.execute(
            text(
                "UPDATE relay_member_identities SET member_id = :mid "
                "WHERE platform = 'x' AND external_user_id = :euid"
            ),
            {"mid": int(new_member_id), "euid": str(x_user_id)},
        )
    return int(result.rowcount or 0) > 0


def member_exists(conn: Connection, member_id: int) -> bool:
    """True iff a ``relay_members`` row exists for ``member_id`` (merge precondition)."""
    row = conn.execute(
        text("SELECT 1 FROM relay_members WHERE id = :id"),
        {"id": int(member_id)},
    ).fetchone()
    return row is not None


# ==================================================================
# C2.5 — relay operator CLI helpers (cli/relay_cmds.py)
#
# These back the `sable-platform relay` command surface (bind-chat,
# register-operator, status, pending, enable, disable/pause-org). They take an
# already-open SQLAlchemy Connection like every other helper here; the CLI owns
# the connection lifecycle and the `immediate_txn` boundary for the writers
# (enable, disable). NO engine is created here (the §5.3 layering boundary).
# ==================================================================
def ensure_relay_client(conn: Connection, org_id: str, *, enabled: int = 0) -> bool:
    """Ensure a ``relay_clients`` row exists for ``org_id`` (idempotent).

    Inserts the row with the given ``enabled`` value if absent. Returns ``True``
    iff a NEW row was created (``False`` if it already existed). Does NOT touch an
    existing row's ``enabled`` flag — flipping ``enabled`` is the caller's job
    (see :func:`set_relay_client_enabled`). ``org_id`` MUST already exist in
    ``orgs`` (``relay_clients.org_id`` is a FK to ``orgs(org_id)``); the caller is
    responsible for that precondition. The caller MUST be inside an
    ``immediate_txn`` (this writes).
    """
    existing = conn.execute(
        text("SELECT 1 FROM relay_clients WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        text(
            "INSERT INTO relay_clients (org_id, enabled, created_at) "
            "VALUES (:org_id, :enabled, :now)"
        ),
        {"org_id": org_id, "enabled": int(enabled), "now": _utc_now_iso()},
    )
    return True


def set_relay_client_enabled(conn: Connection, org_id: str, *, enabled: int) -> None:
    """Set ``relay_clients.enabled`` for an existing client (the poller gate).

    ``enabled=1`` re-admits the org to the poller's per-enabled-client loop;
    ``enabled=0`` removes it. Operates on an existing row only (use
    :func:`ensure_relay_client` first for the create path). The caller MUST be
    inside an ``immediate_txn`` (this writes).
    """
    conn.execute(
        text("UPDATE relay_clients SET enabled = :enabled WHERE org_id = :org_id"),
        {"enabled": int(enabled), "org_id": org_id},
    )


def kill_org_inflight_jobs(
    conn: Connection, org_id: str, *, last_error: str = "org disabled by operator"
) -> int:
    """Halt ALL of an org's in-flight publication jobs (the kill-switch fan-out).

    Flips every ``relay_publication_jobs`` row for ``org_id`` that is in
    ``pending`` / ``retry`` / ``claimed`` to ``state='dead'`` with ``last_error``
    (default ``'org disabled by operator'``). ``'dead'`` is the only CHECK-allowed
    halted value in the LOCKED §3.1 set ``('pending','claimed','retry','done',
    'dead')`` — there is NO ``'halted'`` state (inventing one would violate the
    CHECK). This is the org-scoped sibling of :func:`kill_inflight_jobs` (which is
    keyed by a single kicked destination chat): the relay-level kill-switch must
    stop EVERY destination's in-flight publishing for the org, so it keys on
    ``org_id`` instead. Done/dead jobs are left untouched (terminal). Returns the
    count flipped. The caller MUST be inside an ``immediate_txn`` (this writes).
    """
    result = conn.execute(
        text(
            "UPDATE relay_publication_jobs "
            "SET state = 'dead', last_error = :last_error "
            "WHERE org_id = :org_id AND state IN ('pending', 'retry', 'claimed')"
        ),
        {"last_error": last_error, "org_id": org_id},
    )
    return int(result.rowcount or 0)


def list_pending_submissions(
    conn: Connection, org_id: str, *, limit: int = 50
) -> list[dict]:
    """List an org's open submissions awaiting quorum (the ``/pending`` surface).

    Returns ``pending`` and ``ready_to_publish`` ``relay_submissions`` rows for
    ``org_id`` (the two non-terminal submission states), newest first, joined to
    the tweet's author handle / x_id for a human-readable listing. Read-only.
    """
    rows = conn.execute(
        text(
            "SELECT s.id, s.status, s.source_role, s.note, s.created_at, "
            "       s.expires_at, t.x_id, t.x_author_handle "
            "FROM relay_submissions s "
            "JOIN relay_tweets t ON t.id = s.tweet_id "
            "WHERE s.org_id = :org_id "
            "  AND s.status IN ('pending', 'ready_to_publish') "
            "ORDER BY s.created_at DESC, s.id DESC "
            "LIMIT :limit"
        ),
        {"org_id": org_id, "limit": int(limit)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def get_relay_status(conn: Connection, org_id: str) -> dict | None:
    """Return a bot-health summary for an org (the ``/status`` surface), or None.

    ``None`` iff no ``relay_clients`` row exists for ``org_id``. Otherwise a dict
    with the client's ``enabled`` flag, poll cursor (``last_polled_at`` /
    ``last_seen_x_id``), ``last_error``, the count of active chat bindings, the
    count of open submissions (``pending`` + ``ready_to_publish``), and the count
    of in-flight publication jobs (``pending`` + ``claimed`` + ``retry``).
    Read-only — no transaction required.
    """
    client = conn.execute(
        text(
            "SELECT enabled, last_polled_at, last_seen_x_id, last_error "
            "FROM relay_clients WHERE org_id = :org_id"
        ),
        {"org_id": org_id},
    ).fetchone()
    if client is None:
        return None
    bindings = conn.execute(
        text(
            "SELECT COUNT(*) FROM relay_chat_bindings "
            "WHERE org_id = :org_id AND status = 'active'"
        ),
        {"org_id": org_id},
    ).fetchone()[0]
    pending_submissions = conn.execute(
        text(
            "SELECT COUNT(*) FROM relay_submissions "
            "WHERE org_id = :org_id AND status IN ('pending', 'ready_to_publish')"
        ),
        {"org_id": org_id},
    ).fetchone()[0]
    inflight_jobs = conn.execute(
        text(
            "SELECT COUNT(*) FROM relay_publication_jobs "
            "WHERE org_id = :org_id AND state IN ('pending', 'claimed', 'retry')"
        ),
        {"org_id": org_id},
    ).fetchone()[0]
    return {
        "org_id": org_id,
        "enabled": int(client[0]),
        "last_polled_at": client[1],
        "last_seen_x_id": client[2],
        "last_error": client[3],
        "active_bindings": int(bindings),
        "pending_submissions": int(pending_submissions),
        "inflight_jobs": int(inflight_jobs),
    }


def org_exists(conn: Connection, org_id: str) -> bool:
    """True iff an ``orgs`` row exists for ``org_id`` (relay-client FK precondition)."""
    row = conn.execute(
        text("SELECT 1 FROM orgs WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).fetchone()
    return row is not None


# ==================================================================
# Migration 062 — reply-opportunity feed (reply-assist x SableRelay)
#
# The hourly Slopper sweep auto-sources reply opportunities into the EXISTING
# relay_reply_opportunities table (unified store — manual /flag-reply and
# auto-sweeps land in one table). These helpers back the sweep writer, the
# per-operator web feed, the two thumbs, the per-client curated query set, the
# sweep state machine (the §4 one-click-one-sweep selection), the relay_tweets
# read-through cache, the logged-in heartbeat gate, and GC. See
# SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md §3-§4. Every writer documents the
# immediate_txn contract; reads are transaction-free.
# ==================================================================

# Terminal opportunity statuses — a re-seen opportunity in one of these states
# NEVER flips back to 'active' (plan §4 step 6). Dedup is application-level
# (there is NO UNIQUE(org_id, tweet_id) constraint — §3.1).
_TERMINAL_OPPORTUNITY_STATUSES = ("handled", "expired", "dismissed")

# sweep_source -> origin map. Auto rows reuse an EXISTING allowed origin value so
# the 057 origin CHECK passes unchanged; the real detail rides sweep_source.
_SWEEP_SOURCE_TO_ORIGIN = {
    "operator_submit": "explicit_command",
    "mention": "auto_mention",
    "topic": "auto_mention",
    "from_set": "auto_mention",
}
_VALID_SWEEP_SOURCES = tuple(_SWEEP_SOURCE_TO_ORIGIN.keys())

# Read-through cache TTL for relay_tweets (a read-side policy, plan §3.7).
_TWEET_CACHE_TTL_HOURS = 6

# The sweep sentinel keeps flagger_id NOT NULL on auto-sourced rows.
_SWEEP_SENTINEL_DISPLAY = "__sweep__"


def get_or_create_sweep_sentinel(conn: Connection, org_id: str) -> int:
    """Get-or-create ONE sentinel ``relay_members`` row PER ORG for the sweep.

    Auto-sourced opportunities need a non-NULL ``flagger_id`` (the 057 column
    stays NOT NULL — plan §3.1, no rebuild). The sweep attributes them to a
    deterministic per-org sentinel member (``display_name='__sweep__'``) with a
    ``platform='x'`` identity keyed ``'__sweep__::'+org_id`` so it is stable and
    idempotent across sweeps. Mirrors :func:`auto_create_member_identity`
    (auto-creation grants NO roles). The caller MUST be inside an
    ``immediate_txn`` (this writes). Returns the sentinel ``relay_members.id``.
    """
    external_user_id = f"{_SWEEP_SENTINEL_DISPLAY}::{org_id}"
    existing = conn.execute(
        text(
            "SELECT member_id FROM relay_member_identities "
            "WHERE platform = 'x' AND external_user_id = :euid"
        ),
        {"euid": external_user_id},
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    row = conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES (:name) RETURNING id"),
        {"name": _SWEEP_SENTINEL_DISPLAY},
    ).fetchone()
    member_id = int(row[0])
    conn.execute(
        text(
            "INSERT INTO relay_member_identities "
            "(member_id, platform, external_user_id, handle, linked_at) "
            "VALUES (:member_id, 'x', :euid, :handle, :now)"
        ),
        {
            "member_id": member_id,
            "euid": external_user_id,
            "handle": _SWEEP_SENTINEL_DISPLAY,
            "now": _utc_now_iso(),
        },
    )
    return member_id


def get_opportunity_org(conn: Connection, opportunity_id: int) -> str | None:
    """Return the owning ``org_id`` of an opportunity by id, or ``None`` if absent.

    Org-ownership lookup for callers that receive a client-supplied (global) PK
    ``opportunity_id`` and must verify it belongs to the authorized org BEFORE
    acting on it (defense-in-depth for cross-org IDOR — plan §5). Read-only;
    returns the ``org_id`` regardless of the opportunity's status.
    """
    row = conn.execute(
        text("SELECT org_id FROM relay_reply_opportunities WHERE id = :id"),
        {"id": int(opportunity_id)},
    ).fetchone()
    return None if row is None else row[0]


def find_active_opportunity_for_tweet(
    conn: Connection, org_id: str, tweet_id: int
) -> dict | None:
    """Return the existing NON-TERMINAL opportunity for ``(org_id, tweet_id)``, or None.

    The application-level dedup lookup (plan §3.1 — there is no UNIQUE constraint
    to lean on). "Non-terminal" = status NOT IN ('handled','expired','dismissed').
    Returns ``{id, status, score, expires_at, sweep_source}`` (newest first if
    somehow more than one) or ``None``. Read-only.
    """
    row = conn.execute(
        text(
            "SELECT id, status, score, expires_at, sweep_source "
            "FROM relay_reply_opportunities "
            "WHERE org_id = :org_id AND tweet_id = :tweet_id "
            "  AND status NOT IN ('handled','expired','dismissed') "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"org_id": org_id, "tweet_id": int(tweet_id)},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def upsert_sweep_opportunity(
    conn: Connection,
    *,
    org_id: str,
    tweet_id: int,
    sweep_source: str,
    score: float | None = None,
    score_reason: str | None = None,
    suggested_angle: str | None = None,
    expiry_hours: int = 36,
    note: str | None = None,
) -> int:
    """Application-level upsert of a sweep-sourced opportunity; return its id.

    Dedup is purely app-level (plan §3.1 — NO ``UNIQUE(org_id, tweet_id)``): look
    up any existing non-terminal opportunity for ``(org_id, tweet_id)`` and, if
    present, UPDATE its ``score``/``score_reason``/``suggested_angle`` (re-score
    on re-surface) WITHOUT touching ``expires_at`` or flipping ``status`` (a
    handled/expired/dismissed row is excluded from the lookup and so is never
    revived). Otherwise INSERT a new row with ``flagger_id`` = the per-org sweep
    sentinel, ``origin`` mapped from ``sweep_source`` (operator_submit ->
    explicit_command, else auto_mention), ``status='active'``, and
    ``expires_at = now + expiry_hours`` (set ONCE at creation, never extended).
    The caller MUST be inside an ``immediate_txn`` (this writes).
    """
    if sweep_source not in _VALID_SWEEP_SOURCES:
        raise ValueError(
            f"unknown sweep_source {sweep_source!r}; expected one of {_VALID_SWEEP_SOURCES}"
        )
    existing = find_active_opportunity_for_tweet(conn, org_id, tweet_id)
    if existing is not None:
        conn.execute(
            text(
                "UPDATE relay_reply_opportunities SET "
                "  score = :score, "
                "  score_reason = :score_reason, "
                "  suggested_angle = :suggested_angle "
                "WHERE id = :id"
            ),
            {
                "score": score,
                "score_reason": score_reason,
                "suggested_angle": suggested_angle,
                "id": int(existing["id"]),
            },
        )
        return int(existing["id"])

    flagger_id = get_or_create_sweep_sentinel(conn, org_id)
    origin = _SWEEP_SOURCE_TO_ORIGIN[sweep_source]
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=int(expiry_hours))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        text(
            "INSERT INTO relay_reply_opportunities "
            "(org_id, tweet_id, flagger_id, origin, note, status, score, "
            " score_reason, suggested_angle, expires_at, sweep_source) "
            "VALUES (:org_id, :tweet_id, :flagger_id, :origin, :note, 'active', "
            "        :score, :score_reason, :suggested_angle, :expires_at, :sweep_source) "
            "RETURNING id"
        ),
        {
            "org_id": org_id,
            "tweet_id": int(tweet_id),
            "flagger_id": flagger_id,
            "origin": origin,
            "note": note,
            "score": score,
            "score_reason": score_reason,
            "suggested_angle": suggested_angle,
            "expires_at": expires_at,
            "sweep_source": sweep_source,
        },
    ).fetchone()
    return int(row[0])


def list_feed_opportunities(
    conn: Connection,
    org_id: str,
    operator_handle: str,
    *,
    limit: int = 50,
) -> list[dict]:
    """Return the per-operator reply-opportunity feed for an org (plan §5).

    ORG-FILTERED server-side (defense-in-depth, not just the UI dropdown). Joins
    :func:`relay_opportunity_operator_state` for ``operator_handle`` and EXCLUDES
    rows that operator dismissed or has currently snoozed (``snooze_until`` in the
    future). Orders ``score DESC`` (NULLs last), with ``handled`` depressed to the
    bottom. NEVER selects any cost column (the cost-never-in-response rule). The
    target tweet's author/x_id/text are joined for display. Read-only.
    """
    now = _utc_now_iso()
    rows = conn.execute(
        text(
            "SELECT o.id, o.org_id, o.tweet_id, o.origin, o.note, o.status, "
            "       o.score, o.score_reason, o.suggested_angle, o.expires_at, "
            "       o.sweep_source, o.created_at, "
            "       t.x_id, t.x_author_handle, t.text AS tweet_text "
            "FROM relay_reply_opportunities o "
            "JOIN relay_tweets t ON t.id = o.tweet_id "
            "LEFT JOIN relay_opportunity_operator_state s "
            "  ON s.opportunity_id = o.id AND s.operator_handle = :operator_handle "
            "WHERE o.org_id = :org_id "
            "  AND o.status IN ('active', 'handled') "
            "  AND (s.state IS NULL "
            "       OR (s.state = 'snoozed' "
            "           AND (s.snooze_until IS NULL OR s.snooze_until <= :now))) "
            "ORDER BY CASE WHEN o.status = 'handled' THEN 1 ELSE 0 END ASC, "
            "         CASE WHEN o.score IS NULL THEN 1 ELSE 0 END ASC, "
            "         o.score DESC, o.created_at DESC, o.id DESC "
            "LIMIT :limit"
        ),
        {
            "org_id": org_id,
            "operator_handle": operator_handle,
            "now": now,
            "limit": int(limit),
        },
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def set_operator_opportunity_state(
    conn: Connection,
    *,
    opportunity_id: int,
    operator_handle: str,
    state: str,
    snooze_until: str | None = None,
) -> None:
    """Upsert a per-operator ``dismissed``/``snoozed`` state on an opportunity (§3.2).

    ``state`` must be ``'dismissed'`` or ``'snoozed'`` (``snooze_until`` is the
    ISO-8601-Z wake time for a snooze; ignored for a dismiss). Idempotent on the
    composite PK ``(opportunity_id, operator_handle)`` — a repeat call overwrites
    the prior state. The caller MUST be inside an ``immediate_txn`` (this writes).
    """
    if state not in ("dismissed", "snoozed"):
        raise ValueError(
            f"unknown operator opportunity state {state!r}; expected 'dismissed' or 'snoozed'"
        )
    existing = conn.execute(
        text(
            "SELECT 1 FROM relay_opportunity_operator_state "
            "WHERE opportunity_id = :oid AND operator_handle = :h"
        ),
        {"oid": int(opportunity_id), "h": operator_handle},
    ).fetchone()
    if existing is not None:
        conn.execute(
            text(
                "UPDATE relay_opportunity_operator_state "
                "SET state = :state, snooze_until = :snooze_until "
                "WHERE opportunity_id = :oid AND operator_handle = :h"
            ),
            {
                "state": state,
                "snooze_until": snooze_until,
                "oid": int(opportunity_id),
                "h": operator_handle,
            },
        )
        return
    conn.execute(
        text(
            "INSERT INTO relay_opportunity_operator_state "
            "(opportunity_id, operator_handle, state, snooze_until, created_at) "
            "VALUES (:oid, :h, :state, :snooze_until, :now)"
        ),
        {
            "oid": int(opportunity_id),
            "h": operator_handle,
            "state": state,
            "snooze_until": snooze_until,
            "now": _utc_now_iso(),
        },
    )


def mark_opportunity_handled(conn: Connection, opportunity_id: int) -> None:
    """Depress an opportunity to ``handled`` (team-wide) once an operator acts (§5).

    On generate/post through the feed's Draft-reply, the opportunity is marked
    ``handled`` so it falls to the bottom of every operator's feed (campaign
    targets are exempt — the caller decides). The caller MUST be inside an
    ``immediate_txn`` (this writes).
    """
    conn.execute(
        text(
            "UPDATE relay_reply_opportunities SET status = 'handled' "
            "WHERE id = :id AND status = 'active'"
        ),
        {"id": int(opportunity_id)},
    )


def record_opportunity_feedback(
    conn: Connection,
    *,
    opportunity_id: int,
    rater_handle: str,
    rater_role: str,
    thumb: int,
    suggestion_id: str | None = None,
) -> int:
    """Insert a thumb into ``relay_opportunity_feedback``; return the row id (§3.3).

    ``suggestion_id`` NULL = a thumb on the OPPORTUNITY (relevance / ranker
    label); set = a thumb on a SUGGESTION (generation-quality signal). ``thumb``
    is +1 or -1; ``rater_role`` is ``'operator'`` or ``'client_ops'``. The caller
    MUST be inside an ``immediate_txn`` (this writes).
    """
    if thumb not in (1, -1):
        raise ValueError(f"thumb must be +1 or -1, got {thumb!r}")
    if rater_role not in ("operator", "client_ops"):
        raise ValueError(
            f"unknown rater_role {rater_role!r}; expected 'operator' or 'client_ops'"
        )
    row = conn.execute(
        text(
            "INSERT INTO relay_opportunity_feedback "
            "(opportunity_id, suggestion_id, rater_handle, rater_role, thumb, created_at) "
            "VALUES (:oid, :sid, :rater_handle, :rater_role, :thumb, :now) "
            "RETURNING id"
        ),
        {
            "oid": int(opportunity_id),
            "sid": suggestion_id,
            "rater_handle": rater_handle,
            "rater_role": rater_role,
            "thumb": int(thumb),
            "now": _utc_now_iso(),
        },
    ).fetchone()
    return int(row[0])


# ------------------------------------------------------------------
# relay_sweep_config CRUD + the §4 sweep state machine
# ------------------------------------------------------------------
def get_sweep_config(conn: Connection, org_id: str) -> dict | None:
    """Return the ``relay_sweep_config`` row for an org as a dict, or None. Read-only."""
    row = conn.execute(
        text(
            "SELECT org_id, mention_handles, topic_queries, from_set, "
            "       operator_handles, enabled, expiry_hours, last_sweep_at, "
            "       sweep_requested_at, updated_at "
            "FROM relay_sweep_config WHERE org_id = :org_id"
        ),
        {"org_id": org_id},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def upsert_sweep_config(
    conn: Connection,
    *,
    org_id: str,
    mention_handles: str | None = None,
    topic_queries: str | None = None,
    from_set: str | None = None,
    operator_handles: str | None = None,
    enabled: int | None = None,
    expiry_hours: int | None = None,
) -> None:
    """Create or update the per-client curated query set (plan §3.4).

    JSON-array fields (``mention_handles`` / ``topic_queries`` / ``from_set`` /
    ``operator_handles``) are passed as already-encoded JSON strings; ``None``
    leaves an existing value unchanged (on INSERT, ``None`` falls back to the
    column default ``'[]'`` / ``0`` / ``36``). The daily cost cap is NOT here (it
    lives in ``relay_clients.config.polling.daily_cost_cap_usd`` — the single cap
    source). ``last_sweep_at`` / ``sweep_requested_at`` are managed by the state
    machine, not by this writer. The caller MUST be inside an ``immediate_txn``.
    """
    now = _utc_now_iso()
    existing = conn.execute(
        text("SELECT 1 FROM relay_sweep_config WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).fetchone()
    if existing is None:
        conn.execute(
            text(
                "INSERT INTO relay_sweep_config "
                "(org_id, mention_handles, topic_queries, from_set, operator_handles, "
                " enabled, expiry_hours, updated_at) "
                "VALUES (:org_id, "
                "        COALESCE(:mention_handles, '[]'), "
                "        COALESCE(:topic_queries, '[]'), "
                "        COALESCE(:from_set, '[]'), "
                "        COALESCE(:operator_handles, '[]'), "
                "        COALESCE(:enabled, 0), "
                "        COALESCE(:expiry_hours, 36), "
                "        :now)"
            ),
            {
                "org_id": org_id,
                "mention_handles": mention_handles,
                "topic_queries": topic_queries,
                "from_set": from_set,
                "operator_handles": operator_handles,
                "enabled": enabled,
                "expiry_hours": expiry_hours,
                "now": now,
            },
        )
        return
    conn.execute(
        text(
            "UPDATE relay_sweep_config SET "
            "  mention_handles = COALESCE(:mention_handles, mention_handles), "
            "  topic_queries = COALESCE(:topic_queries, topic_queries), "
            "  from_set = COALESCE(:from_set, from_set), "
            "  operator_handles = COALESCE(:operator_handles, operator_handles), "
            "  enabled = COALESCE(:enabled, enabled), "
            "  expiry_hours = COALESCE(:expiry_hours, expiry_hours), "
            "  updated_at = :now "
            "WHERE org_id = :org_id"
        ),
        {
            "org_id": org_id,
            "mention_handles": mention_handles,
            "topic_queries": topic_queries,
            "from_set": from_set,
            "operator_handles": operator_handles,
            "enabled": enabled,
            "expiry_hours": expiry_hours,
            "now": now,
        },
    )


def mark_sweep_requested(conn: Connection, org_id: str, *, now: str | None = None) -> None:
    """Stamp ``sweep_requested_at`` = now (the "sweep now" ENQUEUE marker, §4).

    SableWeb's ``POST /api/v1/sweep/run`` calls this and returns 202; the next
    timer tick consumes the request because the §4 due-check compares
    ``sweep_requested_at > last_sweep_at`` and completion stamps ``last_sweep_at``
    (so one request triggers exactly one extra sweep). The caller MUST be inside
    an ``immediate_txn`` (this writes).
    """
    conn.execute(
        text(
            "UPDATE relay_sweep_config SET sweep_requested_at = :now "
            "WHERE org_id = :org_id"
        ),
        {"now": now or _utc_now_iso(), "org_id": org_id},
    )


def mark_sweep_completed(conn: Connection, org_id: str, *, now: str | None = None) -> None:
    """Stamp ``last_sweep_at`` = now at sweep completion (§4).

    Stamping ``last_sweep_at`` AUTO-CONSUMES any pending ``sweep_requested_at``
    (the due-check is ``sweep_requested_at > last_sweep_at``), so one "sweep now"
    yields exactly one extra sweep — never a loop. A failed/skipped sweep must NOT
    call this (so it retries next tick). The caller MUST be inside an
    ``immediate_txn`` (this writes).
    """
    conn.execute(
        text(
            "UPDATE relay_sweep_config SET last_sweep_at = :now "
            "WHERE org_id = :org_id"
        ),
        {"now": now or _utc_now_iso(), "org_id": org_id},
    )


def list_due_sweep_orgs(
    conn: Connection,
    *,
    now: str | None = None,
    heartbeat_within_hours: int = 2,
) -> list[str]:
    """Return org_ids due for a sweep this tick — the EXACT §4 selection.

    An org is due iff it is ``enabled`` AND has an operator heartbeat in
    ``relay_operator_heartbeat`` within ``heartbeat_within_hours`` AND
        (``last_sweep_at`` IS NULL
         OR now - last_sweep_at >= 1h
         OR (``sweep_requested_at`` IS NOT NULL AND sweep_requested_at > last_sweep_at)).
    Because completion stamps ``last_sweep_at`` (:func:`mark_sweep_completed`),
    the request clause auto-consumes — one "sweep now" => exactly one extra sweep.
    Read-only. ISO-8601-Z timestamps sort lexicographically == chronologically,
    so the string comparisons below are correct.
    """
    now_iso = now or _utc_now_iso()
    hb_cutoff = (
        datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        - timedelta(hours=int(heartbeat_within_hours))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    one_hour_ago = (
        datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        - timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        text(
            "SELECT c.org_id FROM relay_sweep_config c "
            "WHERE c.enabled = 1 "
            "  AND EXISTS ( "
            "      SELECT 1 FROM relay_operator_heartbeat h "
            "      WHERE h.org_id = c.org_id AND h.last_seen >= :hb_cutoff "
            "  ) "
            "  AND ( "
            "      c.last_sweep_at IS NULL "
            "      OR c.last_sweep_at <= :one_hour_ago "
            "      OR (c.sweep_requested_at IS NOT NULL "
            "          AND c.sweep_requested_at > c.last_sweep_at) "
            "  ) "
            "ORDER BY c.org_id"
        ),
        {"hb_cutoff": hb_cutoff, "one_hour_ago": one_hour_ago},
    ).fetchall()
    return [r[0] for r in rows]


# ------------------------------------------------------------------
# relay_tweets read-through cache (engagement_json / lang / author_followers)
# ------------------------------------------------------------------
def upsert_relay_tweet(
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
    engagement_json: str | None = None,
    lang: str | None = None,
    author_followers: int | None = None,
) -> int:
    """Write-through upsert of a tweet INCLUDING the 062 cache signals; return id.

    Superset of :func:`upsert_tweet` that also persists ``engagement_json`` /
    ``lang`` / ``author_followers`` (the heuristic pre-rank inputs, plan §3.7).
    Idempotent on the ``x_id`` UNIQUE index (a repeat call refreshes the cached
    fields incl. ``fetched_at``). The caller MUST be inside an ``immediate_txn``.
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
        "engagement_json": engagement_json,
        "lang": lang,
        "author_followers": (
            None if author_followers is None else int(author_followers)
        ),
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
                "  raw = :raw, "
                "  engagement_json = :engagement_json, "
                "  lang = :lang, "
                "  author_followers = :author_followers "
                "WHERE x_id = :x_id"
            ),
            {**params, "now": _utc_now_iso()},
        )
        return int(existing[0])
    row = conn.execute(
        text(
            "INSERT INTO relay_tweets "
            "(x_id, x_author_id, x_author_handle, text, media_urls, is_reply, "
            " in_reply_to_x_id, conversation_x_id, fetched_at, raw, engagement_json, "
            " lang, author_followers) "
            "VALUES (:x_id, :x_author_id, :x_author_handle, :text, :media_urls, "
            "        :is_reply, :in_reply_to_x_id, :conversation_x_id, :now, :raw, "
            "        :engagement_json, :lang, :author_followers) "
            "RETURNING id"
        ),
        {**params, "now": _utc_now_iso()},
    ).fetchone()
    return int(row[0])


def get_cached_relay_tweet(
    conn: Connection, x_id: str, *, ttl_hours: int = _TWEET_CACHE_TTL_HOURS
) -> dict | None:
    """Read-through cache lookup: return a tweet ONLY if fetched within ``ttl_hours``.

    Returns the ``relay_tweets`` row (incl. the 062 cache columns) iff it exists
    AND ``fetched_at`` is within ``ttl_hours`` (default 6h, plan §3.7) — otherwise
    ``None`` (so the sweep re-fetches a stale entry). Read-only.
    """
    row = conn.execute(
        text(
            "SELECT id, x_id, x_author_id, x_author_handle, text, media_urls, "
            "       is_reply, in_reply_to_x_id, conversation_x_id, fetched_at, raw, "
            "       engagement_json, lang, author_followers "
            "FROM relay_tweets WHERE x_id = :x_id"
        ),
        {"x_id": str(x_id)},
    ).fetchone()
    if row is None:
        return None
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=int(ttl_hours))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if row._mapping["fetched_at"] is None or row._mapping["fetched_at"] < cutoff:
        return None
    return dict(row._mapping)


# ------------------------------------------------------------------
# relay_operator_heartbeat (logged-in gating)
# ------------------------------------------------------------------
def write_operator_heartbeat(
    conn: Connection, *, org_id: str, operator_handle: str, now: str | None = None
) -> None:
    """Upsert the logged-in heartbeat for ``(org_id, operator_handle)`` (§3.6).

    The production writer is SableWeb (on each ``/ops/reply-assist`` load); this
    Python helper exists for tests + CLI. Idempotent on the composite PK. The
    caller MUST be inside an ``immediate_txn`` (this writes).
    """
    ts = now or _utc_now_iso()
    existing = conn.execute(
        text(
            "SELECT 1 FROM relay_operator_heartbeat "
            "WHERE org_id = :org_id AND operator_handle = :h"
        ),
        {"org_id": org_id, "h": operator_handle},
    ).fetchone()
    if existing is not None:
        conn.execute(
            text(
                "UPDATE relay_operator_heartbeat SET last_seen = :now "
                "WHERE org_id = :org_id AND operator_handle = :h"
            ),
            {"now": ts, "org_id": org_id, "h": operator_handle},
        )
        return
    conn.execute(
        text(
            "INSERT INTO relay_operator_heartbeat (org_id, operator_handle, last_seen) "
            "VALUES (:org_id, :h, :now)"
        ),
        {"org_id": org_id, "h": operator_handle, "now": ts},
    )


def has_recent_heartbeat(
    conn: Connection, org_id: str, *, within_hours: int = 2, now: str | None = None
) -> bool:
    """True iff any operator for ``org_id`` had a heartbeat within ``within_hours`` (§3.6)."""
    now_iso = now or _utc_now_iso()
    cutoff = (
        datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        - timedelta(hours=int(within_hours))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        text(
            "SELECT 1 FROM relay_operator_heartbeat "
            "WHERE org_id = :org_id AND last_seen >= :cutoff LIMIT 1"
        ),
        {"org_id": org_id, "cutoff": cutoff},
    ).fetchone()
    return row is not None


# ------------------------------------------------------------------
# GC (plan §4 step 7 / §3.9 retention) — invoked by the Slopper sweep
# ------------------------------------------------------------------
def gc_expired_opportunities(conn: Connection, *, now: str | None = None) -> dict:
    """GC the reply-opportunity feed (plan §4 step 7 / §3.9). Returns counts.

    Three retention actions, in order:
      1. EXPIRE: flip ``active`` rows whose ``expires_at`` has passed to
         ``'expired'``.
      2. PURGE: delete opportunities 7 days past ``expires_at`` (and their
         per-operator state, FK-safe) — but KEEP ``relay_opportunity_feedback``
         for 90 days (the learning corpus), so feedback rows older than 90 days
         are pruned first and any opportunity still carrying (<90d) feedback is
         retained until the feedback ages out.
      3. (feedback >90d pruned in step 2's first phase.)
    The caller MUST be inside an ``immediate_txn`` (this writes). Returns
    ``{expired, purged, feedback_pruned}``.
    """
    now_iso = now or _utc_now_iso()
    now_dt = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    purge_cutoff = (now_dt - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    feedback_cutoff = (now_dt - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Expire active rows past their expiry.
    expired = conn.execute(
        text(
            "UPDATE relay_reply_opportunities SET status = 'expired' "
            "WHERE status = 'active' "
            "  AND expires_at IS NOT NULL AND expires_at <= :now"
        ),
        {"now": now_iso},
    ).rowcount

    # 2a. Prune feedback older than 90 days (learning-corpus retention).
    feedback_pruned = conn.execute(
        text(
            "DELETE FROM relay_opportunity_feedback "
            "WHERE created_at IS NOT NULL AND created_at < :cutoff"
        ),
        {"cutoff": feedback_cutoff},
    ).rowcount

    # 2b. Purge opportunities 7d past expiry, but only those carrying NO retained
    #     (<90d) feedback — FK-safe: drop their per-operator state first.
    purgeable = [
        int(r[0])
        for r in conn.execute(
            text(
                "SELECT o.id FROM relay_reply_opportunities o "
                "WHERE o.expires_at IS NOT NULL AND o.expires_at < :purge_cutoff "
                "  AND NOT EXISTS ( "
                "      SELECT 1 FROM relay_opportunity_feedback f "
                "      WHERE f.opportunity_id = o.id "
                "  )"
            ),
            {"purge_cutoff": purge_cutoff},
        ).fetchall()
    ]
    purged = 0
    for oid in purgeable:
        conn.execute(
            text(
                "DELETE FROM relay_opportunity_operator_state WHERE opportunity_id = :oid"
            ),
            {"oid": oid},
        )
        conn.execute(
            text("DELETE FROM relay_reply_opportunities WHERE id = :oid"),
            {"oid": oid},
        )
        purged += 1

    return {"expired": expired, "purged": purged, "feedback_pruned": feedback_pruned}


# ------------------------------------------------------------------
# Migration 063 — relay_tweets embedding cache (P3 ranker, plan §8 P3)
#
# The P3 embedding ranker embeds each candidate once and re-ranks per operator.
# To avoid re-embedding every sweep, the vector is cached on relay_tweets keyed
# by the hydrated x_id, alongside the provider/model that produced it (so a model
# swap invalidates correctly — a caller compares the stored embedding_model
# against the model it is about to use and re-embeds on mismatch).
# ------------------------------------------------------------------
def get_tweet_embedding(conn: Connection, x_id: str) -> tuple[str, str | None] | None:
    """Return the cached ``(embedding_json, embedding_model)`` for ``x_id``, or None.

    ``None`` means either the tweet is not cached at all OR it has no embedding
    yet (``embedding_json IS NULL``) — in both cases the P3 ranker must embed it.
    When a row IS returned, ``embedding_json`` is the (non-NULL) vector blob and
    ``embedding_model`` is the producing provider/model (may be NULL on legacy
    rows). The caller decides whether ``embedding_model`` is still acceptable.
    Read-only.
    """
    row = conn.execute(
        text(
            "SELECT embedding_json, embedding_model FROM relay_tweets "
            "WHERE x_id = :x_id"
        ),
        {"x_id": str(x_id)},
    ).fetchone()
    if row is None or row._mapping["embedding_json"] is None:
        return None
    return (row._mapping["embedding_json"], row._mapping["embedding_model"])


def set_tweet_embedding(
    conn: Connection, x_id: str, embedding_json: str, model: str
) -> bool:
    """Write the cached embedding vector + model onto an EXISTING relay_tweets row.

    Updates ``embedding_json`` / ``embedding_model`` for the tweet keyed by
    ``x_id``. Does NOT insert a tweet (the sweep upserts the tweet via
    :func:`upsert_relay_tweet` first, then caches its embedding here). Returns
    ``True`` iff a row was updated (``False`` if no tweet with that ``x_id``
    exists). The caller MUST be inside an ``immediate_txn`` (this writes).
    """
    result = conn.execute(
        text(
            "UPDATE relay_tweets SET embedding_json = :ej, embedding_model = :em "
            "WHERE x_id = :x_id"
        ),
        {"ej": embedding_json, "em": model, "x_id": str(x_id)},
    )
    return int(result.rowcount or 0) > 0


# ------------------------------------------------------------------
# Migration 063 — learning queries for the Slopper scorer + quality dashboard
#
# All read-only and ORG-SCOPED (one client's signal never leaks into another's),
# and NONE select a cost column (the cost-never-in-response rule, plan §5 / §10.5).
# These back the §6 rolling in-context rubric examples, the §10.4/§6
# guardrail-refinement proposals, and the §8 P3 quality dashboard.
# ------------------------------------------------------------------
def recent_picked_skipped_examples(
    conn: Connection, org_id: str, limit: int = 20
) -> dict[str, list[dict]]:
    """Return recent PICKED vs SKIPPED opportunity examples for the §6 rubric.

    PICKED = an operator acted on the opportunity: either a generation was logged
    against it (``reply_suggestions.opportunity_id``) OR it received an
    opportunity-level thumbs-up (``relay_opportunity_feedback.thumb = 1`` with
    ``suggestion_id IS NULL``). SKIPPED = the opportunity went terminal without a
    pick — ``status IN ('dismissed','expired')``. Each row carries the target
    tweet text + ``sweep_source`` (the in-context rubric inputs). ORG-SCOPED,
    read-only, NO cost column. Returns ``{"picked": [...], "skipped": [...]}``,
    each newest-first and capped at ``limit``.
    """
    n = int(limit)
    picked = conn.execute(
        text(
            "SELECT o.id, o.tweet_id, o.sweep_source, o.score, o.created_at, "
            "       t.x_id, t.x_author_handle, t.text AS tweet_text "
            "FROM relay_reply_opportunities o "
            "JOIN relay_tweets t ON t.id = o.tweet_id "
            "WHERE o.org_id = :org_id "
            "  AND ( "
            "      EXISTS ( "
            "          SELECT 1 FROM reply_suggestions s "
            "          WHERE s.opportunity_id = o.id "
            "      ) "
            "      OR EXISTS ( "
            "          SELECT 1 FROM relay_opportunity_feedback f "
            "          WHERE f.opportunity_id = o.id "
            "            AND f.suggestion_id IS NULL AND f.thumb = 1 "
            "      ) "
            "  ) "
            "ORDER BY o.created_at DESC, o.id DESC "
            "LIMIT :limit"
        ),
        {"org_id": org_id, "limit": n},
    ).fetchall()
    skipped = conn.execute(
        text(
            "SELECT o.id, o.tweet_id, o.sweep_source, o.score, o.created_at, "
            "       o.status, t.x_id, t.x_author_handle, t.text AS tweet_text "
            "FROM relay_reply_opportunities o "
            "JOIN relay_tweets t ON t.id = o.tweet_id "
            "WHERE o.org_id = :org_id "
            "  AND o.status IN ('dismissed', 'expired') "
            "  AND NOT EXISTS ( "
            "      SELECT 1 FROM reply_suggestions s WHERE s.opportunity_id = o.id "
            "  ) "
            "  AND NOT EXISTS ( "
            "      SELECT 1 FROM relay_opportunity_feedback f "
            "      WHERE f.opportunity_id = o.id "
            "        AND f.suggestion_id IS NULL AND f.thumb = 1 "
            "  ) "
            "ORDER BY o.created_at DESC, o.id DESC "
            "LIMIT :limit"
        ),
        {"org_id": org_id, "limit": n},
    ).fetchall()
    return {
        "picked": [dict(r._mapping) for r in picked],
        "skipped": [dict(r._mapping) for r in skipped],
    }


def low_quality_suggestions(
    conn: Connection,
    org_id: str,
    limit: int = 20,
    *,
    tell_score_threshold: float = 0.6,
) -> list[dict]:
    """Return recent low-quality suggestions for the §10.4/§6 guardrail proposals.

    A suggestion is "low quality" if it carries a thumbs-DOWN
    (``relay_opportunity_feedback.thumb = -1`` with ``suggestion_id`` SET on it)
    AND/OR a high ``tell_score`` (``>= tell_score_threshold``). Each row carries
    the suggestion's ``tell_score`` / ``tell_flags_json`` plus the lowest thumb
    seen on it (NULL if none) and the source tweet text. ORG-SCOPED (via
    ``reply_suggestions.org_id``), read-only, NO cost column. Newest-first,
    capped at ``limit``.
    """
    rows = conn.execute(
        text(
            "SELECT s.id, s.operator_handle, s.source_tweet_id, s.source_text, "
            "       s.tell_score, s.tell_flags_json, s.opportunity_id, "
            "       s.generated_at, "
            "       MIN(f.thumb) AS min_thumb "
            "FROM reply_suggestions s "
            "LEFT JOIN relay_opportunity_feedback f "
            "  ON f.suggestion_id = s.id AND f.thumb = -1 "
            "WHERE s.org_id = :org_id "
            "GROUP BY s.id, s.operator_handle, s.source_tweet_id, s.source_text, "
            "         s.tell_score, s.tell_flags_json, s.opportunity_id, s.generated_at "
            "HAVING MIN(f.thumb) = -1 "
            "    OR (s.tell_score IS NOT NULL AND s.tell_score >= :thr) "
            "ORDER BY s.generated_at DESC, s.id DESC "
            "LIMIT :limit"
        ),
        {"org_id": org_id, "thr": float(tell_score_threshold), "limit": int(limit)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def quality_dashboard_aggregates(conn: Connection, org_id: str) -> dict:
    """Return the §8 P3 quality-dashboard rollup for an org. Read-only, NO cost.

    Three aggregates, all ORG-SCOPED:
      * ``tell_score_buckets`` — distribution of suggestion tell-scores in 0..1
        quintile buckets (``low`` 0-0.2, ``mid_low`` 0.2-0.4, ``mid`` 0.4-0.6,
        ``mid_high`` 0.6-0.8, ``high`` 0.8-1.0) + a ``null`` count for unlinted
        suggestions.
      * ``pick_rate_by_source`` — per ``sweep_source``: ``{total, picked,
        pick_rate}`` where picked = opportunity drafted (``reply_suggestions``)
        or opportunity-thumbed-up.
      * ``suggestion_thumbs`` — ``{up, down}`` counts of suggestion-level thumbs
        (``relay_opportunity_feedback`` rows with ``suggestion_id`` SET).
    """
    # --- tell-score distribution buckets ---
    buckets = {
        "null": 0,
        "low": 0,
        "mid_low": 0,
        "mid": 0,
        "mid_high": 0,
        "high": 0,
    }
    tell_rows = conn.execute(
        text(
            "SELECT "
            "  SUM(CASE WHEN tell_score IS NULL THEN 1 ELSE 0 END) AS n_null, "
            "  SUM(CASE WHEN tell_score >= 0 AND tell_score < 0.2 THEN 1 ELSE 0 END) AS n_low, "
            "  SUM(CASE WHEN tell_score >= 0.2 AND tell_score < 0.4 THEN 1 ELSE 0 END) AS n_mid_low, "
            "  SUM(CASE WHEN tell_score >= 0.4 AND tell_score < 0.6 THEN 1 ELSE 0 END) AS n_mid, "
            "  SUM(CASE WHEN tell_score >= 0.6 AND tell_score < 0.8 THEN 1 ELSE 0 END) AS n_mid_high, "
            "  SUM(CASE WHEN tell_score >= 0.8 THEN 1 ELSE 0 END) AS n_high "
            "FROM reply_suggestions WHERE org_id = :org_id"
        ),
        {"org_id": org_id},
    ).fetchone()
    if tell_rows is not None:
        m = tell_rows._mapping
        buckets["null"] = int(m["n_null"] or 0)
        buckets["low"] = int(m["n_low"] or 0)
        buckets["mid_low"] = int(m["n_mid_low"] or 0)
        buckets["mid"] = int(m["n_mid"] or 0)
        buckets["mid_high"] = int(m["n_mid_high"] or 0)
        buckets["high"] = int(m["n_high"] or 0)

    # --- pick-rate by sweep_source ---
    source_rows = conn.execute(
        text(
            "SELECT o.sweep_source AS sweep_source, "
            "  COUNT(*) AS total, "
            "  SUM(CASE WHEN ( "
            "        EXISTS (SELECT 1 FROM reply_suggestions s "
            "                WHERE s.opportunity_id = o.id) "
            "        OR EXISTS (SELECT 1 FROM relay_opportunity_feedback f "
            "                   WHERE f.opportunity_id = o.id "
            "                     AND f.suggestion_id IS NULL AND f.thumb = 1) "
            "      ) THEN 1 ELSE 0 END) AS picked "
            "FROM relay_reply_opportunities o "
            "WHERE o.org_id = :org_id "
            "GROUP BY o.sweep_source "
            "ORDER BY o.sweep_source"
        ),
        {"org_id": org_id},
    ).fetchall()
    pick_rate_by_source: dict[str, dict] = {}
    for r in source_rows:
        rm = r._mapping
        src = rm["sweep_source"] if rm["sweep_source"] is not None else "legacy"
        total = int(rm["total"] or 0)
        picked = int(rm["picked"] or 0)
        pick_rate_by_source[src] = {
            "total": total,
            "picked": picked,
            "pick_rate": round(picked / total, 3) if total else 0.0,
        }

    # --- suggestion thumbs up/down ---
    thumb_row = conn.execute(
        text(
            "SELECT "
            "  SUM(CASE WHEN f.thumb = 1 THEN 1 ELSE 0 END) AS up, "
            "  SUM(CASE WHEN f.thumb = -1 THEN 1 ELSE 0 END) AS down "
            "FROM relay_opportunity_feedback f "
            "JOIN reply_suggestions s ON s.id = f.suggestion_id "
            "WHERE s.org_id = :org_id AND f.suggestion_id IS NOT NULL"
        ),
        {"org_id": org_id},
    ).fetchone()
    up = int(thumb_row._mapping["up"] or 0) if thumb_row is not None else 0
    down = int(thumb_row._mapping["down"] or 0) if thumb_row is not None else 0

    return {
        "tell_score_buckets": buckets,
        "pick_rate_by_source": pick_rate_by_source,
        "suggestion_thumbs": {"up": up, "down": down},
    }


# ------------------------------------------------------------------
# Migration 064 — trending-story autopilot CRUD (relay_trending_stories)
# ------------------------------------------------------------------
# Stage A (sable.reply.stories) persists bursting-AND-relevant stories here with
# APP-LEVEL dedup (the same read-then-update philosophy as upsert_sweep_opportunity
# -- there is NO DB UNIQUE constraint, so a story recurring across sweeps collapses
# to ONE row whose relevance/momentum/last_seen update and whose member ids +
# monitor terms merge). Stage B auto-monitors live stories (decaying topic_queries)
# and decays expired ones to 'archived'. Stage C reads them org-scoped for the
# digest. relevance/momentum/summary are INTERPRETIVE; there is NO cost column.

# A new story matches an existing live one (=> UPDATE, not INSERT) when it shares
# the same normalized label, OR >= this many member tweet ids, OR >= this many
# normalized monitor terms. Tunable; deliberately conservative -- collapse the
# same story across sweeps without merging genuinely distinct stories.
_STORY_MEMBER_OVERLAP_MIN = 1
_STORY_TERM_OVERLAP_MIN = 2
_STORY_LIVE_STATUSES = ("emerging", "active", "decaying")
_VALID_STORY_STATUSES = ("emerging", "active", "decaying", "archived")


def _normalize_story_label(label: str) -> str:
    """Lowercase, drop non-alnum, collapse whitespace -- for fuzzy label match."""
    import re as _re

    return _re.sub(r"\s+", " ", _re.sub(r"[^a-z0-9 ]+", " ", (label or "").lower())).strip()


def _normalize_term(term: str) -> str:
    return " ".join((term or "").lower().split())


def _story_matches(
    existing: dict, *, label_norm: str, member_ids: set, term_norms: set
) -> bool:
    """True if ``existing`` (a relay_trending_stories row dict) is the SAME story."""
    import json as _json

    if label_norm and _normalize_story_label(existing.get("label") or "") == label_norm:
        return True
    try:
        ex_members = {int(x) for x in _json.loads(existing.get("member_tweet_ids_json") or "[]")}
    except (TypeError, ValueError):
        ex_members = set()
    if member_ids and len(ex_members & member_ids) >= _STORY_MEMBER_OVERLAP_MIN:
        return True
    try:
        ex_terms = {
            _normalize_term(t) for t in _json.loads(existing.get("monitor_terms_json") or "[]")
        }
    except (TypeError, ValueError):
        ex_terms = set()
    if term_norms and len(ex_terms & term_norms) >= _STORY_TERM_OVERLAP_MIN:
        return True
    return False


def list_live_trending_stories(conn: Connection, org_id: str) -> list[dict]:
    """Return the org's non-archived trending stories (newest activity first).

    Read-only. Used by the Stage A dedup pass (find a matching live story to
    update), by Stage B (auto-monitor the live set), and by the digest read.
    Returns full rows as dicts; ``member_tweet_ids_json`` / ``monitor_terms_json``
    are still raw JSON strings (caller json.loads).
    """
    rows = conn.execute(
        text(
            "SELECT id, org_id, label, summary, relevance, momentum, "
            "       member_tweet_ids_json, monitor_terms_json, status, "
            "       first_seen_at, last_seen_at, expires_at, created_at "
            "FROM relay_trending_stories "
            "WHERE org_id = :org_id AND status != 'archived' "
            "ORDER BY last_seen_at DESC, id DESC"
        ),
        {"org_id": org_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def upsert_trending_story(
    conn: Connection,
    *,
    org_id: str,
    label: str,
    summary: str | None = None,
    relevance: float | None = None,
    momentum: float | None = None,
    member_tweet_ids_json: str = "[]",
    monitor_terms_json: str = "[]",
    status: str = "emerging",
    expires_at: str | None = None,
    now: str | None = None,
) -> int:
    """App-level upsert of a detected trending story; return its id.

    Dedup is purely application-level (mirrors :func:`upsert_sweep_opportunity` --
    NO DB UNIQUE): scan the org's live (non-archived) stories and, if one matches
    by normalized label / member-id overlap / monitor-term overlap (see
    :func:`_story_matches`), UPDATE it in place -- MERGE the member ids + monitor
    terms, refresh relevance/momentum/summary, bump ``last_seen_at``, extend
    ``expires_at`` to the later of old/new, and lift status emerging->active on a
    re-sighting (never downgrade a live status). Otherwise INSERT a new row.

    ``member_tweet_ids_json`` / ``monitor_terms_json`` are passed as already-encoded
    JSON strings (relay/db convention). The caller MUST be inside an
    ``immediate_txn`` (this writes). label/summary/relevance/momentum/expires_at
    are interpretive; there is no cost column.
    """
    import json as _json

    if status not in _VALID_STORY_STATUSES:
        raise ValueError(
            f"unknown story status {status!r}; expected one of {_VALID_STORY_STATUSES}"
        )
    now = now or _utc_now_iso()
    try:
        member_ids = {int(x) for x in _json.loads(member_tweet_ids_json or "[]")}
    except (TypeError, ValueError):
        member_ids = set()
    try:
        terms = [str(t) for t in _json.loads(monitor_terms_json or "[]")]
    except (TypeError, ValueError):
        terms = []
    term_norms = {_normalize_term(t) for t in terms if _normalize_term(t)}
    label_norm = _normalize_story_label(label)

    for existing in list_live_trending_stories(conn, org_id):
        if not _story_matches(
            existing, label_norm=label_norm, member_ids=member_ids, term_norms=term_norms
        ):
            continue
        sid = int(existing["id"])
        # Merge member ids (existing order first, then new).
        try:
            merged_members = list(_json.loads(existing.get("member_tweet_ids_json") or "[]"))
        except (TypeError, ValueError):
            merged_members = []
        seen_m = {int(x) for x in merged_members}
        for m in member_ids:
            if m not in seen_m:
                merged_members.append(m)
                seen_m.add(m)
        # Merge monitor terms (normalized-dedup, preserve order).
        try:
            merged_terms = list(_json.loads(existing.get("monitor_terms_json") or "[]"))
        except (TypeError, ValueError):
            merged_terms = []
        seen_t = {_normalize_term(t) for t in merged_terms}
        for t in terms:
            tn = _normalize_term(t)
            if tn and tn not in seen_t:
                merged_terms.append(t)
                seen_t.add(tn)
        # expires_at = later of existing/new (ISO-Z lexical compare).
        new_exp = existing.get("expires_at")
        if expires_at and (not new_exp or expires_at > str(new_exp)):
            new_exp = expires_at
        # status: lift emerging->active on re-sighting; never downgrade a live one.
        new_status = existing.get("status") or "emerging"
        if new_status == "emerging":
            new_status = "active"
        conn.execute(
            text(
                "UPDATE relay_trending_stories SET "
                "  summary = COALESCE(:summary, summary), "
                "  relevance = COALESCE(:relevance, relevance), "
                "  momentum = COALESCE(:momentum, momentum), "
                "  member_tweet_ids_json = :members, "
                "  monitor_terms_json = :terms, "
                "  status = :status, "
                "  last_seen_at = :now, "
                "  expires_at = :expires_at "
                "WHERE id = :id"
            ),
            {
                "summary": summary,
                "relevance": relevance,
                "momentum": momentum,
                "members": _json.dumps(merged_members),
                "terms": _json.dumps(merged_terms),
                "status": new_status,
                "now": now,
                "expires_at": new_exp,
                "id": sid,
            },
        )
        return sid

    row = conn.execute(
        text(
            "INSERT INTO relay_trending_stories "
            "(org_id, label, summary, relevance, momentum, member_tweet_ids_json, "
            " monitor_terms_json, status, first_seen_at, last_seen_at, expires_at) "
            "VALUES (:org_id, :label, :summary, :relevance, :momentum, :members, "
            "        :terms, :status, :now, :now, :expires_at) "
            "RETURNING id"
        ),
        {
            "org_id": org_id,
            "label": label,
            "summary": summary,
            "relevance": relevance,
            "momentum": momentum,
            "members": member_tweet_ids_json or "[]",
            "terms": monitor_terms_json or "[]",
            "status": status,
            "now": now,
            "expires_at": expires_at,
        },
    ).fetchone()
    return int(row[0])


def decay_trending_stories(conn: Connection, org_id: str, *, now: str | None = None) -> int:
    """Archive trending stories whose monitoring window has expired; return count.

    A story whose ``expires_at`` has passed (momentum faded -- Stage B stopped
    extending it) is moved to ``'archived'`` so it drops out of the digest and
    stops being auto-monitored. Stories with NULL ``expires_at`` are left alone.
    The caller MUST be inside an ``immediate_txn`` (this writes).
    """
    now = now or _utc_now_iso()
    result = conn.execute(
        text(
            "UPDATE relay_trending_stories SET status = 'archived' "
            "WHERE org_id = :org_id AND status != 'archived' "
            "  AND expires_at IS NOT NULL AND expires_at <= :now"
        ),
        {"org_id": org_id, "now": now},
    )
    return result.rowcount if result.rowcount is not None else 0
