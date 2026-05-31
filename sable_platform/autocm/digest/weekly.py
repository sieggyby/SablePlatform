"""Weekly digest assembly + founder buttons (MEGAPLAN C3.7 — DIGEST.md).

The auto-generated "What Mattered" report delivered to the client founder. This
module assembles the digest from the C3.7 :mod:`analytics` signals + the 058
``autocm_time_saved_baseline`` calibration, renders the skimmable ≤500-word body,
exposes the founder inline-button set, records button presses into
``autocm_digest_interactions``, and decides preview-vs-deliver routing.

**The A+C co-primary headline (DIGEST §2a / Q10).** Two numbers + a line:

  * **leg A — time-saved (PINNED, deterministic — the only deterministic leg)**::

        minutes_saved = auto_handled_count * MINUTES_PER_AUTO
                        + hitl_resolved_count * MINUTES_PER_HITL

    where ``MINUTES_PER_AUTO`` / ``MINUTES_PER_HITL`` are the per-client
    engagement-start calibration constants from the 058
    ``autocm_time_saved_baseline`` table (so the fixture's expected value is
    deterministic). ``auto_handled_count`` / ``hitl_resolved_count`` come from the
    C3.7 :func:`~analytics.autonomy_ratios` draft-status buckets.
  * **leg C — community-health delta** — the week-over-week change in the composite
    signal (:func:`~analytics.community_health_delta`).

**Sections (DIGEST §2, the PINNED mandatory set for a green exit).** (1) headline
A+C time-saved, (2) community-health, (3) cultist-candidate, (4)
subsquad-pollination, (5) FAQ-frequency, (6) sentiment, (7) voice-drift, and (8) —
WHEN a 48h pure-HITL freeze fired during the week — the SAFETY §6 freeze
post-mortem (fed by the C3.8a ``safety_freeze_applied`` audit rows). Plus the
Volume + Autonomy scoreboard sections.

**Founder buttons (DIGEST §4).** ``[Approve for KB]`` (top questions),
``[Recognize]`` (cultist candidate), ``[Demote]`` (voice-drift category),
``[Compose]`` (reply-worthy mention, v2), ``[Ignore]`` (pattern), ``[Ask]``
(free-form). A press routes via the C2.7 callback layer and is recorded into
``autocm_digest_interactions`` by :func:`record_interaction`. The
``[Approve for KB]`` → canonical-chunk WRITE handler is NOT here — it is owned by
C3.2c, which CONSUMES the ``autocm_digest_interactions`` row this records (soft
cross-ref edge C3.7 → C3.2c); this module only EMITS the button + records the press.

**Scheduling / delivery (DIGEST §1 / §5 / §6).** A weekly Monday cron via the SP
``WorkflowRunner`` (the builtin lives in
``workflows/builtins/autocm_weekly_digest.py``). Weeks 1–4 of a deployment go to
operator-chat PREVIEW first (gated on the deployment week vs the per-client
``auto_deliver_from`` config); week 5+ auto-delivers to the founder. A cron miss
raises the "no-deliver alarm" (:func:`raise_no_deliver_alarm`, an SP alert).

**No telegram / network here.** All outbound goes through the injected
:class:`DigestDelivery` Protocol (a fake in tests). Timestamps are computed in
Python as UTC ISO-8601 ``...Z`` and bound as parameters.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.autocm.digest import analytics
from sable_platform.autocm.digest.analytics import (
    KeywordSentimentScorer,
    SentimentScorer,
    week_bounds,
)
from sable_platform.autocm.escalation.tier3 import ACTION_FREEZE
from sable_platform.db.alerts import create_alert
from sable_platform.db.audit import list_audit_log, log_audit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUDIT_SOURCE = "sable-autocm"
ACTION_DIGEST_GENERATED = "weekly_digest_generated"
ACTION_DIGEST_DELIVERED = "weekly_digest_delivered"
ACTION_DIGEST_INTERACTION = "weekly_digest_interaction"

#: DIGEST §1/§5: weeks 1–4 go to operator preview; week 5+ auto-delivers. The
#: default ``auto_deliver_from`` deployment-week (per-client override in
#: ``surface_config['digest']['auto_deliver_from']``).
DEFAULT_AUTO_DELIVER_FROM_WEEK = 5

#: the digest "no-deliver alarm" (DIGEST §6) alert type.
ALERT_NO_DELIVER = "autocm_digest_no_deliver"

#: the founder inline-button set (DIGEST §4). Each maps to an
#: ``autocm_digest_interactions.action`` value (the 058 CHECK set).
BUTTON_APPROVE_FOR_KB = "approve_for_kb"
BUTTON_RECOGNIZE = "recognize"
BUTTON_DEMOTE = "demote"
BUTTON_COMPOSE = "compose"
BUTTON_IGNORE = "ignore"
BUTTON_ASK = "ask"
DIGEST_ACTIONS = (
    BUTTON_APPROVE_FOR_KB,
    BUTTON_RECOGNIZE,
    BUTTON_DEMOTE,
    BUTTON_COMPOSE,
    BUTTON_IGNORE,
    BUTTON_ASK,
)

# the PINNED mandatory DIGEST.md section keys for a green exit (C3.7 exit). Section
# (8) freeze post-mortem is CONDITIONAL on a freeze having fired during the week.
SECTION_HEADLINE = "headline"            # (1) A+C time-saved
SECTION_COMMUNITY_HEALTH = "community_health"  # (2)
SECTION_VOLUME = "volume"
SECTION_AUTONOMY = "autonomy"
SECTION_SENTIMENT = "sentiment"          # (6)
SECTION_TOP_QUESTIONS = "top_questions"  # (5) FAQ-frequency
SECTION_CULTIST = "cultist_candidates"   # (3)
SECTION_SUBSQUAD = "subsquad_pollination"  # (4)
SECTION_VOICE_DRIFT = "voice_drift"      # (7)
SECTION_FREEZE_POSTMORTEM = "freeze_postmortem"  # (8) conditional

#: the fixed pass-set the C3.7 exit asserts present (section 8 conditional).
MANDATORY_SECTIONS = (
    SECTION_HEADLINE,
    SECTION_COMMUNITY_HEALTH,
    SECTION_CULTIST,
    SECTION_SUBSQUAD,
    SECTION_TOP_QUESTIONS,
    SECTION_SENTIMENT,
    SECTION_VOICE_DRIFT,
)


# ---------------------------------------------------------------------------
# Clock / timestamp
# ---------------------------------------------------------------------------
def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Time-saved baseline (058 autocm_time_saved_baseline)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TimeSavedBaseline:
    """The per-client engagement-start calibration (058 autocm_time_saved_baseline).

    ``minutes_per_auto`` / ``minutes_per_hitl`` are the founder-minutes an
    auto-handled / HITL-resolved message would otherwise have cost — the PINNED
    time-saved formula's constants. ``engagement_start_at`` anchors the deployment
    week computation (DIGEST §5 preview-vs-deliver gating).
    """

    minutes_per_auto: float
    minutes_per_hitl: float
    engagement_start_at: Optional[str]


def load_time_saved_baseline(
    conn: Connection, client_id: int
) -> Optional[TimeSavedBaseline]:
    """Load the per-client time-saved calibration (or None if not yet calibrated)."""
    row = conn.execute(
        text(
            "SELECT minutes_per_auto, minutes_per_hitl, engagement_start_at "
            "FROM autocm_time_saved_baseline WHERE client_id = :c"
        ),
        {"c": client_id},
    ).fetchone()
    if row is None:
        return None
    m = row._mapping
    return TimeSavedBaseline(
        minutes_per_auto=float(m["minutes_per_auto"] or 0.0),
        minutes_per_hitl=float(m["minutes_per_hitl"] or 0.0),
        engagement_start_at=m["engagement_start_at"],
    )


def compute_minutes_saved(
    auto_handled_count: int,
    hitl_resolved_count: int,
    baseline: TimeSavedBaseline,
) -> float:
    """The PINNED, DETERMINISTIC time-saved formula (DIGEST §2a leg A).

    ``minutes_saved = auto_handled_count * minutes_per_auto
                      + hitl_resolved_count * minutes_per_hitl``

    Pure — given the fixture's calibration constants + counts the result is
    hand-computable (the C3.7 exit asserts the digest's time-saved equals this).
    """
    return round(
        auto_handled_count * baseline.minutes_per_auto
        + hitl_resolved_count * baseline.minutes_per_hitl,
        2,
    )


def deployment_week(
    baseline: Optional[TimeSavedBaseline], week_start: datetime
) -> int:
    """The 1-based deployment week of ``week_start`` relative to engagement start.

    Week 1 is the engagement-start week; each subsequent 7-day window increments it.
    Used by the DIGEST §5 preview-vs-deliver gate. When no baseline /
    engagement_start is set, conservatively returns week 1 (preview-first — the safe
    default before calibration).
    """
    if baseline is None or not baseline.engagement_start_at:
        return 1
    start = _parse_iso(baseline.engagement_start_at)
    if start is None:
        return 1
    ws = week_start if week_start.tzinfo else week_start.replace(tzinfo=timezone.utc)
    delta_days = (ws - start).days
    if delta_days < 0:
        return 1
    return delta_days // 7 + 1


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
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
# Freeze post-mortem (DIGEST §2 section 8 — conditional on a freeze having fired)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FreezePostMortem:
    """A SAFETY §6 freeze that fired during the week (DIGEST §2 section 8).

    Fed by the C3.8a ``safety_freeze_applied`` audit rows. ``reason`` is the
    human-readable freeze reason; ``frozen_categories`` the categories paused;
    ``freeze_until`` the window end; ``frozen_by`` who triggered it.
    """

    reason: Optional[str]
    frozen_by: Optional[str]
    freeze_until: Optional[str]
    frozen_categories: Tuple[str, ...]
    fired_at: Optional[str]


def freeze_postmortems(
    conn: Connection,
    client_id: int,
    week_start: datetime,
    *,
    org_id: Optional[str] = None,
) -> List[FreezePostMortem]:
    """The SAFETY §6 freezes that fired this week (DIGEST §2 section 8 feed).

    Scans the audit log for ``safety_freeze_applied`` rows for the client's org in
    the digest window. Section 8 of the digest is present iff this returns a
    non-empty list (the C3.7 exit: section 8 is conditional on a freeze having fired).
    """
    if org_id is None:
        org_id = analytics._client_org_id(conn, client_id)
    start, end = week_bounds(week_start)
    start_dt, end_dt = _parse_iso(start), _parse_iso(end)
    rows = list_audit_log(conn, action=ACTION_FREEZE, org_id=org_id, limit=1000)
    out: List[FreezePostMortem] = []
    for r in rows:
        m = r._mapping
        # this client only (audit rows are org-scoped; entity_id is the client_id).
        if str(m["entity_id"]) != str(client_id):
            continue
        detail = _decode_detail(m["detail_json"])
        # Window the freeze by its LOGICAL fire time (freeze_until - freeze_hours),
        # NOT the wall-clock audit_log.timestamp. log_audit stamps the row with the
        # column DEFAULT (real wall clock), which is correct in production but is
        # NOT the freeze's logical instant when the clock is injected — and the
        # weekly digest is computed AFTER the week it reports on, so the logical
        # freeze time is the faithful window key. Both legs derive from the C3.8a
        # injected freeze clock.
        fired_dt = _freeze_fired_at(detail)
        if start_dt is not None and end_dt is not None and fired_dt is not None:
            if not (start_dt <= fired_dt < end_dt):
                continue
        cats = tuple(
            c.get("category")
            for c in detail.get("frozen_categories", [])
            if isinstance(c, dict) and c.get("category")
        )
        out.append(
            FreezePostMortem(
                reason=detail.get("reason"),
                frozen_by=detail.get("frozen_by"),
                freeze_until=detail.get("freeze_until"),
                frozen_categories=cats,
                fired_at=_iso_z(fired_dt) if fired_dt is not None else m["timestamp"],
            )
        )
    return out


def _freeze_fired_at(detail: dict) -> Optional[datetime]:
    """The freeze's LOGICAL fire instant = ``freeze_until - freeze_hours``.

    The C3.8a ``safety_freeze_applied`` audit detail carries ``freeze_until`` (=
    fire + ``freeze_hours``) and ``freeze_hours``; subtracting recovers the injected
    fire time. Returns None when either is missing (the caller then falls back to
    not windowing that row out).
    """
    until = _parse_iso(detail.get("freeze_until"))
    hours = detail.get("freeze_hours")
    if until is None or hours is None:
        return None
    try:
        return until - timedelta(hours=int(hours))
    except (TypeError, ValueError):
        return None


def _decode_detail(blob: Optional[str]) -> dict:
    if not blob:
        return {}
    try:
        v = json.loads(blob)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


# ---------------------------------------------------------------------------
# The assembled digest (the structured payload + the rendered body)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FounderButton:
    """One inline button the digest exposes to the founder (DIGEST §4).

    ``action`` is one of :data:`DIGEST_ACTIONS`; ``section`` is the digest section
    the button belongs to; ``target_ref`` identifies the item (e.g. the question
    cluster text, the cultist handle, the voice-drift category). The press routes
    via C2.7 callback and is recorded by :func:`record_interaction`.
    """

    action: str
    section: str
    target_ref: str
    label: str


@dataclass(frozen=True)
class WeeklyDigestReport:
    """The assembled weekly digest — structured payload + rendered body + buttons.

    ``sections`` is the set of present section keys (the C3.7 exit asserts every
    :data:`MANDATORY_SECTIONS` entry is present, plus section 8 when a freeze fired).
    ``body`` is the ≤500-word rendered text. ``buttons`` is the founder button set.
    The numeric legs are carried so the fixture exit can assert them against the
    hand-computed values without re-parsing the body.
    """

    client_id: int
    org_id: Optional[str]
    week_start: str
    week_end: str
    minutes_saved: float
    health_delta: float
    health_score: float
    sentiment: float
    volume: analytics.VolumeStats
    ratios: analytics.AutonomyRatios
    top_questions: List[Tuple[str, int]]
    cultist_candidates: List[analytics.CultistCandidate]
    topic_clusters: List[analytics.TopicCluster]
    voice_drift: List[analytics.VoiceDriftCluster]
    freeze_postmortems: List[FreezePostMortem]
    sections: Tuple[str, ...]
    buttons: List[FounderButton]
    body: str

    def has_section(self, key: str) -> bool:
        return key in self.sections


# ---------------------------------------------------------------------------
# Delivery seam (DIGEST §1/§5 — operator-preview-first then founder)
# ---------------------------------------------------------------------------
class DigestDelivery(Protocol):
    """The injected outbound seam (NO telegram / network in this module).

    ``to_operator`` posts the digest to the operator chat (weeks 1–4 preview, and a
    copy thereafter); ``to_founder`` DMs the founder (week 5+). Tests inject a fake
    that records calls; the real impl wraps the relay outbound surface.
    """

    def to_operator(self, org_id: str, body: str) -> Optional[str]:
        ...

    def to_founder(self, org_id: str, body: str) -> Optional[str]:
        ...


@dataclass(frozen=True)
class DeliveryOutcome:
    """The result of routing a digest (DIGEST §5 preview-vs-deliver)."""

    routed_to: str  # 'operator_preview' | 'founder'
    operator_handle: Optional[str]
    founder_handle: Optional[str]
    deployment_week: int


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def generate(
    conn: Connection,
    client_id: int,
    week_start: datetime,
    *,
    org_id: Optional[str] = None,
    scorer: Optional[SentimentScorer] = None,
) -> WeeklyDigestReport:
    """Assemble the weekly "What Mattered" digest (DIGEST §2 — all sections).

    Pulls every C3.7 analytics signal over the digest week, computes the A+C
    headline (PINNED time-saved formula + community-health delta), renders the body,
    builds the founder button set, and returns the structured report. The sentiment
    seam defaults to the deterministic :class:`KeywordSentimentScorer` (the
    always-available offline default / test double); production passes the
    :class:`LLMSentimentScorer`.

    A missing time-saved baseline yields a zeroed calibration (minutes_saved = 0) —
    the headline still renders (the section is present), it just reports 0 saved
    until the client is calibrated.
    """
    if scorer is None:
        scorer = KeywordSentimentScorer()
    if org_id is None:
        org_id = analytics._client_org_id(conn, client_id)
    start, end = week_bounds(week_start)

    vol = analytics.volume(conn, client_id, week_start, org_id=org_id)
    ratios = analytics.autonomy_ratios(conn, client_id, week_start)
    health = analytics.community_health_delta(
        conn, client_id, week_start, scorer, org_id=org_id
    )
    sentiment = analytics.score_sentiment(
        conn, client_id, week_start, scorer, org_id=org_id
    )
    top_q = analytics.frequent_questions(conn, client_id, week_start, org_id=org_id)
    cultists = analytics.cultist_candidates(conn, client_id, week_start, org_id=org_id)
    topics = analytics.topic_clusters(conn, client_id, week_start, org_id=org_id)
    drift = analytics.voice_drift(conn, client_id, week_start)
    postmortems = freeze_postmortems(conn, client_id, week_start, org_id=org_id)

    baseline = load_time_saved_baseline(conn, client_id) or TimeSavedBaseline(0.0, 0.0, None)
    minutes_saved = compute_minutes_saved(
        ratios.auto_handled, ratios.hitl_handled, baseline
    )

    buttons = _build_buttons(top_q, cultists, drift)
    sections = _present_sections(postmortems)
    body = _render_body(
        client_id=client_id,
        org_id=org_id,
        week_start=start,
        week_end=end,
        minutes_saved=minutes_saved,
        health=health,
        sentiment=sentiment,
        vol=vol,
        ratios=ratios,
        top_q=top_q,
        cultists=cultists,
        topics=topics,
        drift=drift,
        postmortems=postmortems,
    )

    return WeeklyDigestReport(
        client_id=client_id,
        org_id=org_id,
        week_start=start,
        week_end=end,
        minutes_saved=minutes_saved,
        health_delta=health.delta,
        health_score=health.score,
        sentiment=sentiment,
        volume=vol,
        ratios=ratios,
        top_questions=top_q,
        cultist_candidates=cultists,
        topic_clusters=topics,
        voice_drift=drift,
        freeze_postmortems=postmortems,
        sections=sections,
        buttons=buttons,
        body=body,
    )


def _present_sections(postmortems: Sequence[FreezePostMortem]) -> Tuple[str, ...]:
    """The present-section keys: the always-present set + section 8 iff a freeze fired."""
    present = [
        SECTION_HEADLINE,
        SECTION_COMMUNITY_HEALTH,
        SECTION_VOLUME,
        SECTION_AUTONOMY,
        SECTION_SENTIMENT,
        SECTION_TOP_QUESTIONS,
        SECTION_CULTIST,
        SECTION_SUBSQUAD,
        SECTION_VOICE_DRIFT,
    ]
    if postmortems:
        present.append(SECTION_FREEZE_POSTMORTEM)
    return tuple(present)


def _build_buttons(
    top_q: Sequence[Tuple[str, int]],
    cultists: Sequence[analytics.CultistCandidate],
    drift: Sequence[analytics.VoiceDriftCluster],
) -> List[FounderButton]:
    """Build the DIGEST §4 founder button set for the assembled digest.

    ``[Approve for KB]`` per top question; ``[Recognize]`` per cultist candidate;
    ``[Demote]`` per voice-drift category. ``[Ask]`` is always present (free-form
    question about anything in the digest). ``[Ignore]`` accompanies the surfaced
    patterns. ``[Compose]`` is v2 (X-side reply-worthy mentions, deferred in v1) — it
    is NOT emitted in v1 (no X surface), matching DIGEST §2i/§4.
    """
    buttons: List[FounderButton] = []
    for q, _n in top_q:
        buttons.append(
            FounderButton(BUTTON_APPROVE_FOR_KB, SECTION_TOP_QUESTIONS, q, "Approve for KB")
        )
    for c in cultists:
        buttons.append(
            FounderButton(BUTTON_RECOGNIZE, SECTION_CULTIST, c.handle, "Recognize")
        )
        buttons.append(
            FounderButton(BUTTON_IGNORE, SECTION_CULTIST, c.handle, "Ignore")
        )
    for d in drift:
        if d.category:
            buttons.append(
                FounderButton(BUTTON_DEMOTE, SECTION_VOICE_DRIFT, d.category, "Demote")
            )
    buttons.append(FounderButton(BUTTON_ASK, SECTION_HEADLINE, "", "Ask"))
    return buttons


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.0f}%"


def _render_body(
    *,
    client_id: int,
    org_id: Optional[str],
    week_start: str,
    week_end: str,
    minutes_saved: float,
    health: analytics.CommunityHealth,
    sentiment: float,
    vol: analytics.VolumeStats,
    ratios: analytics.AutonomyRatios,
    top_q: Sequence[Tuple[str, int]],
    cultists: Sequence[analytics.CultistCandidate],
    topics: Sequence[analytics.TopicCluster],
    drift: Sequence[analytics.VoiceDriftCluster],
    postmortems: Sequence[FreezePostMortem],
) -> str:
    """Render the skimmable ≤500-word digest body (DIGEST §7 skeleton shape)."""
    lines: List[str] = []
    lines.append(f"{org_id or 'client'} — Week of {week_start[:10]} → {week_end[:10]}")
    lines.append("")
    # (1) + (2) headline A+C.
    lines.append(f"We saved you ~{minutes_saved:g} minutes this week.")
    lines.append(
        f"Community health: {health.delta:+.2f} "
        f"(score {health.score:+.2f}; sentiment {sentiment:+.2f}; "
        f"{vol.new_members} new engaged members)."
    )
    lines.append("")
    # Volume.
    lines.append("VOLUME")
    lines.append(
        f"  {vol.messages} msgs ({_pct(vol.wow_pct)} w/w) · "
        f"{vol.distinct_members} active members · {vol.new_members} new"
    )
    lines.append("")
    # Autonomy scoreboard.
    lines.append("AUTONOMY (scoreboard)")
    lines.append(f"  Auto-handled:    {ratios.auto_pct * 100:.0f}%")
    lines.append(f"  HITL-handled:    {ratios.hitl_pct * 100:.0f}%")
    lines.append(f"  Escalated:       {ratios.escalated_pct * 100:.0f}%")
    lines.append(f"  Clean-approval:  {ratios.clean_approval_rate * 100:.0f}%")
    lines.append("")
    # (6) Sentiment.
    lines.append("SENTIMENT")
    lines.append(f"  Overall: {sentiment:+.2f}")
    lines.append("")
    # (5) Top questions (FAQ-frequency).
    lines.append("TOP QUESTIONS")
    if top_q:
        for i, (q, n) in enumerate(top_q, 1):
            lines.append(f"  {i}. {q!r} (asked {n}x) [Approve for KB]")
    else:
        lines.append("  (none this week)")
    lines.append("")
    # (3) Cultist candidates.
    lines.append("CULTIST CANDIDATES")
    if cultists:
        for c in cultists:
            lines.append(
                f"  • {c.handle}: {c.question_count} substantive questions "
                f"({c.message_count} msgs) [Recognize]"
            )
    else:
        lines.append("  (none surfaced this week)")
    lines.append("")
    # (4) Subsquad pollination.
    lines.append("SUBSQUAD POLLINATION")
    if topics:
        for t in topics:
            lines.append(
                f"  • {', '.join(t.handles)} all asked about {t.topic!r} — consider intro"
            )
    else:
        lines.append("  (no shared-topic pairs this week)")
    lines.append("")
    # (7) Voice drift.
    lines.append("VOICE DRIFT")
    if drift:
        for d in drift:
            reg = f", register={d.register}" if d.register else ""
            lines.append(
                f"  {d.count} draft(s) heavily edited in {d.category or 'unknown'}{reg}"
            )
    else:
        lines.append("  No heavy edits this week.")
    # (8) Freeze post-mortem (conditional).
    if postmortems:
        lines.append("")
        lines.append("SAFETY FREEZE POST-MORTEM")
        for pm in postmortems:
            cats = ", ".join(pm.frozen_categories) or "all categories"
            lines.append(
                f"  A 48h pure-HITL freeze fired ({pm.reason or 'reputational guardrail'}); "
                f"frozen: {cats}; until {pm.freeze_until or 'n/a'}."
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Delivery routing (DIGEST §1/§5)
# ---------------------------------------------------------------------------
def _auto_deliver_from(conn: Connection, client_id: int) -> int:
    """The per-client ``auto_deliver_from`` deployment-week (default week 5).

    Read from ``autocm_clients.surface_config['digest']['auto_deliver_from']`` when
    present; else :data:`DEFAULT_AUTO_DELIVER_FROM_WEEK`.
    """
    from sable_platform.autocm.loaders import load_client_config

    org_id = analytics._client_org_id(conn, client_id)
    if org_id is None:
        return DEFAULT_AUTO_DELIVER_FROM_WEEK
    cfg = load_client_config(conn, org_id, with_persona=False)
    if cfg is None:
        return DEFAULT_AUTO_DELIVER_FROM_WEEK
    digest_cfg = cfg.surface_config.get("digest") if isinstance(cfg.surface_config, dict) else None
    if isinstance(digest_cfg, dict) and "auto_deliver_from" in digest_cfg:
        try:
            return int(digest_cfg["auto_deliver_from"])
        except (TypeError, ValueError):
            return DEFAULT_AUTO_DELIVER_FROM_WEEK
    return DEFAULT_AUTO_DELIVER_FROM_WEEK


def deliver(
    conn: Connection,
    report: WeeklyDigestReport,
    delivery: DigestDelivery,
    week_start: datetime,
    *,
    actor: str = AUDIT_SOURCE,
) -> DeliveryOutcome:
    """Route the digest per the DIGEST §5 preview-vs-deliver gate.

    Weeks 1–4 of the deployment (deployment_week < ``auto_deliver_from``, default 5)
    route to the OPERATOR chat for preview-first; week 5+ DMs the FOUNDER (a copy
    still goes to the operator for visibility per DIGEST §5). Writes a
    ``weekly_digest_delivered`` audit row recording which path fired + the
    deployment week. Returns the :class:`DeliveryOutcome`.
    """
    baseline = load_time_saved_baseline(conn, report.client_id)
    dep_week = deployment_week(baseline, week_start)
    auto_from = _auto_deliver_from(conn, report.client_id)
    org_id = report.org_id or ""

    operator_handle: Optional[str] = None
    founder_handle: Optional[str] = None
    if dep_week < auto_from:
        # preview-first: operator only.
        operator_handle = delivery.to_operator(org_id, report.body)
        routed = "operator_preview"
    else:
        # auto-deliver: founder + a copy to the operator for visibility.
        founder_handle = delivery.to_founder(org_id, report.body)
        operator_handle = delivery.to_operator(org_id, report.body)
        routed = "founder"

    log_audit(
        conn,
        actor=actor,
        action=ACTION_DIGEST_DELIVERED,
        org_id=org_id,
        entity_id=str(report.client_id),
        detail={
            "client_id": report.client_id,
            "week_start": report.week_start,
            "deployment_week": dep_week,
            "auto_deliver_from": auto_from,
            "routed_to": routed,
            "minutes_saved": report.minutes_saved,
            "health_delta": report.health_delta,
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return DeliveryOutcome(
        routed_to=routed,
        operator_handle=operator_handle,
        founder_handle=founder_handle,
        deployment_week=dep_week,
    )


# ---------------------------------------------------------------------------
# Founder interactions (DIGEST §4) — record button presses
# ---------------------------------------------------------------------------
def record_interaction(
    conn: Connection,
    client_id: int,
    action: str,
    *,
    section: Optional[str] = None,
    target_ref: Optional[str] = None,
    digest_period: Optional[str] = None,
    payload: Optional[dict] = None,
    actor: Optional[str] = None,
    org_id: Optional[str] = None,
) -> int:
    """Record a founder digest-button press into ``autocm_digest_interactions``.

    DIGEST §4: every button press (Approve for KB / Recognize / Demote / Compose /
    Ignore / Ask) is captured for weekly review. The press is delivered via the C2.7
    inline-button callback layer and lands here. Returns the new row id. Writes a
    ``weekly_digest_interaction`` audit row too (SAFETY §5: the press is an auditable
    action).

    This does NOT execute the downstream effect — e.g. ``[Approve for KB]`` only
    RECORDS the interaction; the canonical-chunk promotion WRITE handler is owned by
    C3.2c (which CONSUMES this row). The end-to-end button→callback→row→write path is
    a C3.10 e2e assertion, not this chunk's gate.
    """
    if action not in DIGEST_ACTIONS:
        raise ValueError(
            f"unknown digest action {action!r}; expected one of {DIGEST_ACTIONS}"
        )
    if org_id is None:
        org_id = analytics._client_org_id(conn, client_id)
    row = conn.execute(
        text(
            "INSERT INTO autocm_digest_interactions "
            "(client_id, digest_period, section, action, target_ref, payload, actor) "
            "VALUES (:c, :period, :section, :action, :ref, :payload, :actor) RETURNING id"
        ),
        {
            "c": client_id,
            "period": digest_period,
            "section": section,
            "action": action,
            "ref": target_ref,
            "payload": json.dumps(payload or {}),
            "actor": actor,
        },
    ).fetchone()
    interaction_id = int(row[0])
    log_audit(
        conn,
        actor=actor or AUDIT_SOURCE,
        action=ACTION_DIGEST_INTERACTION,
        org_id=org_id,
        entity_id=str(interaction_id),
        detail={
            "client_id": client_id,
            "digest_action": action,
            "section": section,
            "target_ref": target_ref,
            "digest_period": digest_period,
        },
        source=AUDIT_SOURCE,
    )
    conn.commit()
    return interaction_id


# ---------------------------------------------------------------------------
# No-deliver alarm (DIGEST §6 — cron miss detection)
# ---------------------------------------------------------------------------
def raise_no_deliver_alarm(
    conn: Connection,
    client_id: int,
    *,
    org_id: Optional[str] = None,
    week_start: Optional[str] = None,
    reason: str = "weekly digest did not generate/deliver (cron miss)",
) -> Optional[str]:
    """DIGEST §6: raise the "no-deliver alarm" when the weekly digest cron misses.

    Creates an SP alert (``autocm_digest_no_deliver``) so the operator is paged to
    manually trigger + investigate the workflow. Dedup-keyed per client × week so a
    re-check does not spam. Returns the alert id (or None if dedup-suppressed).
    """
    if org_id is None:
        org_id = analytics._client_org_id(conn, client_id)
    dedup = f"{ALERT_NO_DELIVER}:{org_id}:{client_id}:{week_start or 'latest'}"
    # NOTE: alerts.entity_id is a FK to entities(entity_id); a client_id is not an
    # entity, so the client_id rides in data_json (NOT entity_id) to avoid a
    # spurious FK failure. org_id (FK to orgs) scopes the alert + dedup key.
    return create_alert(
        conn,
        ALERT_NO_DELIVER,
        "warning",
        "AutoCM weekly digest did not deliver",
        org_id=org_id,
        body=reason,
        data_json=json.dumps({"client_id": client_id, "week_start": week_start}),
        dedup_key=dedup,
    )


__all__ = [
    # constants
    "DEFAULT_AUTO_DELIVER_FROM_WEEK",
    "ALERT_NO_DELIVER",
    "DIGEST_ACTIONS",
    "BUTTON_APPROVE_FOR_KB",
    "BUTTON_RECOGNIZE",
    "BUTTON_DEMOTE",
    "BUTTON_COMPOSE",
    "BUTTON_IGNORE",
    "BUTTON_ASK",
    "MANDATORY_SECTIONS",
    "SECTION_HEADLINE",
    "SECTION_COMMUNITY_HEALTH",
    "SECTION_VOLUME",
    "SECTION_AUTONOMY",
    "SECTION_SENTIMENT",
    "SECTION_TOP_QUESTIONS",
    "SECTION_CULTIST",
    "SECTION_SUBSQUAD",
    "SECTION_VOICE_DRIFT",
    "SECTION_FREEZE_POSTMORTEM",
    # time-saved
    "TimeSavedBaseline",
    "load_time_saved_baseline",
    "compute_minutes_saved",
    "deployment_week",
    # freeze post-mortem
    "FreezePostMortem",
    "freeze_postmortems",
    # report
    "FounderButton",
    "WeeklyDigestReport",
    "generate",
    # delivery
    "DigestDelivery",
    "DeliveryOutcome",
    "deliver",
    # interactions
    "record_interaction",
    # no-deliver alarm
    "raise_no_deliver_alarm",
]
