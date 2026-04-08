"""Smoke tests for alerts CLI commands (list/evaluate/config — mute/unmute in test_alert_mute.py)."""
from __future__ import annotations

from click.testing import CliRunner

from sable_platform.cli.alert_cmds import alerts_list, alerts_evaluate, alerts_acknowledge, alerts_config_set, alerts_config_show
from tests.conftest import make_test_conn, make_test_file_db


def _setup_file_db(path: str, org_id: str = "o1") -> None:
    conn = make_test_file_db(path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES (?, 'Test', 'active')", (org_id,))
    conn.commit()
    conn.close()


def test_alerts_list_empty(monkeypatch):
    conn = make_test_conn()
    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(alerts_list, [])
    assert result.exit_code == 0
    assert "No alerts found" in result.output


def test_alerts_evaluate_no_orgs(monkeypatch):
    conn = make_test_conn()
    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(alerts_evaluate, [])
    assert result.exit_code == 0
    assert "No new alerts" in result.output


def test_alerts_config_show_missing(monkeypatch):
    conn = make_test_conn()
    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(alerts_config_show, ["--org", "o1"])
    assert result.exit_code == 0
    assert "No alert config" in result.output


def test_alerts_config_set(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    _setup_file_db(db_path)
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    result = CliRunner().invoke(alerts_config_set, ["--org", "o1"])
    assert result.exit_code == 0
    assert "Alert config saved" in result.output


def test_alerts_acknowledge_bad_id(monkeypatch):
    conn = make_test_conn()
    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(alerts_acknowledge, ["bad_alert_id"])
    # acknowledge_alert does an UPDATE; bad id = no-op, should not crash
    assert result.exit_code == 0


def test_alerts_config_show_cooldown_hours(monkeypatch):
    """alerts config show displays the configured cooldown_hours value."""
    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    from sable_platform.db.alerts import upsert_alert_config
    upsert_alert_config(conn, "o1", cooldown_hours=2)
    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(alerts_config_show, ["--org", "o1"])
    assert result.exit_code == 0
    assert "Cooldown hours" in result.output
    assert "2" in result.output


def test_alerts_config_show_cooldown_default_value(monkeypatch):
    """alerts config show shows '4' when cooldown_hours was not explicitly set (DB default=4)."""
    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o1', 'Test', 'active')")
    conn.commit()
    from sable_platform.db.alerts import upsert_alert_config
    upsert_alert_config(conn, "o1")  # no cooldown_hours → DB default 4
    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(alerts_config_show, ["--org", "o1"])
    assert result.exit_code == 0
    assert "Cooldown hours" in result.output
    assert "4" in result.output
