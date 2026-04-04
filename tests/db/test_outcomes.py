"""Tests for sable_platform.db.outcomes module."""
from __future__ import annotations

import json

from unittest.mock import patch

from sable_platform.db.outcomes import (
    compute_and_store_diagnostic_delta,
    create_outcome,
    get_diagnostic_deltas,
    list_outcomes,
)


# ---------------------------------------------------------------------------
# create_outcome
# ---------------------------------------------------------------------------

class TestCreateOutcome:
    def test_create_minimal(self, org_db):
        conn, org_id = org_db
        oid = create_outcome(conn, org_id, "client_signed")
        assert isinstance(oid, str) and len(oid) == 32

    def test_create_full_params(self, org_db):
        conn, org_id = org_db
        oid = create_outcome(
            conn, org_id, "metric_change",
            description="Sentiment improved",
            metric_name="sentiment_positive",
            metric_before=0.5,
            metric_after=0.8,
            recorded_by="sieggy",
        )
        row = conn.execute("SELECT * FROM outcomes WHERE outcome_id=?", (oid,)).fetchone()
        assert row["outcome_type"] == "metric_change"
        assert abs(row["metric_delta"] - 0.3) < 0.001
        assert row["recorded_by"] == "sieggy"

    def test_create_no_delta_when_partial_metrics(self, org_db):
        conn, org_id = org_db
        oid = create_outcome(conn, org_id, "general", metric_before=0.5)
        row = conn.execute("SELECT metric_delta FROM outcomes WHERE outcome_id=?", (oid,)).fetchone()
        assert row["metric_delta"] is None

    def test_committed(self, org_db):
        conn, org_id = org_db
        oid = create_outcome(conn, org_id, "general")
        row = conn.execute("SELECT * FROM outcomes WHERE outcome_id=?", (oid,)).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# list_outcomes
# ---------------------------------------------------------------------------

class TestListOutcomes:
    def test_list_empty(self, org_db):
        conn, org_id = org_db
        assert list_outcomes(conn, org_id) == []

    def test_list_all(self, org_db):
        conn, org_id = org_db
        create_outcome(conn, org_id, "client_signed")
        create_outcome(conn, org_id, "metric_change")
        assert len(list_outcomes(conn, org_id)) == 2

    def test_filter_by_type(self, org_db):
        conn, org_id = org_db
        create_outcome(conn, org_id, "client_signed")
        create_outcome(conn, org_id, "metric_change")
        rows = list_outcomes(conn, org_id, outcome_type="client_signed")
        assert len(rows) == 1
        assert rows[0]["outcome_type"] == "client_signed"

    def test_respects_limit(self, org_db):
        conn, org_id = org_db
        for _ in range(5):
            create_outcome(conn, org_id, "general")
        assert len(list_outcomes(conn, org_id, limit=3)) == 3

    def test_scoped_to_org(self, org_db):
        conn, org_id = org_db
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')")
        conn.commit()
        create_outcome(conn, org_id, "general")
        create_outcome(conn, "other", "general")
        assert len(list_outcomes(conn, org_id)) == 1


# ---------------------------------------------------------------------------
# compute_and_store_diagnostic_delta
# ---------------------------------------------------------------------------

