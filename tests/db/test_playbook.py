"""Tests for sable_platform.db.playbook module."""
from __future__ import annotations

import json

from sable_platform.db.playbook import (
    get_latest_playbook_outcomes,
    get_latest_playbook_targets,
    list_playbook_outcomes,
    list_playbook_targets,
    record_playbook_outcomes,
    upsert_playbook_targets,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_targets():
    return [
        {"metric": "echo_rate", "source": "discord_metrics", "direction": "decrease",
         "baseline": 0.45, "healthy": 0.30, "threshold": 0.375, "gap": 0.15},
        {"metric": "team_reply_rate", "source": "twitter_metrics", "direction": "increase",
         "baseline": 0.05, "healthy": 0.10, "threshold": 0.075, "gap": 0.05},
    ]


def _sample_outcomes():
    return {
        "targets_evaluated": 2,
        "improved": 1,
        "hit_threshold": 1,
        "targets": [
            {"metric": "echo_rate", "baseline": 0.45, "current": 0.35, "improved": True, "hit_threshold": True},
            {"metric": "team_reply_rate", "baseline": 0.05, "current": 0.04, "improved": False, "hit_threshold": False},
        ],
    }


# ---------------------------------------------------------------------------
# Playbook Targets
# ---------------------------------------------------------------------------

class TestUpsertPlaybookTargets:
    def test_insert_returns_row_id(self, org_db):
        conn, org_id = org_db
        row_id = upsert_playbook_targets(conn, org_id, _sample_targets())
        assert row_id is not None and row_id > 0
        row = conn.execute("SELECT id FROM playbook_targets WHERE id=?", (row_id,)).fetchone()
        assert row["id"] == row_id

    def test_targets_json_round_trip(self, org_db):
        conn, org_id = org_db
        targets = _sample_targets()
        upsert_playbook_targets(conn, org_id, targets)
        row = get_latest_playbook_targets(conn, org_id)
        assert json.loads(row["targets_json"]) == targets

    def test_with_artifact_id(self, org_db):
        conn, org_id = org_db
        upsert_playbook_targets(conn, org_id, _sample_targets(), artifact_id="art_123")
        row = get_latest_playbook_targets(conn, org_id)
        assert row["artifact_id"] == "art_123"

    def test_without_artifact_id(self, org_db):
        conn, org_id = org_db
        upsert_playbook_targets(conn, org_id, _sample_targets())
        row = get_latest_playbook_targets(conn, org_id)
        assert row["artifact_id"] is None

    def test_multiple_inserts_accumulate(self, org_db):
        conn, org_id = org_db
        upsert_playbook_targets(conn, org_id, [{"metric": "a"}])
        upsert_playbook_targets(conn, org_id, [{"metric": "b"}])
        rows = list_playbook_targets(conn, org_id)
        assert len(rows) == 2


class TestGetLatestPlaybookTargets:
    def test_returns_none_when_empty(self, org_db):
        conn, org_id = org_db
        assert get_latest_playbook_targets(conn, org_id) is None

    def test_returns_most_recent(self, org_db):
        conn, org_id = org_db
        upsert_playbook_targets(conn, org_id, [{"metric": "old"}])
        # Force distinct timestamps
        conn.execute(
            "UPDATE playbook_targets SET created_at = datetime('now', '-1 hour') WHERE org_id=?",
            (org_id,),
        )
        conn.commit()
        upsert_playbook_targets(conn, org_id, [{"metric": "new"}])
        row = get_latest_playbook_targets(conn, org_id)
        assert json.loads(row["targets_json"]) == [{"metric": "new"}]


class TestListPlaybookTargets:
    def test_ordered_newest_first(self, org_db):
        conn, org_id = org_db
        upsert_playbook_targets(conn, org_id, [{"metric": "first"}])
        conn.execute(
            "UPDATE playbook_targets SET created_at = datetime('now', '-1 hour') WHERE org_id=?",
            (org_id,),
        )
        conn.commit()
        upsert_playbook_targets(conn, org_id, [{"metric": "second"}])
        rows = list_playbook_targets(conn, org_id)
        assert json.loads(rows[0]["targets_json"]) == [{"metric": "second"}]

    def test_limit(self, org_db):
        conn, org_id = org_db
        for i in range(5):
            upsert_playbook_targets(conn, org_id, [{"metric": f"t{i}"}])
        rows = list_playbook_targets(conn, org_id, limit=3)
        assert len(rows) == 3

    def test_empty(self, org_db):
        conn, org_id = org_db
        assert list_playbook_targets(conn, org_id) == []


# ---------------------------------------------------------------------------
# Playbook Outcomes
# ---------------------------------------------------------------------------

class TestRecordPlaybookOutcomes:
    def test_insert_returns_row_id(self, org_db):
        conn, org_id = org_db
        row_id = record_playbook_outcomes(conn, org_id, _sample_outcomes())
        assert row_id is not None and row_id > 0
        row = conn.execute("SELECT id FROM playbook_outcomes WHERE id=?", (row_id,)).fetchone()
        assert row["id"] == row_id

    def test_outcomes_json_round_trip(self, org_db):
        conn, org_id = org_db
        outcomes = _sample_outcomes()
        record_playbook_outcomes(conn, org_id, outcomes)
        row = get_latest_playbook_outcomes(conn, org_id)
        assert json.loads(row["outcomes_json"]) == outcomes

    def test_with_targets_artifact_id(self, org_db):
        conn, org_id = org_db
        record_playbook_outcomes(conn, org_id, _sample_outcomes(), targets_artifact_id="art_456")
        row = get_latest_playbook_outcomes(conn, org_id)
        assert row["targets_artifact_id"] == "art_456"


class TestGetLatestPlaybookOutcomes:
    def test_returns_none_when_empty(self, org_db):
        conn, org_id = org_db
        assert get_latest_playbook_outcomes(conn, org_id) is None

    def test_returns_most_recent(self, org_db):
        conn, org_id = org_db
        record_playbook_outcomes(conn, org_id, {"old": True})
        conn.execute(
            "UPDATE playbook_outcomes SET created_at = datetime('now', '-1 hour') WHERE org_id=?",
            (org_id,),
        )
        conn.commit()
        record_playbook_outcomes(conn, org_id, {"new": True})
        row = get_latest_playbook_outcomes(conn, org_id)
        assert json.loads(row["outcomes_json"]) == {"new": True}


class TestListPlaybookOutcomes:
    def test_ordered_newest_first(self, org_db):
        conn, org_id = org_db
        record_playbook_outcomes(conn, org_id, {"batch": 1})
        conn.execute(
            "UPDATE playbook_outcomes SET created_at = datetime('now', '-1 hour') WHERE org_id=?",
            (org_id,),
        )
        conn.commit()
        record_playbook_outcomes(conn, org_id, {"batch": 2})
        rows = list_playbook_outcomes(conn, org_id)
        assert json.loads(rows[0]["outcomes_json"])["batch"] == 2

    def test_limit(self, org_db):
        conn, org_id = org_db
        for i in range(5):
            record_playbook_outcomes(conn, org_id, {"i": i})
        rows = list_playbook_outcomes(conn, org_id, limit=2)
        assert len(rows) == 2

    def test_empty(self, org_db):
        conn, org_id = org_db
        assert list_playbook_outcomes(conn, org_id) == []
