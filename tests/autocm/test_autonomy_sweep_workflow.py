"""C3.5a — the autocm_autonomy_sweep SP WorkflowRunner workflow (end-to-end).

The scheduled trigger for DESIGN §7 auto-demotion trigger (1) (rolling-7d
clean-approval < 0.85) is an SP ``WorkflowRunner`` workflow. This exercises the
builtin wrapper end-to-end: a real org + AutoCM client + an ``auto`` category with
a failing rolling window flow through ``WorkflowRunner.run`` and the category is
demoted back to HITL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.workflows import registry
from sable_platform.workflows.engine import WorkflowRunner


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def wf_conn():
    """In-memory CompatConnection with schema + an org (matches workflow tests)."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    sa_metadata.create_all(engine)
    sa_conn = engine.connect()
    conn = CompatConnection(sa_conn)
    conn.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("rm_org", "RM Org")
    )
    conn.commit()
    yield conn
    sa_conn.close()
    engine.dispose()


def test_autocm_autonomy_sweep_is_registered() -> None:
    assert "autocm_autonomy_sweep" in registry.list_all()


def _seed_auto_category_with_failing_window(sa, client_id, category):
    sa.execute(
        text(
            "INSERT INTO autocm_category_state (client_id, category, state) "
            "VALUES (:c, :cat, 'auto')"
        ),
        {"c": client_id, "cat": category},
    )
    recent = _iso(datetime.now(timezone.utc) - timedelta(days=1))
    # 10 reviews, 7 clean → 0.70 < 0.85.
    for i in range(10):
        sa.execute(
            text(
                "INSERT INTO autocm_drafts (client_id, category, status) "
                "VALUES (:c, :cat, 'approved')"
            ),
            {"c": client_id, "cat": category},
        )
        did = sa.execute(text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")).fetchone()[0]
        clean = 1 if i < 7 else 0
        decision = "approve" if clean else "reject"
        sa.execute(
            text(
                "INSERT INTO autocm_reviews "
                "(draft_id, client_id, decision, is_clean_approval, reviewed_at) "
                "VALUES (:d, :c, :dec, :cl, :ra)"
            ),
            {"d": did, "c": client_id, "dec": decision, "cl": clean, "ra": recent},
        )


def test_workflow_demotes_failing_auto_category(wf_conn) -> None:
    conn = wf_conn
    sa = conn._conn
    sa.execute(
        text(
            "INSERT INTO autocm_clients (org_id, autonomy_state, enabled) "
            "VALUES ('rm_org', 'auto', 1)"
        )
    )
    client_id = sa.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = 'rm_org'")
    ).fetchone()[0]
    _seed_auto_category_with_failing_window(sa, client_id, "mechanics")
    sa.commit()

    runner = WorkflowRunner(registry.get("autocm_autonomy_sweep"))
    run_id = runner.run("rm_org", {"client_id": client_id}, conn=conn)
    assert run_id

    state = sa.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'mechanics'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"
    status = conn.execute(
        "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
    ).fetchone()["status"]
    assert status == "completed"


def test_workflow_resolves_client_by_org(wf_conn) -> None:
    conn = wf_conn
    sa = conn._conn
    sa.execute(
        text(
            "INSERT INTO autocm_clients (org_id, autonomy_state, enabled) "
            "VALUES ('rm_org', 'auto', 1)"
        )
    )
    client_id = sa.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = 'rm_org'")
    ).fetchone()[0]
    _seed_auto_category_with_failing_window(sa, client_id, "status")
    sa.commit()

    runner = WorkflowRunner(registry.get("autocm_autonomy_sweep"))
    # no explicit client_id → resolved from org
    run_id = runner.run("rm_org", {}, conn=conn)
    assert run_id
    state = sa.execute(
        text("SELECT state FROM autocm_category_state WHERE client_id = :c AND category = 'status'"),
        {"c": client_id},
    ).fetchone()[0]
    assert state == "hitl"


def test_workflow_no_client_fails_clean(wf_conn) -> None:
    conn = wf_conn
    from sable_platform.errors import SableError

    runner = WorkflowRunner(registry.get("autocm_autonomy_sweep"))
    with pytest.raises(SableError):
        runner.run("rm_org", {}, conn=conn)
