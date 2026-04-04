"""Tests for sable_platform.db.actions module."""
from __future__ import annotations

import pytest

from sable_platform.db.actions import (
    action_summary,
    claim_action,
    complete_action,
    create_action,
    get_action,
    list_actions,
    skip_action,
)
from sable_platform.errors import ENTITY_NOT_FOUND, SableError


# ---------------------------------------------------------------------------
# create_action
# ---------------------------------------------------------------------------

class TestCreateAction:
    def test_create_minimal(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "DM top contributor")
        assert isinstance(aid, str) and len(aid) == 32

    def test_create_full_params(self, org_db):
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        aid = create_action(
            conn, org_id, "Follow up",
            source="playbook", action_type="dm_outreach",
            entity_id=eid, description="Reach out about grant",
        )
        row = get_action(conn, aid)
        assert row["source"] == "playbook"
        assert row["action_type"] == "dm_outreach"
        assert row["entity_id"] == eid
        assert row["description"] == "Reach out about grant"
        assert row["status"] == "pending"

    def test_create_defaults(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Test")
        row = get_action(conn, aid)
        assert row["source"] == "manual"
        assert row["action_type"] == "general"
        assert row["status"] == "pending"
        assert row["operator"] is None

    def test_create_committed(self, org_db):
        """Data persists without additional commit."""
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Persist check")
        row = conn.execute("SELECT * FROM actions WHERE action_id=?", (aid,)).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# claim_action
# ---------------------------------------------------------------------------

class TestClaimAction:
    def test_claim_sets_operator_and_status(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Claim me")
        claim_action(conn, aid, "sieggy")
        row = get_action(conn, aid)
        assert row["status"] == "claimed"
        assert row["operator"] == "sieggy"
        assert row["claimed_at"] is not None

    def test_claim_committed(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Claim commit check")
        claim_action(conn, aid, "operator1")
        row = conn.execute("SELECT status FROM actions WHERE action_id=?", (aid,)).fetchone()
        assert row["status"] == "claimed"


# ---------------------------------------------------------------------------
# complete_action
# ---------------------------------------------------------------------------

class TestCompleteAction:
    def test_complete_sets_status_and_timestamp(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Complete me")
        complete_action(conn, aid)
        row = get_action(conn, aid)
        assert row["status"] == "completed"
        assert row["completed_at"] is not None

    def test_complete_with_notes(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Notes test")
        complete_action(conn, aid, outcome_notes="Positive response")
        row = get_action(conn, aid)
        assert row["outcome_notes"] == "Positive response"

    def test_complete_without_notes(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "No notes")
        complete_action(conn, aid)
        row = get_action(conn, aid)
        assert row["outcome_notes"] is None


# ---------------------------------------------------------------------------
# skip_action
# ---------------------------------------------------------------------------

class TestSkipAction:
    def test_skip_sets_status_and_timestamp(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Skip me")
        skip_action(conn, aid)
        row = get_action(conn, aid)
        assert row["status"] == "skipped"
        assert row["skipped_at"] is not None

    def test_skip_with_notes(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Skip notes")
        skip_action(conn, aid, outcome_notes="Not relevant")
        row = get_action(conn, aid)
        assert row["outcome_notes"] == "Not relevant"


# ---------------------------------------------------------------------------
# get_action
# ---------------------------------------------------------------------------

class TestGetAction:
    def test_get_returns_row(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Get test")
        row = get_action(conn, aid)
        assert row["action_id"] == aid
        assert row["title"] == "Get test"

    def test_get_nonexistent_raises(self, org_db):
        conn, _ = org_db
        with pytest.raises(SableError) as exc_info:
            get_action(conn, "nonexistent_id")
        assert exc_info.value.code == ENTITY_NOT_FOUND


# ---------------------------------------------------------------------------
# list_actions
# ---------------------------------------------------------------------------

class TestListActions:
    def test_list_empty(self, org_db):
        conn, org_id = org_db
        assert list_actions(conn, org_id) == []

    def test_list_returns_all(self, org_db):
        conn, org_id = org_db
        create_action(conn, org_id, "A")
        create_action(conn, org_id, "B")
        assert len(list_actions(conn, org_id)) == 2

    def test_list_filter_by_status(self, org_db):
        conn, org_id = org_db
        aid1 = create_action(conn, org_id, "Pending one")
        aid2 = create_action(conn, org_id, "Complete one")
        complete_action(conn, aid2)

        pending = list_actions(conn, org_id, status="pending")
        assert len(pending) == 1
        assert pending[0]["action_id"] == aid1

        completed = list_actions(conn, org_id, status="completed")
        assert len(completed) == 1
        assert completed[0]["action_id"] == aid2

    def test_list_respects_limit(self, org_db):
        conn, org_id = org_db
        for i in range(5):
            create_action(conn, org_id, f"Action {i}")
        assert len(list_actions(conn, org_id, limit=3)) == 3

    def test_list_scoped_to_org(self, org_db):
        conn, org_id = org_db
        create_action(conn, org_id, "Org A action")
        # Create second org
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('other_org', 'Other')")
        conn.commit()
        create_action(conn, "other_org", "Org B action")

        assert len(list_actions(conn, org_id)) == 1
        assert len(list_actions(conn, "other_org")) == 1


# ---------------------------------------------------------------------------
# action_summary
# ---------------------------------------------------------------------------

class TestActionSummary:
    def test_empty_org(self, org_db):
        conn, org_id = org_db
        s = action_summary(conn, org_id)
        assert s["total"] == 0
        assert s["execution_rate"] == 0.0
        assert s["avg_days_to_complete"] is None

    def test_counts_by_status(self, org_db):
        conn, org_id = org_db
        create_action(conn, org_id, "Pending")
        aid2 = create_action(conn, org_id, "Claimed")
        claim_action(conn, aid2, "op")
        aid3 = create_action(conn, org_id, "Completed")
        complete_action(conn, aid3)
        aid4 = create_action(conn, org_id, "Skipped")
        skip_action(conn, aid4)

        s = action_summary(conn, org_id)
        assert s["pending"] == 1
        assert s["claimed"] == 1
        assert s["completed"] == 1
        assert s["skipped"] == 1
        assert s["total"] == 4

    def test_execution_rate(self, org_db):
        conn, org_id = org_db
        # 2 completed, 1 pending = 2/3 rate
        for _ in range(2):
            aid = create_action(conn, org_id, "Done")
            complete_action(conn, aid)
        create_action(conn, org_id, "Pending")

        s = action_summary(conn, org_id)
        assert s["execution_rate"] == round(2 / 3, 4)

    def test_execution_rate_excludes_claimed_from_denominator(self, org_db):
        """claimed is excluded from execution_rate denominator."""
        conn, org_id = org_db
        aid1 = create_action(conn, org_id, "Completed")
        complete_action(conn, aid1)
        aid2 = create_action(conn, org_id, "Claimed")
        claim_action(conn, aid2, "op")

        s = action_summary(conn, org_id)
        # denominator = completed(1) + skipped(0) + pending(0) = 1
        assert s["execution_rate"] == 1.0

    def test_summary_avg_days_to_complete_is_numeric(self, org_db):
        conn, org_id = org_db
        aid = create_action(conn, org_id, "Quick task")
        complete_action(conn, aid)
        s = action_summary(conn, org_id)
        assert isinstance(s["avg_days_to_complete"], float)


# ---------------------------------------------------------------------------
# Silent no-op on nonexistent action_id (documents current behavior)
# ---------------------------------------------------------------------------

class TestSilentNoOps:
    def test_claim_nonexistent_action_is_noop(self, org_db):
        conn, _ = org_db
        claim_action(conn, "bogus_id", "sieggy")  # no raise
        row = conn.execute("SELECT COUNT(*) as cnt FROM actions").fetchone()
        assert row["cnt"] == 0

    def test_complete_nonexistent_action_is_noop(self, org_db):
        conn, _ = org_db
        complete_action(conn, "bogus_id")  # no raise

    def test_skip_nonexistent_action_is_noop(self, org_db):
        conn, _ = org_db
        skip_action(conn, "bogus_id")  # no raise


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

class TestListOrdering:
    def test_list_actions_ordered_newest_first(self, org_db):
        conn, org_id = org_db
        aid_a = create_action(conn, org_id, "First")
        # Force a later created_at for the second action
        conn.execute(
            "UPDATE actions SET created_at=datetime('now', '+1 second') WHERE action_id=?",
            (aid_a,),
        )
        conn.commit()
        aid_b = create_action(conn, org_id, "Second")
        conn.execute(
            "UPDATE actions SET created_at=datetime('now', '+2 seconds') WHERE action_id=?",
            (aid_b,),
        )
        conn.commit()
        rows = list_actions(conn, org_id)
        # newest first: B before A
        assert rows[0]["action_id"] == aid_b
        assert rows[1]["action_id"] == aid_a


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_entity(conn, org_id, entity_id="ent_test_1"):
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status) VALUES (?, ?, ?, ?)",
        (entity_id, org_id, "Test Entity", "confirmed"),
    )
    conn.commit()
    return entity_id
