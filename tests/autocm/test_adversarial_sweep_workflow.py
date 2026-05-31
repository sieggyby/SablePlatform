"""C3.9 — the autocm_adversarial_sweep SP WorkflowRunner workflow (end-to-end).

The daily adversarial regression harness is an SP ``WorkflowRunner`` job. This
exercises the builtin wrapper end-to-end: a real org + AutoCM client flow through
``WorkflowRunner.run``; the battery runs against the LIVE pipeline (over the
deterministic vendored bank + C3.3 dispatch — NO real telegram / Anthropic /
network), one ``autocm_adversarial_runs`` row is written, and a blocked injection
persists an ``injection_blocked`` audit row.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.autocm.adversarial.regression import (
    ACTION_INJECTION_BLOCKED,
    STATUS_PASSED,
)
from sable_platform.db.audit import list_audit_log
from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.workflows import registry
from sable_platform.workflows.engine import WorkflowRunner


@pytest.fixture
def wf_conn():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    sa_metadata.create_all(engine)
    sa_conn = engine.connect()
    conn = CompatConnection(sa_conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("rm_org", "RM Org"))
    conn.commit()
    yield conn
    sa_conn.close()
    engine.dispose()


def _seed_client(conn) -> int:
    sa = conn._conn
    sa.execute(
        text(
            "INSERT INTO autocm_clients (org_id, autonomy_state, enabled) "
            "VALUES ('rm_org', 'hitl', 1)"
        )
    )
    cid = sa.execute(text("SELECT id FROM autocm_clients WHERE org_id = 'rm_org'")).fetchone()[0]
    sa.commit()
    return cid


def test_autocm_adversarial_sweep_is_registered() -> None:
    assert "autocm_adversarial_sweep" in registry.list_all()


def test_workflow_runs_battery_and_records_run(wf_conn) -> None:
    conn = wf_conn
    client_id = _seed_client(conn)

    runner = WorkflowRunner(registry.get("autocm_adversarial_sweep"))
    run_id = runner.run("rm_org", {"client_id": client_id}, conn=conn)
    assert run_id

    sa = conn._conn
    # exactly one adversarial run row, clean.
    row = sa.execute(
        text(
            "SELECT total_cases, passed, failed, status FROM autocm_adversarial_runs "
            "WHERE client_id = :c"
        ),
        {"c": client_id},
    ).fetchone()
    assert row is not None
    assert row._mapping["status"] == STATUS_PASSED
    assert row._mapping["failed"] == 0
    assert row._mapping["passed"] == row._mapping["total_cases"]

    # blocked injections were audited (encounter recorded though nothing published).
    inj_rows = list_audit_log(sa, org_id="rm_org", action=ACTION_INJECTION_BLOCKED, limit=500)
    assert len(inj_rows) > 0

    status = conn.execute(
        "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
    ).fetchone()["status"]
    assert status == "completed"


def test_workflow_resolves_client_by_org(wf_conn) -> None:
    conn = wf_conn
    client_id = _seed_client(conn)
    runner = WorkflowRunner(registry.get("autocm_adversarial_sweep"))
    run_id = runner.run("rm_org", {}, conn=conn)  # no explicit client_id
    assert run_id
    sa = conn._conn
    cnt = sa.execute(
        text("SELECT COUNT(*) FROM autocm_adversarial_runs WHERE client_id = :c"),
        {"c": client_id},
    ).fetchone()[0]
    assert cnt == 1


def test_workflow_no_client_fails_clean(wf_conn) -> None:
    conn = wf_conn
    from sable_platform.errors import SableError

    runner = WorkflowRunner(registry.get("autocm_adversarial_sweep"))
    with pytest.raises(SableError):
        runner.run("rm_org", {}, conn=conn)
