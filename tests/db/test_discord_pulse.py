"""Tests for discord_pulse_runs DB helpers."""
from sable_platform.db.discord_pulse import get_discord_pulse_runs, upsert_discord_pulse_run


def test_upsert_creates_row(org_db):
    conn, org_id = org_db
    upsert_discord_pulse_run(
        conn, org_id, "multisynq", "2026-03-26",
        wow_retention_rate=0.72, echo_rate=0.15,
        avg_silence_gap_hours=4.5, weekly_active_posters=120,
        retention_delta=0.03, echo_rate_delta=-0.01,
    )
    rows = get_discord_pulse_runs(conn, org_id, "multisynq")
    assert len(rows) == 1
    r = rows[0]
    assert r["org_id"] == org_id
    assert r["project_slug"] == "multisynq"
    assert r["run_date"] == "2026-03-26"
    assert r["wow_retention_rate"] == 0.72
    assert r["echo_rate"] == 0.15
    assert r["avg_silence_gap_hours"] == 4.5
    assert r["weekly_active_posters"] == 120
    assert r["retention_delta"] == 0.03
    assert r["echo_rate_delta"] == -0.01


def test_upsert_is_idempotent(org_db):
    conn, org_id = org_db
    upsert_discord_pulse_run(
        conn, org_id, "multisynq", "2026-03-26",
        wow_retention_rate=0.70, echo_rate=0.10,
        avg_silence_gap_hours=3.0, weekly_active_posters=100,
        retention_delta=0.01, echo_rate_delta=0.02,
    )
    upsert_discord_pulse_run(
        conn, org_id, "multisynq", "2026-03-26",
        wow_retention_rate=0.80, echo_rate=0.20,
        avg_silence_gap_hours=5.0, weekly_active_posters=200,
        retention_delta=0.05, echo_rate_delta=0.06,
    )
    rows = get_discord_pulse_runs(conn, org_id, "multisynq")
    assert len(rows) == 1
    assert rows[0]["wow_retention_rate"] == 0.80
    assert rows[0]["weekly_active_posters"] == 200


def test_get_discord_pulse_runs_returns_newest_first(org_db):
    conn, org_id = org_db
    for date in ("2026-03-25", "2026-03-26", "2026-03-24"):
        upsert_discord_pulse_run(
            conn, org_id, "multisynq", date,
            wow_retention_rate=0.5, echo_rate=0.1,
            avg_silence_gap_hours=2.0, weekly_active_posters=50,
            retention_delta=None, echo_rate_delta=None,
        )
    rows = get_discord_pulse_runs(conn, org_id, "multisynq")
    assert [r["run_date"] for r in rows] == ["2026-03-26", "2026-03-25", "2026-03-24"]


def test_get_filters_by_project_slug(org_db):
    conn, org_id = org_db
    upsert_discord_pulse_run(
        conn, org_id, "multisynq", "2026-03-26",
        wow_retention_rate=0.7, echo_rate=0.1,
        avg_silence_gap_hours=3.0, weekly_active_posters=80,
        retention_delta=None, echo_rate_delta=None,
    )
    upsert_discord_pulse_run(
        conn, org_id, "grvt", "2026-03-26",
        wow_retention_rate=0.6, echo_rate=0.2,
        avg_silence_gap_hours=4.0, weekly_active_posters=60,
        retention_delta=None, echo_rate_delta=None,
    )
    multisynq_rows = get_discord_pulse_runs(conn, org_id, "multisynq")
    grvt_rows = get_discord_pulse_runs(conn, org_id, "grvt")
    assert len(multisynq_rows) == 1
    assert multisynq_rows[0]["project_slug"] == "multisynq"
    assert len(grvt_rows) == 1
    assert grvt_rows[0]["project_slug"] == "grvt"


def test_nulls_allowed_on_delta_fields(org_db):
    conn, org_id = org_db
    upsert_discord_pulse_run(
        conn, org_id, "multisynq", "2026-03-26",
        wow_retention_rate=None, echo_rate=None,
        avg_silence_gap_hours=None, weekly_active_posters=None,
        retention_delta=None, echo_rate_delta=None,
    )
    rows = get_discord_pulse_runs(conn, org_id, "multisynq")
    assert len(rows) == 1
    assert rows[0]["wow_retention_rate"] is None
    assert rows[0]["retention_delta"] is None
