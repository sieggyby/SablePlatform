"""C3.8b — incident-mode (war-room subsystem).

Covers (MEGAPLAN C3.8b tests / exit):
  * /incident-mode on flips the war-room register + sets incident_active +
    suppresses non-incident tier-1;
  * the proactive timed poster emits a status-update on schedule while
    incident_active (isolated test of the autonomous outbound path);
  * a proactive post containing an uncited/unvetted factual claim is BLOCKED by
    gate/safety + gate/citation_check and is NOT published (and the block is
    audited); a proactive post whose status fact is exact-matched/slot-filled
    passes the §2.5 gate and publishes;
  * with a SAFETY §6 freeze active (freeze_until in the future), the proactive
    poster does NOT auto-send even while incident_active — it drafts to HITL
    instead (freeze ⊃ incident-mode precedence); and toggling /incident-mode on
    while a freeze is active does NOT clear the freeze;
  * /incident-mode off clears incident_active + restores normal register;
  * the ≥3-in-10min (or any-tier-3) auto-suggest DM fires to the operator.

Everything runs over FAKE publish / operator-DM seams + an injectable clock —
NO real telegram / network. Incident handling is NEVER autonomous.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from sable_platform.autocm.escalation.incident import (
    ACTION_INCIDENT_OFF,
    ACTION_INCIDENT_ON,
    ACTION_PROACTIVE_BLOCKED,
    ACTION_PROACTIVE_FROZEN,
    ACTION_PROACTIVE_POST,
    ACTION_SUGGEST,
    INCIDENT_CATEGORY,
    OUTCOME_BLOCKED,
    OUTCOME_FROZEN_TO_HITL,
    OUTCOME_PUBLISHED,
    SUGGEST_DM_BODY,
    TRIGGER_FLAG_THRESHOLD,
    TRIGGER_TIER3,
    WAR_ROOM_REGISTER,
    IncidentSuggester,
    ProactiveStatusPoster,
    format_war_room_status,
    is_incident_active,
    is_tier1_suppressed,
    set_incident_mode,
    should_engage_during_incident,
    war_room_register,
)
from sable_platform.autocm.classifier.register import CALM, REACTIVE
from sable_platform.autocm.escalation.tier3 import freeze_client
from sable_platform.db.audit import list_audit_log


# ---------------------------------------------------------------------------
# FAKE seams — record calls; NO telegram / network.
# ---------------------------------------------------------------------------
class FakePublisher:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self._n = 0

    def publish(self, org_id, chat_id, text):
        self._n += 1
        self.posts.append({"org_id": org_id, "chat_id": chat_id, "text": text})
        return f"outbox-{self._n}"


class FakeHitlDrafter:
    def __init__(self) -> None:
        self.drafts: list[dict] = []

    def draft_to_hitl(self, org_id, chat_id, text, *, reason):
        self.drafts.append(
            {"org_id": org_id, "chat_id": chat_id, "text": text, "reason": reason}
        )


class FakeOperatorNotifier:
    def __init__(self) -> None:
        self.dms: list[dict] = []
        self._n = 0

    def suggest_incident_mode(self, org_id, client_id, body):
        self._n += 1
        self.dms.append({"org_id": org_id, "client_id": client_id, "body": body})
        return f"dm-{self._n}"


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------
def _seed_client(conn, org_id="orgRM"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
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


def _seed_source(conn, client_id):
    conn.execute(
        text(
            "INSERT INTO autocm_kb_sources (client_id, source_type, authority_default) "
            "VALUES (:c, 'doc', 0.9)"
        ),
        {"c": client_id},
    )
    return int(
        conn.execute(
            text("SELECT id FROM autocm_kb_sources ORDER BY id DESC LIMIT 1")
        ).fetchone()[0]
    )


def _seed_chunk(conn, client_id, source_id, chunk_text, *, status="active"):
    conn.execute(
        text(
            "INSERT INTO autocm_kb_chunks (source_id, client_id, chunk_text, status) "
            "VALUES (:s, :c, :t, :st)"
        ),
        {"s": source_id, "c": client_id, "t": chunk_text, "st": status},
    )
    conn.commit()
    return int(
        conn.execute(
            text("SELECT id FROM autocm_kb_chunks ORDER BY id DESC LIMIT 1")
        ).fetchone()[0]
    )


def _incident_active_value(conn, client_id):
    return conn.execute(
        text("SELECT incident_active FROM autocm_clients WHERE id = :id"),
        {"id": client_id},
    ).fetchone()[0]


T0 = datetime(2026, 5, 30, 14, 23, 0, tzinfo=timezone.utc)


# ===========================================================================
# (1) the per-client toggle
# ===========================================================================
def test_incident_mode_on_sets_flag_and_audits(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    assert is_incident_active(sa_conn, client_id) is False

    prior = set_incident_mode(sa_conn, client_id, True, reason="FUD wave")
    assert prior is False
    assert is_incident_active(sa_conn, client_id) is True
    assert bool(_incident_active_value(sa_conn, client_id)) is True

    rows = list_audit_log(sa_conn, action=ACTION_INCIDENT_ON)
    assert len(rows) == 1


def test_incident_mode_off_clears_flag_and_audits(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    prior = set_incident_mode(sa_conn, client_id, False)
    assert prior is True
    assert is_incident_active(sa_conn, client_id) is False
    assert len(list_audit_log(sa_conn, action=ACTION_INCIDENT_OFF)) == 1


# ===========================================================================
# (2) war-room register override
# ===========================================================================
def test_war_room_register_forces_reactive_while_active(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    # not active → base register passes through (calm stays calm).
    assert war_room_register(sa_conn, client_id, base_register=CALM) == CALM
    set_incident_mode(sa_conn, client_id, True)
    # active → forced to the sober war-room register globally, overriding calm.
    assert war_room_register(sa_conn, client_id, base_register=CALM) == WAR_ROOM_REGISTER
    assert WAR_ROOM_REGISTER == REACTIVE


def test_war_room_register_restored_when_off(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    set_incident_mode(sa_conn, client_id, False)
    assert war_room_register(sa_conn, client_id, base_register=CALM) == CALM


def test_format_war_room_status_is_sober_timestamped():
    line = format_war_room_status("treasury is secure, no funds at risk", now=T0)
    assert line == "Update 14:23 UTC: treasury is secure, no funds at risk"
    # no brand emojis / sass — the body is rendered verbatim under the stamp.


def test_format_war_room_status_with_next_update_promise():
    line = format_war_room_status("investigating", now=T0, next_update_minutes=30)
    assert line == "Update 14:23 UTC: investigating Next update by 14:53 UTC."


# ===========================================================================
# (3) tier-1 chatter suppression
# ===========================================================================
def test_non_incident_tier1_suppressed_while_active(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    # before activation: greeting (tier-1) engages normally.
    assert is_tier1_suppressed(sa_conn, client_id, "greeting") is False
    set_incident_mode(sa_conn, client_id, True)
    # while active: non-incident tier-1 chatter is suppressed.
    for cat in ("greeting", "glossary", "catchphrase_repetition"):
        assert is_tier1_suppressed(sa_conn, client_id, cat) is True
        assert should_engage_during_incident(sa_conn, client_id, cat) is False


def test_incident_category_never_suppressed(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    assert is_tier1_suppressed(sa_conn, client_id, INCIDENT_CATEGORY) is False
    assert should_engage_during_incident(sa_conn, client_id, INCIDENT_CATEGORY) is True


def test_tier2_and_tier3_not_suppressed_by_incident_mode(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    # tier-2 (FUD_borderline) + tier-3 (threat) are NOT tier-1 chatter — unaffected.
    assert is_tier1_suppressed(sa_conn, client_id, "FUD_borderline") is False
    assert is_tier1_suppressed(sa_conn, client_id, "threat") is False


def test_tier1_not_suppressed_when_incident_inactive(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    assert is_tier1_suppressed(sa_conn, client_id, "greeting") is False
    assert should_engage_during_incident(sa_conn, client_id, "greeting") is True


# ===========================================================================
# (4) the proactive timed status poster (autonomous outbound path)
# ===========================================================================
def test_proactive_poster_emits_on_schedule_while_active(sa_conn):
    """Isolated test of the autonomous outbound path: while incident_active, the
    poster publishes a war-room status (exact-matched fact passes §2.5) + audits."""
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    src = _seed_source(sa_conn, client_id)
    # exact-quote path: the FULL rendered status line is a verbatim KB chunk, so the
    # §2.5 exact-match-or-slot-fill gate clears (the fact is grounded, not free-text).
    rendered = format_war_room_status("treasury multisig is untouched", now=T0)
    chunk_id = _seed_chunk(sa_conn, client_id, src, rendered)

    pub = FakePublisher()
    poster = ProactiveStatusPoster(sa_conn, pub)
    result = poster.post_status(
        client_id,
        "chat-1",
        "treasury multisig is untouched",
        available_chunk_ids=[chunk_id],
        now=T0,
    )
    assert result is not None
    assert result.outcome == OUTCOME_PUBLISHED
    assert result.published is True
    assert result.rendered_text == rendered
    # actually published through the (fake) relay outbox.
    assert len(pub.posts) == 1
    assert pub.posts[0]["text"] == rendered
    # SAFETY §5 audit row persisted with the field set.
    rows = list_audit_log(sa_conn, action=ACTION_PROACTIVE_POST)
    assert len(rows) == 1


def test_proactive_poster_noop_when_not_in_incident_mode(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    pub = FakePublisher()
    poster = ProactiveStatusPoster(sa_conn, pub)
    # not in incident-mode → the poster does NOT run at all.
    result = poster.post_status(client_id, "chat-1", "anything", now=T0)
    assert result is None
    assert pub.posts == []


def test_proactive_uncited_factual_claim_blocked_by_citation_gate(sa_conn):
    """A proactive post whose status fact is uncited / free-text is BLOCKED by the
    §2.5 exact-match-or-slot-fill citation gate and is NOT published (block audited)."""
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    # No KB chunk / constant matches the claim → exact-match deviation → BLOCK.
    pub = FakePublisher()
    poster = ProactiveStatusPoster(sa_conn, pub)
    # safety-clean (no hard-refusal pattern) but uncited factual claim → the §2.5
    # exact-match-or-slot-fill citation gate auto-rejects (deviation).
    result = poster.post_status(
        client_id,
        "chat-1",
        "the treasury holds nine million dollars as of today",
        available_chunk_ids=[],
        now=T0,
    )
    assert result is not None
    assert result.outcome == OUTCOME_BLOCKED
    assert result.published is False
    assert result.reason == "citation_gate"
    # NOTHING published.
    assert pub.posts == []
    # the block is audited.
    rows = list_audit_log(sa_conn, action=ACTION_PROACTIVE_BLOCKED)
    assert len(rows) == 1


def test_proactive_hard_refusal_content_blocked_by_safety_gate(sa_conn):
    """A proactive post tripping the vendored hard-refusal bank is BLOCKED by
    gate/safety and is NOT published (block audited)."""
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    pub = FakePublisher()
    poster = ProactiveStatusPoster(sa_conn, pub)
    # a financial-advice shaped status trips the vendored hard-refusal bank even
    # under the war-room prefix (verified: "should I buy now" → financial_advice).
    result = poster.post_status(
        client_id,
        "chat-1",
        "should I buy now",
        available_chunk_ids=[],
        now=T0,
    )
    assert result is not None
    assert result.outcome == OUTCOME_BLOCKED
    assert result.reason == "safety_gate"
    assert pub.posts == []
    rows = list_audit_log(sa_conn, action=ACTION_PROACTIVE_BLOCKED)
    assert len(rows) == 1


def test_proactive_slot_fill_status_fact_passes_gate(sa_conn):
    """A status fact that is a literal slot-fill from autocm_kb_constants passes the
    §2.5 gate. The slot-fill leg requires the WHOLE normalized answer to EQUAL the
    constant value, so the status body IS the irreducible (no war-room wrapper)."""
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    # constant value == the whole rendered line (slot-fill is whole-answer EQUAL).
    rendered = format_war_room_status("contract 0xABCDEF is the only official one", now=T0)
    sa_conn.execute(
        text(
            "INSERT INTO autocm_kb_constants (client_id, key, value) VALUES (:c, :k, :v)"
        ),
        {"c": client_id, "k": "official_contract_status", "v": rendered},
    )
    sa_conn.commit()
    pub = FakePublisher()
    poster = ProactiveStatusPoster(sa_conn, pub)
    result = poster.post_status(
        client_id,
        "chat-1",
        "contract 0xABCDEF is the only official one",
        available_chunk_ids=[],
        now=T0,
    )
    assert result is not None
    assert result.outcome == OUTCOME_PUBLISHED
    assert len(pub.posts) == 1


# ===========================================================================
# (4b) SAFETY §6 freeze precedence at the auto-send gate
# ===========================================================================
def test_proactive_poster_drafts_to_hitl_while_frozen(sa_conn):
    """With a SAFETY §6 freeze active (freeze_until in the future), the proactive
    poster does NOT auto-send even while incident_active — it drafts to HITL
    (freeze ⊃ incident-mode precedence)."""
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    src = _seed_source(sa_conn, client_id)
    rendered = format_war_room_status("treasury multisig is untouched", now=T0)
    chunk_id = _seed_chunk(sa_conn, client_id, src, rendered)
    # a category-state row gives the freeze something to set freeze_until on.
    _seed_category_state(sa_conn, client_id, "status", state="auto")
    # freeze starting at T0 → freeze_until is T0 + 48h (well in the future at T0).
    freeze_client(sa_conn, client_id, reason="bot said something embarrassing", now=T0)

    pub = FakePublisher()
    hitl = FakeHitlDrafter()
    poster = ProactiveStatusPoster(sa_conn, pub, hitl_drafter=hitl)
    result = poster.post_status(
        client_id,
        "chat-1",
        "treasury multisig is untouched",
        available_chunk_ids=[chunk_id],
        now=T0,  # still inside the 48h freeze window
    )
    assert result is not None
    assert result.outcome == OUTCOME_FROZEN_TO_HITL
    assert result.published is False
    # auto-send frozen — nothing on the outbox; drafted to HITL instead.
    assert pub.posts == []
    assert len(hitl.drafts) == 1
    assert hitl.drafts[0]["reason"] == "safety_freeze_active"
    # the frozen-to-HITL fallback is audited.
    assert len(list_audit_log(sa_conn, action=ACTION_PROACTIVE_FROZEN)) == 1


def test_proactive_poster_auto_sends_after_freeze_elapses(sa_conn):
    """Once freeze_until has passed, the poster auto-sends again (the freeze is the
    only thing suppressing it — incident-mode itself does not)."""
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    src = _seed_source(sa_conn, client_id)
    _seed_category_state(sa_conn, client_id, "status", state="auto")
    freeze_client(sa_conn, client_id, reason="oops", now=T0)

    after = T0 + timedelta(hours=49)  # past the 48h freeze window
    rendered = format_war_room_status("all clear", now=after)
    chunk_id = _seed_chunk(sa_conn, client_id, src, rendered)

    pub = FakePublisher()
    poster = ProactiveStatusPoster(sa_conn, pub)
    result = poster.post_status(
        client_id, "chat-1", "all clear", available_chunk_ids=[chunk_id], now=after
    )
    assert result is not None
    assert result.outcome == OUTCOME_PUBLISHED
    assert len(pub.posts) == 1


def test_toggling_incident_mode_does_not_clear_active_freeze(sa_conn):
    """Toggling /incident-mode on while a freeze is active does NOT clear the freeze
    (freeze ⊃ incident-mode precedence — the toggle never touches freeze_until)."""
    org_id, client_id = _seed_client(sa_conn)
    _seed_category_state(sa_conn, client_id, "status", state="auto")
    freeze_client(sa_conn, client_id, reason="oops", now=T0)

    def _freeze_until():
        return sa_conn.execute(
            text(
                "SELECT freeze_until FROM autocm_category_state "
                "WHERE client_id = :c AND category = 'status'"
            ),
            {"c": client_id},
        ).fetchone()[0]

    before = _freeze_until()
    assert before is not None
    # toggle incident-mode ON then OFF — freeze_until must be untouched both times.
    set_incident_mode(sa_conn, client_id, True, now=T0)
    assert _freeze_until() == before
    set_incident_mode(sa_conn, client_id, False, now=T0)
    assert _freeze_until() == before
    # the on-audit records the precedence note.
    on_row = list_audit_log(sa_conn, action=ACTION_INCIDENT_ON)[0]
    import json as _json

    detail = _json.loads(on_row._mapping["detail_json"])
    assert detail["freeze_untouched"] is True


# ===========================================================================
# (5) the auto-suggest threshold
# ===========================================================================
def test_suggest_fires_on_3_incident_flags_in_10min(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeOperatorNotifier()
    sugg = IncidentSuggester(sa_conn, notifier)

    # 1st + 2nd flags within window → no suggestion yet.
    assert sugg.record_incident_flag(client_id, now=T0) is None
    assert sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=2)) is None
    # 3rd flag within the 10-min window → suggestion fires.
    result = sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=5))
    assert result is not None
    assert result.trigger == TRIGGER_FLAG_THRESHOLD
    assert result.flag_count == 3
    # DM delivered to the operator with the [Yes][No, flag only] prompt.
    assert len(notifier.dms) == 1
    assert notifier.dms[0]["body"] == SUGGEST_DM_BODY
    # audited.
    assert len(list_audit_log(sa_conn, action=ACTION_SUGGEST)) == 1


def test_suggest_does_not_fire_when_flags_outside_window(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeOperatorNotifier()
    sugg = IncidentSuggester(sa_conn, notifier)
    # 3 flags but spread >10 min apart so the window never holds 3 at once.
    assert sugg.record_incident_flag(client_id, now=T0) is None
    assert sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=6)) is None
    # this one is 12 min after the first → first pruned, window holds only 2.
    assert sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=12)) is None
    assert notifier.dms == []
    assert list_audit_log(sa_conn, action=ACTION_SUGGEST) == []


def test_suggest_fires_on_any_tier3(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeOperatorNotifier()
    sugg = IncidentSuggester(sa_conn, notifier)
    result = sugg.on_tier3_fired(client_id, now=T0)
    assert result is not None
    assert result.trigger == TRIGGER_TIER3
    assert len(notifier.dms) == 1
    assert len(list_audit_log(sa_conn, action=ACTION_SUGGEST)) == 1


def test_suggest_does_not_fire_when_already_in_incident_mode(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    set_incident_mode(sa_conn, client_id, True)
    notifier = FakeOperatorNotifier()
    sugg = IncidentSuggester(sa_conn, notifier)
    # already in incident-mode → nothing to suggest, on either leg.
    assert sugg.on_tier3_fired(client_id, now=T0) is None
    for i in range(4):
        sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=i))
    assert notifier.dms == []


def test_suggest_debounces_within_same_window(sa_conn):
    """Once a suggestion fires for a client's window it does not re-spam the operator
    on every subsequent flag in the same sustained crisis."""
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeOperatorNotifier()
    sugg = IncidentSuggester(sa_conn, notifier)
    for i in range(3):
        sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=i))
    assert len(notifier.dms) == 1  # fired once
    # more flags in the same window → no additional DM (de-bounced).
    sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=4))
    sugg.record_incident_flag(client_id, now=T0 + timedelta(minutes=5))
    assert len(notifier.dms) == 1


def test_suggester_never_flips_the_flag_itself(sa_conn):
    """The suggester only SUGGESTS — incident handling is never autonomous; the flag
    stays off until the operator confirms via /incident-mode on."""
    org_id, client_id = _seed_client(sa_conn)
    notifier = FakeOperatorNotifier()
    sugg = IncidentSuggester(sa_conn, notifier)
    sugg.on_tier3_fired(client_id, now=T0)
    assert is_incident_active(sa_conn, client_id) is False
