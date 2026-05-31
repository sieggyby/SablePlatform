"""Incident-mode (MEGAPLAN C3.8b) — the war-room subsystem.

Split from the former C3.8: incident-mode is a scheduled-job subsystem with its
OWN state (the per-client ``autocm_clients.incident_active`` flag), a register
OVERRIDE, and a PROACTIVE outbound poster (a new autonomous outbound-message path
that needs isolated audit) — not a thin add-on to the C3.8a tier-3 escalation
router. This module owns (FEATURE_INVENTORY G + HITL_UX §6 + CLASSIFIER §2):

**The per-client toggle (``autocm_clients.incident_active``), :func:`set_incident_mode`.**
``/incident-mode on|off`` flips the per-client flag (added in C3.0 / 058), writes
an audit row, and returns the prior value. Toggling incident-mode (on OR off) does
NOT clear an active SAFETY §6 freeze — the freeze is the stronger guardrail and
only ``freeze_until`` elapsing (or an explicit operator clear) restores autonomous
auto-send (FREEZE ⊃ INCIDENT-MODE PRECEDENCE, owned at C3.8a, enforced here).

**The war-room register (sober / no-meatbag / timestamps / next-update promises),
:func:`war_room_register` / :func:`format_war_room_status`.** While ``incident_active``
the GLOBAL register is forced to the sober war-room voice (FEATURE_INVENTORY G.1 /
G.2.a): no meatbag, no sass, no brand emojis, a UTC timestamp on every reply, and a
"next update by X" promise on a proactive status post. :func:`format_war_room_status`
renders the canonical ``Update HH:MM UTC: <body>`` shape (+ optional next-update
line). The ``incident`` classifier category is permanently HITL — the war-room
register applies to its (human-authored) replies and to the proactive poster's
own output; incident handling is NEVER autonomous regardless of category maturity.

**Tier-1 chatter SUPPRESSION while active, :func:`is_tier1_suppressed` /
:func:`should_engage_during_incident`.** While ``incident_active`` the NON-incident
tier-1 categories are suppressed (no greetings, glossary, catchphrase echo —
FEATURE_INVENTORY G.2.b): a community crisis is not the moment for the bot to greet
newcomers or drip a catchphrase. The ``incident`` category itself, and tier-2/3
routing, are unaffected (they still flow through their normal HITL / escalation
paths).

**The proactive timed status poster (a NEW autonomous outbound path),
:class:`ProactiveStatusPoster`.** A batch/scheduled unit that, while a client is in
incident-mode, emits a war-room status update on a cadence. This is the single
highest-stakes auto-send window (the poster is active during a FUD-heavy crisis),
so EVERY proactive status-update it emits MUST pass through ``gate/safety``
(hard-refusal patterns, the C3.5a vendored bank) AND ``gate/citation_check`` at the
SAFETY §2.5 ``exact-match-or-slot-fill`` tier (a status FACT must be slot-filled /
exact-matched, never free-text), and MUST persist the SAFETY §5 audit field set,
BEFORE publishing. An uncited / unvetted factual claim is BLOCKED (drafted to HITL
+ audited), NOT published. **SAFETY §6 freeze precedence** is enforced at the
poster's auto-send gate: while a client's ``freeze_until`` is active, the poster
does NOT auto-send even if ``incident_active`` — it drafts to HITL / operator-
approved status updates instead. The clock + the publish + the operator-DM seams
are all injectable so the autonomous outbound path is testable offline.

**The auto-suggest threshold (``≥3 incident-flagged msgs in 10min`` OR any tier-3),
:class:`IncidentSuggester`.** A runtime detector that DMs the operator
"Suggest incident-mode? [Yes][No, flag only]" (FEATURE_INVENTORY G.3) when either
trigger fires. The ≥3-in-10-min leg is a clock-injectable sliding window of
incident-flag timestamps per client (deterministic under test); the any-tier-3 leg
fires on a single tier-3. A suggestion writes an audit row. The suggester only
SUGGESTS — it never flips the flag itself (the operator confirms via
``/incident-mode on``); incident handling is never autonomous.

**No telegram / network in this module.** The proactive publish, the operator DM,
and the clock are injected Protocols / callables; tests inject fakes that record
calls. All timestamps are computed in Python as UTC ISO-8601 ``...Z`` and bound as
parameters (never ``strftime('now')``), so the SQL is dialect-agnostic and runs
unchanged on the live Postgres pool — matching the C3.8a / ``autocm/db.py`` /
``relay/db.py`` contract.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.autocm.classifier.categories import (
    TIER_AUTONOMOUS,
    get_category_def,
)
from sable_platform.autocm.classifier.register import CALM, REACTIVE
from sable_platform.autocm.gate.citation_check import (
    TIER_EXACT_MATCH,
    check_citations_db,
)
from sable_platform.autocm.gate.safety import audit_safety_block, check_safety
from sable_platform.db.audit import log_audit

# ---------------------------------------------------------------------------
# Constants (FEATURE_INVENTORY G / CLASSIFIER §2 / HITL_UX §6)
# ---------------------------------------------------------------------------
#: the permanently-HITL incident classifier category (CLASSIFIER §2; never auto).
INCIDENT_CATEGORY = "incident"

#: the war-room register — sober, no meatbag / sass / brand emojis, timestamps,
#: next-update promises (FEATURE_INVENTORY G.1). It is the ``reactive`` register
#: surface (CLASSIFIER §2 "reactive war-room") but in the sober crisis sub-mode —
#: the value the drafter/persona keys on is the register name; the *sub-mode* is
#: signalled by :func:`war_room_register` returning ``reactive`` while
#: ``incident_active`` AND the caller honouring the war-room formatting contract.
WAR_ROOM_REGISTER = REACTIVE

#: auto-suggest threshold: ≥3 incident-flagged messages in a 10-minute window OR
#: any tier-3 escalation (FEATURE_INVENTORY G.3).
SUGGEST_MIN_INCIDENT_FLAGS = 3
SUGGEST_WINDOW_MINUTES = 10

# log_audit verbs (audit-everything; source="sable-autocm").
AUDIT_SOURCE = "sable-autocm"
ACTION_INCIDENT_ON = "incident_mode_on"
ACTION_INCIDENT_OFF = "incident_mode_off"
ACTION_PROACTIVE_POST = "incident_proactive_status_posted"
ACTION_PROACTIVE_BLOCKED = "incident_proactive_status_blocked"
ACTION_PROACTIVE_FROZEN = "incident_proactive_status_frozen_to_hitl"
ACTION_SUGGEST = "incident_mode_suggested"

# Proactive-poster outcome codes (the stable strings on the result / audit).
OUTCOME_PUBLISHED = "published"
OUTCOME_BLOCKED = "blocked"          # gate/safety or gate/citation_check rejected
OUTCOME_FROZEN_TO_HITL = "frozen_to_hitl"  # SAFETY §6 freeze active → drafted, not sent

# Suggest trigger codes.
TRIGGER_TIER3 = "tier3_fired"
TRIGGER_FLAG_THRESHOLD = "incident_flag_threshold"


# ---------------------------------------------------------------------------
# Clock + timestamp helpers (injectable clock; dialect-agnostic ...Z form)
# ---------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    """Render a datetime to the 058 TEXT timestamp form (``...Z``, no micros)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ``...Z`` / ``+00:00`` / naive timestamp → tz-aware UTC."""
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _org_id_for_client(conn: Connection, client_id: int) -> Optional[str]:
    row = conn.execute(
        text("SELECT org_id FROM autocm_clients WHERE id = :id"),
        {"id": client_id},
    ).fetchone()
    return row[0] if row is not None else None


# ---------------------------------------------------------------------------
# (1) the per-client incident_active toggle
# ---------------------------------------------------------------------------
def is_incident_active(conn: Connection, client_id: int) -> bool:
    """True iff the client is currently in incident-mode (``incident_active=1``)."""
    row = conn.execute(
        text("SELECT incident_active FROM autocm_clients WHERE id = :id"),
        {"id": client_id},
    ).fetchone()
    return bool(row[0]) if row is not None else False


def set_incident_mode(
    conn: Connection,
    client_id: int,
    active: bool,
    *,
    actor: str = AUDIT_SOURCE,
    org_id: Optional[str] = None,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """``/incident-mode on|off``: flip the per-client ``incident_active`` flag.

    Sets ``autocm_clients.incident_active`` to ``active`` and writes an
    ``incident_mode_on`` / ``incident_mode_off`` audit row. Returns the PRIOR value
    (so the caller can tell whether this was a no-op re-toggle). Idempotent — a
    re-flip to the same value still audits (the operator action happened) but the
    returned prior reflects the no-change.

    **FREEZE ⊃ INCIDENT-MODE PRECEDENCE (load-bearing).** This does NOT touch the
    SAFETY §6 freeze (``autocm_category_state.freeze_until``, owned by C3.8a):
    turning incident-mode on cannot clear an active freeze (a freeze is triggered
    precisely because the bot did something embarrassing — it must not be silently
    defeated by entering the highest-stakes auto-send window), and turning it off
    cannot un-freeze either. The proactive poster's auto-send gate (below) reads the
    freeze independently and falls back to HITL while a freeze is active even when
    ``incident_active``.
    """
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    prior = is_incident_active(conn, client_id)
    conn.execute(
        text(
            "UPDATE autocm_clients SET incident_active = :v, updated_at = :now "
            "WHERE id = :id"
        ),
        {"v": 1 if active else 0, "now": _iso_z(now), "id": client_id},
    )
    log_audit(
        conn,
        actor=actor,
        action=ACTION_INCIDENT_ON if active else ACTION_INCIDENT_OFF,
        org_id=org_id,
        entity_id=str(client_id),
        detail={
            "client_id": client_id,
            "incident_active": active,
            "prior": prior,
            "reason": reason,
            # the precedence note is recorded so the audit trail makes the
            # invariant explicit: the toggle never touched the freeze.
            "freeze_untouched": True,
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return prior


# ---------------------------------------------------------------------------
# (2) the war-room register override + status formatting
# ---------------------------------------------------------------------------
def war_room_register(
    conn: Connection, client_id: int, *, base_register: str = CALM
) -> str:
    """Resolve the GLOBAL register for a reply while a client may be in incident-mode.

    While ``incident_active`` the register is FORCED to the sober war-room voice
    (:data:`WAR_ROOM_REGISTER`) globally (FEATURE_INVENTORY G.2.a: "all replies use
    war-room register") — overriding whatever calm/reactive the per-category default
    or charge detector would otherwise pick. When NOT in incident-mode the caller's
    ``base_register`` (the normal C3.4b selection) is returned unchanged.
    """
    if is_incident_active(conn, client_id):
        return WAR_ROOM_REGISTER
    return base_register


def format_war_room_status(
    body: str,
    *,
    now: Optional[datetime] = None,
    next_update_minutes: Optional[int] = None,
) -> str:
    """Render the canonical war-room status line (FEATURE_INVENTORY G.1 / G.2.c).

    Produces ``Update HH:MM UTC: <body>`` — a sober, timestamped status update (no
    meatbag / sass / brand emojis; the timestamp is the war-room signature). When
    ``next_update_minutes`` is given, appends the "next update by X" promise
    (``Next update by HH:MM UTC.``) the war-room register requires on a proactive
    post. Deterministic given ``now`` (injectable clock).
    """
    now = now or _utc_now()
    stamp = now.astimezone(timezone.utc).strftime("%H:%M")
    line = f"Update {stamp} UTC: {body.strip()}"
    if next_update_minutes is not None and next_update_minutes > 0:
        nxt = (now + timedelta(minutes=next_update_minutes)).astimezone(timezone.utc)
        line = f"{line} Next update by {nxt.strftime('%H:%M')} UTC."
    return line


# ---------------------------------------------------------------------------
# (3) tier-1 chatter suppression while incident-mode is active
# ---------------------------------------------------------------------------
def is_tier1_suppressed(conn: Connection, client_id: int, category: str) -> bool:
    """True iff this NON-incident tier-1 category is suppressed by incident-mode.

    While ``incident_active`` the non-incident tier-1 chatter categories (greeting,
    glossary, catchphrase echo, etc. — FEATURE_INVENTORY G.2.b) are suppressed: a
    crisis is not the time for the bot to greet newcomers or drip a catchphrase. The
    ``incident`` category itself is NEVER suppressed (it is the whole point of the
    mode), and tier-2/3 categories are unaffected here (they flow through their
    normal HITL / escalation paths). Returns False when the client is not in
    incident-mode, or for an unknown category (the §6 hallucination guard — an
    unknown category is not silently treated as suppressible tier-1).
    """
    if category == INCIDENT_CATEGORY:
        return False
    if not is_incident_active(conn, client_id):
        return False
    d = get_category_def(category)
    if d is None:
        return False
    return d.tier == TIER_AUTONOMOUS


def should_engage_during_incident(
    conn: Connection, client_id: int, category: str
) -> bool:
    """Convenience inverse of :func:`is_tier1_suppressed` for the engage check.

    True iff the category MAY engage given the client's current incident state.
    Equivalent to ``not is_tier1_suppressed(...)`` — provided so the C3.4a engage
    check reads naturally (``if not should_engage_during_incident(...): suppress``).
    """
    return not is_tier1_suppressed(conn, client_id, category)


# ---------------------------------------------------------------------------
# (4) the proactive timed status poster (a NEW autonomous outbound path)
# ---------------------------------------------------------------------------
class StatusPublisher(Protocol):
    """The injected outbound publish seam for the proactive poster (no network).

    A real impl rides the SableRelay publish-exactly-once outbox (relay owns the
    transport; AutoCM never builds its own TG client — see
    ``autocm.publisher.tg.Publisher``). Tests inject a fake that records calls.
    Returns a surface-specific handle.
    """

    def publish(self, org_id: str, chat_id: str, text: str) -> str:
        ...


class HitlStatusDrafter(Protocol):
    """The injected fallback when the poster must NOT auto-send (drafts to HITL).

    Invoked when a SAFETY §6 freeze is active: the proactive status update is drafted
    to the HITL / operator-approved surface instead of auto-sent. Tests inject a fake
    that records the drafted status.
    """

    def draft_to_hitl(self, org_id: str, chat_id: str, text: str, *, reason: str) -> None:
        ...


def _is_client_frozen(
    conn: Connection, client_id: int, *, now: Optional[datetime] = None
) -> bool:
    """True iff ANY category of the client has an active SAFETY §6 freeze.

    Reads ``autocm_category_state.freeze_until`` (C3.8a / 058). The client-wide 48h
    pure-HITL freeze sets a future ``freeze_until`` on EVERY category, so a single
    future-``freeze_until`` row is sufficient to consider the CLIENT frozen for the
    proactive poster's auto-send gate. (The proactive poster is a client-wide
    autonomous outbound path, not per-category, so the freeze precedence is checked
    client-wide.)
    """
    now = now or _utc_now()
    rows = conn.execute(
        text(
            "SELECT freeze_until FROM autocm_category_state "
            "WHERE client_id = :c AND freeze_until IS NOT NULL"
        ),
        {"c": client_id},
    ).fetchall()
    for r in rows:
        until = _parse_iso(r[0])
        if until is not None and until > now:
            return True
    return False


@dataclass(frozen=True)
class ProactivePostResult:
    """The outcome of one proactive status-update attempt.

    ``outcome`` is :data:`OUTCOME_PUBLISHED` (auto-sent), :data:`OUTCOME_BLOCKED`
    (gate/safety or gate/citation_check rejected — drafted/escalated, never sent),
    or :data:`OUTCOME_FROZEN_TO_HITL` (a SAFETY §6 freeze was active — drafted to
    HITL instead of auto-sent). ``audit_id`` is the SAFETY §5 audit row. ``handle``
    is the publish handle (only when published). ``reason`` is a stable code for the
    block / freeze.
    """

    outcome: str
    audit_id: Optional[int]
    rendered_text: str
    handle: Optional[str] = None
    reason: Optional[str] = None

    @property
    def published(self) -> bool:
        return self.outcome == OUTCOME_PUBLISHED


class ProactiveStatusPoster:
    """The incident-mode proactive timed status poster (C3.8b autonomous outbound).

    Construction takes the live SP-pool :class:`Connection` (the caller owns
    lifecycle — this creates no engine), the injected :class:`StatusPublisher`
    (the relay outbox in prod, a fake in tests), and an optional
    :class:`HitlStatusDrafter` (the freeze fallback). An injectable ``now`` makes the
    scheduled cadence deterministic under test.

    The poster emits a war-room status update ONLY while a client is in
    incident-mode, and ONLY through the mandatory gate stack:

      1. **SAFETY §6 freeze precedence** — if the client is frozen, the poster does
         NOT auto-send; it drafts to HITL (:meth:`post_status` returns
         :data:`OUTCOME_FROZEN_TO_HITL`). The freeze is the stronger guardrail;
         incident-mode cannot defeat it.
      2. **gate/safety** — the rendered status runs through the vendored hard-refusal
         bank; a fired refusal BLOCKS the post (audited, not published).
      3. **gate/citation_check at the §2.5 exact-match-or-slot-fill tier** — a status
         FACT must be a literal ``autocm_kb_constants`` slot-fill OR an exact quote of
         a surfaced KB chunk; ANY deviation (an uncited / free-text factual claim)
         BLOCKS the post (audited, not published).
      4. **SAFETY §5 audit** — every emit (published OR blocked OR frozen) persists
         the SAFETY §5 audit field set BEFORE/at publish, so the isolated autonomous
         outbound path is fully auditable.
    """

    def __init__(
        self,
        conn: Connection,
        publisher: StatusPublisher,
        *,
        hitl_drafter: Optional[HitlStatusDrafter] = None,
    ) -> None:
        self._conn = conn
        self._publisher = publisher
        self._hitl_drafter = hitl_drafter

    def post_status(
        self,
        client_id: int,
        chat_id: str,
        body: str,
        *,
        available_chunk_ids: Sequence[int] = (),
        org_id: Optional[str] = None,
        next_update_minutes: Optional[int] = None,
        now: Optional[datetime] = None,
        actor: str = AUDIT_SOURCE,
    ) -> Optional[ProactivePostResult]:
        """Emit one proactive war-room status update (the gated autonomous path).

        Renders the war-room status line (:func:`format_war_room_status`), then runs
        the mandatory gate stack in order: freeze precedence → gate/safety →
        gate/citation_check (§2.5 exact-match-or-slot-fill) → SAFETY §5 audit →
        publish. Returns the :class:`ProactivePostResult`, or ``None`` when the client
        is NOT in incident-mode (the poster only runs while ``incident_active``).

        ``available_chunk_ids`` is the retrieval-surfaced chunk set the citation gate
        validates an exact-quote status fact against (slot-fill values from
        ``autocm_kb_constants`` are validated by the gate directly). ``body`` is the
        operator/template-supplied status BODY (the fact); the war-room wrapper +
        timestamp are added here.
        """
        now = now or _utc_now()
        if not is_incident_active(self._conn, client_id):
            return None
        if org_id is None:
            org_id = _org_id_for_client(self._conn, client_id)

        rendered = format_war_room_status(
            body, now=now, next_update_minutes=next_update_minutes
        )

        # (0) /pause-client kill switch (C3.5c) — the STRONGEST halt: while the
        # client is paused, NOTHING publishes (not even a war-room status, not even
        # under incident-mode). The kill switch outranks incident-mode and the
        # freeze fallback: a paused client does not even draft-to-HITL a proactive
        # status (the operator pulled the cord; the poster goes fully silent).
        from sable_platform.autocm.operator.commands import is_publishing_paused

        if is_publishing_paused(self._conn, client_id):
            audit_id = self._audit(
                ACTION_PROACTIVE_BLOCKED,
                org_id=org_id,
                client_id=client_id,
                chat_id=chat_id,
                rendered=rendered,
                now=now,
                actor=actor,
                extra={"reason": "client_paused_kill_switch", "published": False},
            )
            return ProactivePostResult(
                outcome=OUTCOME_BLOCKED,
                audit_id=audit_id,
                rendered_text=rendered,
                reason="client_paused_kill_switch",
            )

        # (1) SAFETY §6 freeze precedence — the stronger guardrail wins even though
        # incident_active. Draft to HITL instead of auto-sending.
        if _is_client_frozen(self._conn, client_id, now=now):
            if self._hitl_drafter is not None:
                self._hitl_drafter.draft_to_hitl(
                    org_id or "", chat_id, rendered, reason="safety_freeze_active"
                )
            audit_id = self._audit(
                ACTION_PROACTIVE_FROZEN,
                org_id=org_id,
                client_id=client_id,
                chat_id=chat_id,
                rendered=rendered,
                now=now,
                actor=actor,
                extra={"reason": "safety_freeze_active", "published": False},
            )
            return ProactivePostResult(
                outcome=OUTCOME_FROZEN_TO_HITL,
                audit_id=audit_id,
                rendered_text=rendered,
                reason="safety_freeze_active",
            )

        # (2) gate/safety — a fired hard-refusal pattern BLOCKS the post.
        verdict = check_safety(rendered)
        if verdict.tripped:
            block_audit = audit_safety_block(
                self._conn,
                verdict,
                org_id=org_id,
                category=INCIDENT_CATEGORY,
                actor=actor,
            )
            self._audit(
                ACTION_PROACTIVE_BLOCKED,
                org_id=org_id,
                client_id=client_id,
                chat_id=chat_id,
                rendered=rendered,
                now=now,
                actor=actor,
                extra={
                    "reason": "safety_gate",
                    "safety_category": verdict.category,
                    "pattern": verdict.trigger,
                    "published": False,
                    "safety_block_audit_id": block_audit,
                },
            )
            return ProactivePostResult(
                outcome=OUTCOME_BLOCKED,
                audit_id=block_audit,
                rendered_text=rendered,
                reason="safety_gate",
            )

        # (3) gate/citation_check at the §2.5 highest-stakes tier — a status FACT
        # must be slot-filled / exact-matched; an uncited/free-text claim BLOCKS.
        citation = check_citations_db(
            self._conn,
            client_id,
            rendered,
            [],
            available_chunk_ids,
            tier=TIER_EXACT_MATCH,
        )
        if not citation.passed:
            audit_id = self._audit(
                ACTION_PROACTIVE_BLOCKED,
                org_id=org_id,
                client_id=client_id,
                chat_id=chat_id,
                rendered=rendered,
                now=now,
                actor=actor,
                extra={
                    "reason": "citation_gate",
                    "citation_tier": citation.tier,
                    "citation_reason": citation.reason,
                    "published": False,
                },
            )
            return ProactivePostResult(
                outcome=OUTCOME_BLOCKED,
                audit_id=audit_id,
                rendered_text=rendered,
                reason="citation_gate",
            )

        # (4) SAFETY §5 audit + publish (the cleared autonomous outbound path).
        handle = self._publisher.publish(org_id or "", chat_id, rendered)
        audit_id = self._audit(
            ACTION_PROACTIVE_POST,
            org_id=org_id,
            client_id=client_id,
            chat_id=chat_id,
            rendered=rendered,
            now=now,
            actor=actor,
            extra={
                "published": True,
                "publish_handle": handle,
                "citation_tier": citation.tier,
                "citation_reason": citation.reason,
            },
        )
        return ProactivePostResult(
            outcome=OUTCOME_PUBLISHED,
            audit_id=audit_id,
            rendered_text=rendered,
            handle=handle,
        )

    def _audit(
        self,
        action: str,
        *,
        org_id: Optional[str],
        client_id: int,
        chat_id: str,
        rendered: str,
        now: datetime,
        actor: str,
        extra: Optional[dict] = None,
    ) -> int:
        """Persist the SAFETY §5 audit field set for one proactive-post attempt.

        SAFETY §5 logs every reply with: source message id (N/A — proactive, recorded
        as None), KB sources cited, draft text + final posted text, reviewer (N/A —
        autonomous; recorded as the bot actor), tier + category + confidence,
        timestamp, refusal-pattern hits. The proactive poster has no source message
        (it is bot-originated) and no LLM confidence (it is a slot-filled/exact-matched
        status), so those fields carry their N/A markers — the field SET is present so
        the isolated autonomous outbound path is fully auditable.
        """
        detail = {
            "client_id": client_id,
            "chat_id": chat_id,
            "source_message_id": None,  # proactive: bot-originated, no source msg
            "category": INCIDENT_CATEGORY,
            "tier": 3,  # incident is tier-3 (permanently HITL); the poster is its outbound arm
            "register": WAR_ROOM_REGISTER,
            "confidence": None,  # slot-filled/exact-matched, not LLM-scored
            "reviewer": None,  # autonomous outbound path (the bot is the actor)
            "draft_text": rendered,
            "final_text": rendered if action == ACTION_PROACTIVE_POST else None,
            "posted_at": _iso_z(now),
            "proactive": True,
        }
        if extra:
            detail.update(extra)
        return log_audit(
            self._conn,
            actor=actor,
            action=action,
            org_id=org_id,
            entity_id=str(client_id),
            detail=detail,
            source=AUDIT_SOURCE,
        )


# ---------------------------------------------------------------------------
# (5) the auto-suggest threshold (≥3 incident-flagged in 10min OR any tier-3)
# ---------------------------------------------------------------------------
class OperatorNotifier(Protocol):
    """The injected operator-DM seam for the auto-suggest (no telegram/network).

    A real impl DMs the per-client operator chat (HITL surface). Tests inject a fake
    that records calls. Returns a surface handle (or None).
    """

    def suggest_incident_mode(
        self, org_id: str, client_id: int, body: str
    ) -> Optional[str]:
        ...


@dataclass(frozen=True)
class SuggestResult:
    """The outcome of an auto-suggest evaluation that FIRED.

    ``trigger`` is :data:`TRIGGER_TIER3` or :data:`TRIGGER_FLAG_THRESHOLD`;
    ``flag_count`` is the in-window incident-flag count at fire time (0 for a
    tier-3-triggered suggestion); ``handle`` is the operator-DM handle; ``audit_id``
    the suggestion audit row.
    """

    trigger: str
    flag_count: int
    handle: Optional[str]
    audit_id: int


# The "Suggest incident-mode?" DM body (FEATURE_INVENTORY G.3) — the operator
# confirms via /incident-mode on; the bot never flips the flag itself.
SUGGEST_DM_BODY = "Suggest incident-mode? [Yes][No, flag only]"


class IncidentSuggester:
    """Auto-suggest incident-mode on the ≥3-in-10min OR any-tier-3 threshold (C3.8b).

    A RUNTIME detector (not a historical audit query): it keeps a clock-injectable
    sliding window of incident-flag timestamps PER CLIENT in-process, and DMs the
    operator "Suggest incident-mode?" when either trigger fires
    (FEATURE_INVENTORY G.3):

      * **≥3 incident-flagged messages within 10 minutes** — :meth:`record_incident_flag`
        appends a timestamp, prunes the window to the last :data:`SUGGEST_WINDOW_MINUTES`,
        and fires when the count reaches :data:`SUGGEST_MIN_INCIDENT_FLAGS`.
      * **any tier-3 escalation fires** — :meth:`on_tier3_fired` suggests immediately
        (one tier-3 is enough; FEATURE_INVENTORY G.5: a tier-3 always fires alongside
        an incident).

    A fired suggestion DMs the operator (injected :class:`OperatorNotifier`) and
    writes an ``incident_mode_suggested`` audit row. The suggester NEVER flips the
    flag — the operator confirms via ``/incident-mode on`` (incident handling is
    never autonomous). De-bounce: once a suggestion has fired for a client it will not
    re-fire on the flag-threshold leg until the window empties OR the client actually
    enters incident-mode (checked live) — so the operator is not spammed every new
    flag during a sustained crisis. ``now`` is injectable so the 10-minute window is
    deterministic under test.
    """

    def __init__(
        self,
        conn: Connection,
        notifier: OperatorNotifier,
        *,
        window_minutes: int = SUGGEST_WINDOW_MINUTES,
        min_flags: int = SUGGEST_MIN_INCIDENT_FLAGS,
    ) -> None:
        self._conn = conn
        self._notifier = notifier
        self._window = timedelta(minutes=window_minutes)
        self._min_flags = min_flags
        self._flags: Dict[int, Deque[datetime]] = {}
        # de-bounce: clients we have already suggested for on the current window.
        self._suggested: set[int] = set()

    def record_incident_flag(
        self,
        client_id: int,
        *,
        org_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[SuggestResult]:
        """Record an incident-flagged message; suggest if the 10-min threshold trips.

        Appends ``now`` to the client's in-process window, prunes entries older than
        :data:`SUGGEST_WINDOW_MINUTES`, and — when the window count reaches
        :data:`SUGGEST_MIN_INCIDENT_FLAGS` AND no suggestion has already fired for
        this window AND the client is not already in incident-mode — fires a
        suggestion (returns the :class:`SuggestResult`). Otherwise returns ``None``.
        """
        now = now or _utc_now()
        window = self._flags.setdefault(client_id, deque())
        window.append(now)
        cutoff = now - self._window
        while window and window[0] < cutoff:
            window.popleft()
        # window empty-ish → reset de-bounce so a NEW crisis can suggest again.
        if len(window) < self._min_flags:
            self._suggested.discard(client_id)
            return None
        if client_id in self._suggested:
            return None
        if is_incident_active(self._conn, client_id):
            # already in incident-mode — nothing to suggest.
            self._suggested.add(client_id)
            return None
        return self._fire(
            client_id,
            trigger=TRIGGER_FLAG_THRESHOLD,
            flag_count=len(window),
            org_id=org_id,
            now=now,
        )

    def on_tier3_fired(
        self,
        client_id: int,
        *,
        org_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[SuggestResult]:
        """Suggest incident-mode because a tier-3 escalation fired (any one suffices).

        FEATURE_INVENTORY G.3 / G.5: a single tier-3 fires the suggestion. Returns
        the :class:`SuggestResult`, or ``None`` when the client is already in
        incident-mode (nothing to suggest). The tier-3 leg does not consult the
        flag window — one tier-3 is enough.
        """
        now = now or _utc_now()
        if is_incident_active(self._conn, client_id):
            return None
        return self._fire(
            client_id,
            trigger=TRIGGER_TIER3,
            flag_count=len(self._flags.get(client_id, ())),
            org_id=org_id,
            now=now,
        )

    def _fire(
        self,
        client_id: int,
        *,
        trigger: str,
        flag_count: int,
        org_id: Optional[str],
        now: datetime,
    ) -> SuggestResult:
        if org_id is None:
            org_id = _org_id_for_client(self._conn, client_id)
        handle = self._notifier.suggest_incident_mode(
            org_id or "", client_id, SUGGEST_DM_BODY
        )
        audit_id = log_audit(
            self._conn,
            actor=AUDIT_SOURCE,
            action=ACTION_SUGGEST,
            org_id=org_id,
            entity_id=str(client_id),
            detail={
                "client_id": client_id,
                "trigger": trigger,
                "flag_count": flag_count,
                "window_minutes": int(self._window.total_seconds() // 60),
                "min_flags": self._min_flags,
                "suggested_at": _iso_z(now),
                "dm_sent": handle is not None,
                # the suggester only SUGGESTS — the operator confirms.
                "autonomous": False,
            },
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        self._suggested.add(client_id)
        return SuggestResult(
            trigger=trigger, flag_count=flag_count, handle=handle, audit_id=audit_id
        )


__all__ = [
    # constants
    "INCIDENT_CATEGORY",
    "WAR_ROOM_REGISTER",
    "SUGGEST_MIN_INCIDENT_FLAGS",
    "SUGGEST_WINDOW_MINUTES",
    "SUGGEST_DM_BODY",
    "OUTCOME_PUBLISHED",
    "OUTCOME_BLOCKED",
    "OUTCOME_FROZEN_TO_HITL",
    "TRIGGER_TIER3",
    "TRIGGER_FLAG_THRESHOLD",
    # (1) toggle
    "is_incident_active",
    "set_incident_mode",
    # (2) war-room register
    "war_room_register",
    "format_war_room_status",
    # (3) tier-1 suppression
    "is_tier1_suppressed",
    "should_engage_during_incident",
    # (4) proactive poster
    "StatusPublisher",
    "HitlStatusDrafter",
    "ProactivePostResult",
    "ProactiveStatusPoster",
    # (5) auto-suggest
    "OperatorNotifier",
    "SuggestResult",
    "IncidentSuggester",
    # audit verbs
    "ACTION_INCIDENT_ON",
    "ACTION_INCIDENT_OFF",
    "ACTION_PROACTIVE_POST",
    "ACTION_PROACTIVE_BLOCKED",
    "ACTION_PROACTIVE_FROZEN",
    "ACTION_SUGGEST",
]