class TestDiagnosticDelta:
    def _insert_run(self, conn, org_id, run_id, checkpoint_path=None):
        conn.execute(
            """
            INSERT INTO diagnostic_runs (run_id, org_id, run_type, status, checkpoint_path, completed_at)
            VALUES (?, ?, 'full', 'completed', ?, datetime('now'))
            """,
            (run_id, org_id, checkpoint_path),
        )
        conn.commit()

    def test_no_previous_run_returns_empty(self, org_db):
        conn, org_id = org_db
        self._insert_run(conn, org_id, 100, "/some/path")
        result = compute_and_store_diagnostic_delta(conn, org_id, 100)
        assert result == []

    def test_computes_deltas(self, org_db, tmp_path):
        conn, org_id = org_db
        # Set up two checkpoint dirs with metrics
        before_dir = tmp_path / "before"
        before_dir.mkdir()
        (before_dir / "computed_metrics.json").write_text(
            json.dumps({"fit_score": 0.5, "momentum_score": 0.3})
        )
        after_dir = tmp_path / "after"
        after_dir.mkdir()
        (after_dir / "computed_metrics.json").write_text(
            json.dumps({"fit_score": 0.8, "momentum_score": 0.3})
        )

        self._insert_run(conn, org_id, 1, str(before_dir))
        self._insert_run(conn, org_id, 2, str(after_dir))

        delta_ids = compute_and_store_diagnostic_delta(conn, org_id, 2)
        assert len(delta_ids) >= 1

        deltas = get_diagnostic_deltas(conn, org_id, run_id_after=2)
        fit_delta = [d for d in deltas if d["metric_name"] == "fit_score"]
        assert len(fit_delta) == 1
        assert fit_delta[0]["value_before"] == 0.5
        assert fit_delta[0]["value_after"] == 0.8
        assert abs(fit_delta[0]["delta"] - 0.3) < 0.001

    def test_missing_checkpoint_returns_empty(self, org_db):
        conn, org_id = org_db
        self._insert_run(conn, org_id, 1, "/nonexistent/before")
        self._insert_run(conn, org_id, 2, "/nonexistent/after")
        assert compute_and_store_diagnostic_delta(conn, org_id, 2) == []

    def test_one_sided_metric_skipped(self, org_db, tmp_path):
        """Metric in before but not after → float(None) raises TypeError → skipped."""
        conn, org_id = org_db
        before_dir = tmp_path / "before"
        before_dir.mkdir()
        (before_dir / "computed_metrics.json").write_text(json.dumps({"fit_score": 0.5}))
        after_dir = tmp_path / "after"
        after_dir.mkdir()
        (after_dir / "computed_metrics.json").write_text(json.dumps({}))

        self._insert_run(conn, org_id, 1, str(before_dir))
        self._insert_run(conn, org_id, 2, str(after_dir))
        compute_and_store_diagnostic_delta(conn, org_id, 2)

        deltas = get_diagnostic_deltas(conn, org_id, run_id_after=2)
        fit = [d for d in deltas if d["metric_name"] == "fit_score"]
        # One-sided metric is skipped (float(None) raises TypeError, caught)
        assert len(fit) == 0

    def test_pct_change_none_when_before_is_zero(self, org_db, tmp_path):
        conn, org_id = org_db
        before_dir = tmp_path / "before"
        before_dir.mkdir()
        (before_dir / "computed_metrics.json").write_text(json.dumps({"fit_score": 0.0}))
        after_dir = tmp_path / "after"
        after_dir.mkdir()
        (after_dir / "computed_metrics.json").write_text(json.dumps({"fit_score": 0.5}))

        self._insert_run(conn, org_id, 1, str(before_dir))
        self._insert_run(conn, org_id, 2, str(after_dir))
        compute_and_store_diagnostic_delta(conn, org_id, 2)

        deltas = get_diagnostic_deltas(conn, org_id, run_id_after=2)
        fit = [d for d in deltas if d["metric_name"] == "fit_score"]
        assert len(fit) == 1
        assert fit[0]["delta"] == 0.5
        assert fit[0]["pct_change"] is None

    def test_non_numeric_metric_skipped(self, org_db, tmp_path):
        conn, org_id = org_db
        before_dir = tmp_path / "before"
        before_dir.mkdir()
        (before_dir / "computed_metrics.json").write_text(
            json.dumps({"fit_score": "N/A", "momentum_score": 0.5})
        )
        after_dir = tmp_path / "after"
        after_dir.mkdir()
        (after_dir / "computed_metrics.json").write_text(
            json.dumps({"fit_score": "bad", "momentum_score": 0.7})
        )

        self._insert_run(conn, org_id, 1, str(before_dir))
        self._insert_run(conn, org_id, 2, str(after_dir))
        compute_and_store_diagnostic_delta(conn, org_id, 2)

        deltas = get_diagnostic_deltas(conn, org_id, run_id_after=2)
        metric_names = [d["metric_name"] for d in deltas]
        assert "fit_score" not in metric_names
        assert "momentum_score" in metric_names


# ---------------------------------------------------------------------------
# get_diagnostic_deltas
# ---------------------------------------------------------------------------

class TestGetDiagnosticDeltas:
    def test_empty(self, org_db):
        conn, org_id = org_db
        assert get_diagnostic_deltas(conn, org_id) == []

    def test_filter_by_run_id(self, org_db):
        conn, org_id = org_db
        # Insert deltas directly
        conn.execute(
            """
            INSERT INTO diagnostic_deltas (delta_id, org_id, run_id_before, run_id_after, metric_name,
                                           value_before, value_after, delta, pct_change)
            VALUES ('d1', ?, 1, 2, 'fit_score', 0.5, 0.8, 0.3, 0.6)
            """,
            (org_id,),
        )
        conn.execute(
            """
            INSERT INTO diagnostic_deltas (delta_id, org_id, run_id_before, run_id_after, metric_name,
                                           value_before, value_after, delta, pct_change)
            VALUES ('d2', ?, 2, 3, 'fit_score', 0.8, 0.9, 0.1, 0.125)
            """,
            (org_id,),
        )
        conn.commit()
        rows = get_diagnostic_deltas(conn, org_id, run_id_after=2)
        assert len(rows) == 1
        assert rows[0]["delta_id"] == "d1"
