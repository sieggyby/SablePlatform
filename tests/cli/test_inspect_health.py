"""Tests for 'sable-platform inspect health' command."""
from __future__ import annotations

from click.testing import CliRunner

from sable_platform.cli.inspect_cmds import inspect_health
from sable_platform.db.discord_pulse import upsert_discord_pulse_run
from tests.conftest import make_test_conn


def _insert_org(conn, org_id="test_org"):
    conn.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
        (org_id, "Test Org"),
    )
    conn.commit()


def test_health_org_not_found(monkeypatch):
    """inspect health exits gracefully when org does not exist."""
    conn = make_test_conn()
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    runner = CliRunner()
    result = runner.invoke(inspect_health, ["no_such_org"])
    assert result.exit_code == 0
    assert "not found" in result.output.lower() or "not found" in (result.stderr or "").lower()


def test_health_no_data(monkeypatch):
    """inspect health shows 'none' labels when org has no syncs, alerts, or pulse data."""
    conn = make_test_conn()
    _insert_org(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    runner = CliRunner()
    result = runner.invoke(inspect_health, ["test_org"])
    assert result.exit_code == 0
    assert "no completed syncs" in result.output or "none" in result.output.lower()
    assert "no pulse data" in result.output


def test_health_full_output(monkeypatch):
    """inspect health shows all sections when org has data."""
    conn = make_test_conn()
    _insert_org(conn)
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) VALUES (?, 'sable_tracking', 'completed', '2026-03-24 10:00:00')",
        ("test_org",),
    )
    upsert_discord_pulse_run(
        conn, "test_org", "multisynq", "2026-03-26",
        wow_retention_rate=0.72, echo_rate=0.15,
        avg_silence_gap_hours=4.5, weekly_active_posters=120,
        retention_delta=0.03, echo_rate_delta=-0.01,
    )
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status) VALUES ('r1', 'test_org', 'alert_check', 'completed')",
    )
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    runner = CliRunner()
    result = runner.invoke(inspect_health, ["test_org"])
    assert result.exit_code == 0
    assert "sable_tracking" in result.output
    assert "0.72" in result.output
    assert "alert_check" in result.output


def test_health_json_flag(monkeypatch):
    """--json flag emits valid JSON with expected keys."""
    import json
    conn = make_test_conn()
    _insert_org(conn)
    monkeypatch.setattr("sable_platform.cli.inspect_cmds.get_db", lambda: conn)

    runner = CliRunner()
    result = runner.invoke(inspect_health, ["test_org", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "org_id" in data
    assert "syncs" in data
    assert "open_alerts" in data
    assert "discord_pulse" in data
    assert "recent_workflows" in data
