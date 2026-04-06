"""Tests for sable_platform.db.prospects module."""
from __future__ import annotations

import json

from sable_platform.db.prospects import (
    get_prospect_summary,
    list_prospect_scores,
    sync_prospect_scores,
)


def _sample_score(org_id="zoth", composite=0.72, tier="Tier 1", **overrides):
    base = {
        "org_id": org_id,
        "composite_score": composite,
        "tier": tier,
        "dimensions": {
            "community_health": 0.65,
            "language_signal": 0.5,
            "growth_trajectory": 0.8,
            "engagement_quality": 0.55,
            "sable_fit": composite,
        },
        "rationale": {"reasoning": ["Strong community"], "signal_gaps": []},
        "enrichment": {"sector": "DePIN", "follower_count": 12000},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# sync_prospect_scores
# ---------------------------------------------------------------------------

class TestSyncProspectScores:
    def test_sync_single(self, in_memory_db):
        scores = [_sample_score()]
        count = sync_prospect_scores(in_memory_db, scores, "2026-04-01")
        assert count == 1
        row = in_memory_db.execute("SELECT * FROM prospect_scores WHERE org_id='zoth'").fetchone()
        assert row["composite_score"] == 0.72
        assert row["tier"] == "Tier 1"
        assert row["run_date"] == "2026-04-01"
        dims = json.loads(row["dimensions_json"])
        assert dims["community_health"] == 0.65

    def test_sync_multiple(self, in_memory_db):
        scores = [
            _sample_score("zoth", 0.72, "Tier 1"),
            _sample_score("psy_protocol", 0.58, "Tier 2"),
        ]
        count = sync_prospect_scores(in_memory_db, scores, "2026-04-01")
        assert count == 2

    def test_upsert_on_conflict(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [_sample_score(composite=0.60, tier="Tier 2")], "2026-04-01")
        sync_prospect_scores(in_memory_db, [_sample_score(composite=0.75, tier="Tier 1")], "2026-04-01")
        rows = in_memory_db.execute("SELECT * FROM prospect_scores WHERE org_id='zoth'").fetchall()
        assert len(rows) == 1
        assert rows[0]["composite_score"] == 0.75
        assert rows[0]["tier"] == "Tier 1"

    def test_different_run_dates_coexist(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [_sample_score()], "2026-04-01")
        sync_prospect_scores(in_memory_db, [_sample_score(composite=0.80)], "2026-04-02")
        rows = in_memory_db.execute("SELECT * FROM prospect_scores WHERE org_id='zoth'").fetchall()
        assert len(rows) == 2

    def test_committed(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [_sample_score()], "2026-04-01")
        row = in_memory_db.execute("SELECT COUNT(*) as c FROM prospect_scores").fetchone()
        assert row["c"] == 1

    def test_optional_fields_nullable(self, in_memory_db):
        score = {"org_id": "bare", "composite_score": 0.5, "tier": "Tier 2"}
        sync_prospect_scores(in_memory_db, [score], "2026-04-01")
        row = in_memory_db.execute("SELECT * FROM prospect_scores WHERE org_id='bare'").fetchone()
        assert row["stage"] is None
        assert row["rationale_json"] is None
        assert row["enrichment_json"] is None
        assert row["next_action"] is None
        assert json.loads(row["dimensions_json"]) == {}

    def test_new_fields_stored_and_retrieved(self, in_memory_db):
        """Migration 029: sync accepts and stores the four new Lead Identifier fields."""
        score = _sample_score(
            recommended_action="pursue",
            score_band_low=0.67,
            score_band_high=0.77,
            timing_urgency="Launch window",
        )
        sync_prospect_scores(in_memory_db, [score], "2026-04-01")
        row = in_memory_db.execute("SELECT * FROM prospect_scores WHERE org_id='zoth'").fetchone()
        assert row["recommended_action"] == "pursue"
        assert row["score_band_low"] == 0.67
        assert row["score_band_high"] == 0.77
        assert row["timing_urgency"] == "Launch window"

    def test_new_fields_absent_backward_compat(self, in_memory_db):
        """Older Lead Identifier payloads without new fields still work."""
        score = {"org_id": "legacy", "composite_score": 0.55, "tier": "Tier 2"}
        sync_prospect_scores(in_memory_db, [score], "2026-04-01")
        row = in_memory_db.execute("SELECT * FROM prospect_scores WHERE org_id='legacy'").fetchone()
        assert row["recommended_action"] is None
        assert row["score_band_low"] is None
        assert row["score_band_high"] is None
        assert row["timing_urgency"] is None

    def test_enrichment_json_round_trip(self, in_memory_db):
        enrichment = {"sector": "AI", "follower_count": 50000, "confidence": "high"}
        score = _sample_score(enrichment=enrichment)
        sync_prospect_scores(in_memory_db, [score], "2026-04-01")
        row = in_memory_db.execute("SELECT enrichment_json FROM prospect_scores WHERE org_id='zoth'").fetchone()
        assert json.loads(row["enrichment_json"]) == enrichment


# ---------------------------------------------------------------------------
# list_prospect_scores
# ---------------------------------------------------------------------------

class TestListProspectScores:
    def test_empty(self, in_memory_db):
        assert list_prospect_scores(in_memory_db) == []

    def test_defaults_to_latest_run_date(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [_sample_score("old", 0.5, "Tier 2")], "2026-03-01")
        sync_prospect_scores(in_memory_db, [_sample_score("new", 0.7, "Tier 1")], "2026-04-01")
        rows = list_prospect_scores(in_memory_db)
        assert len(rows) == 1
        assert rows[0]["org_id"] == "new"

    def test_filter_by_run_date(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [_sample_score("a")], "2026-03-01")
        sync_prospect_scores(in_memory_db, [_sample_score("b")], "2026-04-01")
        rows = list_prospect_scores(in_memory_db, run_date="2026-03-01")
        assert len(rows) == 1
        assert rows[0]["org_id"] == "a"

    def test_filter_by_tier(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [
            _sample_score("a", 0.72, "Tier 1"),
            _sample_score("b", 0.58, "Tier 2"),
        ], "2026-04-01")
        rows = list_prospect_scores(in_memory_db, tier="Tier 1")
        assert len(rows) == 1
        assert rows[0]["org_id"] == "a"

    def test_filter_by_min_score(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [
            _sample_score("a", 0.72),
            _sample_score("b", 0.40),
        ], "2026-04-01")
        rows = list_prospect_scores(in_memory_db, min_score=0.5)
        assert len(rows) == 1
        assert rows[0]["org_id"] == "a"

    def test_ordered_by_score_desc(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [
            _sample_score("low", 0.30, "Tier 3"),
            _sample_score("high", 0.90, "Tier 1"),
            _sample_score("mid", 0.60, "Tier 2"),
        ], "2026-04-01")
        rows = list_prospect_scores(in_memory_db)
        scores = [r["composite_score"] for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_new_fields_returned_by_list(self, in_memory_db):
        """list_prospect_scores returns new fields when present."""
        score = _sample_score(
            recommended_action="monitor",
            score_band_low=0.50,
            score_band_high=0.60,
            timing_urgency="Dormant audience",
        )
        sync_prospect_scores(in_memory_db, [score], "2026-04-01")
        rows = list_prospect_scores(in_memory_db)
        assert len(rows) == 1
        assert rows[0]["recommended_action"] == "monitor"
        assert rows[0]["score_band_low"] == 0.50
        assert rows[0]["score_band_high"] == 0.60
        assert rows[0]["timing_urgency"] == "Dormant audience"

    def test_respects_limit(self, in_memory_db):
        for i in range(5):
            sync_prospect_scores(in_memory_db, [_sample_score(f"org_{i}", 0.5 + i * 0.05)], "2026-04-01")
        assert len(list_prospect_scores(in_memory_db, limit=3)) == 3


# ---------------------------------------------------------------------------
# get_prospect_summary
# ---------------------------------------------------------------------------

class TestGetProspectSummary:
    def test_empty(self, in_memory_db):
        s = get_prospect_summary(in_memory_db)
        assert s["total_scored"] == 0
        assert s["by_tier"] == {}
        assert s["run_date"] is None

    def test_counts_by_tier(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [
            _sample_score("a", 0.75, "Tier 1"),
            _sample_score("b", 0.72, "Tier 1"),
            _sample_score("c", 0.58, "Tier 2"),
        ], "2026-04-01")
        s = get_prospect_summary(in_memory_db)
        assert s["total_scored"] == 3
        assert s["by_tier"]["Tier 1"] == 2
        assert s["by_tier"]["Tier 2"] == 1
        assert s["run_date"] == "2026-04-01"

    def test_defaults_to_latest_run_date(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [_sample_score("old")], "2026-03-01")
        sync_prospect_scores(in_memory_db, [_sample_score("new")], "2026-04-01")
        s = get_prospect_summary(in_memory_db)
        assert s["run_date"] == "2026-04-01"
        assert s["total_scored"] == 1

    def test_explicit_run_date(self, in_memory_db):
        sync_prospect_scores(in_memory_db, [_sample_score("a")], "2026-03-01")
        sync_prospect_scores(in_memory_db, [_sample_score("b")], "2026-04-01")
        s = get_prospect_summary(in_memory_db, run_date="2026-03-01")
        assert s["total_scored"] == 1
        assert s["run_date"] == "2026-03-01"
