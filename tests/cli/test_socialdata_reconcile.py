"""Tests for relay socialdata-reconcile."""
from __future__ import annotations

from click.testing import CliRunner

from sable_platform.cli.main import cli
from tests.conftest import make_test_file_db


def test_socialdata_reconcile_derives_ledger_from_filtered_cost_events(tmp_path, monkeypatch):
    db_path = str(tmp_path / "sable.db")
    conn = make_test_file_db(db_path)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('t', 'T', 'active')")
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('other', 'Other', 'active')")
    rows = [
        ("t", "relay_socialdata.timeline", 0.004, 20, 0.0002, "socialdata_meter_basis=item; changeover_at=2026-08-26T00:00:00Z; items=20", "2026-08-26 12:00:00"),
        ("t", "relay_socialdata.hydrate", 0.0002, 1, 0.0002, "socialdata_meter_basis=item; changeover_at=2026-08-26T00:00:00Z; items=1", "2026-08-26 13:00:00"),
        ("t", "relay_socialdata.timeline", 0.002, None, None, None, "2026-08-26 14:00:00"),
        ("t", "relay_socialdata.replies", 0.006, 30, 0.0002, "changeover_at=2026-08-26T00:00:00Z; items=30", "2026-08-26 15:00:00"),
        ("other", "relay_socialdata.timeline", 0.5, 2500, 0.0002, "socialdata_meter_basis=item; changeover_at=2026-08-26T00:00:00Z; items=2500", "2026-08-26 12:00:00"),
        ("t", "relay_socialdata.timeline", 0.5, 2500, 0.0002, "socialdata_meter_basis=item; changeover_at=2026-08-26T00:00:00Z; items=2500", "2026-08-25 23:59:59"),
        ("t", "llm_call", 9.99, 49950, 0.0002, "socialdata_meter_basis=item; changeover_at=2026-08-26T00:00:00Z; items=49950", "2026-08-26 12:00:00"),
    ]
    for row in rows:
        conn.execute(
            "INSERT INTO cost_events (org_id, call_type, cost_usd, credits,"
            " credit_rate_usd, note, call_status, created_at) VALUES (?, ?, ?,"
            " ?, ?, ?, 'success', ?)",
            row,
        )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "operator_test")

    result = CliRunner().invoke(
        cli,
        [
            "relay",
            "socialdata-reconcile",
            "--org",
            "t",
            "--since",
            "2026-08-26 00:00:00",
            "--until",
            "2026-08-27 00:00:00",
            "--balance-before-usd",
            "10.0000",
            "--balance-after-usd",
            "9.9900",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ledger_usd=0.0042" in result.output
    assert "balance_drawdown_usd=0.0100" in result.output
    assert "delta_usd=+0.0058" in result.output
    assert "basis=item-ledger" in result.output
