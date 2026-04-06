"""T1-VALIDATE: adapter get_result() rejects malformed output."""
from __future__ import annotations

import json
import sqlite3

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.errors import SableError


# ---------------------------------------------------------------------------
# CultGraderAdapter: malformed run_meta
# ---------------------------------------------------------------------------

def test_cult_grader_rejects_missing_run_id(tmp_path):
    """run_meta.json without run_id raises SableError."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    (tmp_path / "run_meta.json").write_text('{"fit_score": 7.5}')
    adapter = CultGraderAdapter()
    with pytest.raises(SableError, match="missing required 'run_id'"):
        adapter.get_result(str(tmp_path))


def test_cult_grader_rejects_non_dict_run_meta(tmp_path):
    """run_meta.json that's a list (not dict) raises SableError."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    (tmp_path / "run_meta.json").write_text('[1, 2, 3]')
    adapter = CultGraderAdapter()
    with pytest.raises(SableError, match="missing required 'run_id'"):
        adapter.get_result(str(tmp_path))


def test_cult_grader_rejects_non_dict_diagnostic(tmp_path):
    """diagnostic.json that's not a dict raises SableError."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    (tmp_path / "diagnostic.json").write_text('"just a string"')
    adapter = CultGraderAdapter()
    with pytest.raises(SableError, match="not a JSON object"):
        adapter.get_result(str(tmp_path))


def test_cult_grader_accepts_valid_output(tmp_path):
    """Valid run_meta + diagnostic passes validation."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    (tmp_path / "run_meta.json").write_text('{"run_id": "abc123", "fit_score": 8}')
    (tmp_path / "diagnostic.json").write_text('{"overall_grade": "A"}')
    adapter = CultGraderAdapter()
    result = adapter.get_result(str(tmp_path))
    assert result["run_meta"]["run_id"] == "abc123"
    assert result["diagnostic"]["overall_grade"] == "A"


def test_cult_grader_empty_checkpoint(tmp_path):
    """Empty checkpoint dir returns empty dict (no files to validate)."""
    from sable_platform.adapters.cult_grader import CultGraderAdapter

    adapter = CultGraderAdapter()
    result = adapter.get_result(str(tmp_path))
    assert result == {}


# ---------------------------------------------------------------------------
# SlopperAdvisoryAdapter: malformed artifact
# ---------------------------------------------------------------------------

def _make_conn_with_org(org_id="test_org"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        (org_id, "Test", "active"),
    )
    conn.commit()
    return conn


def test_slopper_accepts_valid_artifacts():
    """Valid artifact rows pass validation."""
    from sable_platform.adapters.slopper import SlopperAdvisoryAdapter

    conn = _make_conn_with_org()
    conn.execute(
        "INSERT INTO artifacts (org_id, artifact_type, stale) VALUES (?, ?, 0)",
        ("test_org", "twitter_strategy_brief"),
    )
    conn.commit()

    adapter = SlopperAdvisoryAdapter()
    result = adapter.get_result("test_org", conn=conn)
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0]["artifact_type"] == "twitter_strategy_brief"


def test_slopper_empty_result():
    """No artifacts returns empty list (valid)."""
    from sable_platform.adapters.slopper import SlopperAdvisoryAdapter

    conn = _make_conn_with_org()
    adapter = SlopperAdvisoryAdapter()
    result = adapter.get_result("test_org", conn=conn)
    assert result == {"artifacts": []}


# ---------------------------------------------------------------------------
# SableTrackingAdapter: malformed sync run
# ---------------------------------------------------------------------------

def test_tracking_accepts_valid_sync_run():
    """Valid sync_runs row passes validation."""
    from sable_platform.adapters.tracking_sync import SableTrackingAdapter

    conn = _make_conn_with_org()
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status) VALUES (?, 'sable_tracking', 'completed')",
        ("test_org",),
    )
    conn.commit()

    adapter = SableTrackingAdapter()
    result = adapter.get_result("test_org", conn=conn)
    assert result["org_id"] == "test_org"
    assert result["status"] == "completed"


def test_tracking_empty_result():
    """No sync runs returns empty dict (valid)."""
    from sable_platform.adapters.tracking_sync import SableTrackingAdapter

    conn = _make_conn_with_org()
    adapter = SableTrackingAdapter()
    result = adapter.get_result("test_org", conn=conn)
    assert result == {}
