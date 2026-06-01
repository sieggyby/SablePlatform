"""Tests for coordinated reply-campaign CRUD (migration 061)."""
from __future__ import annotations

import pytest

from sable_platform.db.campaigns import (
    add_assignment,
    create_campaign,
    get_campaign,
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
