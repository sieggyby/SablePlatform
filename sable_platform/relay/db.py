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

from datetime import datetime, timezone

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
