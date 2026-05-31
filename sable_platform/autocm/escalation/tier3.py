"""Tier-3 escalation + Arf-only routing + SAFETY §6 48h freeze (MEGAPLAN C3.8a).

DESIGN §5 (tier-3 = founder + Sable on-call) / §7 (autonomy trigger 4 — founder
complaint about an auto-sent reply) / SAFETY §6 (the client-wide 48h pure-HITL
freeze). This module owns:

**Dual-route (founder + Sable on-call / Arf), DESIGN §5 / decision-10.** The
tier-3 categories — ``threat`` / ``whale_inbound`` / ``founder_voice_needed`` and
the regulatory ``incident`` — route to the founder AND the Sable on-call (Arf)
SIMULTANEOUSLY (:func:`dual_route_tier3`). The public reply is SUPPRESSED — NULO
NEVER auto-drafts a substantive answer to a tier-3 (the founder authors). If the
founder is unacknowledged within ``ack_window_hours`` (the documented N-hour
backstop, HITL_UX §0 / DESIGN decision-10), the on-call handles via the
documented playbook (:func:`handle_unacknowledged_escalations`) — and the ack
window the founder was given is RECORDED on the escalation row + audit.

**The tier-3 2-min PushNotification (HITL_UX §3 SLA, distinct from the N-hour
backstop).** A tier-3 escalation untouched (founder still ``notified``, never
``acknowledged``) for :data:`PUSH_AFTER_MINUTES` fires a PushNotification via the
injected :class:`EscalationNotifier` seam (:func:`sweep_tier3_push_notifications`)
— the higher-priority alert, NOT the same thing as the on-call N-hour playbook
handoff.

**Arf-only routing (CLASSIFIER §2), :func:`route_arf_only`.** ``conflict_detected``
and ``moderation_flag`` route to Arf for HUMAN handling with the public reply
SUPPRESSED (NULO never responds publicly) — this is NOT the tier-3 founder
dual-route (the founder is not pulled in). On ``moderation_flag`` the author is
ALSO auto-silenced (:func:`auto_silence_user` → ``autocm_flagged_users``
``status='silenced'``) so the C3.4a flagged-user pre-filter
(``db.is_flagged_user``) drops them until a mod ``/clear-flag``s them.

**Founder-complaint auto-demote (DESIGN §7 trigger 4), :func:`demote_on_founder_complaint`.**
When the founder escalates a complaint about an AUTO-sent reply, the offending
category is demoted ``auto`` → ``hitl`` immediately (one category) — distinct
from the global freeze below. (C3.5a owns triggers 1+3; C3.5c owns trigger 2 —
the operator-mark demote; this is trigger 4.)

**SAFETY §6 48h pure-HITL freeze (the client-wide reputational guardrail),
:func:`freeze_client` / :func:`restore_expired_freezes`.** A founder DM-flag /
operator trigger sets EVERY ``autocm_category_state`` row for the client to
``hitl`` with a ``freeze_until`` ≥48h in the future, writes an audit row, and
emits a digest post-mortem hook (consumed by C3.7). During the freeze the bot
KEEPS DRAFTING + HITL-reviewing — only autonomous AUTO-SEND is frozen (the C3.5a
``gate/confidence.is_frozen`` read forces every category to HITL while
``freeze_until`` is active). After ``freeze_until`` passes,
:func:`restore_expired_freezes` AUTO-RESTORES each category to its PRIOR state
(the state captured at freeze time, stashed in the freeze row) and clears the
freeze columns. This is a DISTINCT global mode — NOT per-category demotion
(C3.5a, one category), NOT ``/pause-client`` (C3.5c, which halts ALL publishing
incl. HITL-approved replies — the OPPOSITE of "pure HITL"), and NOT relay
``disable``/``pause-org``.

**Schema note (prior-state capture without a schema change).** 058's
``autocm_category_state`` carries ``freeze_until`` / ``freeze_reason`` /
``frozen_by`` but NO ``prior_state`` column. Auto-restore must remember each
category's state at freeze time, so :func:`freeze_client` encodes a structured
JSON envelope into ``freeze_reason`` — ``{"reason": <text>, "prior_state":
<hitl|auto>, "frozen_at": <iso>}`` — and :func:`restore_expired_freezes` reads
``prior_state`` back out of it. The human-readable reason is preserved under the
envelope's ``reason`` key (and surfaced by :func:`freeze_reason_text`).

**No telegram / network in this module.** All outbound (founder DM, on-call ping,
PushNotification) goes through the injected :class:`EscalationNotifier` Protocol;
tests inject a fake that records calls. All timestamps are computed in Python as
UTC ISO-8601 ``...Z`` and bound as parameters (never ``strftime('now')``), so the
SQL is dialect-agnostic and runs unchanged on the live Postgres pool — matching
the ``relay/db.py`` / ``autocm/db.py`` contract.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.audit import log_audit

# ---------------------------------------------------------------------------
# Routing constants (CLASSIFIER §2 / DESIGN §5)
# ---------------------------------------------------------------------------
#: The tier-3 founder dual-route categories: founder + Sable on-call (Arf),
#: public reply suppressed, NO auto-draft. ``incident`` is the regulatory /
#: war-room tier-3 (its incident-MODE subsystem is C3.8b; its tier-3 dual-route
#: AWARENESS is here). ``threat`` is safety-critical (physical / exploit threat)
#: and must never fall through to a lower tier or auto-draft.
DUAL_ROUTE_CATEGORIES = (
    "threat",
    "whale_inbound",
    "founder_voice_needed",
    "incident",
)

#: The Arf-only HUMAN-handling categories (CLASSIFIER §2): routed to the Sable
#: on-call (Arf), public reply suppressed (NULO does not respond publicly), the
#: founder is NOT pulled in. ``moderation_flag`` ALSO auto-silences the author.
ARF_ONLY_CATEGORIES = ("conflict_detected", "moderation_flag")

#: The category whose Arf-only routing ALSO auto-silences the author.
AUTO_SILENCE_CATEGORY = "moderation_flag"

# Route kinds (the stable strings carried on the route plan / audit / escalation).
ROUTE_DUAL = "dual_route"   # founder + on-call
ROUTE_ARF_ONLY = "arf_only"  # on-call (Arf) only — human handling

# ---------------------------------------------------------------------------
# SLA / backstop windows
# ---------------------------------------------------------------------------
#: HITL_UX §3: a tier-3 escalation untouched in 2 min fires a PushNotification
#: (distinct from the N-hour → on-call playbook backstop).
PUSH_AFTER_MINUTES = 2
#: DESIGN decision-10 / HITL_UX §0: if the founder is unacknowledged within N
#: hours, the Sable on-call (Arf) handles via the documented playbook.
DEFAULT_ACK_WINDOW_HOURS = 2
#: SAFETY §6: the pure-HITL freeze lasts AT LEAST 48h.
FREEZE_MIN_HOURS = 48

# log_audit verbs (audit-everything; source="sable-autocm").
AUDIT_SOURCE = "sable-autocm"
ACTION_TIER3_DUAL_ROUTE = "tier3_dual_routed"
ACTION_TIER3_PUSH = "tier3_push_notification"
ACTION_TIER3_ONCALL_HANDOFF = "tier3_oncall_handoff"
ACTION_ARF_ROUTED = "arf_only_routed"
ACTION_USER_SILENCED = "flagged_user_auto_silenced"
ACTION_DEMOTE_FOUNDER = "autonomy_demoted_founder_complaint"
ACTION_FREEZE = "safety_freeze_applied"
ACTION_FREEZE_RESTORED = "safety_freeze_restored"

# autocm_escalations status values (058 CHECK set).
STATUS_PENDING = "pending"
STATUS_NOTIFIED = "notified"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RESOLVED = "resolved"


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


# ---------------------------------------------------------------------------
# Routing decision (pure — no I/O)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoutePlan:
    """How a classified category should be escalated/routed (pure decision).

    ``route`` is :data:`ROUTE_DUAL` (founder + on-call), :data:`ROUTE_ARF_ONLY`
    (on-call only, human handling), or ``None`` (not an escalation category — the
    normal tier-1/2 pipeline handles it). ``suppress_public_reply`` is True for
    EVERY escalation/route category (NULO never auto-answers a tier-3, and never
    responds publicly to a conflict/moderation route). ``auto_silence`` is True
    only for ``moderation_flag``.
    """

    category: str
    route: Optional[str]
    suppress_public_reply: bool
    notify_founder: bool
    notify_oncall: bool
    auto_silence: bool

    @property
    def is_escalation(self) -> bool:
        return self.route is not None


def route_for_category(category: str) -> RoutePlan:
    """Pure routing decision for a classified category (DESIGN §5 / CLASSIFIER §2).

    Tier-3 (``threat`` / ``whale_inbound`` / ``founder_voice_needed`` /
    ``incident``) → :data:`ROUTE_DUAL` (founder + on-call), public reply
    suppressed. ``conflict_detected`` / ``moderation_flag`` → :data:`ROUTE_ARF_ONLY`
    (on-call only), public reply suppressed; ``moderation_flag`` also auto-silences
    the author. Any other category → no route (``route=None``) — the normal
    pipeline owns it.
    """
    if category in DUAL_ROUTE_CATEGORIES:
        return RoutePlan(
            category=category,
            route=ROUTE_DUAL,
            suppress_public_reply=True,
            notify_founder=True,
            notify_oncall=True,
            auto_silence=False,
        )
    if category in ARF_ONLY_CATEGORIES:
        return RoutePlan(
            category=category,
            route=ROUTE_ARF_ONLY,
            suppress_public_reply=True,
            notify_founder=False,
            notify_oncall=True,
            auto_silence=(category == AUTO_SILENCE_CATEGORY),
        )
    return RoutePlan(
        category=category,
        route=None,
        suppress_public_reply=False,
        notify_founder=False,
        notify_oncall=False,
        auto_silence=False,
    )


# ---------------------------------------------------------------------------
# Notifier seam — the injected transport for founder DM / on-call ping / push
# ---------------------------------------------------------------------------
class EscalationNotifier(Protocol):
    """The injected outbound seam (NO telegram/network in this module).

    A real impl wraps the founder DM channel (TG DM per DESIGN §10), the Sable
    on-call ping (Arf), and the PushNotification surface; tests inject a fake that
    records calls. Every method returns a surface-specific handle (or None). The
    module NEVER imports a transport — it only ever talks to this Protocol, so the
    tier-3 logic is unit-testable offline.
    """

    def notify_founder(self, org_id: str, escalation_id: int, body: str) -> Optional[str]:
        ...

    def notify_oncall(self, org_id: str, escalation_id: int, body: str) -> Optional[str]:
        ...

    def push(self, org_id: str, escalation_id: int, body: str) -> Optional[str]:
        ...


# ---------------------------------------------------------------------------
# Tier3Escalator Protocol (the C3.1 seam — kept) + the C3.8a impl
# ---------------------------------------------------------------------------
class Tier3Escalator(Protocol):
    """Record + dual-route a tier-3 escalation (founder + Sable on-call)."""

    def escalate(self, client_id: int, draft_id: int, reason: str) -> int:
        """Record an ``autocm_escalations`` row; return its id."""
        ...


@dataclass(frozen=True)
class EscalationResult:
    """The outcome of routing one classified message through the escalator.

    ``escalation_id`` is the ``autocm_escalations`` row (None for an Arf-only route
    that is recorded but founder-less is still an escalation row — see below).
    ``route`` mirrors the :class:`RoutePlan`. ``founder_handle`` / ``oncall_handle``
    / ``push_handle`` are the notifier handles (None when that leg was not sent).
    ``flagged_user_id`` is the ``autocm_flagged_users`` row id when a moderation
    flag auto-silenced the author. ``ack_deadline`` is the ISO ``...Z`` instant the
    founder must acknowledge by (dual-route only).
    """

    escalation_id: Optional[int]
    route: Optional[str]
    suppressed_public_reply: bool
    founder_handle: Optional[str] = None
    oncall_handle: Optional[str] = None
    push_handle: Optional[str] = None
    flagged_user_id: Optional[int] = None
    ack_deadline: Optional[str] = None


class Tier3EscalationRouter:
    """The C3.8a tier-3 + Arf-only router over ``autocm_escalations`` + the notifier.

    Construction takes the live SP-pool :class:`Connection` (the caller owns
    lifecycle — this creates no engine) and an injected :class:`EscalationNotifier`
    (a fake in tests; the real founder-DM / on-call / push transport in prod). The
    ``ack_window_hours`` is the documented N-hour founder-acknowledgement backstop
    (DESIGN decision-10 / HITL_UX §0).
    """

    def __init__(
        self,
        conn: Connection,
        notifier: EscalationNotifier,
        *,
        ack_window_hours: int = DEFAULT_ACK_WINDOW_HOURS,
    ) -> None:
        self._conn = conn
        self._notifier = notifier
        self._ack_window_hours = ack_window_hours

    # -- the entry point: route one classified message ------------------------
    def route(
        self,
        client_id: int,
        category: str,
        *,
        org_id: Optional[str] = None,
        draft_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        reason: Optional[str] = None,
        member_id: Optional[int] = None,
        external_user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[EscalationResult]:
        """Route a classified message per :func:`route_for_category`.

        Returns an :class:`EscalationResult` for an escalation/route category, or
        ``None`` for a non-escalation category (the normal pipeline owns it). The
        public reply is ALWAYS suppressed for any returned result (no auto-draft on
        a tier-3, no public reply on a conflict/moderation route).
        """
        plan = route_for_category(category)
        if not plan.is_escalation:
            return None
        if plan.route == ROUTE_DUAL:
            return self.dual_route_tier3(
                client_id,
                category,
                org_id=org_id,
                draft_id=draft_id,
                source_message_id=source_message_id,
                reason=reason,
                now=now,
            )
        return self.route_arf_only(
            client_id,
            category,
            org_id=org_id,
            draft_id=draft_id,
            source_message_id=source_message_id,
            reason=reason,
            member_id=member_id,
            external_user_id=external_user_id,
            now=now,
        )

    # -- dual-route (founder + on-call) ---------------------------------------
    def dual_route_tier3(
        self,
        client_id: int,
        category: str,
        *,
        org_id: Optional[str] = None,
        draft_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        reason: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> EscalationResult:
        """DESIGN §5 / decision-10: route a tier-3 to founder AND on-call (Arf).

        Records ONE ``autocm_escalations`` row, notifies BOTH the founder and the
        Sable on-call SIMULTANEOUSLY (both legs of the dual-route), flips
        ``founder_status`` + ``oncall_status`` to ``notified``, and records the
        founder ack deadline (``now + ack_window_hours``) on the audit trail so the
        N-hour backstop is auditable. The public reply is suppressed — NULO never
        auto-drafts a tier-3.
        """
        now = now or _utc_now()
        if org_id is None:
            org_id = self._org_id_for_client(client_id)
        reason = reason or f"tier-3 dual-route ({category})"

        escalation_id = self._insert_escalation(
            client_id,
            draft_id=draft_id,
            source_message_id=source_message_id,
            reason=reason,
            now=now,
        )
        ack_deadline = _iso_z(now + timedelta(hours=self._ack_window_hours))
        body = (
            f"TIER-3 ESCALATION ({category}) — founder authorship required. "
            f"Ack within {self._ack_window_hours}h (by {ack_deadline}) or on-call "
            f"(Arf) handles via playbook. reason: {reason}"
        )
        # both legs SIMULTANEOUSLY (founder + on-call).
        founder_handle = self._notifier.notify_founder(org_id or "", escalation_id, body)
        oncall_handle = self._notifier.notify_oncall(org_id or "", escalation_id, body)
        self._set_statuses(escalation_id, founder=STATUS_NOTIFIED, oncall=STATUS_NOTIFIED)

        log_audit(
            self._conn,
            actor=AUDIT_SOURCE,
            action=ACTION_TIER3_DUAL_ROUTE,
            org_id=org_id,
            entity_id=str(escalation_id),
            detail={
                "client_id": client_id,
                "category": category,
                "route": ROUTE_DUAL,
                "draft_id": draft_id,
                "source_message_id": source_message_id,
                "reason": reason,
                "suppress_public_reply": True,
                "founder_notified": founder_handle is not None,
                "oncall_notified": oncall_handle is not None,
                "ack_window_hours": self._ack_window_hours,
                "ack_deadline": ack_deadline,
            },
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        return EscalationResult(
            escalation_id=escalation_id,
            route=ROUTE_DUAL,
            suppressed_public_reply=True,
            founder_handle=founder_handle,
            oncall_handle=oncall_handle,
            ack_deadline=ack_deadline,
        )

    # -- Arf-only route (conflict / moderation) -------------------------------
    def route_arf_only(
        self,
        client_id: int,
        category: str,
        *,
        org_id: Optional[str] = None,
        draft_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        reason: Optional[str] = None,
        member_id: Optional[int] = None,
        external_user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> EscalationResult:
        """CLASSIFIER §2: route ``conflict_detected`` / ``moderation_flag`` to Arf.

        On-call (Arf) ONLY — the founder is NOT pulled in (this is human handling,
        never the founder dual-route). The public reply is SUPPRESSED (NULO does not
        respond publicly). On ``moderation_flag`` the author is ALSO auto-silenced
        (:func:`auto_silence_user`) so the C3.4a flagged-user pre-filter drops them
        until a mod clears the flag.

        Records an ``autocm_escalations`` row (the on-call leg notified, the founder
        leg left ``pending`` — it never routes to the founder) so the Arf-only
        handling is auditable on the same ledger as the dual-route.
        """
        now = now or _utc_now()
        if org_id is None:
            org_id = self._org_id_for_client(client_id)
        reason = reason or f"Arf-only route ({category})"

        escalation_id = self._insert_escalation(
            client_id,
            draft_id=draft_id,
            source_message_id=source_message_id,
            reason=reason,
            now=now,
        )
        body = (
            f"ARF-ONLY ROUTE ({category}) — human handling; NULO public reply "
            f"suppressed. reason: {reason}"
        )
        oncall_handle = self._notifier.notify_oncall(org_id or "", escalation_id, body)
        # founder leg stays 'pending' (never routes to the founder for Arf-only).
        self._set_statuses(escalation_id, oncall=STATUS_NOTIFIED)

        flagged_user_id: Optional[int] = None
        if category == AUTO_SILENCE_CATEGORY:
            flagged_user_id = auto_silence_user(
                self._conn,
                client_id,
                member_id=member_id,
                external_user_id=external_user_id,
                reason=f"moderation_flag auto-silence (escalation {escalation_id})",
                org_id=org_id,
            )

        log_audit(
            self._conn,
            actor=AUDIT_SOURCE,
            action=ACTION_ARF_ROUTED,
            org_id=org_id,
            entity_id=str(escalation_id),
            detail={
                "client_id": client_id,
                "category": category,
                "route": ROUTE_ARF_ONLY,
                "draft_id": draft_id,
                "source_message_id": source_message_id,
                "reason": reason,
                "suppress_public_reply": True,
                "oncall_notified": oncall_handle is not None,
                "auto_silenced": flagged_user_id is not None,
                "flagged_user_id": flagged_user_id,
            },
            source=AUDIT_SOURCE,
        )
        self._conn.commit()
        return EscalationResult(
            escalation_id=escalation_id,
            route=ROUTE_ARF_ONLY,
            suppressed_public_reply=True,
            oncall_handle=oncall_handle,
            flagged_user_id=flagged_user_id,
        )

    # -- founder acknowledgement ----------------------------------------------
    def acknowledge(self, escalation_id: int, *, now: Optional[datetime] = None) -> bool:
        """Mark an escalation founder-acknowledged (stops the on-call N-hour handoff).

        Returns True iff the row existed and was flipped (idempotent — a row already
        ``acknowledged`` / ``resolved`` returns False). A founder ack within the
        N-hour window means the on-call backstop does NOT fire.
        """
        now = now or _utc_now()
        row = self._conn.execute(
            text("SELECT founder_status FROM autocm_escalations WHERE id = :id"),
            {"id": escalation_id},
        ).fetchone()
        if row is None or row[0] in (STATUS_ACKNOWLEDGED, STATUS_RESOLVED):
            return False
        self._conn.execute(
            text(
                "UPDATE autocm_escalations SET founder_status = :s WHERE id = :id"
            ),
            {"s": STATUS_ACKNOWLEDGED, "id": escalation_id},
        )
        self._conn.commit()
        return True

    # -- the N-hour → on-call playbook backstop -------------------------------
    def handle_unacknowledged_escalations(
        self,
        client_id: int,
        *,
        org_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> List[int]:
        """DESIGN decision-10: founder unacknowledged in N hours → on-call playbook.

        For every DUAL-ROUTE escalation of the client whose founder leg is still
        ``notified`` (never acknowledged) older than ``ack_window_hours``, hand off
        to the Sable on-call (Arf) via the documented playbook: flip the on-call leg
        to ``acknowledged`` (Arf now owns it), notify the on-call of the handoff, and
        write an ``tier3_oncall_handoff`` audit row RECORDING THE ACK WINDOW the
        founder was given (so the backstop is auditable). Returns the handed-off
        escalation ids. Distinct from the 2-min PushNotification (a louder alert,
        not a handoff).
        """
        now = now or _utc_now()
        if org_id is None:
            org_id = self._org_id_for_client(client_id)
        cutoff = _iso_z(now - timedelta(hours=self._ack_window_hours))

        rows = self._conn.execute(
            text(
                "SELECT id, created_at, reason FROM autocm_escalations "
                "WHERE client_id = :c AND founder_status = :notified "
                "  AND oncall_status = :notified "
                "  AND created_at <= :cutoff "
                "ORDER BY id"
            ),
            {"c": client_id, "notified": STATUS_NOTIFIED, "cutoff": cutoff},
        ).fetchall()

        handed_off: List[int] = []
        for r in rows:
            escalation_id = int(r[0])
            created_at = r[1]
            body = (
                f"ON-CALL HANDOFF — founder unacknowledged after "
                f"{self._ack_window_hours}h; Arf handles per playbook "
                f"(escalation {escalation_id})."
            )
            self._notifier.notify_oncall(org_id or "", escalation_id, body)
            self._conn.execute(
                text(
                    "UPDATE autocm_escalations SET oncall_status = :s WHERE id = :id"
                ),
                {"s": STATUS_ACKNOWLEDGED, "id": escalation_id},
            )
            log_audit(
                self._conn,
                actor=AUDIT_SOURCE,
                action=ACTION_TIER3_ONCALL_HANDOFF,
                org_id=org_id,
                entity_id=str(escalation_id),
                detail={
                    "client_id": client_id,
                    "escalation_id": escalation_id,
                    "ack_window_hours": self._ack_window_hours,
                    "escalation_created_at": created_at,
                    "handed_off_at": _iso_z(now),
                    "playbook": "on-call (Arf) handles per documented tier-3 playbook",
                },
                source=AUDIT_SOURCE,
            )
            handed_off.append(escalation_id)
        self._conn.commit()
        return handed_off

    # -- the 2-min PushNotification (HITL_UX §3) ------------------------------
    def sweep_tier3_push_notifications(
        self,
        client_id: int,
        *,
        org_id: Optional[str] = None,
        now: Optional[datetime] = None,
        push_after_minutes: int = PUSH_AFTER_MINUTES,
    ) -> List[int]:
        """HITL_UX §3: a tier-3 untouched in 2 min fires a PushNotification.

        For every DUAL-ROUTE escalation of the client whose founder leg is still
        ``notified`` (never acknowledged) older than ``push_after_minutes``, fire a
        PushNotification via the injected notifier and write a ``tier3_push_notification``
        audit row. Idempotent within a sweep via the ``pushed_at`` bookkeeping on
        ``resolved_at`` is NOT used (that column tracks resolution) — instead the
        push is recorded in the audit trail; the sweep is intended to run on a short
        cadence and the louder alert is acceptable to re-fire until the founder acks.
        Returns the escalation ids a push fired for. Distinct from the N-hour on-call
        handoff (a different, longer backstop).
        """
        now = now or _utc_now()
        if org_id is None:
            org_id = self._org_id_for_client(client_id)
        cutoff = _iso_z(now - timedelta(minutes=push_after_minutes))

        rows = self._conn.execute(
            text(
                "SELECT id FROM autocm_escalations "
                "WHERE client_id = :c AND founder_status = :notified "
                "  AND created_at <= :cutoff "
                "ORDER BY id"
            ),
            {"c": client_id, "notified": STATUS_NOTIFIED, "cutoff": cutoff},
        ).fetchall()

        pushed: List[int] = []
        for r in rows:
            escalation_id = int(r[0])
            body = (
                f"PUSH — tier-3 escalation {escalation_id} untouched for "
                f"{push_after_minutes} min. First touch overdue."
            )
            self._notifier.push(org_id or "", escalation_id, body)
            log_audit(
                self._conn,
                actor=AUDIT_SOURCE,
                action=ACTION_TIER3_PUSH,
                org_id=org_id,
                entity_id=str(escalation_id),
                detail={
                    "client_id": client_id,
                    "escalation_id": escalation_id,
                    "push_after_minutes": push_after_minutes,
                    "pushed_at": _iso_z(now),
                },
                source=AUDIT_SOURCE,
            )
            pushed.append(escalation_id)
        self._conn.commit()
        return pushed

    # -- the C3.1 Protocol method (kept) --------------------------------------
    def escalate(self, client_id: int, draft_id: int, reason: str) -> int:
        """Tier3Escalator Protocol: dual-route + return the escalation row id.

        Thin compatibility wrapper over :func:`dual_route_tier3` so the C3.1 seam
        callers (which pass a free-text reason, not a category) still work — it
        records + dual-routes and returns the ``autocm_escalations`` id.
        """
        result = self.dual_route_tier3(client_id, "founder_voice_needed", draft_id=draft_id, reason=reason)
        assert result.escalation_id is not None
        return result.escalation_id

    # -- internals ------------------------------------------------------------
    def _insert_escalation(
        self,
        client_id: int,
        *,
        draft_id: Optional[int],
        source_message_id: Optional[int],
        reason: Optional[str],
        now: datetime,
    ) -> int:
        row = self._conn.execute(
            text(
                "INSERT INTO autocm_escalations "
                "(client_id, draft_id, source_message_id, reason, created_at) "
                "VALUES (:c, :d, :smi, :reason, :now) RETURNING id"
            ),
            {
                "c": client_id,
                "d": draft_id,
                "smi": source_message_id,
                "reason": reason,
                "now": _iso_z(now),
            },
        ).fetchone()
        return int(row[0])

    def _set_statuses(
        self,
        escalation_id: int,
        *,
        founder: Optional[str] = None,
        oncall: Optional[str] = None,
    ) -> None:
        sets = []
        params: dict = {"id": escalation_id}
        if founder is not None:
            sets.append("founder_status = :fs")
            params["fs"] = founder
        if oncall is not None:
            sets.append("oncall_status = :os")
            params["os"] = oncall
        if not sets:
            return
        self._conn.execute(
            text(f"UPDATE autocm_escalations SET {', '.join(sets)} WHERE id = :id"),
            params,
        )

    def _org_id_for_client(self, client_id: int) -> Optional[str]:
        return _org_id_for_client(self._conn, client_id)


# ---------------------------------------------------------------------------
# Auto-silence into autocm_flagged_users (read by the C3.4a flagged-user pre-filter)
# ---------------------------------------------------------------------------
def auto_silence_user(
    conn: Connection,
    client_id: int,
    *,
    member_id: Optional[int] = None,
    external_user_id: Optional[str] = None,
    reason: Optional[str] = None,
    org_id: Optional[str] = None,
    now: Optional[datetime] = None,
    actor: str = AUDIT_SOURCE,
) -> Optional[int]:
    """Auto-silence a flagged user into ``autocm_flagged_users`` (status='silenced').

    Inserts (or re-activates) a ``silenced`` row so the C3.4a flagged-user
    pre-filter (``autocm.db.is_flagged_user``) drops the author until a mod
    ``/clear-flag``s them. Matches on EITHER ``member_id`` OR ``external_user_id``;
    when an active ``silenced`` row already exists for the identity it is a no-op
    that returns the existing id (idempotent). Returns the flagged-user row id, or
    ``None`` when neither identifier is supplied (we cannot silence an unidentified
    author — the caller's other routing still applies).

    Writes a ``flagged_user_auto_silenced`` audit row (SAFETY §5: the silence is an
    auditable action). The author is silenced for THIS client only.
    """
    if member_id is None and external_user_id is None:
        return None
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)

    # idempotent: an already-active silenced row for this identity is reused.
    existing = _find_active_silenced(conn, client_id, member_id, external_user_id)
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
            "reason": reason or "auto-silenced (moderation_flag)",
            "now": _iso_z(now),
        },
    ).fetchone()
    flagged_id = int(row[0])
    log_audit(
        conn,
        actor=actor,
        action=ACTION_USER_SILENCED,
        org_id=org_id,
        entity_id=str(flagged_id),
        detail={
            "client_id": client_id,
            "member_id": member_id,
            "external_user_id": external_user_id,
            "reason": reason or "auto-silenced (moderation_flag)",
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return flagged_id


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
# DESIGN §7 trigger (4): founder complaint about an auto-sent reply → demote
# ---------------------------------------------------------------------------
def demote_on_founder_complaint(
    conn: Connection,
    client_id: int,
    category: str,
    *,
    org_id: Optional[str] = None,
    actor: str = AUDIT_SOURCE,
    detail: Optional[dict] = None,
) -> bool:
    """DESIGN §7 trigger (4): founder complaint about an auto-sent reply → HITL.

    Flips the offending category from ``auto`` to ``hitl`` immediately and writes an
    ``autonomy_demoted_founder_complaint`` audit row. Returns True iff the category
    was actually ``auto`` and got flipped (idempotent — a category already HITL is a
    no-op returning False). This is ONE category (the offending one) — distinct from
    the SAFETY §6 global 48h freeze (:func:`freeze_client`), which freezes EVERY
    category for the client.

    Reuses the C3.5a ``gate/autonomy`` state-write path (``_set_category_state``) so
    the demotion goes through the same upsert the rest of the autonomy machine uses;
    only the audit verb differs (trigger 4 vs trigger 3's safety-slip).
    """
    from sable_platform.autocm.gate.autonomy import (
        _get_category_state,
        _set_category_state,
    )

    current = _get_category_state(conn, client_id, category)
    if current != "auto":
        return False
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    _set_category_state(conn, client_id, category, "hitl")
    log_audit(
        conn,
        actor=actor,
        action=ACTION_DEMOTE_FOUNDER,
        org_id=org_id,
        entity_id=f"{client_id}:{category}",
        detail={"client_id": client_id, "category": category, **(detail or {})},
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# SAFETY §6 — the client-wide 48h pure-HITL freeze
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FrozenCategory:
    """One category frozen by :func:`freeze_client` (its state before the freeze)."""

    category: str
    prior_state: str
    freeze_until: str


def _freeze_envelope(reason: Optional[str], prior_state: str, frozen_at: str) -> str:
    """Encode the freeze_reason envelope (carries prior_state for auto-restore).

    058's ``autocm_category_state`` has no ``prior_state`` column, so the state to
    restore to after the freeze is stashed in the ``freeze_reason`` TEXT column as a
    structured JSON envelope. The human-readable reason is preserved under
    ``reason``.
    """
    return json.dumps(
        {"reason": reason or "SAFETY §6 freeze", "prior_state": prior_state, "frozen_at": frozen_at}
    )


def freeze_reason_text(freeze_reason: Optional[str]) -> Optional[str]:
    """Extract the human-readable reason from a stored ``freeze_reason`` value.

    Handles both the JSON envelope (this module's writes) and a bare legacy string.
    """
    if not freeze_reason:
        return None
    try:
        env = json.loads(freeze_reason)
    except (ValueError, TypeError):
        return freeze_reason
    if isinstance(env, dict):
        return env.get("reason")
    return freeze_reason


def _envelope_prior_state(freeze_reason: Optional[str]) -> str:
    """Read the prior_state out of a freeze_reason envelope (defaults to 'hitl')."""
    if not freeze_reason:
        return "hitl"
    try:
        env = json.loads(freeze_reason)
    except (ValueError, TypeError):
        return "hitl"
    if isinstance(env, dict):
        ps = env.get("prior_state")
        if ps in ("hitl", "auto"):
            return ps
    return "hitl"


def freeze_client(
    conn: Connection,
    client_id: int,
    *,
    reason: Optional[str] = None,
    frozen_by: str = "founder",
    org_id: Optional[str] = None,
    hours: int = FREEZE_MIN_HOURS,
    now: Optional[datetime] = None,
    actor: str = AUDIT_SOURCE,
) -> List[FrozenCategory]:
    """SAFETY §6: pause ALL autonomous categories for the client; pure HITL ≥48h.

    Sets EVERY ``autocm_category_state`` row for the client to ``state='hitl'`` with
    a ``freeze_until`` of ``now + hours`` (``hours`` >= 48 is enforced — a smaller
    value is raised to :data:`FREEZE_MIN_HOURS`), stashing each category's PRIOR
    state in the ``freeze_reason`` envelope so :func:`restore_expired_freezes` can
    auto-restore it. Writes a ``safety_freeze_applied`` audit row carrying the full
    frozen-category list + their prior states (the digest post-mortem hook, C3.7,
    reads these freeze audit rows).

    The bot KEEPS DRAFTING + HITL-reviewing during the freeze — only autonomous
    auto-send is frozen (the C3.5a ``gate/confidence`` forces every category to HITL
    while ``freeze_until`` is active). This is a DISTINCT global mode — NOT
    per-category demotion (:func:`demote_on_founder_complaint`), NOT ``/pause-client``
    (which halts ALL publishing incl. HITL-approved replies), NOT relay
    ``disable``/``pause-org``.

    Returns the list of frozen categories (with their prior states). Only categories
    with an existing ``autocm_category_state`` row are frozen (a category with no row
    is already HITL-by-default — there is nothing to freeze/restore).
    """
    hours = max(int(hours), FREEZE_MIN_HOURS)
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    frozen_at = _iso_z(now)
    freeze_until = _iso_z(now + timedelta(hours=hours))

    rows = conn.execute(
        text(
            "SELECT category, state FROM autocm_category_state "
            "WHERE client_id = :c ORDER BY category"
        ),
        {"c": client_id},
    ).fetchall()

    frozen: List[FrozenCategory] = []
    for r in rows:
        category = r[0]
        prior_state = r[1]
        envelope = _freeze_envelope(reason, prior_state, frozen_at)
        conn.execute(
            text(
                "UPDATE autocm_category_state "
                "SET state = 'hitl', freeze_until = :fu, freeze_reason = :fr, "
                "    frozen_by = :fb, updated_at = :now "
                "WHERE client_id = :c AND category = :cat"
            ),
            {
                "fu": freeze_until,
                "fr": envelope,
                "fb": frozen_by,
                "now": frozen_at,
                "c": client_id,
                "cat": category,
            },
        )
        frozen.append(
            FrozenCategory(category=category, prior_state=prior_state, freeze_until=freeze_until)
        )

    log_audit(
        conn,
        actor=actor,
        action=ACTION_FREEZE,
        org_id=org_id,
        entity_id=str(client_id),
        detail={
            "client_id": client_id,
            "reason": reason or "SAFETY §6 freeze",
            "frozen_by": frozen_by,
            "freeze_until": freeze_until,
            "freeze_hours": hours,
            "frozen_categories": [
                {"category": fc.category, "prior_state": fc.prior_state} for fc in frozen
            ],
            "post_mortem_hook": True,
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return frozen


def restore_expired_freezes(
    conn: Connection,
    client_id: int,
    *,
    org_id: Optional[str] = None,
    now: Optional[datetime] = None,
    actor: str = AUDIT_SOURCE,
) -> List[FrozenCategory]:
    """Auto-restore each category to its PRIOR state after its freeze elapses.

    For every ``autocm_category_state`` row of the client whose ``freeze_until`` is
    in the PAST (the SAFETY §6 freeze has elapsed), restore the category to the
    PRIOR state captured at freeze time (read from the ``freeze_reason`` envelope)
    and CLEAR the freeze columns (``freeze_until`` / ``freeze_reason`` /
    ``frozen_by`` → NULL). Writes a ``safety_freeze_restored`` audit row per
    restored category. Returns the restored categories.

    A category whose ``freeze_until`` is still in the FUTURE is left frozen. This is
    the auto-restore leg of the SAFETY §6 freeze — the operator does not have to
    manually un-freeze; the freeze self-heals after ≥48h.
    """
    now = now or _utc_now()
    if org_id is None:
        org_id = _org_id_for_client(conn, client_id)
    now_iso = _iso_z(now)

    rows = conn.execute(
        text(
            "SELECT category, freeze_until, freeze_reason FROM autocm_category_state "
            "WHERE client_id = :c AND freeze_until IS NOT NULL "
            "ORDER BY category"
        ),
        {"c": client_id},
    ).fetchall()

    restored: List[FrozenCategory] = []
    for r in rows:
        category = r[0]
        freeze_until = r[1]
        until_dt = _parse_iso(freeze_until)
        if until_dt is not None and until_dt > now:
            continue  # still frozen
        prior_state = _envelope_prior_state(r[2])
        conn.execute(
            text(
                "UPDATE autocm_category_state "
                "SET state = :s, freeze_until = NULL, freeze_reason = NULL, "
                "    frozen_by = NULL, updated_at = :now "
                "WHERE client_id = :c AND category = :cat"
            ),
            {"s": prior_state, "now": now_iso, "c": client_id, "cat": category},
        )
        log_audit(
            conn,
            actor=actor,
            action=ACTION_FREEZE_RESTORED,
            org_id=org_id,
            entity_id=f"{client_id}:{category}",
            detail={
                "client_id": client_id,
                "category": category,
                "restored_state": prior_state,
                "freeze_until": freeze_until,
            },
            source=AUDIT_SOURCE,
        )
        restored.append(
            FrozenCategory(category=category, prior_state=prior_state, freeze_until=freeze_until)
        )
    conn.commit()
    return restored


# ---------------------------------------------------------------------------
# shared helper
# ---------------------------------------------------------------------------
def _org_id_for_client(conn: Connection, client_id: int) -> Optional[str]:
    row = conn.execute(
        text("SELECT org_id FROM autocm_clients WHERE id = :id"),
        {"id": client_id},
    ).fetchone()
    return row[0] if row is not None else None


__all__ = [
    # routing constants
    "DUAL_ROUTE_CATEGORIES",
    "ARF_ONLY_CATEGORIES",
    "AUTO_SILENCE_CATEGORY",
    "ROUTE_DUAL",
    "ROUTE_ARF_ONLY",
    "PUSH_AFTER_MINUTES",
    "DEFAULT_ACK_WINDOW_HOURS",
    "FREEZE_MIN_HOURS",
    # pure routing
    "RoutePlan",
    "route_for_category",
    # seam
    "EscalationNotifier",
    "Tier3Escalator",
    # router + result
    "EscalationResult",
    "Tier3EscalationRouter",
    # auto-silence
    "auto_silence_user",
    # trigger 4
    "demote_on_founder_complaint",
    # SAFETY §6 freeze
    "FrozenCategory",
    "freeze_client",
    "restore_expired_freezes",
    "freeze_reason_text",
    # audit verbs
    "ACTION_TIER3_DUAL_ROUTE",
    "ACTION_TIER3_PUSH",
    "ACTION_TIER3_ONCALL_HANDOFF",
    "ACTION_ARF_ROUTED",
    "ACTION_USER_SILENCED",
    "ACTION_DEMOTE_FOUNDER",
    "ACTION_FREEZE",
    "ACTION_FREEZE_RESTORED",
]
