"""Chat-binding lifecycle (SableRelay PLAN §15.3, correctness-critical).

Three transitions, each applied inside ONE ``BEGIN IMMEDIATE`` (no external
API call inside the transaction — PLAN §3.1):

  1. **Telegram supergroup migration** (:func:`migrate_chat_binding`). When a
     Telegram group is upgraded to a supergroup it gets a NEW ``chat_id`` and
     emits ``migrate_to_chat_id``. We must re-point the binding to the new id
     AND re-point in-flight submissions' ``source_chat_id`` — otherwise
     reactions in the migrated supergroup miss ``relay_submissions_control_lookup``
     and quorum silently breaks (the §15.3 "submission update is critical" note).

  2. **Bot kicked / removed** (:func:`kick_chat_binding`). A ``my_chat_member``
     update where ``new_chat_member.status`` is ``kicked`` / ``left`` flips the
     binding to ``status='kicked'``, expires this chat's pending submissions,
     and kills its in-flight publication jobs (``state='dead'``) so the
     publisher stops sending to a chat we've been removed from.

  3. **Discord 403/404 binding-flip** (:func:`flip_discord_binding_on_failure`).
     A channel-deleted / no-access send failure increments a per-destination
     consecutive-failure counter; after ``kicked_after_consecutive_failures``
     (default 5, from ``relay_clients.config.publish``) the binding flips to
     ``kicked`` and the same cleanup runs.

All three reuse :func:`~sable_platform.relay.bot.txn.immediate_txn`. The
admin-notify side effect (notify ``RELAY_ADMIN_TG_CHAT_ID``) is an EXTERNAL API
call and therefore happens OUTSIDE the transaction — these functions return a
small result object the caller uses to decide whether to alert, so the API call
never leaks into the ``BEGIN IMMEDIATE`` (the C2.2 audit invariant).

Per the LOCKED C2.1 §5.3 layering contract, this handler module embeds NO raw
SQL: every lifecycle statement is a named, ``text()``-parameterized helper in
:mod:`sable_platform.relay.db` (``select_active_binding`` / ``mark_binding_migrated``
/ ``clone_active_binding`` / ``repoint_submissions_chat_id`` / ``flip_binding_kicked``
/ ``expire_pending_submissions`` / ``kill_inflight_jobs`` / ``read_client_config``).
Those helpers are dialect-agnostic — named binds (not ``?``), a Python ISO-Z
timestamp param (not ``strftime``), ``result.rowcount`` (not ``changes()``) — so
the §15.3 transitions run unchanged on the live Postgres pool on the VPS, not
just on in-memory SQLite.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)

DEFAULT_KICKED_AFTER_CONSECUTIVE_FAILURES = 5


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of a supergroup-migration re-point."""

    migrated: bool
    org_id: str | None
    submissions_repointed: int


@dataclass(frozen=True)
class KickResult:
    """Outcome of a bot-kicked / Discord-flip cleanup."""

    flipped: bool
    org_id: str | None
    submissions_expired: int
    jobs_killed: int


# ---------------------------------------------------------------------------
# 1. Telegram supergroup migration
# ---------------------------------------------------------------------------
def migrate_chat_binding(
    conn: Connection,
    old_chat_id: str,
    new_chat_id: str,
) -> MigrationResult:
    """Re-point a Telegram binding from ``old_chat_id`` to ``new_chat_id``.

    PLAN §15.3 migration handler, in one ``BEGIN IMMEDIATE``:
      * mark the active old binding ``status='migrated'`` with
        ``superseded_by_chat_id=new_chat_id``;
      * clone an ``active`` binding at the new chat id (same org/platform/role);
      * re-point in-flight submissions (``pending`` / ``ready_to_publish``)
        ``source_chat_id`` to the new id so the control-message lookup still
        resolves them.

    Returns a :class:`MigrationResult`. If there was no active telegram binding
    at ``old_chat_id`` (``migrated=False``), nothing is changed.
    """
    old = str(old_chat_id)
    new = str(new_chat_id)
    with immediate_txn(conn):
        binding = relay_db.select_active_binding(conn, "telegram", old)
        if binding is None:
            return MigrationResult(migrated=False, org_id=None, submissions_repointed=0)
        org_id, role = binding[0], binding[1]

        relay_db.mark_binding_migrated(conn, old, new)
        relay_db.clone_active_binding(conn, org_id, new, role)
        repointed = relay_db.repoint_submissions_chat_id(conn, old, new)
    return MigrationResult(
        migrated=True, org_id=org_id, submissions_repointed=repointed
    )


