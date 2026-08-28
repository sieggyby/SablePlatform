"""Every timestamp column in schema.py is TEXT, and now_offset_param emits a timestamptz.

PostgreSQL has no ``text > timestamp with time zone`` operator, so each comparison RAISES.
Two sites swallow it and return [], three propagate out of a CLI command, one exits 1.
Either way the check never fires on PostgreSQL.

Evidence this is real and predates the fix: the PostgreSQL server log in GitHub Actions
run 32667204414 (and 31560456600, 2026-08-12) carries

    ERROR:  operator does not exist: text > timestamp with time zone at character 117

These tests drive the REAL functions. They fail against the unfixed code.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.db.compat import get_dialect, now_offset_param
from sable_platform.db.gc import run_gc
from sable_platform.db.workflow_store import mark_timed_out_runs
from sable_platform.workflows.alert_checks import _check_stuck_runs, _check_workflow_failures


def _seed_run(conn, run_id: str, status: str) -> None:
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status,"
        " started_at, created_at, completed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, "wf_org", "seed_wf", status,
         "2020-01-01 00:00:00", "2020-01-01 00:00:00", "2020-01-01 00:00:00"),
    )
    conn.commit()


def test_check_workflow_failures_runs_on_postgres(postgres_wf_db):
    # alert_checks.py:237 -- workflow_runs.created_at (Text) > timestamptz.
    # Swallowed by alert_evaluator._run_check, so the alert silently never fires.
    _seed_run(postgres_wf_db, "run_fail", "failed")
    _check_workflow_failures(postgres_wf_db, "wf_org")  # must not raise


def test_check_stuck_runs_runs_on_postgres(postgres_wf_db):
    # alert_checks.py:379 -- workflow_runs.started_at (Text) < timestamptz.
    # Swallowed by a local `except Exception`, so the warning silently never fires.
    # The `except Exception` at alert_checks.py:397 turns the raise into `return []`, so a
    # weak assertion here passes against BROKEN code. Assert the alert is actually created.
    _seed_run(postgres_wf_db, "run_stuck", "running")
    created = _check_stuck_runs(postgres_wf_db, "wf_org")
    assert created, "stuck-run alert never fired: the query raised and was swallowed"


def test_mark_timed_out_runs_runs_on_postgres(postgres_wf_db):
    # workflow_store.py:188 -- started_at (Text) < timestamptz. Propagates out of `workflow gc`.
    _seed_run(postgres_wf_db, "run_timeout", "running")
    assert mark_timed_out_runs(postgres_wf_db) == ["run_timeout"]


def test_run_gc_runs_on_postgres(postgres_wf_db):
    # gc.py:20 -- ONE cutoff, FOUR comparisons: workflow_runs.completed_at,
    # workflow_events.created_at, cost_events.created_at, alerts.created_at, all Text.
    # The first raises before any later delete runs, and `gc` exits 1.
    _seed_run(postgres_wf_db, "run_gc", "completed")
    counts = run_gc(postgres_wf_db, retention_days=1)
    assert counts["workflow_runs"] == 1


def _seed_into(url: str, run_id: str, status: str) -> None:
    from sqlalchemy import create_engine
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO orgs (org_id, display_name, status)"
                       " VALUES ('wf_org', 'WF', 'active')"))
        c.execute(text(
            "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status,"
            " started_at, created_at, completed_at)"
            " VALUES (:r, 'wf_org', 'seed_wf', :s,"
            " '2020-01-01 00:00:00', '2020-01-01 00:00:00', '2020-01-01 00:00:00')"),
            {"r": run_id, "s": status})
    eng.dispose()


@pytest.mark.parametrize("argv", [["dashboard"], ["workflow", "preflight"]])
def test_cli_commands_run_on_postgres(postgres_engine, postgres_db_url, monkeypatch, argv):
    # dashboard_cmds.py:60 and workflow_cmds.py:251 compare started_at (Text) to a
    # timestamptz. NEITHER has an exception handler, so the raise propagates straight out
    # of the command and the operator sees a traceback instead of a dashboard.
    # This drives the REAL commands. An earlier version rebuilt the SQL inside the test,
    # which proves nothing: it passes or fails on the test's own string, not the CLI's.
    from click.testing import CliRunner

    from sable_platform.cli.main import cli

    _seed_into(postgres_db_url, f"run_{argv[-1]}", "running")
    monkeypatch.setenv("SABLE_DATABASE_URL", postgres_db_url)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "ci")
    result = CliRunner().invoke(cli, argv)
    # Assert the check FIRES, not merely that nothing crashed. "Did not raise" is the weak
    # contract: a swallowed exception satisfies it. Both commands must SEE the seeded run.
    assert not isinstance(result.exception, Exception) or isinstance(result.exception, SystemExit), (
        f"{' '.join(argv)} raised {result.exception!r}\n{result.output}")
    assert "stuck" in result.output.lower(), (
        f"{' '.join(argv)} never saw the stuck run: the comparison did not run\n{result.output}")


def test_an_empty_timestamp_does_not_take_the_whole_query_down(postgres_wf_db):
    """A cast alone was not enough. Verified on live PostgreSQL 16: ONE row holding '' makes
    ``''::timestamptz`` raise `invalid input syntax for type timestamp with time zone`, which
    is the exact symptom the cast was added to remove. ts_column wraps the column in NULLIF."""
    _seed_run(postgres_wf_db, "run_ok", "running")
    postgres_wf_db.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("run_empty_ts", "wf_org", "other_wf", "running", ""),
    )
    postgres_wf_db.commit()
    # AUDIT CORRECTION. My first version asserted the empty row was SKIPPED. That was wrong:
    # idx_workflow_runs_active_lock still counts a 'running' row as active, so skipping it
    # blocks that workflow forever. An empty started_at on a running row is a data fault and
    # must time out, which releases the lock.
    assert sorted(mark_timed_out_runs(postgres_wf_db)) == ["run_empty_ts", "run_ok"]


def test_expires_at_comparisons_run_on_postgres(postgres_wf_db):
    """entity_tags.expires_at is Text and was compared to CURRENT_TIMESTAMP directly, in
    three places that never used now_offset_param and that my own sweep MISSED. Codex found
    them. Verified raising on live PostgreSQL 16 before the fix."""
    from sable_platform.db.journey import entity_funnel
    from sable_platform.db.watchlist import _take_snapshot

    postgres_wf_db.execute("INSERT INTO entities (entity_id, org_id, display_name)"
                           " VALUES (?, ?, ?)", ("e1", "wf_org", "E1"))
    postgres_wf_db.execute("INSERT INTO entity_tags (entity_id, tag, is_current, expires_at)"
                           " VALUES (?, ?, ?, ?)", ("e1", "watchlist", 1, "2099-01-01 00:00:00"))
    postgres_wf_db.commit()
    _take_snapshot(postgres_wf_db, "wf_org", "e1")   # watchlist.py:81
    entity_funnel(postgres_wf_db, "wf_org")          # journey.py:146 and :158


def test_postgres_sessions_are_pinned_to_utc(postgres_engine, postgres_db_url):
    """Naive TEXT timestamps are written in UTC, but PostgreSQL resolves a naive string cast
    to timestamptz using the SESSION timezone. Measured on PostgreSQL 16: the same
    '2026-08-27 12:00:00' is +00 under UTC, -07 under America/Los_Angeles and +09 under
    Asia/Tokyo, so every elapsed-time check shifts by the server's zone. CI runs Etc/UTC and
    hides this, which is why it needs its own test.

    Drive get_engine(), which is what the application uses. The conftest fixture builds its
    engine with create_engine() directly and so bypasses the pin; asserting on that fixture
    tested the wrong object and failed for the wrong reason."""
    from sqlalchemy import text

    from sable_platform.db.engine import get_engine

    engine = get_engine(postgres_db_url)
    with engine.connect() as c:
        assert c.execute(text("SHOW timezone")).scalar() == "UTC"
        got = c.execute(text("SELECT ('2026-08-27 12:00:00'::text)::timestamptz")).scalar()
        assert got.utcoffset().total_seconds() == 0


def test_an_iso_expires_at_expires_on_the_same_day(postgres_wf_db):
    """AUDIT CORRECTION. My first version used a 2020 date, which the OLD text compare also
    rejected, so it passed against broken code and proved nothing. The bug is SAME-DAY only:
    "T" (0x54) sorts above " " (0x20), so an ISO value expiring earlier today compares as
    later than a space-separated "now" and the tag stays active for the rest of the day.

        '2026-08-27T10:00:00' > '2026-08-27 23:00:00'   ->  true   as text
        '2020-01-01T10:00:00' > CAST(NOW() AS TEXT)     ->  false  <- the useless case
    """
    from datetime import datetime, timedelta, timezone

    from sable_platform.db.tags import get_active_tags

    now = datetime.now(timezone.utc)
    # The expired value must land EARLIER THE SAME UTC DAY. A flat "now - 1 hour" crosses
    # midnight between 00:00 and 00:59 UTC and the case stops being same-day, so the test
    # would fail for an hour a day for the wrong reason. Shrink the delta to fit the day.
    since_midnight = now - now.replace(hour=0, minute=0, second=0, microsecond=0)
    if since_midnight <= timedelta(seconds=1):
        pytest.skip("within 1s of UTC midnight; the same-day case cannot be constructed")
    expired_iso = (now - min(timedelta(hours=1), since_midnight - timedelta(seconds=1))
                   ).strftime("%Y-%m-%dT%H:%M:%S")
    future_iso = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    # the text compare must genuinely get this wrong, or the test is not testing the bug
    assert expired_iso > now.strftime("%Y-%m-%d %H:%M:%S"), "not the same-day case"

    postgres_wf_db.execute("INSERT INTO entities (entity_id, org_id, display_name)"
                           " VALUES (?, ?, ?)", ("e_iso", "wf_org", "E"))
    for tag, exp in (("stale", expired_iso), ("live", future_iso)):
        postgres_wf_db.execute(
            "INSERT INTO entity_tags (entity_id, tag, is_current, expires_at)"
            " VALUES (?, ?, ?, ?)", ("e_iso", tag, 1, exp))
    postgres_wf_db.commit()
    tags = {r["tag"] for r in get_active_tags(postgres_wf_db, "e_iso")}
    assert "live" in tags
    assert "stale" not in tags, "an ISO expiry outlived its own expiry time, same day"


def test_an_empty_expires_at_does_not_kill_the_tag_expiry_alert(postgres_wf_db):
    """AUDIT CORRECTION x2. My first version inserted tag='cultist' while the query filters
    'cultist_candidate', so it never touched the code path, and it asserted `is not None`,
    which a swallowed failure satisfies because `[] is not None`. Both are rubber stamps.

    days_until cast expires_at without NULLIF, so ONE row holding '' raised, the `except` at
    alert_checks.py swallowed it, and the org got NO expiry alerts at all -- including for
    the genuinely expiring tag sitting beside it."""
    from datetime import datetime, timedelta, timezone

    from sable_platform.workflows.alert_checks import _check_cultist_tag_expiring

    soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    for eid, exp in (("e_empty", ""), ("e_real", soon)):
        postgres_wf_db.execute("INSERT INTO entities (entity_id, org_id, display_name)"
                               " VALUES (?, ?, ?)", (eid, "wf_org", "E"))
        postgres_wf_db.execute(
            "INSERT INTO entity_tags (entity_id, tag, is_current, expires_at)"
            " VALUES (?, ?, ?, ?)", (eid, "cultist_candidate", 1, exp))
    postgres_wf_db.commit()
    # assert the alert FIRES for the real row, not merely that a list came back
    created = _check_cultist_tag_expiring(postgres_wf_db, "wf_org")
    assert created, "the empty row killed the whole expiry check, alert never fired"


def test_days_between_survives_an_empty_completed_at(postgres_wf_db):
    """days_between cast both columns raw, so a completed action with completed_at=''
    raised inside `actions summary`."""
    from sqlalchemy import text

    from sable_platform.db.compat import days_between, get_dialect

    expr = days_between("completed_at", "created_at", get_dialect(postgres_wf_db))
    row = postgres_wf_db.execute(
        text(f"SELECT {expr} AS d FROM (SELECT ''::text AS completed_at,"
             f" '2020-01-01 00:00:00'::text AS created_at) s")).fetchone()
    assert row["d"] is None          # NULL, not a raise
