"""C3.5c — the mod-gated operator slash-command surface (HITL_UX §6 / §5).

Covers (MEGAPLAN C3.5c tests / exit):
  * each command is ROLE-GATED — a non-operator is rejected with NO side effect;
  * /silence writes autocm_flagged_users; /clear-flag clears it;
  * /kb-add inserts a chunk; /kb-stale + /kb-remove flip status; /kb-refresh-source
    forces a live refresh;
  * /promote returns the C3.5a gate result (flips iff the gate passes);
  * /demote always succeeds (operator-mark, no gate) and is idempotent;
  * /category-state reports the merged auto/hitl + threshold + samples + clean rate;
  * /voice-drift pulls the last-7d heavy-edit drafts (filterable by register);
  * /punt manually dual-routes a tier-3 to founder + Sable on-call (C3.8a);
  * /pause-client halts ALL publishing (the proactive poster is gated live) and
    /resume-client restores it;
  * /incident-mode on|off toggles C3.8b incident-mode;
  * /approve-all-tier1-<category> over N pending drafts writes a bulk audit row whose
    detail ENUMERATES ALL N approved draft ids (HITL_UX §5);
  * representative commands write an audit row.

Everything runs over a FAKE OperatorReplySender + the C2.7 registry command path —
NO real telegram / network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from sable_platform.autocm.gate.autonomy import _set_category_state
from sable_platform.autocm.operator.commands import (
    ACTION_BULK_APPROVE,
    ACTION_COMMAND_REJECTED,
    ACTION_DEMOTE_OPERATOR,
    ACTION_FLAG_CLEARED,
    ACTION_FLAG_SILENCED,
    ACTION_KB_ADD,
    ACTION_KB_REMOVE,
    ACTION_KB_STALE,
    ACTION_PAUSE,
    ACTION_RESUME,
    AUTONOMY_PAUSED,
    CommandRouter,
    is_publishing_paused,
)
from sable_platform.autocm.db import is_flagged_user
from sable_platform.db.audit import list_audit_log
from sable_platform.relay.bot.registry import CommandEvent, RelayHandlerRegistry


# ---------------------------------------------------------------------------
# Fakes — NO telegram / network.
# ---------------------------------------------------------------------------
class FakeReply:
    def __init__(self) -> None:
        self.replies: list[tuple] = []
        self._n = 0

    def reply(self, chat_id, body):
        self._n += 1
        self.replies.append((chat_id, body))
        return f"reply-{self._n}"

    @property
    def last(self) -> str:
        return self.replies[-1][1] if self.replies else ""


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Seed helpers — org + relay_client + autocm_client + an OPERATOR member.
# ---------------------------------------------------------------------------
def _seed_client(conn, org_id="orgRM"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    # relay_clients is the FK target of relay_member_roles.org_id.
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RobotMoney', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    conn.commit()
    client_id = int(
        conn.execute(
            text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
        ).fetchone()[0]
    )
    return org_id, client_id


def _seed_member(conn, *, platform="telegram", external_user_id="tg-op-1", handle="arf"):
    conn.execute(text("INSERT INTO relay_members (display_name) VALUES (:d)"), {"d": handle})
    member_id = int(
        conn.execute(text("SELECT id FROM relay_members ORDER BY id DESC LIMIT 1")).fetchone()[0]
    )
    conn.execute(
        text(
            "INSERT INTO relay_member_identities (member_id, platform, external_user_id, handle) "
            "VALUES (:m, :p, :ext, :h)"
        ),
        {"m": member_id, "p": platform, "ext": external_user_id, "h": handle},
    )
    conn.commit()
    return member_id


def _grant_operator(conn, member_id, org_id):
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role) "
            "VALUES (:m, :o, 'sable_operator')"
        ),
        {"m": member_id, "o": org_id},
    )
    conn.commit()


def _operator_event(verb, argstr="", *, org_id="orgRM", external_user_id="tg-op-1", chat_id="op-chat"):
    args = argstr.split() if argstr else []
    return CommandEvent(
        org_id=org_id,
        platform="telegram",
        chat_id=chat_id,
        command=verb,
        args=tuple(args),
        argstr=argstr,
        external_user_id=external_user_id,
    )


def _seed_operator_client(conn):
    org_id, client_id = _seed_client(conn)
    member_id = _seed_member(conn)
    _grant_operator(conn, member_id, org_id)
    return org_id, client_id, member_id


def _audits(conn, action):
    """Audit rows matching ``action``, each as a dict with ``detail`` parsed."""
    import json as _json

    out = []
    for row in list_audit_log(conn, limit=500):
        m = row._mapping
        if m["action"] != action:
            continue
        detail = {}
        if m["detail_json"]:
            try:
                detail = _json.loads(m["detail_json"])
            except (ValueError, TypeError):
                detail = {}
        out.append({"action": m["action"], "detail": detail, "actor": m["actor"]})
    return out


# ===========================================================================
# MOD-GATE — a non-operator is rejected with NO side effect (every command).
# ===========================================================================
def test_non_operator_rejected_no_side_effect(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    # seed an auto category so a /demote WOULD have an effect if it ran.
    _set_category_state(sa_conn, client_id, "greeting", "auto")
    sa_conn.commit()
    reply = FakeReply()
    router = CommandRouter(sa_conn, reply)

    # caller is NOT an operator (unknown external id, no role).
    evt = _operator_event("demote", "greeting", external_user_id="rando-99")
    result = router.on_command(evt)

    assert result.rejected is True
    assert result.ok is False
    assert "not authorized" in result.message
    # NO side effect: the category is still 'auto'.
    state = sa_conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'greeting'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "auto"
    # the rejection itself IS audited (operator_command_rejected), but no demote row.
    assert len(_audits(sa_conn, ACTION_COMMAND_REJECTED)) == 1
    assert _audits(sa_conn, ACTION_DEMOTE_OPERATOR) == []


@pytest.mark.parametrize(
    "verb,argstr",
    [
        ("demote", "greeting"),
        ("promote", "greeting"),
        ("silence", "@troll"),
        ("clear-flag", "@troll"),
        ("kb-add", "tag some text"),
        ("kb-stale", "1"),
        ("kb-remove", "1"),
        ("pause-client", ""),
        ("resume-client", ""),
        ("incident-mode", "on"),
        ("punt", "msg-123"),
        ("approve-all-tier1-greeting", ""),
    ],
)
def test_every_command_role_gated(sa_conn, verb, argstr):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    reply = FakeReply()
    router = CommandRouter(sa_conn, reply)
    evt = _operator_event(verb, argstr, external_user_id="not-an-operator")
    result = router.on_command(evt)
    assert result.rejected is True and result.ok is False
    # incident_active / autonomy_state untouched by a rejected command.
    row = sa_conn.execute(
        text("SELECT autonomy_state, incident_active FROM autocm_clients WHERE id = :c"),
        {"c": client_id},
    ).fetchone()
    assert row[0] == "hitl" and row[1] == 0


def test_unresolvable_caller_fails_closed(sa_conn):
    """A caller with no external id and no member_id is not an operator."""
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    reply = FakeReply()
    router = CommandRouter(sa_conn, reply)
    evt = CommandEvent(
        org_id=org_id, platform="telegram", chat_id="c", command="demote",
        args=("greeting",), argstr="greeting", external_user_id=None, member_id=None,
    )
    assert router.on_command(evt).rejected is True


# ===========================================================================
# /demote — C3.5a trigger 2, always allowed, no gate, idempotent.
# ===========================================================================
def test_demote_always_succeeds_and_is_idempotent(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    _set_category_state(sa_conn, client_id, "greeting", "auto")
    sa_conn.commit()
    router = CommandRouter(sa_conn, FakeReply())

    r1 = router.on_command(_operator_event("demote", "greeting"))
    assert r1.ok is True and r1.data["flipped"] is True
    state = sa_conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category='greeting'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"
    # the operator-mark demote audit verb (trigger 2, distinct from rolling/safety/founder).
    assert len(_audits(sa_conn, ACTION_DEMOTE_OPERATOR)) == 1

    # second /demote on an already-HITL category: no change, no second audit row.
    r2 = router.on_command(_operator_event("demote", "greeting"))
    assert r2.ok is True and r2.data["flipped"] is False
    assert len(_audits(sa_conn, ACTION_DEMOTE_OPERATOR)) == 1


# ===========================================================================
# /promote — returns the C3.5a flip-criteria gate result.
# ===========================================================================
def test_promote_returns_gate_result_when_not_met(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    # greeting has 0 samples → gate fails on sample_count.
    r = router.on_command(_operator_event("promote", "greeting"))
    assert r.ok is False
    assert r.data["promote"] is False
    assert any("sample_count" in reason for reason in r.data["reasons"])
    # category stays HITL.
    state = sa_conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category='greeting'"),
        {"c": client_id},
    ).fetchone()
    assert state is None or state[0] == "hitl"


def test_promote_flips_to_auto_when_gate_passes(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    # Seed 50 clean approvals on greeting so the §7 gate passes.
    _seed_clean_reviews(sa_conn, client_id, "greeting", n=50)
    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(_operator_event("promote", "greeting"))
    assert r.ok is True and r.data["promote"] is True
    state = sa_conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category='greeting'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "auto"


def _seed_clean_reviews(conn, client_id, category, *, n):
    for _ in range(n):
        conn.execute(
            text(
                "INSERT INTO autocm_drafts (client_id, category, tier, status, draft_text) "
                "VALUES (:c, :cat, 1, 'approved', 'hi')"
            ),
            {"c": client_id, "cat": category},
        )
        draft_id = int(
            conn.execute(text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")).fetchone()[0]
        )
        conn.execute(
            text(
                "INSERT INTO autocm_reviews (draft_id, client_id, decision, is_clean_approval) "
                "VALUES (:d, :c, 'approve', 1)"
            ),
            {"d": draft_id, "c": client_id},
        )
    conn.commit()


# ===========================================================================
# /silence + /clear-flag — autocm_flagged_users.
# ===========================================================================
def test_silence_writes_flagged_users_and_clear_flag_clears_it(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    # target a known member by handle.
    target_member = _seed_member(sa_conn, external_user_id="tg-troll", handle="troll")
    router = CommandRouter(sa_conn, FakeReply())

    r = router.on_command(_operator_event("silence", "@troll 24h"))
    assert r.ok is True
    assert is_flagged_user(sa_conn, client_id, member_id=target_member) is True
    assert len(_audits(sa_conn, ACTION_FLAG_SILENCED)) == 1

    rc = router.on_command(_operator_event("clear-flag", "@troll"))
    assert rc.ok is True and rc.data["cleared_count"] == 1
    assert is_flagged_user(sa_conn, client_id, member_id=target_member) is False
    assert len(_audits(sa_conn, ACTION_FLAG_CLEARED)) == 1


def test_silence_unlinked_user_by_external_id(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    # no identity row for this handle → silenced by external id.
    r = router.on_command(_operator_event("silence", "@ghost"))
    assert r.ok is True
    assert is_flagged_user(sa_conn, client_id, external_user_id="ghost") is True


# ===========================================================================
# /kb-add /kb-stale /kb-remove /kb-refresh-source — C3.2c KB.
# ===========================================================================
def test_kb_add_inserts_a_chunk(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(
        _operator_event("kb-add", "price The token price is set by the bonding curve.")
    )
    assert r.ok is True
    chunk_id = r.data["chunk_id"]
    row = sa_conn.execute(
        text(
            "SELECT client_id, chunk_text, chunk_authority, status "
            "FROM autocm_kb_chunks WHERE id = :id"
        ),
        {"id": chunk_id},
    ).fetchone()
    assert int(row[0]) == client_id
    assert "bonding curve" in row[1]
    assert float(row[2]) == pytest.approx(0.9)
    assert row[3] == "active"
    assert len(_audits(sa_conn, ACTION_KB_ADD)) == 1


def test_kb_stale_and_kb_remove_flip_status_client_scoped(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    c1 = router.on_command(_operator_event("kb-add", "tagA first chunk text")).data["chunk_id"]
    c2 = router.on_command(_operator_event("kb-add", "tagB second chunk text")).data["chunk_id"]

    rs = router.on_command(_operator_event("kb-stale", str(c1)))
    assert rs.ok is True
    assert _chunk_status(sa_conn, c1) == "stale"
    assert len(_audits(sa_conn, ACTION_KB_STALE)) == 1

    rr = router.on_command(_operator_event("kb-remove", f"{c2} wrong contract address"))
    assert rr.ok is True
    assert _chunk_status(sa_conn, c2) == "wrong"
    removes = _audits(sa_conn, ACTION_KB_REMOVE)
    assert len(removes) == 1
    assert removes[0]["detail"]["reason"] == "wrong contract address"


def test_kb_stale_other_clients_chunk_is_rejected(sa_conn):
    """An operator cannot touch another tenant's chunk (KB_DESIGN §6)."""
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    # a second client + a chunk that belongs to IT, not orgRM.
    other_org, other_client = _seed_client(sa_conn, org_id="orgOTHER")
    sa_conn.execute(
        text(
            "INSERT INTO autocm_kb_sources (client_id, source_type) VALUES (:c, 'manual')"
        ),
        {"c": other_client},
    )
    src = int(sa_conn.execute(text("SELECT id FROM autocm_kb_sources ORDER BY id DESC LIMIT 1")).fetchone()[0])
    sa_conn.execute(
        text(
            "INSERT INTO autocm_kb_chunks (source_id, client_id, chunk_text, status) "
            "VALUES (:s, :c, 'theirs', 'active')"
        ),
        {"s": src, "c": other_client},
    )
    other_chunk = int(sa_conn.execute(text("SELECT id FROM autocm_kb_chunks ORDER BY id DESC LIMIT 1")).fetchone()[0])
    sa_conn.commit()
    router = CommandRouter(sa_conn, FakeReply())
    # orgRM operator tries to stale orgOTHER's chunk → not found for this client.
    r = router.on_command(_operator_event("kb-stale", str(other_chunk)))
    assert r.ok is False
    assert _chunk_status(sa_conn, other_chunk) == "active"  # untouched
    assert _audits(sa_conn, ACTION_KB_STALE) == []


