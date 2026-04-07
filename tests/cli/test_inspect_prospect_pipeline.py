"""Tests for sable-platform inspect prospect_pipeline CLI command."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.connection import ensure_schema
from sable_platform.db.prospects import sync_prospect_scores


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _seed_prospects(conn):
    sync_prospect_scores(conn, [
        {"org_id": "alpha", "composite_score": 0.82, "tier": "Tier 1"},
        {"org_id": "beta", "composite_score": 0.60, "tier": "Tier 2"},
        {"org_id": "gamma", "composite_score": 0.40, "tier": "Tier 3"},
    ], "2026-04-01")


def _seed_diagnostic(conn, org_id, fit_score=75, completed_at="2026-03-25T12:00:00"):
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", (org_id, org_id))
    conn.execute(
        """INSERT INTO diagnostic_runs (org_id, run_type, status, completed_at, fit_score, recommended_action)
           VALUES (?, 'full', 'completed', ?, ?, 'pursue')""",
        (org_id, completed_at, fit_score),
    )
    conn.commit()


class TestProspectPipelineEmpty:
    def test_empty_db(self):
        conn = _make_conn()
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospect_pipeline"])
            assert result.exit_code == 0
            assert "No prospects found" in result.output


class TestProspectPipelineWithData:
    def test_table_output_with_diagnostics(self):
        conn = _make_conn()
        _seed_diagnostic(conn, "alpha", fit_score=80)
        _seed_prospects(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospect_pipeline"])
            assert result.exit_code == 0
            assert "alpha" in result.output
            assert "0.82" in result.output
            assert "80" in result.output  # fit_score

    def test_prospects_without_diagnostic_show_dash(self):
        conn = _make_conn()
        _seed_prospects(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospect_pipeline"])
            assert result.exit_code == 0
            assert "\u2014" in result.output  # em dash for missing data

    def test_json_output(self):
        conn = _make_conn()
        _seed_prospects(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospect_pipeline", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 3
            assert data[0]["org_id"] == "alpha"  # highest score first

    def test_tier_filter(self):
        conn = _make_conn()
        _seed_prospects(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospect_pipeline", "--tier", "Tier 1", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["org_id"] == "alpha"

    def test_stale_days_filter(self):
        conn = _make_conn()
        # Old diagnostic — should be stale
        _seed_diagnostic(conn, "alpha", completed_at="2025-01-01T00:00:00")
        _seed_prospects(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospect_pipeline", "--stale-days", "7", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            # alpha has stale diagnostic, beta and gamma have none (also stale)
            org_ids = {d["org_id"] for d in data}
            assert "alpha" in org_ids
            assert "beta" in org_ids  # no diagnostic = stale

    def test_stale_days_excludes_fresh(self):
        conn = _make_conn()
        # Very recent diagnostic
        _seed_diagnostic(conn, "alpha", completed_at="2099-01-01T00:00:00")
        _seed_prospects(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospect_pipeline", "--stale-days", "7", "--json"])
            data = json.loads(result.output)
            org_ids = {d["org_id"] for d in data}
            # alpha has fresh diagnostic — excluded by stale filter
            assert "alpha" not in org_ids
