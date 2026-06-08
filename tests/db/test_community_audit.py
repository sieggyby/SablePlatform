"""Tests for the community-audit helpers (migration 067).

Covers: guild join/consent lifecycle, run/finding/check/snapshot writes, the
reaction-existence ledger with DERIVED decrementing leaderboard (PLAN R3-N2), the
rate-limit counter, and the prospect-org convention + cost-cap closure (PLAN C3).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sable_platform.db import community_audit as ca
from sable_platform.db.cost import get_org_cost_cap
from sable_platform.db.orgs import PROSPECT_AI_USD_PER_WEEK, upsert_prospect_org


# ---------------------------------------------------------------------------
# Guilds + consent
# ---------------------------------------------------------------------------
def test_record_guild_join_idempotent(in_memory_db):
    ca.record_guild_join(in_memory_db, "g1", invited_by="u_admin")
    ca.record_guild_join(in_memory_db, "g1", invited_by=None)  # re-invite
    rows = in_memory_db.execute(
        text("SELECT guild_id, invited_by, org_id, consent_at FROM community_audit_guilds")
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "u_admin"  # invited_by preserved, not clobbered by None
    assert rows[0][2] is None  # no org until consent
    assert rows[0][3] is None  # no consent yet


def test_reinvite_after_consent_preserves_org_and_consent(in_memory_db):
    # B-T2: re-invite after consent must preserve org_id + consent_at (the
    # higher-value invariant) and only refresh invited_by.
    upsert_prospect_org(in_memory_db, org_id="prospect:gZ", display_name="gZ")
    ca.record_guild_join(in_memory_db, "gZ", invited_by="u1")
    ca.set_consent(in_memory_db, "gZ", "prospect:gZ")
    consent = ca.get_guild(in_memory_db, "gZ")["consent_at"]
    ca.record_guild_join(in_memory_db, "gZ", invited_by="u2")  # re-invite
    g = ca.get_guild(in_memory_db, "gZ")
    assert g["org_id"] == "prospect:gZ"  # preserved across re-invite
    assert g["consent_at"] == consent  # preserved
    assert g["invited_by"] == "u2"  # refreshed


def test_set_consent_with_unknown_org_violates_fk(in_memory_db):
    # B-T1: org_id is a real FK to orgs -- binding a non-existent org must fail
    # rather than silently strand the cost gate.
    ca.record_guild_join(in_memory_db, "gFK")
    with pytest.raises(IntegrityError):
        ca.set_consent(in_memory_db, "gFK", "no_such_org")


def test_create_run_rejects_bad_kind(in_memory_db):
    # B-T1: the CHECK on kind is enforced (guards against typos writing junk runs).
    ca.record_guild_join(in_memory_db, "gCK")
    with pytest.raises(IntegrityError):
        ca.create_run(in_memory_db, "gCK", kind="bogus")


def test_security_check_rejects_bad_status(in_memory_db):
    ca.record_guild_join(in_memory_db, "gCK2")
    run_id = ca.create_run(in_memory_db, "gCK2", kind="metadata")
    with pytest.raises(IntegrityError):
        ca.record_security_check(in_memory_db, run_id, "vlevel", "maybe")


def test_set_consent_binds_org_and_is_first_write_wins(in_memory_db):
    upsert_prospect_org(in_memory_db, org_id="prospect:g1", display_name="g1")
    ca.record_guild_join(in_memory_db, "g1", invited_by="u_admin")
    ca.set_consent(in_memory_db, "g1", "prospect:g1")
    g = ca.get_guild(in_memory_db, "g1")
    assert g["org_id"] == "prospect:g1"
    first_consent = g["consent_at"]
    assert first_consent is not None
    # Re-consent keeps the original timestamp (COALESCE).
    ca.set_consent(in_memory_db, "g1", "prospect:g1")
    assert ca.get_guild(in_memory_db, "g1")["consent_at"] == first_consent


# ---------------------------------------------------------------------------
# Prospect-org convention + cost-cap closure (PLAN C3 / R3-N1)
# ---------------------------------------------------------------------------
def test_upsert_prospect_org_follows_convention(in_memory_db):
    import json

    upsert_prospect_org(
        in_memory_db, org_id="prospect:gX", display_name="gX", twitter_handle="@gx"
    )
    row = in_memory_db.execute(
        text("SELECT status, config_json FROM orgs WHERE org_id = 'prospect:gX'")
    ).fetchone()
    assert row[0] == "inactive"  # convention: NOT a new 'prospect' status enum
    cfg = json.loads(row[1])
    assert cfg["org_type"] == "prospect"
    assert cfg["created_via"] == "community_audit"
    assert cfg["max_ai_usd_per_org_per_week"] == PROSPECT_AI_USD_PER_WEEK


def test_prospect_org_cap_is_readable_by_get_org_cost_cap(in_memory_db):
    # C3: the cap must be written under the EXACT key get_org_cost_cap reads.
    upsert_prospect_org(in_memory_db, org_id="prospect:gC", display_name="gC")
    assert get_org_cost_cap(in_memory_db, "prospect:gC") == PROSPECT_AI_USD_PER_WEEK


def test_upsert_prospect_org_preserves_operator_status(in_memory_db):
    upsert_prospect_org(in_memory_db, org_id="prospect:gP", display_name="gP")
    # Operator promotes the prospect to an active client.
    in_memory_db.execute(
        text("UPDATE orgs SET status = 'active' WHERE org_id = 'prospect:gP'")
    )
    in_memory_db.commit()
    # A later re-consent must NOT demote it back to 'inactive'.
    upsert_prospect_org(in_memory_db, org_id="prospect:gP", display_name="gP")
    row = in_memory_db.execute(
        text("SELECT status FROM orgs WHERE org_id = 'prospect:gP'")
    ).fetchone()
    assert row[0] == "active"


# ---------------------------------------------------------------------------
# Run lifecycle + findings/checks/snapshot
# ---------------------------------------------------------------------------
def test_run_lifecycle_and_null_grade(in_memory_db):
    ca.record_guild_join(in_memory_db, "g2")
    run_id = ca.create_run(in_memory_db, "g2", kind="metadata")
    assert isinstance(run_id, int)
    # Grade-suppression: a metadata-only run emits NO overall letter (PLAN C5).
    ca.finish_run(
        in_memory_db,
        run_id,
        status="ok",
        overall_grade=None,
        category_grades_json='{"identity":"B","security":"A"}',
        channels_active=4,
        channels_dead=2,
    )
    row = in_memory_db.execute(
        text(
            "SELECT status, overall_grade, channels_dead, finished_at "
            "FROM community_audit_runs WHERE id = :id"
        ),
        {"id": run_id},
    ).fetchone()
    assert row[0] == "ok"
    assert row[1] is None  # no overall letter
    assert row[2] == 2
    assert row[3] is not None  # finished_at stamped


def test_run_starts_running_until_finished(in_memory_db):
    # AC-M1: a run is 'running' until finish_run flips it — so a mid-write crash
    # leaves a 'running'/finished_at-NULL row, never a 'ok' zombie.
    ca.record_guild_join(in_memory_db, "gRUN")
    run_id = ca.create_run(in_memory_db, "gRUN", kind="deep")
    row = in_memory_db.execute(
        text("SELECT status, finished_at FROM community_audit_runs WHERE id = :id"),
        {"id": run_id},
    ).fetchone()
    assert row[0] == "running"
    assert row[1] is None
    ca.finish_run(in_memory_db, run_id, status="ok")
    row2 = in_memory_db.execute(
        text("SELECT status, finished_at FROM community_audit_runs WHERE id = :id"),
        {"id": run_id},
    ).fetchone()
    assert row2[0] == "ok"
    assert row2[1] is not None


def test_add_finding_stores_jumplink_not_snippet(in_memory_db):
    ca.record_guild_join(in_memory_db, "g3")
    run_id = ca.create_run(in_memory_db, "g3", kind="deep")
    fid = ca.add_finding(
        in_memory_db,
        run_id,
        category="safety",
        type="scam_link",
        title="Likely scam link in #announcements",
        severity="warn",
        message_ref="https://discord.com/channels/g3/c1/m1",
        confidence=0.92,
    )
    row = in_memory_db.execute(
        text("SELECT message_ref, plain_detail, confidence FROM community_audit_findings WHERE id = :id"),
        {"id": fid},
    ).fetchone()
    assert row[0].startswith("https://discord.com/channels/")  # jump-link
    assert row[1] is None  # no verbatim snippet stored (free-tier privacy)
    assert abs(row[2] - 0.92) < 1e-9


def test_security_check_and_settings_snapshot(in_memory_db):
    ca.record_guild_join(in_memory_db, "g4")
    run_id = ca.create_run(in_memory_db, "g4", kind="metadata")
    ca.record_security_check(in_memory_db, run_id, "verification_level", "fail", "NONE on a 5k server")
    ca.save_settings_snapshot(in_memory_db, run_id, boost_level=2, custom_emoji_count=40, has_icon=1)
    # Snapshot is one-per-run (upsert) — re-save updates, not duplicates.
    ca.save_settings_snapshot(in_memory_db, run_id, boost_level=3, custom_emoji_count=41, has_icon=1)
    snap = in_memory_db.execute(
        text("SELECT boost_level, custom_emoji_count FROM community_audit_settings_snapshot WHERE run_id = :id"),
        {"id": run_id},
    ).fetchall()
    assert len(snap) == 1
    assert snap[0][0] == 3 and snap[0][1] == 41
    chk = in_memory_db.execute(
        text("SELECT status FROM community_audit_security_checks WHERE run_id = :id"),
        {"id": run_id},
    ).fetchone()
    assert chk[0] == "fail"


# ---------------------------------------------------------------------------
# Reaction ledger — the R3-N2 derived-decrement correctness test
# ---------------------------------------------------------------------------
def test_reaction_ledger_derived_score_decrements_on_removal(in_memory_db):
    ca.record_guild_join(in_memory_db, "g5")
    # Author A receives 3 distinct reactions.
    ca.add_reaction(in_memory_db, "g5", "post1", "r1", "🔥", author_id="A")
    ca.add_reaction(in_memory_db, "g5", "post1", "r2", "🔥", author_id="A")
    ca.add_reaction(in_memory_db, "g5", "post2", "r1", "👍", author_id="A")
    assert ca.reactions_received(in_memory_db, "g5", "A") == 3
    # Idempotent ADD: same (post, reactor, emoji) does not double-count.
    ca.add_reaction(in_memory_db, "g5", "post1", "r1", "🔥", author_id="A")
    assert ca.reactions_received(in_memory_db, "g5", "A") == 3
    # REMOVE decrements (the whole point — an increment-only counter could not).
    ca.remove_reaction(in_memory_db, "g5", "post1", "r1", "🔥")
    assert ca.reactions_received(in_memory_db, "g5", "A") == 2
    # Removing a non-existent reaction is a harmless no-op.
    ca.remove_reaction(in_memory_db, "g5", "postX", "rX", "🤖")
    assert ca.reactions_received(in_memory_db, "g5", "A") == 2


def test_top_contributors_ranks_by_live_reactions(in_memory_db):
    ca.record_guild_join(in_memory_db, "g6")
    ca.add_reaction(in_memory_db, "g6", "p1", "r1", "🔥", author_id="A")
    ca.add_reaction(in_memory_db, "g6", "p1", "r2", "🔥", author_id="A")
    ca.add_reaction(in_memory_db, "g6", "p2", "r3", "🔥", author_id="B")
    board = ca.top_contributors(in_memory_db, "g6", limit=10)
    assert board[0] == {"author_id": "A", "reactions_received": 2}
    assert board[1] == {"author_id": "B", "reactions_received": 1}


# ---------------------------------------------------------------------------
# Rate-limit counter
# ---------------------------------------------------------------------------
def test_bump_rate_limit_accumulates(in_memory_db):
    t1 = ca.bump_rate_limit(in_memory_db, "global", "audits", "2026-06-06", count=1, ai_usd=0.10)
    assert t1 == {"count": 1, "ai_usd": 0.10}
    t2 = ca.bump_rate_limit(in_memory_db, "global", "audits", "2026-06-06", count=1, ai_usd=0.05)
    assert t2["count"] == 2
    assert abs(t2["ai_usd"] - 0.15) < 1e-9
    # A different window starts fresh.
    t3 = ca.bump_rate_limit(in_memory_db, "global", "audits", "2026-06-07", count=1)
    assert t3["count"] == 1


# ---------------------------------------------------------------------------
# Schema parity (schema.py / migrations agree on columns)
# ---------------------------------------------------------------------------
def test_record_lead(in_memory_db):
    lead_id = ca.record_lead(in_memory_db, "founder@dao.xyz", guild_id="g1", source="audit_page")
    assert isinstance(lead_id, int)
    row = in_memory_db.execute(
        text("SELECT email, guild_id, source FROM community_audit_leads WHERE id = :id"),
        {"id": lead_id},
    ).fetchone()
    assert row[0] == "founder@dao.xyz"
    assert row[1] == "g1"
    assert row[2] == "audit_page"
    # email-only lead (no guild yet) is allowed
    lead2 = ca.record_lead(in_memory_db, "early@bird.xyz")
    assert lead2 != lead_id


def test_community_audit_tables_present_with_key_columns(in_memory_db):
    expected = {
        "community_audit_guilds": {"guild_id", "org_id", "consent_at", "status"},
        "community_audit_runs": {"id", "guild_id", "kind", "overall_grade"},
        "community_audit_findings": {"id", "run_id", "message_ref", "confidence"},
        "community_audit_reaction_ledger": {"guild_id", "post_id", "reactor_id", "emoji", "author_id"},
        "community_audit_rate_limits": {"scope", "key", "window_start", "count", "ai_usd"},
    }
    for table, cols in expected.items():
        present = {
            r[1]
            for r in in_memory_db.execute(text(f"PRAGMA table_info({table})")).fetchall()
        }
        missing = cols - present
        assert not missing, f"{table} missing columns {missing}"
