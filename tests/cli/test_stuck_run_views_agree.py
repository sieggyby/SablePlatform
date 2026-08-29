"""Four call sites ask "is this workflow run stuck". They must give one answer.

``workflow_runs.started_at`` is nullable TEXT, so a crashed writer can leave a RUNNING row
with an empty or NULL start time. That row is a data fault, and the dangerous reading is the
optimistic one: ``idx_workflow_runs_active_lock`` still counts it as active, so the workflow
it belongs to cannot start again until something reports it.

Before ``compat.stuck_run_predicate`` the four sites disagreed. ``mark_timed_out_runs`` and
``_check_stuck_runs`` carried the ``NULLIF(...) IS NULL`` disjunct and called the row stuck.
``dashboard`` and ``workflow preflight`` compared only ``started_at < cutoff``; NULL compares
to NULL, so both operator views showed a clean org while the alert path was firing on it.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from sqlalchemy import text

from sable_platform.cli.main import cli
from tests.conftest import make_test_file_db

ORG = "stuckorg"
# The four states a RUNNING row's start time can be in, and the verdict each must get.
CASES = [
    ("empty",     "''",                                  True),
    ("null",      "NULL",                                True),
    ("old",       "datetime('now', '-9 hours')",         True),
    ("fresh",     "datetime('now', '-1 minutes')",       False),
]


def _seed(db_path: str, when_sql: str) -> None:
    conn = make_test_file_db(db_path)
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, 'Stuck Org', 'active')",
        (ORG,),
    )
    conn.execute(
        f"INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, started_at)"
        f" VALUES ('run-1', ?, 'daily', 'running', {when_sql})",
        (ORG,),
    )
    conn.commit()
    conn.close()


def _dashboard_says_stuck(db_path, monkeypatch) -> bool:
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    r = CliRunner().invoke(cli, ["dashboard", "--json"])
    assert r.exit_code == 0, r.output
    orgs = json.loads(r.output)
    row = next(o for o in (orgs if isinstance(orgs, list) else orgs["orgs"])
               if o["org_id"] == ORG)
    return bool(row["stuck_runs"])


def _preflight_says_stuck(db_path, monkeypatch) -> bool:
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    r = CliRunner().invoke(cli, ["workflow", "preflight", "--org", ORG])
    # A misspelled option exits 2 with a usage message and no "stuck_runs" in it, which
    # reads exactly like a clean org. Pin the two exit codes preflight is allowed to use:
    # 0 for healthy, 1 for a reported failure. Anything else is a broken probe.
    assert r.exit_code in (0, 1), f"preflight did not run: exit={r.exit_code}\n{r.output}"
    assert ORG in r.output or r.exit_code == 0, f"preflight never saw the org:\n{r.output}"
    return "stuck_runs" in r.output


def _alert_path_says_stuck(db_path) -> bool:
    from sable_platform.db.connection import get_db
    from sable_platform.workflows.alert_checks import _check_stuck_runs
    with get_db(db_path) as conn:
        rows = conn.execute(text(
            "SELECT run_id FROM workflow_runs WHERE org_id=:o AND status='running'"),
            {"o": ORG}).fetchall()
        assert rows, "seed failed"
        return bool(_check_stuck_runs(conn, ORG))


def _gc_says_stuck(db_path) -> bool:
    from sable_platform.db.connection import get_db
    from sable_platform.db.workflow_store import mark_timed_out_runs
    with get_db(db_path) as conn:
        return bool(mark_timed_out_runs(conn, hours=2))


@pytest.mark.parametrize("label,when_sql,expected", CASES,
                         ids=[c[0] for c in CASES])
def test_all_four_stuck_run_views_agree(tmp_path, monkeypatch, label, when_sql, expected):
    """One row, four readers, one verdict.

    The alert path and the GC path were already right. The two operator views were not, so
    for the ``empty`` and ``null`` cases this fails on the pre-fix tree with exactly the
    disagreement it names.
    """
    verdicts = {}
    for name, probe in (
        ("dashboard", lambda p: _dashboard_says_stuck(p, monkeypatch)),
        ("preflight", lambda p: _preflight_says_stuck(p, monkeypatch)),
        ("alert_checks", _alert_path_says_stuck),
        ("workflow_gc", _gc_says_stuck),
    ):
        # A fresh database per reader: mark_timed_out_runs WRITES, so a shared one would
        # let the GC probe decide what the later probes see.
        db_path = str(tmp_path / f"{label}_{name}.db")
        _seed(db_path, when_sql)
        verdicts[name] = probe(db_path)

    assert set(verdicts.values()) == {expected}, (
        f"started_at={label}: the four readers disagree -> {verdicts}. "
        f"Every one of them must say stuck={expected}."
    )
