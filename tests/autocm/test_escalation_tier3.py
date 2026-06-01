"""C3.8a — tier-3 escalation + Arf-only routing + SAFETY §6 48h freeze.

Covers (MEGAPLAN C3.8a tests / exit):
  * tier-3 dual-route delivers to BOTH endpoints (founder + Arf on-call);
  * a tier-3 untouched in 2 min fires a PushNotification;
  * each of threat / whale_inbound / founder_voice_needed routes to founder+on-call
    with NO draft posted (public reply suppressed);
  * conflict_detected suppresses the public reply + routes Arf-only;
  * moderation_flag writes autocm_flagged_users (auto-silence) + routes Arf-only;
  * the founder-complaint → C3.5a autonomy auto-demote (DESIGN §7 trigger 4);
  * the SAFETY §6 48h pure-HITL freeze: founder/operator trigger flips EVERY
    autocm_category_state to hitl with freeze_until ≥48h + audit row; the bot still
    DRAFTS + HITL-reviews during the freeze (auto-send frozen, drafting not); after
    freeze_until passes each category auto-restores to its prior state; a digest
    post-mortem hook is emitted;
  * the N-hour founder-unacknowledged → on-call playbook handoff records the ack
    window.

Everything runs over a FAKE EscalationNotifier — NO real telegram / network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from sable_platform.autocm.escalation.tier3 import (
    ACTION_ARF_ROUTED,
    ACTION_DEMOTE_FOUNDER,
    ACTION_FREEZE,
    ACTION_FREEZE_RESTORED,
    ACTION_TIER3_DUAL_ROUTE,
    ACTION_TIER3_ONCALL_HANDOFF,
    ACTION_TIER3_PUSH,
    ACTION_USER_SILENCED,
    ARF_ONLY_CATEGORIES,
    DUAL_ROUTE_CATEGORIES,
    FREEZE_MIN_HOURS,
    PUSH_AFTER_MINUTES,
    ROUTE_ARF_ONLY,
    ROUTE_DUAL,
    Tier3EscalationRouter,
    auto_silence_user,
    demote_on_founder_complaint,
    freeze_client,
    freeze_reason_text,
    restore_expired_freezes,
    route_for_category,
)
from sable_platform.autocm.gate.confidence import decide, is_frozen
from sable_platform.autocm.db import is_flagged_user
from sable_platform.db.audit import list_audit_log


# ---------------------------------------------------------------------------
# FAKE notifier — records founder / on-call / push calls; NO telegram/network.
# ---------------------------------------------------------------------------
class FakeNotifier:
    def __init__(self) -> None:
        self.founder: list[dict] = []
        self.oncall: list[dict] = []
        self.pushes: list[dict] = []
        self._n = 0

    def _h(self, kind: str) -> str:
        self._n += 1
        return f"{kind}-{self._n}"

    def notify_founder(self, org_id, escalation_id, body):
        self.founder.append({"org_id": org_id, "escalation_id": escalation_id, "body": body})
        return self._h("founder")

    def notify_oncall(self, org_id, escalation_id, body):
        self.oncall.append({"org_id": org_id, "escalation_id": escalation_id, "body": body})
        return self._h("oncall")

    def push(self, org_id, escalation_id, body):
        self.pushes.append({"org_id": org_id, "escalation_id": escalation_id, "body": body})
        return self._h("push")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_client(conn, org_id="orgRM"):
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RobotMoney', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    conn.commit()
    client_id = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]
    return org_id, int(client_id)


def _seed_category_state(conn, client_id, category, state="auto"):
    conn.execute(
        text(
            "INSERT INTO autocm_category_state (client_id, category, state) "
            "VALUES (:c, :cat, :s)"
        ),
        {"c": client_id, "cat": category, "s": state},
    )
    conn.commit()


def _seed_member(conn, name="troll"):
    conn.execute(text("INSERT INTO relay_members (display_name) VALUES (:d)"), {"d": name})
    return int(
        conn.execute(
            text("SELECT id FROM relay_members ORDER BY id DESC LIMIT 1")
        ).fetchone()[0]
    )


def _escalation_row(conn, escalation_id):
    return conn.execute(
        text(
            "SELECT founder_status, oncall_status, reason FROM autocm_escalations "
            "WHERE id = :id"
        ),
        {"id": escalation_id},
    ).fetchone()._mapping


# ---------------------------------------------------------------------------
# Pure routing — the CLASSIFIER §2 / DESIGN §5 decision table
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("category", list(DUAL_ROUTE_CATEGORIES))
def test_dual_route_categories_route_to_both_endpoints_no_public_reply(category):
    plan = route_for_category(category)
    assert plan.route == ROUTE_DUAL
    assert plan.notify_founder is True
    assert plan.notify_oncall is True
    assert plan.suppress_public_reply is True  # NO auto-draft on a tier-3
    assert plan.auto_silence is False


@pytest.mark.parametrize("category", list(ARF_ONLY_CATEGORIES))
def test_arf_only_categories_suppress_public_reply_and_skip_founder(category):
    plan = route_for_category(category)
    assert plan.route == ROUTE_ARF_ONLY
    assert plan.notify_founder is False  # founder NOT pulled in (human handling)
    assert plan.notify_oncall is True
    assert plan.suppress_public_reply is True


def test_moderation_flag_auto_silences_conflict_does_not():
    assert route_for_category("moderation_flag").auto_silence is True
    assert route_for_category("conflict_detected").auto_silence is False


def test_non_escalation_category_has_no_route():
    plan = route_for_category("mechanics")
    assert plan.route is None
    assert plan.is_escalation is False
    assert plan.suppress_public_reply is False


# ---------------------------------------------------------------------------
# Dual-route delivers to BOTH endpoints (founder + Arf)
# ---------------------------------------------------------------------------
def test_dual_route_delivers_to_both_founder_and_oncall(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier)

    result = router.dual_route_tier3(
        client_id, "threat", org_id=org_id, reason="exploit chatter"
    )

    assert result.route == ROUTE_DUAL
    assert result.suppressed_public_reply is True
    assert result.escalation_id is not None
    # BOTH endpoints received.
    assert len(notifier.founder) == 1
    assert len(notifier.oncall) == 1
    assert result.founder_handle is not None
    assert result.oncall_handle is not None
    # both legs marked notified.
    row = _escalation_row(sa_conn, result.escalation_id)
    assert row["founder_status"] == "notified"
    assert row["oncall_status"] == "notified"
    # audited as a dual-route with both legs + the ack window.
    rows = list_audit_log(sa_conn, action=ACTION_TIER3_DUAL_ROUTE)
    assert len(rows) == 1


@pytest.mark.parametrize("category", ["threat", "whale_inbound", "founder_voice_needed"])
def test_tier3_categories_route_both_with_no_draft_posted(sa_conn, category):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier)

    result = router.route(client_id, category, org_id=org_id, draft_id=None)

    assert result is not None
    assert result.route == ROUTE_DUAL
    assert result.suppressed_public_reply is True  # public reply suppressed (no draft)
    assert len(notifier.founder) == 1 and len(notifier.oncall) == 1
    # NO draft was posted: there is no autocm_drafts row created by routing, and the
    # public-reply suppression flag is set.
    n_drafts = sa_conn.execute(
        text("SELECT COUNT(*) FROM autocm_drafts WHERE client_id = :c"), {"c": client_id}
    ).fetchone()[0]
    assert n_drafts == 0


# ---------------------------------------------------------------------------
# 2-min PushNotification (HITL_UX §3) — distinct from the N-hour handoff
# ---------------------------------------------------------------------------
def test_tier3_untouched_2min_fires_push_notification(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier)

    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = router.dual_route_tier3(client_id, "threat", org_id=org_id, now=t0)

    # not yet 2 min — no push.
    pushed_early = router.sweep_tier3_push_notifications(
        client_id, org_id=org_id, now=t0 + timedelta(minutes=1)
    )
    assert pushed_early == []
    assert notifier.pushes == []

    # past 2 min, founder still un-acked → push fires.
    pushed = router.sweep_tier3_push_notifications(
        client_id, org_id=org_id, now=t0 + timedelta(minutes=PUSH_AFTER_MINUTES, seconds=1)
    )
    assert pushed == [result.escalation_id]
    assert len(notifier.pushes) == 1
    assert list_audit_log(sa_conn, action=ACTION_TIER3_PUSH)


def test_push_does_not_fire_after_founder_acknowledges(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier)
    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = router.dual_route_tier3(client_id, "threat", org_id=org_id, now=t0)

    assert router.acknowledge(result.escalation_id) is True

    pushed = router.sweep_tier3_push_notifications(
        client_id, org_id=org_id, now=t0 + timedelta(minutes=10)
    )
    assert pushed == []
    assert notifier.pushes == []


# ---------------------------------------------------------------------------
# N-hour founder-unacknowledged → on-call playbook (records the ack window)
# ---------------------------------------------------------------------------
def test_unacknowledged_in_n_hours_hands_off_to_oncall_and_records_window(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier, ack_window_hours=2)
    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = router.dual_route_tier3(client_id, "whale_inbound", org_id=org_id, now=t0)

    notifier.oncall.clear()  # isolate the handoff ping from the dual-route ping

    # within the window: no handoff.
    none_yet = router.handle_unacknowledged_escalations(
        client_id, org_id=org_id, now=t0 + timedelta(hours=1)
    )
    assert none_yet == []
    assert notifier.oncall == []

    # past the window, founder still un-acked → on-call handoff.
    handed = router.handle_unacknowledged_escalations(
        client_id, org_id=org_id, now=t0 + timedelta(hours=2, minutes=1)
    )
    assert handed == [result.escalation_id]
    assert len(notifier.oncall) == 1  # on-call pinged for the handoff
    row = _escalation_row(sa_conn, result.escalation_id)
    assert row["oncall_status"] == "acknowledged"  # Arf now owns it
    # the ack window is recorded on the handoff audit row.
    audit = list_audit_log(sa_conn, action=ACTION_TIER3_ONCALL_HANDOFF)
    assert len(audit) == 1
    import json as _json

    detail = _json.loads(audit[0]._mapping["detail_json"])
    assert detail["ack_window_hours"] == 2


def test_acknowledged_escalation_is_not_handed_off(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier, ack_window_hours=2)
    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = router.dual_route_tier3(client_id, "threat", org_id=org_id, now=t0)
    router.acknowledge(result.escalation_id)

    handed = router.handle_unacknowledged_escalations(
        client_id, org_id=org_id, now=t0 + timedelta(hours=5)
    )
    assert handed == []


# ---------------------------------------------------------------------------
# conflict_detected — Arf-only, public reply suppressed, NO silence
# ---------------------------------------------------------------------------
def test_conflict_detected_routes_arf_only_suppresses_public_reply(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier)
    member_id = _seed_member(sa_conn, "heated")

    result = router.route(
        client_id, "conflict_detected", org_id=org_id, member_id=member_id
    )

    assert result.route == ROUTE_ARF_ONLY
    assert result.suppressed_public_reply is True
    assert len(notifier.oncall) == 1
    assert notifier.founder == []  # founder NOT pulled in
    assert result.flagged_user_id is None  # conflict does NOT silence
    assert is_flagged_user(sa_conn, client_id, member_id=member_id) is False
    assert list_audit_log(sa_conn, action=ACTION_ARF_ROUTED)


# ---------------------------------------------------------------------------
# moderation_flag — Arf-only AND auto-silences the author
# ---------------------------------------------------------------------------
def test_moderation_flag_routes_arf_only_and_auto_silences_author(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier)
    member_id = _seed_member(sa_conn, "spammer")

    result = router.route(
        client_id, "moderation_flag", org_id=org_id, member_id=member_id
    )

    assert result.route == ROUTE_ARF_ONLY
    assert result.suppressed_public_reply is True
    assert notifier.founder == []
    assert len(notifier.oncall) == 1
    # author auto-silenced into autocm_flagged_users (status=silenced).
    assert result.flagged_user_id is not None
    assert is_flagged_user(sa_conn, client_id, member_id=member_id) is True
    silenced = sa_conn.execute(
        text(
            "SELECT status FROM autocm_flagged_users WHERE id = :id"
        ),
        {"id": result.flagged_user_id},
    ).fetchone()[0]
    assert silenced == "silenced"
    assert list_audit_log(sa_conn, action=ACTION_USER_SILENCED)


def test_auto_silence_is_idempotent_by_identity(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    member_id = _seed_member(sa_conn, "repeat")
    first = auto_silence_user(sa_conn, client_id, member_id=member_id, org_id=org_id)
    second = auto_silence_user(sa_conn, client_id, member_id=member_id, org_id=org_id)
    assert first is not None and first == second  # no duplicate silenced row
    n = sa_conn.execute(
        text(
            "SELECT COUNT(*) FROM autocm_flagged_users "
            "WHERE client_id = :c AND member_id = :m AND status = 'silenced'"
        ),
        {"c": client_id, "m": member_id},
    ).fetchone()[0]
    assert n == 1


def test_auto_silence_noop_without_identity(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    assert auto_silence_user(sa_conn, client_id, org_id=org_id) is None


# ---------------------------------------------------------------------------
# DESIGN §7 trigger 4 — founder complaint about an auto-sent reply → demote
# ---------------------------------------------------------------------------
def test_founder_complaint_demotes_auto_category(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")

    demoted = demote_on_founder_complaint(
        sa_conn, client_id, "mechanics", org_id=org_id, detail={"draft_id": 7}
    )

    assert demoted is True
    state = sa_conn.execute(
        text(
            "SELECT state FROM autocm_category_state "
            "WHERE client_id = :c AND category = 'mechanics'"
        ),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"
    # trigger 4 has its OWN audit verb (distinct from trigger 3 safety-slip).
    assert list_audit_log(sa_conn, action=ACTION_DEMOTE_FOUNDER)


def test_founder_complaint_on_hitl_category_is_noop(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="hitl")
    assert demote_on_founder_complaint(sa_conn, client_id, "mechanics", org_id=org_id) is False


# ---------------------------------------------------------------------------
# SAFETY §6 — the client-wide 48h pure-HITL freeze
# ---------------------------------------------------------------------------
def test_freeze_flips_every_category_to_hitl_with_freeze_until_48h(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    _seed_category_state(sa_conn, client_id, "greeting", state="auto")
    _seed_category_state(sa_conn, client_id, "trust", state="hitl")

    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    frozen = freeze_client(
        sa_conn, client_id, reason="bot said something embarrassing", org_id=org_id, now=t0
    )

    assert {fc.category for fc in frozen} == {"mechanics", "greeting", "trust"}
    # EVERY category is now hitl with a freeze_until ≥48h in the future.
    rows = sa_conn.execute(
        text(
            "SELECT category, state, freeze_until FROM autocm_category_state "
            "WHERE client_id = :c"
        ),
        {"c": client_id},
    ).fetchall()
    for r in rows:
        assert r._mapping["state"] == "hitl"
        until = datetime.fromisoformat(r._mapping["freeze_until"].replace("Z", "+00:00"))
        assert until >= t0 + timedelta(hours=FREEZE_MIN_HOURS)
        assert is_frozen(sa_conn, client_id, r._mapping["category"], now=t0) is True
    # audit row written + the digest post-mortem hook flag is set.
    audit = list_audit_log(sa_conn, action=ACTION_FREEZE)
    assert len(audit) == 1
    import json as _json

    detail = _json.loads(audit[0]._mapping["detail_json"])
    assert detail["post_mortem_hook"] is True
    assert len(detail["frozen_categories"]) == 3


def test_freeze_hours_floor_is_enforced_to_48h(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    # request a too-short freeze — it must be raised to the 48h floor.
    frozen = freeze_client(sa_conn, client_id, org_id=org_id, hours=1, now=t0)
    until = datetime.fromisoformat(frozen[0].freeze_until.replace("Z", "+00:00"))
    assert until >= t0 + timedelta(hours=FREEZE_MIN_HOURS)


def test_bot_still_drafts_and_hitl_reviews_during_freeze(sa_conn):
    """During the freeze, auto-send is frozen but drafting + HITL review continue.

    The C3.5a gate/confidence.decide forces every category to HITL while frozen
    (REASON_FROZEN), NOT a hard 'drop' — i.e. the message still goes to HITL (the
    bot drafts + a human reviews), it just never auto-sends.
    """
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    freeze_client(sa_conn, client_id, org_id=org_id, now=t0)

    # a high-confidence auto-eligible category that WOULD auto-send if unfrozen.
    verdict = decide(sa_conn, client_id, "mechanics", 0.99, now=t0)
    assert verdict.outcome == "hitl"
    assert verdict.reason == "frozen"
    assert verdict.frozen is True
    # the category is auto-ELIGIBLE (still drafts + routes to HITL, not dropped).
    assert verdict.auto_eligible is True


def test_freeze_auto_restores_prior_state_after_window(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    _seed_category_state(sa_conn, client_id, "greeting", state="auto")
    _seed_category_state(sa_conn, client_id, "trust", state="hitl")
    t0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    freeze_client(sa_conn, client_id, org_id=org_id, now=t0)

    # before the window elapses: nothing restored.
    none_yet = restore_expired_freezes(
        sa_conn, client_id, org_id=org_id, now=t0 + timedelta(hours=1)
    )
    assert none_yet == []

    # after ≥48h: each category auto-restores to its PRIOR state.
    after = t0 + timedelta(hours=FREEZE_MIN_HOURS, minutes=1)
    restored = restore_expired_freezes(sa_conn, client_id, org_id=org_id, now=after)
    by_cat = {fc.category: fc.prior_state for fc in restored}
    assert by_cat == {"mechanics": "auto", "greeting": "auto", "trust": "hitl"}

    rows = sa_conn.execute(
        text(
            "SELECT category, state, freeze_until FROM autocm_category_state "
            "WHERE client_id = :c"
        ),
        {"c": client_id},
    ).fetchall()
    states = {r._mapping["category"]: r._mapping for r in rows}
    assert states["mechanics"]["state"] == "auto"  # restored
    assert states["greeting"]["state"] == "auto"  # restored
    assert states["trust"]["state"] == "hitl"  # restored to its (already-hitl) prior
    # freeze columns cleared → no longer frozen.
    for cat in ("mechanics", "greeting", "trust"):
        assert states[cat]["freeze_until"] is None
        assert is_frozen(sa_conn, client_id, cat, now=after) is False
    # a restore audit row per restored category.
    assert len(list_audit_log(sa_conn, action=ACTION_FREEZE_RESTORED)) == 3


def test_freeze_reason_text_extracts_human_reason_from_envelope(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    freeze_client(sa_conn, client_id, reason="embarrassing post", org_id=org_id)
    fr = sa_conn.execute(
        text(
            "SELECT freeze_reason FROM autocm_category_state "
            "WHERE client_id = :c AND category = 'mechanics'"
        ),
        {"c": client_id},
    ).fetchone()[0]
    assert freeze_reason_text(fr) == "embarrassing post"


def test_freeze_is_distinct_from_pause_client_drafting_preserved(sa_conn):
    """Freeze is NOT /pause-client: it keeps the client enabled + drafting.

    Freezing must not touch autocm_clients.enabled (that is the /pause-client
    surface). The freeze only writes the per-category freeze columns.
    """
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    freeze_client(sa_conn, client_id, org_id=org_id)
    enabled = sa_conn.execute(
        text("SELECT enabled FROM autocm_clients WHERE id = :c"), {"c": client_id}
    ).fetchone()[0]
    assert enabled == 1  # NOT paused — drafting/HITL continues


# ---------------------------------------------------------------------------
# Tier3Escalator Protocol compatibility (the C3.1 seam method)
# ---------------------------------------------------------------------------
def test_escalate_protocol_method_dual_routes(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    # autocm_escalations.draft_id FKs autocm_drafts(id) — seed a real draft.
    sa_conn.execute(
        text(
            "INSERT INTO autocm_drafts (client_id, category, status) "
            "VALUES (:c, 'founder_voice_needed', 'escalated')"
        ),
        {"c": client_id},
    )
    draft_id = int(
        sa_conn.execute(
            text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")
        ).fetchone()[0]
    )
    notifier = FakeNotifier()
    router = Tier3EscalationRouter(sa_conn, notifier)
    escalation_id = router.escalate(client_id, draft_id=draft_id, reason="needs Lex")
    assert isinstance(escalation_id, int)
    assert len(notifier.founder) == 1 and len(notifier.oncall) == 1