# ---------------------------------------------------------------------------
# 2. Bot kicked / removed (Telegram my_chat_member)
# ---------------------------------------------------------------------------
def kick_chat_binding(
    conn: Connection,
    chat_id: str,
    *,
    platform: str = "telegram",
    last_error: str = "bot removed",
) -> KickResult:
    """Flip a binding to ``kicked`` and halt that chat's in-flight work.

    PLAN §15.3 bot-kicked handler, in one ``BEGIN IMMEDIATE``:
      * flip the active binding to ``status='kicked'`` with ``last_error``;
      * expire this chat's pending submissions (``status='expired'``);
      * kill its in-flight publication jobs (``state='dead'`` with
        ``last_error='destination chat kicked the bot'``) so the publisher
        stops sending. ``'dead'`` is the only CHECK-allowed halted state in the
        LOCKED §3.1 set — we do NOT invent a ``'halted'`` state.

    Returns a :class:`KickResult`; ``flipped=False`` if there was no active
    binding at ``chat_id`` (idempotent — a redelivered kick event is a no-op).
    """
    cid = str(chat_id)
    with immediate_txn(conn):
        binding = relay_db.select_active_binding(conn, platform, cid)
        if binding is None:
            return KickResult(
                flipped=False, org_id=None, submissions_expired=0, jobs_killed=0
            )
        org_id = binding[0]

        relay_db.flip_binding_kicked(conn, platform, cid, last_error)
        # Expire pending submissions sourced from this chat (TG only — Discord
        # submissions are not sourced by chat_id in v1, but the filter is
        # harmless there).
        submissions_expired = relay_db.expire_pending_submissions(conn, cid)
        # Kill pending destination jobs that target this chat.
        jobs_killed = relay_db.kill_inflight_jobs(conn, platform, cid)
    return KickResult(
        flipped=True,
        org_id=org_id,
        submissions_expired=submissions_expired,
        jobs_killed=jobs_killed,
    )


# ---------------------------------------------------------------------------
# 3. Discord 403/404 binding-flip
# ---------------------------------------------------------------------------
def _kicked_after_threshold(conn: Connection, org_id: str) -> int:
    """Read ``relay_clients.config.publish.kicked_after_consecutive_failures``.

    Falls back to the default (5) when the org / key is absent or malformed.
    """
    raw_config = relay_db.read_client_config(conn, org_id)
    if not raw_config:
        return DEFAULT_KICKED_AFTER_CONSECUTIVE_FAILURES
    try:
        cfg = json.loads(raw_config)
    except (TypeError, ValueError):
        return DEFAULT_KICKED_AFTER_CONSECUTIVE_FAILURES
    publish = cfg.get("publish") or {}
    val = publish.get("kicked_after_consecutive_failures")
    if isinstance(val, int) and val > 0:
        return val
    return DEFAULT_KICKED_AFTER_CONSECUTIVE_FAILURES


def flip_discord_binding_on_failure(
    conn: Connection,
    org_id: str,
    chat_id: str,
    consecutive_failures: int,
) -> KickResult:
    """Flip a Discord binding to ``kicked`` once it has failed too many times.

    PLAN §15.3 Discord 403/404 path: a channel-deleted / no-access send raises
    403/404; the publisher increments a per-destination consecutive-failure
    counter and calls this with the new count. Once
    ``consecutive_failures >= kicked_after_consecutive_failures`` (default 5,
    from ``relay_clients.config.publish``), the binding flips to ``kicked`` and
    the same cleanup as a TG kick runs — all inside one ``BEGIN IMMEDIATE``.

    Below the threshold this is a no-op (``flipped=False``) — the publisher
    keeps retrying; the binding only flips when the threshold is crossed.
    """
    threshold = _kicked_after_threshold(conn, org_id)
    if consecutive_failures < threshold:
        return KickResult(
            flipped=False, org_id=org_id, submissions_expired=0, jobs_killed=0
        )
    return kick_chat_binding(
        conn,
        chat_id,
        platform="discord",
        last_error=f"discord 403/404: {consecutive_failures} consecutive failures",
    )
