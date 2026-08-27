"""Tests for recorded-ledger spend basis labels."""
from __future__ import annotations

import json

from click.testing import CliRunner

from sable_platform.cli.main import cli
from sable_platform.db.cost import check_budget, get_daily_spend, get_weekly_spend
from sable_platform.workflows.builtins.lead_discovery import _sync_cult_grader_results
from tests.conftest import make_test_file_db


class _Ctx:
    def __init__(self, db):
        self.db = db
        self.org_id = "t"
        self.input_data = {"diagnostics_triggered": 2}


def _setup_spend_db(tmp_path, monkeypatch, *, cost=1.50):
    db_path = str(tmp_path / "sable.db")
    conn = make_test_file_db(db_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.commit()
    conn.execute(
        "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status)"
        " VALUES ('t', 'relay_socialdata.timeline', ?, 'success')",
        (cost,),
    )
    conn.commit()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "operator_test")
    return conn


def test_recorded_spend_surfaces_label_recorded_ledger_basis(tmp_path, monkeypatch):
    conn = _setup_spend_db(tmp_path, monkeypatch, cost=4.60)
    conn.close()
    runner = CliRunner()

    spend_text = runner.invoke(cli, ["inspect", "spend", "--org", "t"])
    assert spend_text.exit_code == 0, spend_text.output
    assert "recorded ledger" in spend_text.output

    spend_json = runner.invoke(cli, ["inspect", "spend", "--org", "t", "--json"])
    assert spend_json.exit_code == 0, spend_json.output
    spend_data = json.loads(spend_json.output)
    assert spend_data[0]["spend_basis"] == "recorded_ledger_cost_usd"

    dashboard_json = runner.invoke(cli, ["dashboard", "--org", "t", "--json"])
    assert dashboard_json.exit_code == 0, dashboard_json.output
    dashboard_data = json.loads(dashboard_json.output)
    assert dashboard_data[0]["budget"]["spend_basis"] == "recorded_ledger_cost_usd"

    preflight = runner.invoke(cli, ["workflow", "preflight", "--org", "t"])
    assert preflight.exit_code == 1, preflight.output
    assert "recorded ledger" in preflight.output

    conn = make_test_file_db(str(tmp_path / "sable.db"))
    result = _sync_cult_grader_results(_Ctx(conn))
    conn.close()
    assert result.output["spend_basis"] == "recorded_ledger_cost_usd"

    for fn in (get_weekly_spend, get_daily_spend, check_budget):
        assert "recorded cost_usd" in (fn.__doc__ or "")
