"""Tests for Outcome Tracking and Diagnostic Deltas (Feature 2)."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from sable_platform.db.outcomes import (
    create_outcome,
    list_outcomes,
    compute_and_store_diagnostic_delta,
    get_diagnostic_deltas,
    TRACKED_METRICS,
)


def _insert_diagnostic_run(conn, org_id, checkpoint_path, status="completed") -> int:
    """Insert a diagnostic_run and return its auto-assigned run_id."""
    cur = conn.execute(
        """
        INSERT INTO diagnostic_runs
            (org_id, project_slug, run_type, run_date, checkpoint_path, status)
        VALUES (?, 'test_proj', 'full', '2026-01-01', ?, ?)
        """,
        (org_id, checkpoint_path, status),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Outcome CRUD
# ---------------------------------------------------------------------------

def test_create_outcome_returns_id(org_db):
    conn, org_id = org_db
    oid = create_outcome(conn, org_id, "client_signed", description="They signed!")
    assert oid and len(oid) == 32


def test_create_outcome_metric_delta_computed(org_db):
    conn, org_id = org_db
    oid = create_outcome(
        conn, org_id, "metric_change",
        metric_name="fit_score",
        metric_before=6.0,
        metric_after=8.0,
    )
    row = conn.execute("SELECT * FROM outcomes WHERE outcome_id=?", (oid,)).fetchone()
    assert row["metric_delta"] == pytest.approx(2.0)


def test_list_outcomes_filter_type(org_db):
    conn, org_id = org_db
    create_outcome(conn, org_id, "client_signed")
    create_outcome(conn, org_id, "client_churned")
    create_outcome(conn, org_id, "client_signed")
    signed = list_outcomes(conn, org_id, outcome_type="client_signed")
    assert len(signed) == 2
    churned = list_outcomes(conn, org_id, outcome_type="client_churned")
    assert len(churned) == 1


# ---------------------------------------------------------------------------
# Diagnostic deltas
# ---------------------------------------------------------------------------

def test_compute_delta_single_run_returns_empty(org_db):
    conn, org_id = org_db
    with tempfile.TemporaryDirectory() as tmpdir:
        metrics = {"fit_score": 6.0, "bot_reply_rate": 0.2}
        json.dump(metrics, open(os.path.join(tmpdir, "computed_metrics.json"), "w"))
        run_id = _insert_diagnostic_run(conn, org_id, tmpdir)
        delta_ids = compute_and_store_diagnostic_delta(conn, org_id, run_id)
    assert delta_ids == []


def test_compute_delta_two_runs(org_db):
    conn, org_id = org_db
    with tempfile.TemporaryDirectory() as before_dir, \
         tempfile.TemporaryDirectory() as after_dir:

        before_metrics = {m: 5.0 for m in TRACKED_METRICS}
        after_metrics = {m: 6.0 for m in TRACKED_METRICS}
        json.dump(before_metrics, open(os.path.join(before_dir, "computed_metrics.json"), "w"))
        json.dump(after_metrics, open(os.path.join(after_dir, "computed_metrics.json"), "w"))

        run_before = _insert_diagnostic_run(conn, org_id, before_dir)
        run_after = _insert_diagnostic_run(conn, org_id, after_dir)

        delta_ids = compute_and_store_diagnostic_delta(conn, org_id, run_after)

    assert len(delta_ids) == len(TRACKED_METRICS)
    deltas = get_diagnostic_deltas(conn, org_id, run_id_after=run_after)
    assert len(deltas) == len(TRACKED_METRICS)

    fit_delta = next(d for d in deltas if d["metric_name"] == "fit_score")
    assert fit_delta["value_before"] == pytest.approx(5.0)
    assert fit_delta["value_after"] == pytest.approx(6.0)
    assert fit_delta["delta"] == pytest.approx(1.0)
    assert fit_delta["pct_change"] == pytest.approx(0.2)


def test_compute_delta_pct_change_zero_before(org_db):
    """pct_change is None when value_before == 0."""
    conn, org_id = org_db
    with tempfile.TemporaryDirectory() as before_dir, \
         tempfile.TemporaryDirectory() as after_dir:

        json.dump({"fit_score": 0.0}, open(os.path.join(before_dir, "computed_metrics.json"), "w"))
        json.dump({"fit_score": 3.0}, open(os.path.join(after_dir, "computed_metrics.json"), "w"))

        run_before = _insert_diagnostic_run(conn, org_id, before_dir)
        run_after = _insert_diagnostic_run(conn, org_id, after_dir)

        delta_ids = compute_and_store_diagnostic_delta(conn, org_id, run_after)

    assert delta_ids  # at least fit_score row
    deltas = get_diagnostic_deltas(conn, org_id, run_id_after=run_after)
    fit = next(d for d in deltas if d["metric_name"] == "fit_score")
    assert fit["pct_change"] is None


def test_compute_delta_missing_checkpoint_returns_empty(org_db):
    """If after checkpoint_path doesn't have computed_metrics.json, returns []."""
    conn, org_id = org_db
    with tempfile.TemporaryDirectory() as before_dir, \
         tempfile.TemporaryDirectory() as after_dir:

        # Only before has metrics; after does NOT
        json.dump({"fit_score": 5.0}, open(os.path.join(before_dir, "computed_metrics.json"), "w"))

        run_before = _insert_diagnostic_run(conn, org_id, before_dir)
        run_after = _insert_diagnostic_run(conn, org_id, after_dir)

        delta_ids = compute_and_store_diagnostic_delta(conn, org_id, run_after)

    assert delta_ids == []
