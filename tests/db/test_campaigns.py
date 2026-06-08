"""Tests for coordinated reply-campaign CRUD (migration 061)."""
from __future__ import annotations

import pytest

from sable_platform.db.campaigns import (
    add_assignment,
    create_campaign,
    get_campaign,
    get_campaign_outcomes,
    list_angles_taken,
    list_assignments,
    list_campaigns,
    record_post,
    set_status,
)
from sable_platform.db.connection import get_db


@pytest.fixture
def conn(tmp_path):
    c = get_db(db_path=str(tmp_path / "sable.db"))
    c.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)", ("tig", "TIG", "active"))
    c.commit()
    yield c
    c.close()


def test_create_and_get_campaign(conn):
    cid = create_campaign(
        conn, org_id="tig", target_tweet_id="123",
        target_url="https://x.com/a/status/123", target_author="keone",
        objective="bait the Prometheus question", created_by="@arf",
    )
    conn.commit()
    c = get_campaign(conn, cid)
    assert c["org_id"] == "tig" and c["status"] == "active"
    assert c["objective"] == "bait the Prometheus question"
    assert c["target_author"] == "keone" and c["created_by"] == "@arf"
    assert get_campaign(conn, "nope") is None


def test_list_active_campaigns_is_org_scoped(conn):
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)", ("other", "O", "active"))
    create_campaign(conn, org_id="tig", target_tweet_id="1")
    create_campaign(conn, org_id="tig", target_tweet_id="2")
    create_campaign(conn, org_id="other", target_tweet_id="3")
    conn.commit()
    tig = list_campaigns(conn, "tig")
    assert {c["target_tweet_id"] for c in tig} == {"1", "2"}  # org-scoped


def test_set_status_won_stamps_and_drops_from_active(conn):
    cid = create_campaign(conn, org_id="tig", target_tweet_id="1")
    conn.commit()
    set_status(conn, cid, "won")
    conn.commit()
    c = get_campaign(conn, cid)
    assert c["status"] == "won" and c["won_at"]
    assert cid not in {x["id"] for x in list_campaigns(conn, "tig")}           # not active
    assert cid in {x["id"] for x in list_campaigns(conn, "tig", status=None)}  # but still listed


def test_assignments_and_angle_dedup_excludes_self(conn):
    cid = create_campaign(conn, org_id="tig", target_tweet_id="1")
    add_assignment(conn, campaign_id=cid, operator_handle="@arf", angle="reframe as IP asset")
    add_assignment(conn, campaign_id=cid, operator_handle="@p0ison", angle="dunk on closed labs")
    add_assignment(conn, campaign_id=cid, operator_handle="@mona", angle="ask the naive question")
    conn.commit()
    # the next operator (mona) sees teammates' angles to AVOID, not their own
    others = list_angles_taken(conn, cid, exclude_operator="@mona")
    assert set(others) == {"reframe as IP asset", "dunk on closed labs"}
    assert "ask the naive question" not in others
    assert len(list_assignments(conn, cid)) == 3


def test_record_post_marks_assignment(conn):
    cid = create_campaign(conn, org_id="tig", target_tweet_id="1")
    aid = add_assignment(conn, campaign_id=cid, operator_handle="@arf", angle="x")
    conn.commit()
    record_post(conn, assignment_id=aid, posted_tweet_id="999")
    conn.commit()
    a = next(x for x in list_assignments(conn, cid) if x["id"] == aid)
    assert a["status"] == "posted" and a["posted_tweet_id"] == "999" and a["posted_at"]


# --- Phase 4: objective-aware outcomes -------------------------------------


def _seed_suggestion(conn, *, sid, org="tig"):
    conn.execute(
        "INSERT INTO reply_suggestions (id, operator_handle, org_id, source_tweet_id, variants_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, "@arf", org, "9001", "[]"),
    )


def _seed_outcome(conn, *, sid, ptid, engagement_json, was_edited, chosen_idx):
    import uuid
    conn.execute(
        "INSERT INTO reply_outcomes (id, suggestion_id, posted_tweet_id, posted_at, "
        " chosen_variant_idx, was_edited, engagement_json, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, sid, ptid, "2026-06-05T00:00:00Z", chosen_idx, was_edited,
         engagement_json, "2026-06-05T00:00:00Z"),
    )


def test_get_campaign_outcomes_rolls_up_fixed_age_engagement(conn):
    import json
    cid = create_campaign(conn, org_id="tig", target_tweet_id="1", objective="bait the question")
    for sid in ("sug1", "sug2", "sug3"):
        _seed_suggestion(conn, sid=sid)
    a1 = add_assignment(conn, campaign_id=cid, operator_handle="@arf", suggestion_id="sug1", angle="x")
    a2 = add_assignment(conn, campaign_id=cid, operator_handle="@mona", suggestion_id="sug2", angle="y")
    a3 = add_assignment(conn, campaign_id=cid, operator_handle="@p0ison", suggestion_id="sug3", angle="z")
    for aid, ptid in ((a1, "111"), (a2, "222"), (a3, "333")):
        record_post(conn, assignment_id=aid, posted_tweet_id=ptid)
    # out1: matured (24h total=50), unedited + chosen  -> measured AND adopted
    _seed_outcome(conn, sid="sug1", ptid="111",
                  engagement_json=json.dumps({"total": 50, "source": "fixed_age_snapshot"}),
                  was_edited=0, chosen_idx=0)
    # out2: still maturing ('{}'), unedited + chosen   -> adopted but NOT measured
    _seed_outcome(conn, sid="sug2", ptid="222", engagement_json="{}", was_edited=0, chosen_idx=1)
    # out3: matured (total=10) but EDITED             -> measured but NOT adopted
    _seed_outcome(conn, sid="sug3", ptid="333", engagement_json=json.dumps({"total": 10}),
                  was_edited=1, chosen_idx=2)
    conn.commit()

    r = get_campaign_outcomes(conn, cid)
    assert r is not None
    assert r["objective"] == "bait the question"
    assert r["total_assignments"] == 3
    assert r["total_posted"] == 3 and r["post_rate"] == 1.0
    assert r["outcomes_count"] == 3
    assert r["measured_count"] == 2          # out1 + out3 carry a real 'total'
    assert r["avg_engagement"] == 30.0       # (50 + 10) / 2 — the '{}' is NOT counted as 0
    assert r["adoption_rate"] == pytest.approx(2 / 3)  # out1 + out2 (unedited + chosen)


def test_get_campaign_outcomes_unknown_is_none(conn):
    assert get_campaign_outcomes(conn, "nope") is None


def test_get_campaign_outcomes_no_outcomes_yet(conn):
    cid = create_campaign(conn, org_id="tig", target_tweet_id="1", objective="grow mindshare")
    add_assignment(conn, campaign_id=cid, operator_handle="@arf", suggestion_id="sug1", angle="x")
    conn.commit()
    r = get_campaign_outcomes(conn, cid)
    assert r["objective"] == "grow mindshare"
    assert r["total_assignments"] == 1 and r["total_posted"] == 0 and r["post_rate"] == 0.0
    assert r["outcomes_count"] == 0 and r["measured_count"] == 0
    assert r["avg_engagement"] is None       # nothing matured -> None, never 0
    assert r["adoption_rate"] is None
