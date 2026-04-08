"""Tests for 'sable-platform alerts mute/unmute' commands."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from click.testing import CliRunner

from sable_platform.cli.alert_cmds import alerts_mute, alerts_unmute
from sable_platform.db.alerts import get_alert_config, upsert_alert_config
from sable_platform.db.connection import ensure_schema, get_db
from tests.conftest import make_test_conn, make_test_file_db


def _setup_file_db(path: str) -> None:
    """Create schema + test org in a file-based DB."""
    conn = make_test_file_db(path, with_org="test_org")
    conn.close()


def test_mute_sets_enabled_false(tmp_path, monkeypatch):
    """alerts mute sets enabled=0 on alert_configs."""
    db_path = str(tmp_path / "sable.db")
    _setup_file_db(db_path)

    # Pre-create the alert config
    conn = make_test_file_db(db_path)
    upsert_alert_config(conn, "test_org", min_severity="warning")
    conn.close()

    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: get_db(db_path))

    runner = CliRunner()
    result = runner.invoke(alerts_mute, ["test_org"])
    assert result.exit_code == 0

    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    cfg = conn2.execute("SELECT enabled FROM alert_configs WHERE org_id='test_org'").fetchone()
    conn2.close()
    assert cfg["enabled"] == 0


def test_unmute_sets_enabled_true(tmp_path, monkeypatch):
    """alerts unmute sets enabled=1 on alert_configs."""
    db_path = str(tmp_path / "sable.db")
    _setup_file_db(db_path)

    conn = make_test_file_db(db_path)
    upsert_alert_config(conn, "test_org", enabled=False)
    conn.close()

    monkeypatch.setattr("sable_platform.cli.alert_cmds.get_db", lambda: get_db(db_path))

    runner = CliRunner()
    result = runner.invoke(alerts_unmute, ["test_org"])
    assert result.exit_code == 0

    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    cfg = conn2.execute("SELECT enabled FROM alert_configs WHERE org_id='test_org'").fetchone()
    conn2.close()
    assert cfg["enabled"] == 1


def test_mute_suppresses_alert_delivery(monkeypatch):
    """evaluate_alerts fires no Telegram/Discord delivery when org is muted."""
    from sable_platform.workflows.alert_evaluator import evaluate_alerts
    from unittest.mock import patch

    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('test_org', 'Test Org')")
    # Mute the org
    upsert_alert_config(conn, "test_org", enabled=False)
    # Insert stale tracking data that would normally trigger delivery
    conn.execute(
        "INSERT INTO sync_runs (org_id, sync_type, status, completed_at) "
        "VALUES ('test_org', 'sable_tracking', 'completed', '2020-01-01 00:00:00')"
    )
    conn.commit()

    with patch("urllib.request.urlopen") as mock_urlopen:
        evaluate_alerts(conn, org_id="test_org")
        assert not mock_urlopen.called
