"""C3.7 — weekly digest assembly + headline + sections + routing + buttons.

Fixture-driven (the C3.7 exit): seed drafts/reviews + relay_messages (incl.
filter-skipped) + a time-saved baseline, then assert:

  * the time-saved headline == the PINNED formula with the fixture's calibration;
  * approval ratios + FAQ-frequency == hand-computed;
  * Volume + member-activity counts INCLUDE filter-skipped messages;
  * EVERY mandatory DIGEST.md section is present (the fixed pass-set), and section
    8 (freeze post-mortem) is present iff a freeze fired during the week;
  * the digest routes to operator-PREVIEW before week 5 and to the FOUNDER from
    week 5+;
  * a cron miss raises the no-deliver alarm;
  * the founder button set (Approve for KB / Recognize / Demote / Ask) is emitted
    and a press is recorded into autocm_digest_interactions.

Everything offline — FAKE delivery seam + deterministic sentiment scorer; NO
telegram / anthropic / network.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from sable_platform.autocm.digest import weekly
from sable_platform.autocm.digest.weekly import (
    ALERT_NO_DELIVER,
    BUTTON_APPROVE_FOR_KB,
    BUTTON_ASK,
    BUTTON_DEMOTE,
    BUTTON_RECOGNIZE,
    DIGEST_ACTIONS,
    MANDATORY_SECTIONS,
    SECTION_FREEZE_POSTMORTEM,
    compute_minutes_saved,
    deliver,
    deployment_week,
    generate,
    raise_no_deliver_alarm,
    record_interaction,
    TimeSavedBaseline,
)
from sable_platform.autocm.escalation.tier3 import freeze_client
from sable_platform.db.audit import list_audit_log

WEEK = datetime(2026, 5, 18, 0, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# FAKE delivery seam — records calls; NO telegram / network.
# ---------------------------------------------------------------------------
class FakeDelivery:
    def __init__(self):
        self.operator = []
        self.founder = []

    def to_operator(self, org_id, body):
        self.operator.append({"org_id": org_id, "body": body})
        return f"op-{len(self.operator)}"

    def to_founder(self, org_id, body):
        self.founder.append({"org_id": org_id, "body": body})
        return f"founder-{len(self.founder)}"


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
def _seed_org_client(conn, org_id="orgRM", *, engagement_start=None, auto_deliver_from=None):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})
    surface_config = "{}"
    if auto_deliver_from is not None:
        import json

        surface_config = json.dumps({"digest": {"auto_deliver_from": auto_deliver_from}})
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled, surface_config) "
            "VALUES (:o, 'RobotMoney', 'hitl', 1, :sc)"
        ),
        {"o": org_id, "sc": surface_config},
    )
    conn.commit()
    client_id = int(conn.execute(text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}).fetchone()[0])
    if engagement_start is not None:
        conn.execute(
            text(
                "INSERT INTO autocm_time_saved_baseline "
                "(client_id, minutes_per_auto, minutes_per_hitl, engagement_start_at) "
                "VALUES (:c, 2.0, 5.0, :es)"
            ),
            {"c": client_id, "es": _iso(engagement_start)},
        )
        conn.commit()
    return org_id, client_id


def _seed_baseline(conn, client_id, *, per_auto, per_hitl, engagement_start=None):
    conn.execute(
        text(
            "INSERT INTO autocm_time_saved_baseline "
            "(client_id, minutes_per_auto, minutes_per_hitl, engagement_start_at) "
            "VALUES (:c, :a, :h, :es)"
        ),
        {"c": client_id, "a": per_auto, "h": per_hitl, "es": _iso(engagement_start) if engagement_start else None},
    )
    conn.commit()


def _seed_chat(conn, org_id, chat_id="-100"):
    conn.execute(
        text("INSERT INTO relay_chats (org_id, platform, chat_id, title) VALUES (:o, 'telegram', :cid, 'main')"),
        {"o": org_id, "cid": chat_id},
    )
    conn.commit()
    return int(conn.execute(text("SELECT id FROM relay_chats WHERE chat_id = :cid"), {"cid": chat_id}).fetchone()[0])


def _seed_member(conn, name):
    conn.execute(text("INSERT INTO relay_members (display_name) VALUES (:d)"), {"d": name})
    return int(conn.execute(text("SELECT id FROM relay_members ORDER BY id DESC LIMIT 1")).fetchone()[0])


_SEQ = {"n": 0}


def _seed_message(conn, org_id, chat, *, member_id, text_body, received_at):
    _SEQ["n"] += 1
    conn.execute(
        text(
            "INSERT INTO relay_messages "
            "(org_id, chat_id, member_id, platform, external_message_id, text, received_at) "
            "VALUES (:o, :chat, :mid, 'telegram', :emid, :txt, :ra)"
        ),
        {"o": org_id, "chat": chat, "mid": member_id, "emid": f"e{_SEQ['n']}", "txt": text_body, "ra": received_at},
    )
    conn.commit()


def _seed_draft(conn, client_id, *, category, status, created_at):
    conn.execute(
        text(
            "INSERT INTO autocm_drafts (client_id, category, status, register, draft_text, created_at) "
            "VALUES (:c, :cat, :st, 'calm', 'd', :ca)"
        ),
        {"c": client_id, "cat": category, "st": status, "ca": created_at},
    )
    conn.commit()
    return int(conn.execute(text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")).fetchone()[0])


def _seed_review(conn, client_id, draft_id, *, decision, eds, is_clean, reviewed_at):
    conn.execute(
        text(
            "INSERT INTO autocm_reviews (draft_id, client_id, decision, edit_diff_size, is_clean_approval, reviewed_at) "
            "VALUES (:d, :c, :dec, :eds, :cl, :ra)"
        ),
        {"d": draft_id, "c": client_id, "dec": decision, "eds": eds, "cl": is_clean, "ra": reviewed_at},
    )
    conn.commit()


def _seed_category_state(conn, client_id, category, state="auto"):
    conn.execute(
        text("INSERT INTO autocm_category_state (client_id, category, state) VALUES (:c, :cat, :s)"),
        {"c": client_id, "cat": category, "s": state},
    )
    conn.commit()


def _full_week_fixture(conn, org_id, client_id):
    """A representative week: drafts (auto/HITL/escalated) + reviews + corpus."""
    ca = _iso(datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc))
    # 4 auto_sent, 2 approved (HITL), 1 escalated.
    for _ in range(4):
        _seed_draft(conn, client_id, category="mechanics", status="auto_sent", created_at=ca)
    hitl = [_seed_draft(conn, client_id, category="mechanics", status="approved", created_at=ca) for _ in range(2)]
    _seed_draft(conn, client_id, category="threat", status="escalated", created_at=ca)
    _seed_review(conn, client_id, hitl[0], decision="approve", eds=0.0, is_clean=1, reviewed_at=ca)
    _seed_review(conn, client_id, hitl[1], decision="edit", eds=0.6, is_clean=0, reviewed_at=ca)  # heavy → voice-drift
    # corpus: 2 members asking shared-topic substantive questions (incl. filter-skipped).
    chat = _seed_chat(conn, org_id)
    rohan = _seed_member(conn, "rohan")
    adi = _seed_member(conn, "adi")
    base = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
    for i in range(3):
        _seed_message(conn, org_id, chat, member_id=rohan, text_body="how does the agent reasoning work?", received_at=_iso(base.replace(minute=i)))
    for i in range(2):
        _seed_message(conn, org_id, chat, member_id=adi, text_body="what are the agent reasoning logs?", received_at=_iso(base.replace(minute=10 + i)))
    return chat


# ---------------------------------------------------------------------------
# Time-saved formula — PINNED + deterministic
# ---------------------------------------------------------------------------
def test_compute_minutes_saved_is_the_pinned_formula():
    b = TimeSavedBaseline(minutes_per_auto=2.0, minutes_per_hitl=5.0, engagement_start_at=None)
    # 4*2 + 2*5 = 8 + 10 = 18
    assert compute_minutes_saved(4, 2, b) == 18.0


def test_headline_time_saved_equals_pinned_formula_with_fixture_calibration(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _seed_baseline(sa_conn, client_id, per_auto=3.0, per_hitl=7.0, engagement_start=datetime(2026, 5, 4, tzinfo=timezone.utc))
    _full_week_fixture(sa_conn, org_id, client_id)

    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    # 4 auto_sent, 2 HITL-handled (approved) → 4*3 + 2*7 = 12 + 14 = 26.
    assert report.ratios.auto_handled == 4
    assert report.ratios.hitl_handled == 2
    assert report.minutes_saved == 26.0
    # the body prints the same number.
    assert "26" in report.body


def test_missing_baseline_yields_zero_minutes_but_headline_present(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _full_week_fixture(sa_conn, org_id, client_id)
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    assert report.minutes_saved == 0.0
    assert report.has_section("headline")


# ---------------------------------------------------------------------------
# Ratios + FAQ-frequency hand-computed via the assembled report
# ---------------------------------------------------------------------------
def test_report_ratios_and_faq_hand_computed(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _seed_baseline(sa_conn, client_id, per_auto=2.0, per_hitl=5.0)
    _full_week_fixture(sa_conn, org_id, client_id)

    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    r = report.ratios
    assert r.total_drafts == 7
    assert r.auto_pct == round(4 / 7, 4)
    assert r.escalated == 1
    # FAQ: "how does the agent reasoning work" 3x, "agent reasoning logs please" 2x.
    assert report.top_questions[0][1] == 3
    assert report.top_questions[1][1] == 2


# ---------------------------------------------------------------------------
# Volume includes filter-skipped messages
# ---------------------------------------------------------------------------
def test_report_volume_includes_filter_skipped(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _full_week_fixture(sa_conn, org_id, client_id)
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    # 5 corpus messages, NONE with a draft FK → all counted.
    assert report.volume.messages == 5
    assert report.volume.distinct_members == 2


# ---------------------------------------------------------------------------
# Mandatory sections present (the fixed pass-set)
# ---------------------------------------------------------------------------
def test_all_mandatory_sections_present(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _seed_baseline(sa_conn, client_id, per_auto=2.0, per_hitl=5.0)
    _full_week_fixture(sa_conn, org_id, client_id)
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    for section in MANDATORY_SECTIONS:
        assert report.has_section(section), f"missing mandatory section {section}"
    # no freeze fired → section 8 absent.
    assert not report.has_section(SECTION_FREEZE_POSTMORTEM)


def test_freeze_postmortem_section_present_only_when_freeze_fired(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _seed_baseline(sa_conn, client_id, per_auto=2.0, per_hitl=5.0)
    _full_week_fixture(sa_conn, org_id, client_id)
    # seed a promoted category + fire a SAFETY §6 freeze INSIDE the digest week.
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    freeze_client(
        sa_conn, client_id, reason="bot said something off", org_id=org_id,
        now=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
    )
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    assert report.has_section(SECTION_FREEZE_POSTMORTEM)
    assert len(report.freeze_postmortems) == 1
    assert "mechanics" in report.freeze_postmortems[0].frozen_categories
    assert "POST-MORTEM" in report.body


def test_freeze_outside_week_does_not_add_section(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _full_week_fixture(sa_conn, org_id, client_id)
    _seed_category_state(sa_conn, client_id, "mechanics", state="auto")
    # freeze fired the PRIOR week — must not appear in this week's post-mortem.
    freeze_client(
        sa_conn, client_id, org_id=org_id,
        now=datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc),
    )
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    assert not report.has_section(SECTION_FREEZE_POSTMORTEM)


# ---------------------------------------------------------------------------
# Founder buttons (DIGEST §4)
# ---------------------------------------------------------------------------
def test_founder_button_set_emitted(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    _seed_baseline(sa_conn, client_id, per_auto=2.0, per_hitl=5.0)
    _full_week_fixture(sa_conn, org_id, client_id)
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    actions = {b.action for b in report.buttons}
    # Approve-for-KB on top questions, Recognize on cultists, Demote on voice-drift, Ask always.
    assert BUTTON_APPROVE_FOR_KB in actions
    assert BUTTON_RECOGNIZE in actions
    assert BUTTON_DEMOTE in actions
    assert BUTTON_ASK in actions
    # an Approve-for-KB button targets one of the surfaced question clusters.
    kb_btns = [b for b in report.buttons if b.action == BUTTON_APPROVE_FOR_KB]
    assert kb_btns and kb_btns[0].target_ref in {q for q, _ in report.top_questions}


# ---------------------------------------------------------------------------
# Interaction recording into autocm_digest_interactions
# ---------------------------------------------------------------------------
def test_record_interaction_writes_row_and_audits(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    iid = record_interaction(
        sa_conn, client_id, BUTTON_APPROVE_FOR_KB,
        section="top_questions", target_ref="how does it work", digest_period="2026-W21",
        actor="founder", org_id=org_id,
    )
    assert iid > 0
    row = sa_conn.execute(
        text("SELECT action, section, target_ref, actor FROM autocm_digest_interactions WHERE id = :i"),
        {"i": iid},
    ).fetchone()._mapping
    assert row["action"] == "approve_for_kb"
    assert row["section"] == "top_questions"
    assert row["actor"] == "founder"
    # audited.
    audits = list_audit_log(sa_conn, action=weekly.ACTION_DIGEST_INTERACTION, org_id=org_id)
    assert len(audits) == 1


def test_record_interaction_rejects_unknown_action(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    with pytest.raises(ValueError):
        record_interaction(sa_conn, client_id, "frobnicate", org_id=org_id)


def test_all_digest_actions_are_recordable(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    for action in DIGEST_ACTIONS:
        iid = record_interaction(sa_conn, client_id, action, org_id=org_id)
        assert iid > 0


# ---------------------------------------------------------------------------
# Preview-vs-deliver routing (DIGEST §5)
# ---------------------------------------------------------------------------
def test_deployment_week_computation():
    b = TimeSavedBaseline(2.0, 5.0, "2026-05-04T00:00:00Z")
    # week of 2026-05-04 is week 1; +14 days is week 3; +28 days week 5.
    assert deployment_week(b, datetime(2026, 5, 4, tzinfo=timezone.utc)) == 1
    assert deployment_week(b, datetime(2026, 5, 18, tzinfo=timezone.utc)) == 3
    assert deployment_week(b, datetime(2026, 6, 1, tzinfo=timezone.utc)) == 5


def test_routes_to_operator_preview_before_week_5(sa_conn):
    # engagement start makes WEEK (2026-05-18) deployment-week 3 (< 5) → operator preview.
    org_id, client_id = _seed_org_client(sa_conn, engagement_start=datetime(2026, 5, 4, tzinfo=timezone.utc))
    _full_week_fixture(sa_conn, org_id, client_id)
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    fake = FakeDelivery()
    outcome = deliver(sa_conn, report, fake, WEEK)
    assert outcome.routed_to == "operator_preview"
    assert len(fake.operator) == 1
    assert len(fake.founder) == 0  # founder NOT delivered before week 5


def test_routes_to_founder_from_week_5(sa_conn):
    # engagement start 4 weeks before WEEK → deployment-week 5 → founder.
    org_id, client_id = _seed_org_client(sa_conn, engagement_start=datetime(2026, 4, 20, tzinfo=timezone.utc))
    _full_week_fixture(sa_conn, org_id, client_id)
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    fake = FakeDelivery()
    outcome = deliver(sa_conn, report, fake, WEEK)
    assert outcome.deployment_week >= 5
    assert outcome.routed_to == "founder"
    assert len(fake.founder) == 1
    # a copy STILL goes to the operator for visibility (DIGEST §5).
    assert len(fake.operator) == 1


def test_auto_deliver_from_override_forces_earlier_founder_delivery(sa_conn):
    # auto_deliver_from=1 → even deployment-week 1 delivers to the founder.
    org_id, client_id = _seed_org_client(
        sa_conn, engagement_start=datetime(2026, 5, 18, tzinfo=timezone.utc), auto_deliver_from=1
    )
    _full_week_fixture(sa_conn, org_id, client_id)
    report = generate(sa_conn, client_id, WEEK, org_id=org_id)
    fake = FakeDelivery()
    outcome = deliver(sa_conn, report, fake, WEEK)
    assert outcome.routed_to == "founder"


# ---------------------------------------------------------------------------
# No-deliver alarm (DIGEST §6)
# ---------------------------------------------------------------------------
def test_cron_miss_raises_no_deliver_alarm(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    alert_id = raise_no_deliver_alarm(sa_conn, client_id, org_id=org_id, week_start="2026-05-18")
    assert alert_id
    row = sa_conn.execute(
        text("SELECT alert_type, org_id FROM alerts WHERE alert_id = :a"), {"a": alert_id}
    ).fetchone()._mapping
    assert row["alert_type"] == ALERT_NO_DELIVER
    assert row["org_id"] == org_id


def test_no_deliver_alarm_dedups_per_week(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    first = raise_no_deliver_alarm(sa_conn, client_id, org_id=org_id, week_start="2026-05-18")
    second = raise_no_deliver_alarm(sa_conn, client_id, org_id=org_id, week_start="2026-05-18")
    assert first
    assert second is None  # dedup-suppressed for the same client × week
