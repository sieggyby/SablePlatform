"""Tests for sable-platform inspect prospects CLI command."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from sable_platform.cli.main import cli
from tests.conftest import make_test_conn


def _make_conn():
    return make_test_conn()


def _seed_scores(conn):
    from sable_platform.db.prospects import sync_prospect_scores
    sync_prospect_scores(conn, [
        {
            "org_id": "zoth", "composite_score": 0.72, "tier": "Tier 1",
            "enrichment": {"sector": "DePIN", "follower_count": 12000},
        },
        {
            "org_id": "psy_protocol", "composite_score": 0.58, "tier": "Tier 2",
            "enrichment": {"sector": "AI"},
        },
    ], "2026-04-01")


class TestInspectProspects:
    def test_empty(self):
        conn = _make_conn()
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospects"])
            assert result.exit_code == 0
            assert "No prospect scores found" in result.output

    def test_table_output(self):
        conn = _make_conn()
        _seed_scores(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospects"])
            assert result.exit_code == 0
            assert "zoth" in result.output
            assert "0.72" in result.output
            assert "Tier 1" in result.output
            assert "DePIN" in result.output

    def test_json_output(self):
        conn = _make_conn()
        _seed_scores(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospects", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 2

    def test_filter_by_tier(self):
        conn = _make_conn()
        _seed_scores(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospects", "--tier", "Tier 1"])
            assert result.exit_code == 0
            assert "zoth" in result.output
            assert "psy_protocol" not in result.output

    def test_filter_by_min_score(self):
        conn = _make_conn()
        _seed_scores(conn)
        with patch("sable_platform.cli.inspect_cmds.get_db", return_value=conn):
            result = CliRunner().invoke(cli, ["inspect", "prospects", "--min-score", "0.7"])
            assert result.exit_code == 0
            assert "zoth" in result.output
            assert "psy_protocol" not in result.output
