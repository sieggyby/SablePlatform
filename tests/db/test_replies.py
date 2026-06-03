"""Tests for operator reply-suggestion CRUD (migration 056)."""
from __future__ import annotations

import json

import pytest

from sable_platform.db.connection import get_db
from sable_platform.db.replies import (
    conversation_already_replied,
    count_image_recs,
    find_suggestion,
    get_outcomes_summary,
    get_quota,
    log_suggestion,
    record_outcome,
    refund_generation,
    reserve_generation,
)
from datetime import datetime, timedelta, timezone


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "sable.db"
    c = get_db(db_path=str(db_path))
    c.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        ("tig", "TIG", "active"),
    )
    c.commit()
    yield c
    c.close()


# ---- quota ----------------------------------------------------------------

def test_reserve_decrements_remaining(conn):
    r1 = reserve_generation(conn, "@CahitArf11", limit=3, org_id="tig")
    assert r1.allowed and r1.used == 1 and r1.remaining == 2
    r2 = reserve_generation(conn, "@CahitArf11", limit=3, org_id="tig")
    assert r2.allowed and r2.used == 2 and r2.remaining == 1
    conn.commit()


def test_reserve_blocks_at_limit_and_does_not_overcount(conn):
    for _ in range(3):
        assert reserve_generation(conn, "@arf", limit=3).allowed
    blocked = reserve_generation(conn, "@arf", limit=3)
    assert not blocked.allowed and blocked.remaining == 0
    conn.commit()
    # Stored count must remain exactly at the limit (the over-limit reserve
    # was refunded), not limit+1.
    assert get_quota(conn, "@arf", limit=3).used == 3


def test_quota_is_per_operator(conn):
    reserve_generation(conn, "@a", limit=2)
    reserve_generation(conn, "@a", limit=2)
    conn.commit()
    assert not reserve_generation(conn, "@a", limit=2).allowed
    # Different operator has an independent budget.
    assert reserve_generation(conn, "@b", limit=2).allowed
    conn.commit()


def test_refund_releases_slot(conn):
    reserve_generation(conn, "@arf", limit=1)
    conn.commit()
    assert not reserve_generation(conn, "@arf", limit=1).allowed
    refund_generation(conn, "@arf")
    conn.commit()
    assert reserve_generation(conn, "@arf", limit=1).allowed
    conn.commit()


def test_refund_never_goes_negative(conn):
    refund_generation(conn, "@nobody")
    conn.commit()
    assert get_quota(conn, "@nobody").used == 0


# ---- suggestion log + outcomes -------------------------------------------

def test_log_suggestion_persists(conn):
    sid = log_suggestion(
        conn,
        operator_handle="@CahitArf11",
        org_id="tig",
        source_tweet_id="2060858201587994927",
        variants=[{"text": "the part ppl miss...", "voice_fit": 8}],
        source_author="Trillion_Tao",
        model="claude-opus-4-8",
        cost_usd=0.012,
    )
    conn.commit()
    row = conn.execute(
        "SELECT operator_handle, org_id, variants_json, cost_usd FROM reply_suggestions WHERE id = ?",
        (sid,),
    ).fetchone()
    assert row[0] == "@CahitArf11" and row[1] == "tig"
    assert json.loads(row[2])[0]["voice_fit"] == 8
    assert abs(row[3] - 0.012) < 1e-9


def test_log_suggestion_records_clip_media_kind(conn):
    sid = log_suggestion(
        conn, operator_handle="@arf", org_id="tig", source_tweet_id="1",
        variants=[{"text": "x"}], clip_media_kind="image",
    )
    conn.commit()
    row = conn.execute("SELECT clip_media_kind FROM reply_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row[0] == "image"


# ---- mig 062: opportunity_id + source_conversation_id round-trip ---------

def test_log_suggestion_persists_opportunity_and_conversation(conn):
    """mig 062 columns round-trip through log_suggestion."""
    sid = log_suggestion(
        conn,
        operator_handle="@arf",
        org_id="tig",
        source_tweet_id="555",
        variants=[{"text": "x"}],
        opportunity_id=42,
        source_conversation_id="conv-abc",
    )
    conn.commit()
    row = conn.execute(
        "SELECT opportunity_id, source_conversation_id FROM reply_suggestions WHERE id = ?",
        (sid,),
    ).fetchone()
    assert row[0] == 42
    assert row[1] == "conv-abc"


def test_log_suggestion_mig062_columns_default_null(conn):
    """Existing callers (no new kwargs) leave the mig 062 columns NULL — backward compat."""
    sid = log_suggestion(
        conn, operator_handle="@arf", org_id="tig",
        source_tweet_id="556", variants=[{"text": "x"}],
    )
    conn.commit()
    row = conn.execute(
        "SELECT opportunity_id, source_conversation_id FROM reply_suggestions WHERE id = ?",
        (sid,),
    ).fetchone()
    assert row[0] is None
    assert row[1] is None


# ---- mig 062: conversation_already_replied (local depress-already-replied) -

def test_conversation_already_replied_hit_and_miss(conn):
    log_suggestion(
        conn, operator_handle="@arf", org_id="tig", source_tweet_id="700",
        variants=[{"text": "x"}], source_conversation_id="conv-1",
    )
    conn.commit()
    # hit — a reply was logged in this conversation for this org
    assert conversation_already_replied(conn, "tig", "conv-1") is True
    # miss — different conversation
    assert conversation_already_replied(conn, "tig", "conv-unknown") is False


def test_conversation_already_replied_is_org_scoped(conn):
    # second org so the scoping is observable
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        ("solstitch", "SolStitch", "active"),
    )
    log_suggestion(
        conn, operator_handle="@arf", org_id="tig", source_tweet_id="800",
        variants=[{"text": "x"}], source_conversation_id="conv-shared",
    )
    conn.commit()
    # same conversation id, different org → must NOT depress
    assert conversation_already_replied(conn, "tig", "conv-shared") is True
    assert conversation_already_replied(conn, "solstitch", "conv-shared") is False


