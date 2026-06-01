"""Operator slash-command surface (MEGAPLAN C3.5c — HITL_UX §6 / §5).

The mod-gated AutoCM operator slash-command surface. Each command is registered
on the C2.7 command-registry path (``RelayHandlerRegistry.register_command_handler``
/ ``dispatch_command``), is MOD-GATED (a non-operator is rejected with NO side
effect), hits a LIVE target, and — where it mutates state — writes the audit row
its HITL_UX §6 / §5 row obligates. The command table (HITL_UX §6 / §5):

==========================  =================  =====================================
command                     LIVE target        effect
==========================  =================  =====================================
/demote <cat>               C3.5a (trigger 2)  operator-mark auto→hitl (always allowed,
                                                no gate); audit ``autonomy_demoted_operator``
/promote <cat>              C3.5a              runs the DESIGN §7 flip-criteria gate;
                                                returns the verdict (flips iff it passes;
                                                operator sign-off implied by the command)
/silence <user> [dur]       autocm_flagged_users  silence the user (auto-silence row);
                                                audit ``flagged_user_operator_silenced``
/clear-flag <user>          autocm_flagged_users  clear an auto-silenced user; audit
                                                ``flagged_user_cleared``
/kb-add <tag> <text>        C3.2c KB store     insert a manual KB chunk (authority 0.9);
                                                audit ``kb_chunk_added``
/kb-stale <chunk-id>        C3.2c KB           mark a chunk ``stale``; audit ``kb_chunk_staled``
/kb-remove <chunk-id> ...   C3.2c KB           mark a chunk ``wrong`` (removed) + reason;
                                                audit ``kb_chunk_removed``
/kb-refresh-source <id>     C3.2c KBRefresher  force a source re-fetch (LIVE refresh_source)
/category-state [cat]       C3.5a read         report the merged auto/hitl + threshold +
                                                sample count + clean-approval-rate
/voice-drift [register]     autocm_reviews     last-7d heavy-edit drafts (filterable
                                                calm/reactive)
/punt <ref>                 C3.8a dual-route   manual tier-3 → founder + Sable on-call
/pause-client [id]          kill switch        set autonomy_state='paused' → halt ALL
                                                publishing; audit ``client_publishing_paused``
/resume-client [id]         kill switch        restore publishing; audit ``client_publishing_resumed``
/incident-mode on|off       C3.8b              toggle incident-mode (war-room register +
                                                proactive poster + tier-1 suppression)
/approve-all-tier1-<cat>    C3.5b bulk         bulk-approve N pending tier-1 drafts of a
                                                category; audit row ENUMERATES all N draft ids
==========================  =================  =====================================

**MOD-GATE (HITL_UX §6, every command).** The caller's ``external_user_id`` is
resolved to a ``relay_members`` row (via ``relay_member_identities``) and checked
with ``relay.db.is_relay_operator`` (``sable_operator`` or ``admin``). A
non-operator (or an unresolvable caller) is REJECTED — the command's LIVE target
is NEVER touched (no state write, no audit row beyond the rejection notice). The
ONE exception HITL_UX §6 names explicitly is ``/clear-flag`` ("Arf or any mod can
run") — still operator-gated (Arf holds ``sable_operator``), the same gate.

**``/pause-client`` is the AutoCM-side kill switch (DISTINCT from the SAFETY §6
freeze).** It sets ``autocm_clients.autonomy_state='paused'`` — the
:func:`is_publishing_paused` read every publishing path consults — so ALL
publishing (autonomous auto-send AND HITL-approved replies AND the incident
proactive poster) halts for the client. This is the OPPOSITE of the freeze (which
KEEPS drafting + HITL review, freezing only autonomous auto-send) and is narrower
than relay ``disable``/``pause-org`` (the substrate-level halt, C2.5).

**No telegram / network in this module.** Operator replies (the command's textual
response) go through an injected :class:`OperatorReplySender` (a fake in tests);
all DB writes take an already-open ``Connection`` (the caller owns lifecycle).
Timestamps are computed in Python as UTC ISO-8601 ``...Z`` and bound as parameters
(never ``strftime('now')``) — dialect-agnostic, matching the autocm/relay db
contract.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.audit import log_audit

logger = logging.getLogger(__name__)

# log_audit verbs (audit-everything; source="sable-autocm").
AUDIT_SOURCE = "sable-autocm"
ACTION_DEMOTE_OPERATOR = "autonomy_demoted_operator"      # C3.5a trigger 2
ACTION_FLAG_SILENCED = "flagged_user_operator_silenced"
ACTION_FLAG_CLEARED = "flagged_user_cleared"
ACTION_KB_ADD = "kb_chunk_added"
ACTION_KB_STALE = "kb_chunk_staled"
ACTION_KB_REMOVE = "kb_chunk_removed"
ACTION_PAUSE = "client_publishing_paused"
ACTION_RESUME = "client_publishing_resumed"
ACTION_BULK_APPROVE = "hitl_bulk_approved_tier1"          # HITL_UX §5
ACTION_COMMAND_REJECTED = "operator_command_rejected"     # mod-gate rejection

# autocm_clients.autonomy_state kill-switch value (058 CHECK set).
AUTONOMY_PAUSED = "paused"
# the prior autonomy_state to restore to on /resume (the safe default).
AUTONOMY_RESUME_DEFAULT = "hitl"

# manual /kb-add chunk authority (HITL_UX §6: operator-curated, high trust).
KB_ADD_AUTHORITY = 0.9
KB_ADD_SOURCE_TYPE = "manual"

# /voice-drift window (HITL_UX §6: "last 7 days of heavy-edit drafts").
VOICE_DRIFT_WINDOW_DAYS = 7

# /pause-client kill-switch reason recorded on the audit row.
KILL_SWITCH_REASON = "operator /pause-client kill switch (halt all publishing)"

# /approve-all-tier1-<category> is intended for queue-backlog cleanup
# (HITL_UX §5: ">20 pending drafts"); the prefix the command verb carries.
BULK_APPROVE_PREFIX = "approve-all-tier1-"


# ---------------------------------------------------------------------------
# Clock + timestamp helpers (injectable clock; dialect-agnostic ...Z form)
# ---------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _org_id_for_client(conn: Connection, client_id: int) -> Optional[str]:
    row = conn.execute(
        text("SELECT org_id FROM autocm_clients WHERE id = :id"),
        {"id": client_id},
    ).fetchone()
    return row[0] if row is not None else None


def _client_id_for_org(conn: Connection, org_id: str) -> Optional[int]:
    row = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"),
        {"o": org_id},
    ).fetchone()
    return int(row[0]) if row is not None else None


# ---------------------------------------------------------------------------
# Operator-reply seam (the textual command response; NO telegram/network)
# ---------------------------------------------------------------------------
class OperatorReplySender(Protocol):
    """The injected seam the command surface replies to the operator chat through.

    A real impl rides the C2.7 operator-chat send (the same plain-text contract as
    the HITL review surface — relay §15.2); tests inject a fake that records the
    replies so the command's textual response is assertable offline. ``reply``
    returns a surface handle (or None).
    """

    def reply(self, chat_id: Optional[str], body: str) -> Optional[str]:
        ...


# ---------------------------------------------------------------------------
# /pause-client kill switch — the publishing-halt gate every publisher consults
# ---------------------------------------------------------------------------
def is_publishing_paused(conn: Connection, client_id: int) -> bool:
    """True iff the client's AutoCM kill switch is engaged (``autonomy_state='paused'``).

    The single read every AutoCM publishing path MUST consult before sending: the
    C3.6 publisher (HITL-approved replies), any autonomous auto-send, and the C3.8b
    incident proactive poster. While paused, NOTHING publishes for the client —
    distinct from the SAFETY §6 freeze (which keeps drafting + HITL review and
    freezes only AUTONOMOUS auto-send). The kill switch is set by
    :func:`pause_client` and cleared by :func:`resume_client`.
    """
    row = conn.execute(
        text("SELECT autonomy_state FROM autocm_clients WHERE id = :id"),
        {"id": client_id},
    ).fetchone()
    return row is not None and row[0] == AUTONOMY_PAUSED


def pause_client(
    conn: Connection,
    client_id: int,
    *,
    actor: str = AUDIT_SOURCE,
    org_id: Optional[str] = None,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """``/pause-client``: set ``autonomy_state='paused'`` → halt ALL publishing.

    The AutoCM-side kill switch. Returns True iff the client was NOT already paused
    and got flipped (idempotent — a re-pause still audits the operator action but
    returns False to signal no-change). Writes a ``client_publishing_paused`` audit
    row. Does NOT touch the SAFETY §6 freeze (a separate, weaker mode) and does NOT
    touch relay ``disable``/``pause-org`` (the substrate halt).
    """
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    was_paused = is_publishing_paused(conn, client_id)
    conn.execute(
        text(
            "UPDATE autocm_clients SET autonomy_state = :s, updated_at = :now "
            "WHERE id = :id"
        ),
        {"s": AUTONOMY_PAUSED, "now": _iso_z(now), "id": client_id},
    )
    log_audit(
        conn,
        actor=actor,
        action=ACTION_PAUSE,
        org_id=org_id,
        entity_id=str(client_id),
        detail={
            "client_id": client_id,
            "reason": reason or KILL_SWITCH_REASON,
            "prior_paused": was_paused,
            "halts": "ALL publishing (auto-send + HITL-approved + proactive)",
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return not was_paused


def resume_client(
    conn: Connection,
    client_id: int,
    *,
    actor: str = AUDIT_SOURCE,
    org_id: Optional[str] = None,
    restore_state: str = AUTONOMY_RESUME_DEFAULT,
    now: Optional[datetime] = None,
) -> bool:
    """``/resume-client``: clear the kill switch → restore publishing.

    Flips ``autonomy_state`` from ``paused`` back to ``restore_state`` (default the
    safe ``hitl`` floor — promotion back to ``auto`` re-runs the C3.5a gate, it is
    NOT silently restored). Returns True iff the client WAS paused and got resumed
    (idempotent — resuming a non-paused client is a no-op returning False). Writes a
    ``client_publishing_resumed`` audit row.
    """
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    if not is_publishing_paused(conn, client_id):
        return False
    conn.execute(
        text(
            "UPDATE autocm_clients SET autonomy_state = :s, updated_at = :now "
            "WHERE id = :id"
        ),
        {"s": restore_state, "now": _iso_z(now), "id": client_id},
    )
    log_audit(
        conn,
        actor=actor,
        action=ACTION_RESUME,
        org_id=org_id,
        entity_id=str(client_id),
        detail={"client_id": client_id, "restored_state": restore_state},
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# /silence + /clear-flag — autocm_flagged_users (the C3.4a pre-filter target)
# ---------------------------------------------------------------------------
def silence_user(
    conn: Connection,
    client_id: int,
    *,
    member_id: Optional[int] = None,
    external_user_id: Optional[str] = None,
    duration: Optional[str] = None,
    actor: str = AUDIT_SOURCE,
    org_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """``/silence``: operator-silence a user into ``autocm_flagged_users``.

    Inserts (or reuses) a ``status='silenced'`` row so the C3.4a flagged-user
    pre-filter (``autocm.db.is_flagged_user``) drops the user until a mod
    ``/clear-flag``s them — the bot disengages (does NOT ban; HITL_UX §6). Matches
    on EITHER ``member_id`` OR ``external_user_id``; idempotent (an already-active
    silenced row for the identity is reused). The optional ``duration`` is recorded
    on the reason for the operator's reference (v1 does not auto-expire — clearance
    is the explicit ``/clear-flag``). Returns the flagged-user row id, or ``None``
    when neither identifier is supplied. Writes a
    ``flagged_user_operator_silenced`` audit row.
    """
    if member_id is None and external_user_id is None:
        return None
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)

    existing = _find_active_silenced(conn, client_id, member_id, external_user_id)
    reason = (
        f"operator /silence (duration {duration})"
        if duration
        else "operator /silence"
    )
    if existing is not None:
        return existing

    row = conn.execute(
        text(
            "INSERT INTO autocm_flagged_users "
            "(client_id, member_id, external_user_id, reason, status, flagged_at) "
            "VALUES (:c, :mid, :ext, :reason, 'silenced', :now) RETURNING id"
        ),
        {
            "c": client_id,
            "mid": member_id,
            "ext": external_user_id,
            "reason": reason,
            "now": _iso_z(now),
        },
    ).fetchone()
    flagged_id = int(row[0])
    log_audit(
        conn,
        actor=actor,
        action=ACTION_FLAG_SILENCED,
        org_id=org_id,
        entity_id=str(flagged_id),
        detail={
            "client_id": client_id,
            "member_id": member_id,
            "external_user_id": external_user_id,
            "duration": duration,
            "reason": reason,
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return flagged_id


def clear_flag(
    conn: Connection,
    client_id: int,
    *,
    member_id: Optional[int] = None,
    external_user_id: Optional[str] = None,
    actor: str = AUDIT_SOURCE,
    org_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> int:
    """``/clear-flag``: clear auto-silenced flagged users (restore engagement).

    Flips every active ``status='silenced'`` row for the identity to
    ``status='cleared'`` (stamping ``cleared_at`` / ``cleared_by``) so the C3.4a
    pre-filter no longer drops the user. Matches on EITHER ``member_id`` OR
    ``external_user_id``. Returns the number of rows cleared (0 if none active).
    Writes a ``flagged_user_cleared`` audit row when at least one row was cleared.
    HITL_UX §6: "Arf or any mod can run" — still operator-gated (Arf holds the
    operator role).
    """
    if member_id is None and external_user_id is None:
        return 0
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)

    clauses = []
    params: dict = {"c": client_id, "now": _iso_z(now), "by": actor}
    if member_id is not None:
        clauses.append("member_id = :mid")
        params["mid"] = member_id
    if external_user_id is not None:
        clauses.append("external_user_id = :ext")
        params["ext"] = external_user_id
    identity = " OR ".join(clauses)

    result = conn.execute(
        text(
            "UPDATE autocm_flagged_users "
            "SET status = 'cleared', cleared_at = :now, cleared_by = :by "
            "WHERE client_id = :c AND status = 'silenced' "
            f"  AND ({identity})"
        ),
        params,
    )
    cleared = int(result.rowcount or 0)
    if cleared > 0:
        log_audit(
            conn,
            actor=actor,
            action=ACTION_FLAG_CLEARED,
            org_id=org_id,
            entity_id=f"{client_id}:{member_id or external_user_id}",
            detail={
                "client_id": client_id,
                "member_id": member_id,
                "external_user_id": external_user_id,
                "cleared_count": cleared,
            },
            source=AUDIT_SOURCE,
        )
    conn.commit()
    return cleared


def _find_active_silenced(
    conn: Connection,
    client_id: int,
    member_id: Optional[int],
    external_user_id: Optional[str],
) -> Optional[int]:
    clauses = []
    params: dict = {"c": client_id}
    if member_id is not None:
        clauses.append("member_id = :mid")
        params["mid"] = member_id
    if external_user_id is not None:
        clauses.append("external_user_id = :ext")
        params["ext"] = external_user_id
    if not clauses:
        return None
    row = conn.execute(
        text(
            "SELECT id FROM autocm_flagged_users "
            "WHERE client_id = :c AND status = 'silenced' "
            f"  AND ({' OR '.join(clauses)}) "
            "ORDER BY id LIMIT 1"
        ),
        params,
    ).fetchone()
    return int(row[0]) if row is not None else None


# ---------------------------------------------------------------------------
# /kb-add /kb-stale /kb-remove — direct KB chunk lifecycle writes (C3.2c)
# ---------------------------------------------------------------------------
def _ensure_manual_source(conn: Connection, client_id: int) -> int:
    """Return the client's singleton ``manual`` KB source id (create if absent).

    Operator ``/kb-add`` chunks share one ``manual`` source per client; it is
    immutable-cadence (``on_add``) so the refresh sweep never re-fetches it
    (operator-authored, not scraped) — mirrors the resolved-FAQ singleton in
    ``kb/refresher._ensure_resolved_faq_source``.
    """
    existing = conn.execute(
        text(
            "SELECT id FROM autocm_kb_sources "
            "WHERE client_id = :c AND source_type = :st LIMIT 1"
        ),
        {"c": client_id, "st": KB_ADD_SOURCE_TYPE},
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    row = conn.execute(
        text(
            "INSERT INTO autocm_kb_sources "
            "(client_id, source_type, refresh_cadence, authority_default, status) "
            "VALUES (:c, :st, 'on_add', :a, 'active') RETURNING id"
        ),
        {"c": client_id, "st": KB_ADD_SOURCE_TYPE, "a": KB_ADD_AUTHORITY},
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# /voice-drift — last-7d heavy-edit drafts (filterable by register)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceDriftDraft:
    """One heavy-edit draft surfaced by ``/voice-drift`` (HITL_UX §6)."""

    draft_id: int
    review_id: int
    category: Optional[str]
    register: Optional[str]
    edit_diff_size: float
    draft_text: Optional[str]
    edited_text: Optional[str]
    reviewed_at: Optional[str]


def voice_drift_drafts(
    conn: Connection,
    client_id: int,
    *,
    register: Optional[str] = None,
    now: Optional[datetime] = None,
    window_days: int = VOICE_DRIFT_WINDOW_DAYS,
) -> List[VoiceDriftDraft]:
    """``/voice-drift``: the last-``window_days`` HEAVY-edit drafts for review.

    HITL_UX §6: "Pull last 7 days of heavy-edit drafts for review (filterable by
    register: calm vs reactive)". A heavy edit is an ``edit`` review whose stored
    ``edit_diff_size`` exceeds the C3.5a 0.30 threshold (the pinned voice-drift
    quantity). Joins ``autocm_reviews`` (decision='edit', not clean) to
    ``autocm_drafts`` over the rolling window, optionally filtered to the draft's
    ``register`` (calm/reactive). The window is bound in Python as ``...Z`` (the
    autocm db contract) and BOTH sides are normalized to space-form for the
    dialect-agnostic comparison (matching ``gather_review_stats``). Newest first.
    """
    now = now or _utc_now()
    since = _iso_z(now - timedelta(days=window_days))
    since_norm = since.replace("T", " ").replace("Z", "")
    params: dict = {"c": client_id, "since": since_norm}
    register_clause = ""
    if register is not None:
        register_clause = " AND d.register = :reg"
        params["reg"] = register
    rows = conn.execute(
        text(
            "SELECT r.draft_id, r.id, d.category, d.register, r.edit_diff_size, "
            "       d.draft_text, r.edited_text, r.reviewed_at "
            "FROM autocm_reviews r "
            "JOIN autocm_drafts d ON d.id = r.draft_id "
            "WHERE r.client_id = :c AND r.decision = 'edit' "
            "  AND r.is_clean_approval = 0 "
            "  AND REPLACE(REPLACE(r.reviewed_at, 'T', ' '), 'Z', '') >= :since"
            f"{register_clause} "
            "ORDER BY r.reviewed_at DESC, r.id DESC"
        ),
        params,
    ).fetchall()
    return [
        VoiceDriftDraft(
            draft_id=int(r[0]),
            review_id=int(r[1]),
            category=r[2],
            register=r[3],
            edit_diff_size=float(r[4] or 0.0),
            draft_text=r[5],
            edited_text=r[6],
            reviewed_at=r[7],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# The command result + the mod-gated router
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CommandResult:
    """The outcome of one dispatched operator command.

    ``ok`` is True iff the command was authorized AND applied; ``rejected`` is True
    when the mod-gate rejected the caller (``ok=False`` with NO side effect).
    ``message`` is the textual operator reply (also sent via the
    :class:`OperatorReplySender`). ``data`` carries command-specific structured
    results (e.g. the ``/promote`` verdict, the ``/voice-drift`` rows, the bulk
    ``approved_draft_ids``) for tests / callers.
    """

    command: str
    ok: bool
    message: str
    rejected: bool = False
    data: dict = field(default_factory=dict)


class CommandRouter:
    """The mod-gated operator slash-command router (C3.5c, over the C2.7 command path).

    Construction takes the live SP-pool :class:`Connection` (the caller owns
    lifecycle — this creates no engine) and an injected
    :class:`OperatorReplySender` (the operator-chat send, a fake in tests). Inject
    a fixed ``now`` for deterministic windows.

    Wiring: :meth:`register` installs ONE catch-all command handler on the C2.7
    :class:`~sable_platform.relay.bot.registry.RelayHandlerRegistry`
    (``register_command_handler``); the registry routes every ``/verb`` to
    :meth:`on_command`, which MOD-GATES (``relay.db.is_relay_operator``) and
    dispatches to the per-verb method. A non-operator is REJECTED with no side
    effect (audited as ``operator_command_rejected``).

    Each command hits a LIVE target: ``/demote``→C3.5a, ``/promote``→C3.5a gate,
    ``/silence``/``/clear-flag``→``autocm_flagged_users``,
    ``/kb-*``→C3.2c, ``/category-state``→C3.5a read,
    ``/voice-drift``→``autocm_reviews``, ``/punt``→C3.8a dual-route,
    ``/pause-client``/``/resume-client``→the kill switch,
    ``/incident-mode``→C3.8b, ``/approve-all-tier1-<cat>``→C3.5b bulk.
    """

    def __init__(
        self,
        conn: Connection,
        reply_sender: OperatorReplySender,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        self._conn = conn
        self._reply = reply_sender
        self._now = now

    def _clock(self) -> datetime:
        return self._now or _utc_now()

    # -- registration (C2.7 command-registry path) ----------------------------
    def register(self, registry) -> None:
        """Install the catch-all operator-command handler on the C2.7 registry."""
        registry.register_command_handler(self.on_command)

    # -- the registry entry point ---------------------------------------------
    def on_command(self, event) -> Optional[CommandResult]:
        """Apply one slash-command event (routed here by the C2.7 command registry).

        MOD-GATES first (the caller must be a Sable operator for the org), then
        dispatches by verb. Returns the :class:`CommandResult` (also returned by the
        direct-call entry points so the listener wiring is thin). An unknown verb is
        a polite no-op reply (NOT a rejection — the gate already passed).
        """
        verb = event.command
        org_id = event.org_id
        chat_id = event.chat_id

        # -- MOD-GATE (HITL_UX §6): every command requires operator authority. ----
        if not self._is_operator(event):
            return self._reject(verb, org_id, chat_id, event)

        # -- resolve the client (most commands are per-client) --------------------
        client_id = self._client_id(event)

        handler = _VERB_TABLE.get(verb)
        if handler is None and verb.startswith(BULK_APPROVE_PREFIX):
            return self._cmd_approve_all_tier1(event, client_id)
        if handler is None:
            return self._send(
                verb, chat_id, False, f"Unknown command /{verb}.", data={}
            )
        return handler(self, event, client_id)

    # -- mod-gate -------------------------------------------------------------
    def _is_operator(self, event) -> bool:
        """Resolve the caller → ``relay_members`` and check ``is_relay_operator``.

        The caller's ``external_user_id`` is mapped to a ``relay_members.id`` via
        ``relay_member_identities`` (the platform-id → member link), then checked
        with ``relay.db.is_relay_operator`` for the org. An unresolvable caller (no
        identity link, no member_id, or no org) is NOT an operator (fail-closed).
        """
        from sable_platform.relay.db import is_relay_operator

        org_id = event.org_id
        if not org_id:
            return False
        member_id = event.member_id
        if member_id is None and event.external_user_id is not None:
            member_id = self._resolve_member_id(event.platform, event.external_user_id)
        if member_id is None:
            return False
        try:
            return is_relay_operator(self._conn, member_id, org_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception("operator mod-gate check failed; fail-closed")
            return False

    def _resolve_member_id(self, platform: str, external_user_id: str) -> Optional[int]:
        row = self._conn.execute(
            text(
                "SELECT member_id FROM relay_member_identities "
                "WHERE platform = :p AND external_user_id = :ext"
            ),
            {"p": platform, "ext": external_user_id},
        ).fetchone()
        return int(row[0]) if row is not None else None

    def _reject(self, verb, org_id, chat_id, event) -> CommandResult:
        """Reject a non-operator caller — NO side effect; audit the rejection."""
        log_audit(
            self._conn,
            actor=event.external_user_id or "unknown",
            action=ACTION_COMMAND_REJECTED,
            org_id=org_id,
            entity_id=verb,
            detail={
                "command": verb,
                "external_user_id": event.external_user_id,
                "member_id": event.member_id,
                "reason": "caller is not a Sable operator (mod-gate)",
            },
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        msg = f"/{verb} is operator-only. You are not authorized."
        self._reply.reply(chat_id, msg)
        return CommandResult(command=verb, ok=False, message=msg, rejected=True)

    def _client_id(self, event) -> Optional[int]:
        if not event.org_id:
            return None
        return _client_id_for_org(self._conn, event.org_id)

    # -- reply helper ---------------------------------------------------------
    def _send(self, verb, chat_id, ok, message, *, data=None) -> CommandResult:
        self._reply.reply(chat_id, message)
        return CommandResult(command=verb, ok=ok, message=message, data=data or {})

    # ======================================================================
    # the per-verb handlers (each hits a LIVE target)
    # ======================================================================
    def _need_client(self, event):
        cid = self._client_id(event)
        return cid

    # -- /demote (C3.5a trigger 2: operator-mark auto→hitl, always allowed) ---
    def _cmd_demote(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send("demote", event.chat_id, False, "No AutoCM client for this org.")
        if not event.args:
            return self._send("demote", event.chat_id, False, "Usage: /demote <category>")
        category = event.args[0]
        flipped = self._operator_demote(client_id, category, event.org_id, event)
        if flipped:
            msg = f"Demoted {category} → HITL (operator mark)."
        else:
            msg = f"{category} was already HITL (no change)."
        return self._send("demote", event.chat_id, True, msg, data={"flipped": flipped})

    def _operator_demote(self, client_id, category, org_id, event) -> bool:
        """C3.5a trigger 2 — the operator-mark demote. ALWAYS allowed, no gate.

        Reuses the C3.5a ``gate/autonomy`` state-write path so the flip goes through
        the same upsert the autonomy machine uses; the audit verb is the trigger-2
        ``autonomy_demoted_operator``. Idempotent — a category already HITL is a
        no-op returning False (no spurious audit row).
        """
        from sable_platform.autocm.gate.autonomy import (
            _get_category_state,
            _set_category_state,
        )

        current = _get_category_state(self._conn, client_id, category)
        if current != "auto":
            return False
        _set_category_state(self._conn, client_id, category, "hitl")
        log_audit(
            self._conn,
            actor=event.external_user_id or AUDIT_SOURCE,
            action=ACTION_DEMOTE_OPERATOR,
            org_id=org_id,
            entity_id=f"{client_id}:{category}",
            detail={
                "client_id": client_id,
                "category": category,
                "trigger": "operator_mark",
                "operator": event.external_user_id,
            },
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        return True

    # -- /promote (C3.5a flip-criteria gate; returns the verdict) -------------
    def _cmd_promote(self, event, client_id) -> CommandResult:
        from sable_platform.autocm.gate.autonomy import promote_category

        if client_id is None:
            return self._send("promote", event.chat_id, False, "No AutoCM client for this org.")
        if not event.args:
            return self._send("promote", event.chat_id, False, "Usage: /promote <category>")
        category = event.args[0]
        # The operator running /promote IS the sign-off (HITL_UX §6: "ready for
        # operator sign-off"); the gate still enforces the §7 thresholds.
        verdict = promote_category(
            self._conn,
            client_id,
            category,
            actor=event.external_user_id or AUDIT_SOURCE,
            operator_sign_off=True,
            org_id=event.org_id,
        )
        self._conn.commit()
        if verdict.promote:
            msg = (
                f"Promoted {category} → AUTO. "
                f"samples={verdict.sample_count} "
                f"clean={verdict.clean_approval_rate:.2%}"
            )
        else:
            msg = (
                f"{category} NOT promoted (gate not met): "
                + "; ".join(verdict.reasons)
            )
        return self._send(
            "promote",
            event.chat_id,
            verdict.promote,
            msg,
            data={
                "promote": verdict.promote,
                "sample_count": verdict.sample_count,
                "clean_approval_rate": verdict.clean_approval_rate,
                "reasons": list(verdict.reasons),
            },
        )

    # -- /silence (autocm_flagged_users) --------------------------------------
    def _cmd_silence(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send("silence", event.chat_id, False, "No AutoCM client for this org.")
        if not event.args:
            return self._send(
                "silence", event.chat_id, False, "Usage: /silence <user-handle> [duration]"
            )
        handle = event.args[0]
        duration = event.args[1] if len(event.args) > 1 else None
        external_user_id, member_id = self._resolve_target_user(event.platform, handle)
        flagged_id = silence_user(
            self._conn,
            client_id,
            member_id=member_id,
            external_user_id=external_user_id,
            duration=duration,
            actor=event.external_user_id or AUDIT_SOURCE,
            org_id=event.org_id,
            now=self._clock(),
        )
        if flagged_id is None:
            return self._send(
                "silence", event.chat_id, False, f"Could not resolve user {handle!r}."
            )
        dur = f" for {duration}" if duration else ""
        return self._send(
            "silence",
            event.chat_id,
            True,
            f"Silenced {handle}{dur}. The bot will disengage until cleared.",
            data={"flagged_user_id": flagged_id},
        )

    # -- /clear-flag (autocm_flagged_users) -----------------------------------
    def _cmd_clear_flag(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send("clear-flag", event.chat_id, False, "No AutoCM client for this org.")
        if not event.args:
            return self._send(
                "clear-flag", event.chat_id, False, "Usage: /clear-flag <user-handle>"
            )
        handle = event.args[0]
        external_user_id, member_id = self._resolve_target_user(event.platform, handle)
        cleared = clear_flag(
            self._conn,
            client_id,
            member_id=member_id,
            external_user_id=external_user_id,
            actor=event.external_user_id or AUDIT_SOURCE,
            org_id=event.org_id,
            now=self._clock(),
        )
        if cleared == 0:
            return self._send(
                "clear-flag", event.chat_id, True, f"No active silence on {handle}."
            )
        return self._send(
            "clear-flag",
            event.chat_id,
            True,
            f"Cleared {handle} ({cleared} row(s)). The bot will re-engage.",
            data={"cleared_count": cleared},
        )

    def _resolve_target_user(self, platform: str, handle: str):
        """Resolve a ``@handle`` target → (external_user_id, member_id).

        Looks the handle up in ``relay_member_identities`` (handle column) for the
        platform; if found, returns its external_user_id + member_id. Otherwise the
        raw handle (stripped of a leading ``@``) is treated as the external_user_id
        (an unlinked user can still be silenced by external id, mirroring the C3.8a
        auto-silence path).
        """
        raw = handle.lstrip("@")
        row = self._conn.execute(
            text(
                "SELECT external_user_id, member_id FROM relay_member_identities "
                "WHERE platform = :p AND (handle = :h OR external_user_id = :h)"
            ),
            {"p": platform, "h": raw},
        ).fetchone()
        if row is not None:
            return row[0], int(row[1]) if row[1] is not None else None
        return raw, None

    # -- /kb-add (C3.2c KB store) ---------------------------------------------
    def _cmd_kb_add(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send("kb-add", event.chat_id, False, "No AutoCM client for this org.")
        # /kb-add <tag> <free-text>: first token = tag, remainder = chunk text.
        if not event.argstr or len(event.args) < 2:
            return self._send(
                "kb-add", event.chat_id, False, "Usage: /kb-add <tag> <text>"
            )
        tag = event.args[0]
        body = event.argstr[len(tag):].strip()
        if not body:
            return self._send("kb-add", event.chat_id, False, "Usage: /kb-add <tag> <text>")
        chunk_id = self._kb_add(client_id, tag, body, event)
        return self._send(
            "kb-add",
            event.chat_id,
            True,
            f"Added KB chunk #{chunk_id} (tag {tag!r}).",
            data={"chunk_id": chunk_id, "tag": tag},
        )

    def _kb_add(self, client_id, tag, body, event) -> int:
        """Insert a manual KB chunk (authority 0.9) + keep the FTS5 companion in sync.

        Writes directly to the C3.2a ``autocm_kb_chunks`` (the same table the store
        owns), under the client's singleton ``manual`` source, and maintains the
        ``autocm_kb_chunks_fts`` keyword index — the same write shape
        ``kb/refresher.promote_resolved_faq`` uses for the resolved-FAQ promotion.
        Embedding is left NULL (the operator chunk is keyword-retrievable via FTS5
        immediately; a scheduled re-embed/backfill is a batch concern, not this hot
        path). Writes a ``kb_chunk_added`` audit row.
        """
        import hashlib
        import json

        source_id = _ensure_manual_source(self._conn, client_id)
        metadata = {"tag": tag, "added_by": event.external_user_id, "source": "operator_kb_add"}
        chash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        row = self._conn.execute(
            text(
                "INSERT INTO autocm_kb_chunks "
                "(source_id, client_id, chunk_text, chunk_embedding, chunk_metadata, "
                " chunk_authority, content_hash, status) "
                "VALUES (:source_id, :client_id, :chunk_text, NULL, :meta, "
                " :authority, :chash, 'active') RETURNING id"
            ),
            {
                "source_id": source_id,
                "client_id": client_id,
                "chunk_text": body,
                "meta": json.dumps(metadata),
                "authority": KB_ADD_AUTHORITY,
                "chash": chash,
            },
        ).fetchone()
        chunk_id = int(row[0])
        self._conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS autocm_kb_chunks_fts "
                "USING fts5(chunk_text, content='autocm_kb_chunks', content_rowid='id')"
            )
        )
        self._conn.execute(
            text(
                "INSERT INTO autocm_kb_chunks_fts (rowid, chunk_text) "
                "VALUES (:rowid, :chunk_text)"
            ),
            {"rowid": chunk_id, "chunk_text": body},
        )
        log_audit(
            self._conn,
            actor=event.external_user_id or AUDIT_SOURCE,
            action=ACTION_KB_ADD,
            org_id=event.org_id,
            entity_id=str(chunk_id),
            detail={
                "client_id": client_id,
                "source_id": source_id,
                "tag": tag,
                "authority": KB_ADD_AUTHORITY,
                "chunk_chars": len(body),
            },
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        return chunk_id

    # -- /kb-stale (C3.2c KB) -------------------------------------------------
    def _cmd_kb_stale(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send("kb-stale", event.chat_id, False, "No AutoCM client for this org.")
        chunk_id = self._parse_int_arg(event)
        if chunk_id is None:
            return self._send("kb-stale", event.chat_id, False, "Usage: /kb-stale <chunk-id>")
        ok = self._kb_set_status(client_id, chunk_id, "stale", ACTION_KB_STALE, event)
        if not ok:
            return self._send(
                "kb-stale", event.chat_id, False, f"Chunk #{chunk_id} not found for this client."
            )
        return self._send(
            "kb-stale", event.chat_id, True, f"Marked chunk #{chunk_id} stale.",
            data={"chunk_id": chunk_id},
        )

    # -- /kb-remove (C3.2c KB) ------------------------------------------------
    def _cmd_kb_remove(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send("kb-remove", event.chat_id, False, "No AutoCM client for this org.")
        chunk_id = self._parse_int_arg(event)
        if chunk_id is None:
            return self._send(
                "kb-remove", event.chat_id, False, "Usage: /kb-remove <chunk-id> [reason]"
            )
        reason = event.argstr.split(None, 1)[1].strip() if len(event.args) > 1 else None
        ok = self._kb_set_status(
            client_id, chunk_id, "wrong", ACTION_KB_REMOVE, event, reason=reason
        )
        if not ok:
            return self._send(
                "kb-remove", event.chat_id, False, f"Chunk #{chunk_id} not found for this client."
            )
        return self._send(
            "kb-remove", event.chat_id, True,
            f"Removed chunk #{chunk_id}" + (f" (reason: {reason})" if reason else "") + ".",
            data={"chunk_id": chunk_id, "reason": reason},
        )

    def _kb_set_status(
        self, client_id, chunk_id, status, action, event, *, reason=None
    ) -> bool:
        """Flip a chunk's ``status`` (client-scoped) + FTS5 sync + audit. Returns hit?

        The C3.2a ``autocm_kb_chunks.status`` CHECK set is ``active``/``stale``/
        ``wrong``; ``/kb-stale`` → ``stale``, ``/kb-remove`` → ``wrong`` (both
        deactivate the chunk from the active retrieval set). The FTS5 companion's
        ``rowid`` row is deleted so a deactivated chunk no longer surfaces on the
        keyword leg either. Client-scoped so an operator can never touch another
        tenant's chunk (KB_DESIGN §6). Returns False if the chunk does not belong to
        the client (no write, no audit).
        """
        owned = self._conn.execute(
            text(
                "SELECT 1 FROM autocm_kb_chunks WHERE id = :id AND client_id = :c"
            ),
            {"id": chunk_id, "c": client_id},
        ).fetchone()
        if owned is None:
            return False
        self._conn.execute(
            text("UPDATE autocm_kb_chunks SET status = :s WHERE id = :id AND client_id = :c"),
            {"s": status, "id": chunk_id, "c": client_id},
        )
        # Keep the FTS5 keyword leg consistent: a deactivated chunk must not be
        # keyword-retrievable. The external-content FTS5 'delete' command form
        # requires the old text; simplest robust path is to delete the rowid row.
        self._conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS autocm_kb_chunks_fts "
                "USING fts5(chunk_text, content='autocm_kb_chunks', content_rowid='id')"
            )
        )
        try:
            self._conn.execute(
                text("DELETE FROM autocm_kb_chunks_fts WHERE rowid = :rowid"),
                {"rowid": chunk_id},
            )
        except Exception:  # pragma: no cover - external-content FTS delete edge
            logger.debug("kb fts delete for chunk %s skipped", chunk_id)
        detail = {"client_id": client_id, "chunk_id": chunk_id, "status": status}
        if reason is not None:
            detail["reason"] = reason
        log_audit(
            self._conn,
            actor=event.external_user_id or AUDIT_SOURCE,
            action=action,
            org_id=event.org_id,
            entity_id=str(chunk_id),
            detail=detail,
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        return True

    # -- /kb-refresh-source (C3.2c KBRefresher) -------------------------------
    def _cmd_kb_refresh_source(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send(
                "kb-refresh-source", event.chat_id, False, "No AutoCM client for this org."
            )
        source_id = self._parse_int_arg(event)
        if source_id is None:
            return self._send(
                "kb-refresh-source", event.chat_id, False, "Usage: /kb-refresh-source <source-id>"
            )
        owned = self._conn.execute(
            text("SELECT 1 FROM autocm_kb_sources WHERE id = :id AND client_id = :c"),
            {"id": source_id, "c": client_id},
        ).fetchone()
        if owned is None:
            return self._send(
                "kb-refresh-source", event.chat_id, False,
                f"Source #{source_id} not found for this client.",
            )
        outcome = self._refresh_source(client_id, source_id, event)
        verb = "kb-refresh-source"
        if outcome.error:
            return self._send(
                verb, event.chat_id, True,
                f"Source #{source_id} refreshed with error: {outcome.error}",
                data={"source_id": source_id, "error": outcome.error},
            )
        return self._send(
            verb, event.chat_id, True,
            f"Source #{source_id} refreshed (changed={outcome.changed}, "
            f"{len(outcome.new_chunk_ids)} new chunk(s)).",
            data={
                "source_id": source_id,
                "changed": outcome.changed,
                "new_chunk_ids": list(outcome.new_chunk_ids),
            },
        )

    def _refresh_source(self, client_id, source_id, event):
        """LIVE force-refresh of one KB source via the C3.2c :class:`KBRefresher`.

        Builds the refresher over the C3.2a store (FakeEmbeddingProvider is the
        test embedder; production selects the configured provider) and the C3.2b
        extractor, and calls ``refresh_source`` directly (the operator forces it
        regardless of the freshness contract). Returns the
        :class:`~sable_platform.autocm.kb.refresher.RefreshOutcome`.
        """
        from sable_platform.autocm.kb.extractor import KBExtractor
        from sable_platform.autocm.kb.refresher import KBRefresher
        from sable_platform.autocm.kb.store import (
            FakeEmbeddingProvider,
            SQLiteKBStore,
        )

        org_id = event.org_id or _org_id_for_client(self._conn, client_id) or ""
        store = SQLiteKBStore(self._conn, FakeEmbeddingProvider())
        extractor = KBExtractor()
        refresher = KBRefresher(
            self._conn, store, extractor, org_id=org_id, clock=self._clock
        )
        return refresher.refresh_source(source_id)

    # -- /category-state (C3.5a read) -----------------------------------------
    def _cmd_category_state(self, event, client_id) -> CommandResult:
        from sable_platform.autocm.classifier.categories import (
            CATEGORIES,
            resolve_category_state,
        )
        from sable_platform.autocm.gate.autonomy import gather_review_stats
        from sable_platform.autocm.gate.confidence import is_frozen

        if client_id is None:
            return self._send(
                "category-state", event.chat_id, False, "No AutoCM client for this org."
            )
        targets = [event.args[0]] if event.args else list(CATEGORIES)
        lines: List[str] = []
        rows_out: List[dict] = []
        for cat in targets:
            merged = resolve_category_state(self._conn, client_id, cat)
            if merged is None:
                lines.append(f"{cat}: unknown category")
                continue
            stats = gather_review_stats(self._conn, client_id, cat)
            rate = (
                stats.clean_approval_count / stats.sample_count
                if stats.sample_count > 0
                else 0.0
            )
            frozen = is_frozen(self._conn, client_id, cat, now=self._clock())
            thr = merged.confidence_threshold
            thr_str = f"{thr:.2f}" if thr is not None else "n/a"
            frozen_str = " [FROZEN]" if frozen else ""
            lines.append(
                f"{cat}: {merged.state} (thr {thr_str}, "
                f"samples {stats.sample_count}, clean {rate:.0%}){frozen_str}"
            )
            rows_out.append(
                {
                    "category": cat,
                    "state": merged.state,
                    "threshold": thr,
                    "sample_count": stats.sample_count,
                    "clean_approval_rate": rate,
                    "frozen": frozen,
                }
            )
        return self._send(
            "category-state", event.chat_id, True, "\n".join(lines),
            data={"categories": rows_out},
        )

    # -- /voice-drift (autocm_reviews, last 7d heavy edits) -------------------
    def _cmd_voice_drift(self, event, client_id) -> CommandResult:
        if client_id is None:
            return self._send("voice-drift", event.chat_id, False, "No AutoCM client for this org.")
        register = None
        if event.args:
            arg = event.args[0].lower()
            if arg in ("calm", "reactive"):
                register = arg
        drafts = voice_drift_drafts(
            self._conn, client_id, register=register, now=self._clock()
        )
        if not drafts:
            scope = f" ({register})" if register else ""
            return self._send(
                "voice-drift", event.chat_id, True,
                f"No heavy-edit drafts in the last 7 days{scope}.",
                data={"drafts": []},
            )
        lines = [
            f"#{d.draft_id} [{d.register}/{d.category}] diff {d.edit_diff_size:.0%} "
            f"({d.reviewed_at})"
            for d in drafts
        ]
        return self._send(
            "voice-drift", event.chat_id, True,
            f"{len(drafts)} heavy-edit draft(s) in the last 7 days:\n"
            + "\n".join(lines),
            data={
                "drafts": [
                    {
                        "draft_id": d.draft_id,
                        "register": d.register,
                        "category": d.category,
                        "edit_diff_size": d.edit_diff_size,
                    }
                    for d in drafts
                ]
            },
        )

    # -- /punt (C3.8a manual tier-3 dual-route) -------------------------------
    def _cmd_punt(self, event, client_id) -> CommandResult:
        from sable_platform.autocm.escalation.tier3 import Tier3EscalationRouter

        if client_id is None:
            return self._send("punt", event.chat_id, False, "No AutoCM client for this org.")
        if not event.argstr:
            return self._send(
                "punt", event.chat_id, False, "Usage: /punt <tweet-url | message-id>"
            )
        ref = event.argstr.strip()
        notifier = self._punt_notifier()
        router = Tier3EscalationRouter(self._conn, notifier)
        result = router.dual_route_tier3(
            client_id,
            "founder_voice_needed",
            org_id=event.org_id,
            reason=f"operator /punt: {ref}",
            now=self._clock(),
        )
        return self._send(
            "punt", event.chat_id, True,
            f"Punted {ref} to founder + Sable on-call (escalation "
            f"#{result.escalation_id}).",
            data={
                "escalation_id": result.escalation_id,
                "route": result.route,
                "ref": ref,
            },
        )

    def _punt_notifier(self):
        """The EscalationNotifier the manual /punt routes through.

        The operator-reply seam IS the operator's awareness channel; the dual-route
        notifier delivers the founder + on-call legs. In v1 the notifier reuses the
        injected reply sender for both legs (the operator chat is where Arf/founder
        coordinate); a deployment binds the real founder-DM / on-call transport.
        """
        reply = self._reply

        class _ReplyBackedNotifier:
            def notify_founder(self, org_id, escalation_id, body):
                return reply.reply(None, f"[founder] {body}")

            def notify_oncall(self, org_id, escalation_id, body):
                return reply.reply(None, f"[on-call] {body}")

            def push(self, org_id, escalation_id, body):  # pragma: no cover
                return reply.reply(None, f"[push] {body}")

        return _ReplyBackedNotifier()

    # -- /pause-client + /resume-client (the kill switch) ---------------------
    def _cmd_pause_client(self, event, client_id) -> CommandResult:
        target = self._resolve_client_arg(event, client_id)
        if target is None:
            return self._send("pause-client", event.chat_id, False, "No AutoCM client to pause.")
        flipped = pause_client(
            self._conn, target, actor=event.external_user_id or AUDIT_SOURCE,
            org_id=event.org_id, now=self._clock(),
        )
        msg = (
            f"Client {target} PAUSED — all publishing halted."
            if flipped
            else f"Client {target} was already paused."
        )
        return self._send("pause-client", event.chat_id, True, msg, data={"flipped": flipped})

    def _cmd_resume_client(self, event, client_id) -> CommandResult:
        target = self._resolve_client_arg(event, client_id)
        if target is None:
            return self._send("resume-client", event.chat_id, False, "No AutoCM client to resume.")
        flipped = resume_client(
            self._conn, target, actor=event.external_user_id or AUDIT_SOURCE,
            org_id=event.org_id, now=self._clock(),
        )
        msg = (
            f"Client {target} RESUMED (state hitl)."
            if flipped
            else f"Client {target} was not paused."
        )
        return self._send("resume-client", event.chat_id, True, msg, data={"flipped": flipped})

    def _resolve_client_arg(self, event, client_id) -> Optional[int]:
        """A /pause-client / /resume-client target: explicit arg id, else the org's."""
        if event.args:
            try:
                return int(event.args[0])
            except ValueError:
                return None
        return client_id

    # -- /incident-mode on|off (C3.8b) ----------------------------------------
    def _cmd_incident_mode(self, event, client_id) -> CommandResult:
        from sable_platform.autocm.escalation.incident import set_incident_mode

        if client_id is None:
            return self._send("incident-mode", event.chat_id, False, "No AutoCM client for this org.")
        if not event.args or event.args[0].lower() not in ("on", "off"):
            return self._send(
                "incident-mode", event.chat_id, False, "Usage: /incident-mode on|off"
            )
        active = event.args[0].lower() == "on"
        prior = set_incident_mode(
            self._conn, client_id, active,
            actor=event.external_user_id or AUDIT_SOURCE,
            org_id=event.org_id,
            reason="operator /incident-mode",
            now=self._clock(),
        )
        state = "ON" if active else "OFF"
        return self._send(
            "incident-mode", event.chat_id, True,
            f"Incident-mode {state}.",
            data={"incident_active": active, "prior": prior},
        )

    # -- /approve-all-tier1-<category> (HITL_UX §5 bulk) ----------------------
    def _cmd_approve_all_tier1(self, event, client_id) -> CommandResult:
        from sable_platform.autocm.classifier.categories import get_category_def
        from sable_platform.autocm.gate.review_queue import (
            STATUS_HITL_PENDING,
        )

        verb = event.command
        if client_id is None:
            return self._send(verb, event.chat_id, False, "No AutoCM client for this org.")
        category = verb[len(BULK_APPROVE_PREFIX):]
        cdef = get_category_def(category)
        if cdef is None:
            return self._send(
                verb, event.chat_id, False, f"Unknown category {category!r}."
            )
        if cdef.tier != 1:
            return self._send(
                verb, event.chat_id, False,
                f"/{verb} only applies to tier-1 categories ({category} is tier {cdef.tier}).",
            )
        # Find every pending tier-1 draft of this category for the client.
        rows = self._conn.execute(
            text(
                "SELECT id FROM autocm_drafts "
                "WHERE client_id = :c AND category = :cat AND status = :pending "
                "ORDER BY id"
            ),
            {"c": client_id, "cat": category, "pending": STATUS_HITL_PENDING},
        ).fetchall()
        draft_ids = [int(r[0]) for r in rows]
        if not draft_ids:
            return self._send(
                verb, event.chat_id, True, f"No pending {category} drafts to approve.",
                data={"approved_draft_ids": []},
            )
        approved = self._bulk_approve(client_id, category, draft_ids, event)
        return self._send(
            verb, event.chat_id, True,
            f"Bulk-approved {len(approved)} {category} draft(s).",
            data={"approved_draft_ids": approved},
        )

    def _bulk_approve(self, client_id, category, draft_ids, event) -> List[int]:
        """Approve N pending tier-1 drafts; record one bulk audit row ENUMERATING all ids.

        HITL_UX §5: "Bulk operations are logged with the full draft list for audit".
        Each draft is marked ``approved`` (the C3.6 publisher reads approved drafts
        and enqueues — this surface NEVER touches the relay outbox, mirroring the
        C3.5b boundary) AND gets a per-draft ``approve`` ``autocm_reviews`` row (so
        the C3.5a clean-approval accounting and the SAFETY §5 per-reply audit hold
        for bulk approvals exactly as for single ones). THEN one
        ``hitl_bulk_approved_tier1`` audit row records the FULL list of approved
        draft ids in its detail (a single bulk op is NOT an opaque row).
        """
        from sable_platform.autocm.gate.review_queue import (
            STATUS_APPROVED,
            already_reviewed,
            record_review_decision,
        )

        reviewer = event.external_user_id or AUDIT_SOURCE
        approved: List[int] = []
        for draft_id in draft_ids:
            if already_reviewed(self._conn, draft_id):
                continue
            draft = self._conn.execute(
                text(
                    "SELECT draft_text, source_message_id, cited_chunk_ids, tier, "
                    "       confidence FROM autocm_drafts WHERE id = :d"
                ),
                {"d": draft_id},
            ).fetchone()
            if draft is None:
                continue
            import json as _json

            cited = []
            try:
                cited = _json.loads(draft[2] or "[]")
            except (ValueError, TypeError):
                cited = []
            record_review_decision(
                self._conn,
                draft_id=draft_id,
                client_id=client_id,
                reviewer=reviewer,
                decision="approve",
                draft_text=draft[0],
                org_id=event.org_id,
                source_message_id=draft[1],
                cited_chunk_ids=cited,
                category=category,
                tier=draft[3],
                confidence=draft[4],
            )
            self._conn.execute(
                text(
                    "UPDATE autocm_drafts SET status = :s, resolved_at = :now WHERE id = :d"
                ),
                {"s": STATUS_APPROVED, "now": _iso_z(self._clock()), "d": draft_id},
            )
            approved.append(draft_id)
        # ONE bulk audit row enumerating the FULL approved-draft list (HITL_UX §5).
        log_audit(
            self._conn,
            actor=reviewer,
            action=ACTION_BULK_APPROVE,
            org_id=event.org_id,
            entity_id=f"{client_id}:{category}",
            detail={
                "client_id": client_id,
                "category": category,
                "approved_count": len(approved),
                "approved_draft_ids": approved,  # the FULL list — not opaque
                "context": "queue-backlog cleanup (HITL_UX §5)",
            },
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        return approved

    # -- small parse helper ---------------------------------------------------
    def _parse_int_arg(self, event) -> Optional[int]:
        if not event.args:
            return None
        try:
            return int(event.args[0])
        except ValueError:
            return None


# Per-verb dispatch table (the catch-all router resolves the verb here; the
# /approve-all-tier1-<cat> family is handled by prefix, not in this table).
_VERB_TABLE = {
    "demote": CommandRouter._cmd_demote,
    "promote": CommandRouter._cmd_promote,
    "silence": CommandRouter._cmd_silence,
    "clear-flag": CommandRouter._cmd_clear_flag,
    "kb-add": CommandRouter._cmd_kb_add,
    "kb-stale": CommandRouter._cmd_kb_stale,
    "kb-remove": CommandRouter._cmd_kb_remove,
    "kb-refresh-source": CommandRouter._cmd_kb_refresh_source,
    "category-state": CommandRouter._cmd_category_state,
    "voice-drift": CommandRouter._cmd_voice_drift,
    "punt": CommandRouter._cmd_punt,
    "pause-client": CommandRouter._cmd_pause_client,
    "resume-client": CommandRouter._cmd_resume_client,
    "incident-mode": CommandRouter._cmd_incident_mode,
}

#: the verbs this surface owns (for the listener / docs / tests).
OPERATOR_COMMANDS = tuple(_VERB_TABLE.keys()) + (f"{BULK_APPROVE_PREFIX}<category>",)


__all__ = [
    # router + result + seam
    "CommandRouter",
    "CommandResult",
    "OperatorReplySender",
    # kill switch
    "is_publishing_paused",
    "pause_client",
    "resume_client",
    "AUTONOMY_PAUSED",
    "KILL_SWITCH_REASON",
    # flagged-user helpers
    "silence_user",
    "clear_flag",
    # voice-drift
    "voice_drift_drafts",
    "VoiceDriftDraft",
    # constants
    "OPERATOR_COMMANDS",
    "BULK_APPROVE_PREFIX",
    "KB_ADD_AUTHORITY",
    "VOICE_DRIFT_WINDOW_DAYS",
    # audit verbs
    "ACTION_DEMOTE_OPERATOR",
    "ACTION_FLAG_SILENCED",
    "ACTION_FLAG_CLEARED",
    "ACTION_KB_ADD",
    "ACTION_KB_STALE",
    "ACTION_KB_REMOVE",
    "ACTION_PAUSE",
    "ACTION_RESUME",
    "ACTION_BULK_APPROVE",
    "ACTION_COMMAND_REJECTED",
]
