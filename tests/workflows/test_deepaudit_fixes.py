"""Tests for deep-audit (third-pass) fixes: B1, B2, B3, B4."""
from __future__ import annotations

import sqlite3
import unittest.mock

import pytest

from sable_platform.db.connection import ensure_schema


@pytest.fixture
def wf_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('wf_org', 'WF Test Org')")
    conn.commit()
    return conn


def _make_ctx(db, org_id="wf_org", run_id="run-test"):
    """Build a minimal step context object."""
    return type("Ctx", (), {
        "run_id": run_id,
        "org_id": org_id,
        "db": db,
        "input_data": {},
        "config": {},
    })()


# ---------------------------------------------------------------------------
# B1: _create_initial_sync_record raises SableError on DB failure
# ---------------------------------------------------------------------------

def test_create_initial_sync_record_raises_sable_error_on_db_failure(wf_db):
    """If the INSERT into sync_runs fails, SableError must be raised (not raw sqlite3.Error)."""
    from sable_platform.errors import SableError
    from sable_platform.workflows.builtins.onboard_client import _create_initial_sync_record

    # Wrap the real connection so we can intercept INSERT INTO sync_runs
    class FailOnSyncRunsInsert:
        def __init__(self, real_conn):
            self._conn = real_conn

        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO sync_runs" in sql:
                raise sqlite3.OperationalError("no such table: sync_runs")
            return self._conn.execute(sql, *args, **kwargs)

        def commit(self):
            return self._conn.commit()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    ctx = _make_ctx(FailOnSyncRunsInsert(wf_db))

    with pytest.raises(SableError) as exc_info:
        _create_initial_sync_record(ctx)

    assert exc_info.value.code == "INVALID_CONFIG"
    assert "sync_run insert failed" in exc_info.value.message


# ---------------------------------------------------------------------------
# B2: _send_telegram does not log the bot token on HTTP failure
# ---------------------------------------------------------------------------

def test_send_telegram_http_error_does_not_log_token(caplog):
    """HTTPError from urlopen must not cause the bot token to appear in log output."""
    import urllib.error
    from sable_platform.workflows.alert_delivery import _send_telegram

    token = "secret-bot-token-12345"

    http_error = urllib.error.HTTPError(
        url=f"https://api.telegram.org/bot{token}/sendMessage",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )

    with unittest.mock.patch("urllib.request.urlopen", side_effect=http_error):
        with caplog.at_level("WARNING"):
            result = _send_telegram(token, "123", "test")

    assert result is not None
    assert token not in caplog.text, (
        "Bot token must not appear in log output when Telegram delivery fails"
    )
    assert "HTTP 401" in result


def test_send_telegram_url_error_does_not_log_token(caplog):
    """URLError from urlopen must not cause the bot token to appear in log output."""
    import urllib.error
    from sable_platform.workflows.alert_delivery import _send_telegram

    token = "secret-bot-token-99999"

    url_error = urllib.error.URLError(reason="Connection refused")

    with unittest.mock.patch("urllib.request.urlopen", side_effect=url_error):
        with caplog.at_level("WARNING"):
            result = _send_telegram(token, "123", "test")

    assert result is not None
    assert token not in caplog.text, (
        "Bot token must not appear in log output when Telegram delivery fails with URLError"
    )
    assert "URLError" in result


# ---------------------------------------------------------------------------
# B3: _parse_actions_from_artifact warns when artifact path is None
# ---------------------------------------------------------------------------

def test_parse_actions_warns_on_missing_artifact_path(wf_db, caplog):
    """When artifact exists but path is NULL, a warning must be logged."""
    import datetime
    from sable_platform.workflows.builtins.weekly_client_loop import _parse_actions_from_artifact

    org_id = "wf_org"
    run_id = "run-warn-test"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Create the workflow_runs row so _get_run_started_at has something to find
    wf_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) "
        "VALUES (?, ?, 'weekly_client_loop', 'running', ?)",
        (run_id, org_id, now),
    )
    # Insert artifact with NULL path
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, stale, path, created_at) "
        "VALUES (?, 'advisory', 0, NULL, ?)",
        (org_id, now),
    )
    wf_db.commit()

    ctx = _make_ctx(wf_db, org_id=org_id, run_id=run_id)

    with caplog.at_level("WARNING"):
        result = _parse_actions_from_artifact(ctx, "advisory", "slopper", "advisory")

    assert result == []
    assert "No artifact path" in caplog.text


# ---------------------------------------------------------------------------
# B4: _warn_migration_027_autofails logs when auto-failed runs exist
# ---------------------------------------------------------------------------

def test_migration_027_warns_on_autofailed_runs(wf_db, caplog):
    """After migration 027, a warning is logged if any runs were auto-failed."""
    from sable_platform.db.connection import _warn_migration_027_autofails

    # Insert a run with the auto-fail error message migration 027 sets
    wf_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, error) "
        "VALUES ('dup-run-1', 'wf_org', 'test_wf', 'failed', "
        "'auto-failed by migration 027: duplicate active workflow run')"
    )
    wf_db.commit()

    with caplog.at_level("WARNING"):
        _warn_migration_027_autofails(wf_db)

    assert "Migration 027" in caplog.text
    assert "1" in caplog.text


def test_migration_027_no_warning_when_no_autofails(wf_db, caplog):
    """No warning is emitted when no runs were auto-failed by migration 027."""
    from sable_platform.db.connection import _warn_migration_027_autofails

    with caplog.at_level("WARNING"):
        _warn_migration_027_autofails(wf_db)

    assert "Migration 027" not in caplog.text