def test_conversation_already_replied_ignores_null_conversation(conn):
    # a suggestion logged WITHOUT a conversation id must never match
    log_suggestion(
        conn, operator_handle="@arf", org_id="tig", source_tweet_id="900",
        variants=[{"text": "x"}],
    )
    conn.commit()
    assert conversation_already_replied(conn, "tig", "conv-x") is False


def test_count_image_recs_filters_kind_window_and_operator(conn):
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)

    def _log(h, tid, kind, when):
        log_suggestion(conn, operator_handle=h, org_id="tig", source_tweet_id=tid,
                       variants=[{"text": tid}], clip_media_kind=kind, now=when)

    _log("@arf", "1", "image", now)                       # in-window image ✓
    _log("@arf", "2", "image", now - timedelta(days=2))   # in-window image ✓
    _log("@arf", "3", "video", now)                       # video — ignored
    _log("@arf", "4", None, now)                          # text-only — ignored
    _log("@arf", "5", "image", now - timedelta(days=8))   # out-of-window — ignored
    _log("@other", "6", "image", now)                     # other operator — ignored
    conn.commit()

    assert count_image_recs(conn, "@arf", days=7, now=now) == 2
    assert count_image_recs(conn, "@other", days=7, now=now) == 1
    assert count_image_recs(conn, "@nobody", days=7, now=now) == 0


def test_record_outcome_is_idempotent(conn):
    sid = log_suggestion(
        conn, operator_handle="@arf", org_id="tig",
        source_tweet_id="123", variants=[{"text": "x"}],
    )
    conn.commit()
    first = record_outcome(conn, suggestion_id=sid, posted_tweet_id="999",
                           chosen_variant_idx=0, was_edited=True)
    conn.commit()
    second = record_outcome(conn, suggestion_id=sid, posted_tweet_id="999",
                            chosen_variant_idx=0)
    conn.commit()
    assert first is True and second is False
    count = conn.execute(
        "SELECT COUNT(*) FROM reply_outcomes WHERE suggestion_id = ? AND posted_tweet_id = ?",
        (sid, "999"),
    ).fetchone()[0]
    assert count == 1


# ---- reconciliation helpers (lift) ---------------------------------------

def test_find_suggestion_returns_latest(conn):
    older = datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    log_suggestion(conn, operator_handle="@arf", org_id="tig", source_tweet_id="T",
                   variants=[{"text": "old"}], now=older)
    sid_new = log_suggestion(conn, operator_handle="@arf", org_id="tig", source_tweet_id="T",
                             variants=[{"text": "new"}], now=newer)
    conn.commit()
    found = find_suggestion(conn, "@arf", "T")
    assert found is not None
    assert found[0] == sid_new and found[1][0]["text"] == "new"
    assert find_suggestion(conn, "@arf", "nope") is None


def test_outcomes_summary_adoption_and_engagement(conn):
    sid = log_suggestion(conn, operator_handle="@arf", org_id="tig",
                         source_tweet_id="T1", variants=[{"text": "x"}])
    sid2 = log_suggestion(conn, operator_handle="@arf", org_id="tig",
                          source_tweet_id="T2", variants=[{"text": "y"}])
    conn.commit()
    # one adopted (variant used, not edited), one edited
    record_outcome(conn, suggestion_id=sid, posted_tweet_id="p1", chosen_variant_idx=0,
                   was_edited=False, engagement={"total": 10})
    record_outcome(conn, suggestion_id=sid2, posted_tweet_id="p2", chosen_variant_idx=0,
                   was_edited=True, engagement={"total": 20})
    conn.commit()
    s = get_outcomes_summary(conn, "tig")
    assert s["assisted_count"] == 2
    assert s["adopted_count"] == 1
    assert s["adoption_rate"] == 0.5
    assert s["mean_engagement"] == 15.0
    # org isolation
    assert get_outcomes_summary(conn, "other")["assisted_count"] == 0


def test_record_outcome_updates_engagement_on_rerun(conn):
    sid = log_suggestion(conn, operator_handle="@arf", org_id="tig",
                         source_tweet_id="T", variants=[{"text": "x"}])
    conn.commit()
    record_outcome(conn, suggestion_id=sid, posted_tweet_id="p", engagement={"total": 5})
    conn.commit()
    # re-reconcile: same row, fresher engagement → returns False but updates
    again = record_outcome(conn, suggestion_id=sid, posted_tweet_id="p", engagement={"total": 42})
    conn.commit()
    assert again is False
    s = get_outcomes_summary(conn, "tig")
    assert s["assisted_count"] == 1 and s["mean_engagement"] == 42.0