def test_kb_refresh_source_forces_live_refresh(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    # a structured-doc source (no network): refreshing re-indexes its inline text.
    import json

    sa_conn.execute(
        text(
            "INSERT INTO autocm_kb_sources (client_id, source_type, refresh_cadence, fetch_config) "
            "VALUES (:c, 'doc', 'weekly', :fc)"
        ),
        {"c": client_id, "fc": json.dumps({"text": "The vault audit was completed in March."})},
    )
    source_id = int(sa_conn.execute(text("SELECT id FROM autocm_kb_sources ORDER BY id DESC LIMIT 1")).fetchone()[0])
    sa_conn.commit()
    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(_operator_event("kb-refresh-source", str(source_id)))
    assert r.ok is True
    assert r.data["changed"] is True
    assert len(r.data["new_chunk_ids"]) >= 1
    # the live refresh wrote chunks + bumped last_refreshed_at.
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM autocm_kb_chunks WHERE source_id = :s"), {"s": source_id}
    ).fetchone()[0]
    assert n >= 1


def _chunk_status(conn, chunk_id):
    return conn.execute(
        text("SELECT status FROM autocm_kb_chunks WHERE id = :id"), {"id": chunk_id}
    ).fetchone()[0]


# ===========================================================================
# /category-state — C3.5a read.
# ===========================================================================
def test_category_state_reports_merged_view(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    _set_category_state(sa_conn, client_id, "greeting", "auto")
    sa_conn.commit()
    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(_operator_event("category-state", "greeting"))
    assert r.ok is True
    cats = r.data["categories"]
    assert len(cats) == 1
    g = cats[0]
    assert g["category"] == "greeting"
    assert g["state"] == "auto"
    # a runtime autocm_category_state row exists (we set state='auto'), so the
    # runtime confidence_threshold (058 default 0.8) wins over the registry floor.
    assert g["threshold"] == pytest.approx(0.8)
    assert g["sample_count"] == 0
    assert g["frozen"] is False
    assert "greeting" in r.message and "auto" in r.message


def test_category_state_all_categories_when_no_arg(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(_operator_event("category-state"))
    assert r.ok is True
    assert len(r.data["categories"]) > 5  # the full registry


# ===========================================================================
# /voice-drift — last-7d heavy-edit drafts, filterable by register.
# ===========================================================================
def test_voice_drift_pulls_last_7d_heavy_edits_filterable(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    # a HEAVY edit in-window (calm), a LIGHT edit in-window (should NOT show),
    # and a heavy edit OUT of window (8 days ago, should NOT show).
    _seed_edit_review(sa_conn, client_id, "mechanics", "calm", heavy=True, when=now - timedelta(days=1))
    _seed_edit_review(sa_conn, client_id, "greeting", "calm", heavy=False, when=now - timedelta(days=1))
    _seed_edit_review(sa_conn, client_id, "FUD_borderline", "reactive", heavy=True, when=now - timedelta(days=2))
    _seed_edit_review(sa_conn, client_id, "mechanics", "calm", heavy=True, when=now - timedelta(days=8))

    router = CommandRouter(sa_conn, FakeReply(), now=now)
    r = router.on_command(_operator_event("voice-drift"))
    assert r.ok is True
    got = {(d["register"], d["category"]) for d in r.data["drafts"]}
    assert ("calm", "mechanics") in got
    assert ("reactive", "FUD_borderline") in got
    assert ("calm", "greeting") not in got  # light edit excluded
    assert len(r.data["drafts"]) == 2  # the 8-day-old one is out of window

    # register filter: calm only.
    rc = router.on_command(_operator_event("voice-drift", "calm"))
    cats = {d["category"] for d in rc.data["drafts"]}
    assert cats == {"mechanics"}


def _seed_edit_review(conn, client_id, category, register, *, heavy, when):
    draft = "the quick brown fox jumps over the lazy dog every single day"
    if heavy:
        edited = "completely different rewritten text nothing in common at all here"
    else:
        edited = "the quick brown fox jumps over the lazy dog every single night"
    conn.execute(
        text(
            "INSERT INTO autocm_drafts (client_id, category, tier, register, status, draft_text) "
            "VALUES (:c, :cat, 2, :reg, 'approved', :dt)"
        ),
        {"c": client_id, "cat": category, "reg": register, "dt": draft},
    )
    draft_id = int(conn.execute(text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")).fetchone()[0])
    from sable_platform.autocm.gate.autonomy import edit_diff_ratio, is_heavy_edit

    ratio = edit_diff_ratio(draft, edited)
    is_clean = 0 if is_heavy_edit(ratio) else 1
    conn.execute(
        text(
            "INSERT INTO autocm_reviews "
            "(draft_id, client_id, decision, edited_text, edit_diff_size, is_clean_approval, reviewed_at) "
            "VALUES (:d, :c, 'edit', :ed, :eds, :clean, :ra)"
        ),
        {"d": draft_id, "c": client_id, "ed": edited, "eds": ratio, "clean": is_clean, "ra": _iso(when)},
    )
    conn.commit()


# ===========================================================================
# /punt — manual tier-3 dual-route (C3.8a → founder + Sable on-call).
# ===========================================================================
def test_punt_dual_routes_to_founder_and_oncall(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    reply = FakeReply()
    router = CommandRouter(sa_conn, reply)
    r = router.on_command(_operator_event("punt", "https://x.com/some/status/123"))
    assert r.ok is True
    assert r.data["escalation_id"] is not None
    assert r.data["route"] == "dual_route"
    # one escalation row exists.
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM autocm_escalations WHERE client_id = :c"), {"c": client_id}
    ).fetchone()[0]
    assert n == 1
    # both legs were notified via the reply-backed notifier.
    bodies = " ".join(b for _, b in reply.replies)
    assert "[founder]" in bodies and "[on-call]" in bodies


# ===========================================================================
# /pause-client + /resume-client — the kill switch HALTS publishing live.
# ===========================================================================
def test_pause_client_halts_publishing_resume_restores(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())

    assert is_publishing_paused(sa_conn, client_id) is False
    rp = router.on_command(_operator_event("pause-client"))
    assert rp.ok is True and rp.data["flipped"] is True
    assert is_publishing_paused(sa_conn, client_id) is True
    state = sa_conn.execute(
        text("SELECT autonomy_state FROM autocm_clients WHERE id = :c"), {"c": client_id}
    ).fetchone()[0]
    assert state == AUTONOMY_PAUSED
    assert len(_audits(sa_conn, ACTION_PAUSE)) == 1

    rr = router.on_command(_operator_event("resume-client"))
    assert rr.ok is True and rr.data["flipped"] is True
    assert is_publishing_paused(sa_conn, client_id) is False
    assert len(_audits(sa_conn, ACTION_RESUME)) == 1


def test_pause_client_gates_the_live_proactive_poster(sa_conn):
    """The kill switch demonstrably halts a REAL publishing path (the C3.8b poster)."""
    from sable_platform.autocm.escalation.incident import (
        OUTCOME_BLOCKED,
        ProactiveStatusPoster,
        set_incident_mode,
    )

    org_id, client_id, _ = _seed_operator_client(sa_conn)
    # client is in incident-mode (the poster's only-runs-while-active gate passes).
    set_incident_mode(sa_conn, client_id, True)
    # seed a slot-fill constant so the citation gate would otherwise pass.
    sa_conn.execute(
        text(
            "INSERT INTO autocm_kb_constants (client_id, key, value) "
            "VALUES (:c, 'status', 'All systems nominal.')"
        ),
        {"c": client_id},
    )
    sa_conn.commit()

    class FakePub:
        def __init__(self):
            self.sent = []

        def publish(self, org_id, chat_id, text):
            self.sent.append((chat_id, text))
            return "pub-1"

    pub = FakePub()
    poster = ProactiveStatusPoster(sa_conn, pub)

    # PAUSE the client → the poster must NOT publish.
    CommandRouter(sa_conn, FakeReply()).on_command(_operator_event("pause-client"))
    result = poster.post_status(
        client_id, "main-chat", "All systems nominal.",
        available_chunk_ids=[],
    )
    assert result.outcome == OUTCOME_BLOCKED
    assert result.reason == "client_paused_kill_switch"
    assert pub.sent == []  # nothing published — the kill switch held.


# ===========================================================================
# /incident-mode on|off — C3.8b toggle.
# ===========================================================================
def test_incident_mode_on_off_toggles_flag(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    ron = router.on_command(_operator_event("incident-mode", "on"))
    assert ron.ok is True and ron.data["incident_active"] is True
    assert _incident_active(sa_conn, client_id) == 1
    roff = router.on_command(_operator_event("incident-mode", "off"))
    assert roff.ok is True and roff.data["incident_active"] is False
    assert _incident_active(sa_conn, client_id) == 0


def test_incident_mode_bad_arg_rejected_as_usage(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(_operator_event("incident-mode", "maybe"))
    assert r.ok is False
    assert "Usage" in r.message
    assert _incident_active(sa_conn, client_id) == 0


def _incident_active(conn, client_id):
    return conn.execute(
        text("SELECT incident_active FROM autocm_clients WHERE id = :c"), {"c": client_id}
    ).fetchone()[0]


# ===========================================================================
# /approve-all-tier1-<category> — HITL_UX §5 bulk; audit ENUMERATES all N ids.
# ===========================================================================
def test_approve_all_tier1_bulk_enumerates_all_draft_ids(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    # seed N pending tier-1 greeting drafts.
    n = 5
    ids = []
    for _ in range(n):
        sa_conn.execute(
            text(
                "INSERT INTO autocm_drafts (client_id, category, tier, status, draft_text) "
                "VALUES (:c, 'greeting', 1, 'hitl_pending', 'gm')"
            ),
            {"c": client_id},
        )
        ids.append(int(sa_conn.execute(text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")).fetchone()[0]))
    sa_conn.commit()

    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(_operator_event("approve-all-tier1-greeting"))
    assert r.ok is True
    assert sorted(r.data["approved_draft_ids"]) == sorted(ids)

    # every draft is now 'approved' (the C3.6 publisher reads them; this never enqueues).
    statuses = [
        row[0]
        for row in sa_conn.execute(
            text("SELECT status FROM autocm_drafts WHERE client_id = :c"), {"c": client_id}
        ).fetchall()
    ]
    assert statuses == ["approved"] * n

    # ONE bulk audit row whose detail ENUMERATES the FULL list of approved ids.
    bulk = _audits(sa_conn, ACTION_BULK_APPROVE)
    assert len(bulk) == 1
    assert sorted(bulk[0]["detail"]["approved_draft_ids"]) == sorted(ids)
    assert bulk[0]["detail"]["approved_count"] == n
    # each approved draft ALSO got a per-draft review row (clean-approval accounting).
    review_n = sa_conn.execute(
        text("SELECT COUNT(*) FROM autocm_reviews WHERE client_id = :c AND decision='approve'"),
        {"c": client_id},
    ).fetchone()[0]
    assert review_n == n


def test_approve_all_rejects_non_tier1_category(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    # FUD_borderline is tier-2.
    r = router.on_command(_operator_event("approve-all-tier1-FUD_borderline"))
    assert r.ok is False
    assert "tier-1" in r.message


def test_approve_all_unknown_category_rejected(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    r = router.on_command(_operator_event("approve-all-tier1-bogus"))
    assert r.ok is False
    assert "Unknown category" in r.message


# ===========================================================================
# Registration on the C2.7 command-registry path + end-to-end dispatch.
# ===========================================================================
def test_router_registers_and_dispatches_via_registry(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    _set_category_state(sa_conn, client_id, "greeting", "auto")
    sa_conn.commit()
    reply = FakeReply()
    router = CommandRouter(sa_conn, reply)
    registry = RelayHandlerRegistry(sa_conn)
    router.register(registry)
    assert registry.has_command_handler is True

    # the registry parses + dedupes + routes the raw message to the catch-all.
    dispatched = registry.dispatch_command(
        platform="telegram",
        update_id="u-1",
        text="/demote greeting",
        org_id=org_id,
        chat_id="op-chat",
        external_user_id="tg-op-1",
    )
    assert dispatched is True
    state = sa_conn.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category='greeting'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"


def test_registry_dispatch_dedupes_redelivered_command(sa_conn):
    """A redelivered command (same update_id) is NOT double-applied."""
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    registry = RelayHandlerRegistry(sa_conn)
    router.register(registry)

    first = registry.dispatch_command(
        platform="telegram", update_id="dup-1", text="/pause-client",
        org_id=org_id, chat_id="op-chat", external_user_id="tg-op-1",
    )
    second = registry.dispatch_command(
        platform="telegram", update_id="dup-1", text="/pause-client",
        org_id=org_id, chat_id="op-chat", external_user_id="tg-op-1",
    )
    assert first is True and second is False
    # only ONE pause audit row despite two dispatches.
    assert len(_audits(sa_conn, ACTION_PAUSE)) == 1


def test_registry_dispatch_ignores_non_command_text(sa_conn):
    org_id, client_id, _ = _seed_operator_client(sa_conn)
    router = CommandRouter(sa_conn, FakeReply())
    registry = RelayHandlerRegistry(sa_conn)
    router.register(registry)
    assert (
        registry.dispatch_command(
            platform="telegram", update_id="u-x", text="just a normal message",
            org_id=org_id, external_user_id="tg-op-1",
        )
        is False
    )
