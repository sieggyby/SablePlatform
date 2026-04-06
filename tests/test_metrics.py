"""Tests for SP-OBS Phase 3: Prometheus metrics export."""
from __future__ import annotations

import sqlite3

from sable_platform.db.connection import ensure_schema
from sable_platform.metrics import export_metrics


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def test_metrics_contains_required_metric_names():
    """export_metrics() output must contain all required metric names."""
    conn = _make_conn()
    output = export_metrics(conn)

    required = [
        "sable_active_orgs",
        "sable_workflow_runs_total",
        "sable_alerts_total",
        "sable_last_alert_eval_age_seconds",
    ]
    for name in required:
        assert name in output, f"Metric '{name}' not found in output"


def test_metrics_active_orgs_count():
    """sable_active_orgs must reflect the number of active orgs."""
    conn = _make_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'O1', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o2', 'O2', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o3', 'O3', 'inactive')")
    conn.commit()

    output = export_metrics(conn)
    assert "sable_active_orgs 2" in output


def test_metrics_prometheus_format():
    """Each data line must be metric_name or metric_name{labels} followed by a value."""
    conn = _make_conn()
    output = export_metrics(conn)
    for line in output.strip().splitlines():
        if not line or line.startswith("# "):
            continue
        # Must be "name value" or "name{...} value"
        parts = line.rsplit(" ", 1)
        assert len(parts) == 2, f"Bad Prometheus line: {line!r}"
        try:
            float(parts[1])
        except ValueError:
            assert False, f"Non-numeric value in line: {line!r}"


def test_metrics_no_eval_returns_minus_one():
    """sable_last_alert_eval_age_seconds must be -1 when evaluate_alerts has never run."""
    conn = _make_conn()
    output = export_metrics(conn)
    assert "sable_last_alert_eval_age_seconds -1" in output
