"""C3.7 — the autocm_weekly_digest SP WorkflowRunner workflow (end-to-end).

The scheduled weekly Monday digest is an SP ``WorkflowRunner`` workflow. This
exercises the builtin wrapper end-to-end: a real org + AutoCM client + a seeded
week (drafts/reviews + relay_messages + a time-saved baseline) flows through
``WorkflowRunner.run``; the digest generates, the FAKE delivery seam (installed via
the delivery-factory hook) receives it, and the routing decision is asserted. The
digest week is pinned via the config ``week_start`` (the FAKE CLOCK) so the run is
deterministic — NO real telegram / network.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.workflows import registry
from sable_platform.workflows.builtins import autocm_weekly_digest as wf
from sable_platform.workflows.engine import WorkflowRunner


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeDelivery:
    def __init__(self):
        self.operator = []
        self.founder = []

    def to_operator(self, org_id, body):
        self.operator.append(body)
        return "op-1"

    def to_founder(self, org_id, body):
        self.founder.append(body)
        return "founder-1"


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
    conn.execute("INSERT INTO relay_clients (org_id, enabled) VALUES (?, ?)", ("rm_org", 1))
    conn.commit()
    yield conn
    sa_conn.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _reset_factory():
    """Ensure the global delivery factory is restored after each test."""
    yield
    wf.reset_delivery_factory()


def _seed_week(sa, org_id, *, engagement_start):
    sa.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RobotMoney', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    client_id = int(sa.execute(text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}).fetchone()[0])
    sa.execute(
        text(
            "INSERT INTO autocm_time_saved_baseline "
            "(client_id, minutes_per_auto, minutes_per_hitl, engagement_start_at) "
            "VALUES (:c, 2.0, 5.0, :es)"
        ),
        {"c": client_id, "es": _iso(engagement_start)},
    )
    ca = _iso(datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc))
    for _ in range(3):
        sa.execute(
            text("INSERT INTO autocm_drafts (client_id, category, status, created_at) VALUES (:c, 'mechanics', 'auto_sent', :ca)"),
            {"c": client_id, "ca": ca},
        )
    # a relay_chat + a couple corpus messages.
    sa.execute(
        text("INSERT INTO relay_chats (org_id, platform, chat_id, title) VALUES (:o, 'telegram', '-1', 'main')"),
        {"o": org_id},
    )
    chat = int(sa.execute(text("SELECT id FROM relay_chats WHERE chat_id = '-1'")).fetchone()[0])
    sa.execute(text("INSERT INTO relay_members (display_name) VALUES ('rohan')"))
    mid = int(sa.execute(text("SELECT id FROM relay_members ORDER BY id DESC LIMIT 1")).fetchone()[0])
    for i in range(2):
        sa.execute(
            text(
                "INSERT INTO relay_messages (org_id, chat_id, member_id, platform, external_message_id, text, received_at) "
                "VALUES (:o, :chat, :mid, 'telegram', :emid, 'how does it work?', :ra)"
            ),
            {"o": org_id, "chat": chat, "mid": mid, "emid": f"e{i}", "ra": _iso(datetime(2026, 5, 19, 9, i, tzinfo=timezone.utc))},
        )
    sa.commit()
    return client_id


def test_autocm_weekly_digest_is_registered():
    assert "autocm_weekly_digest" in registry.list_all()


def test_workflow_generates_and_delivers_operator_preview(wf_conn):
    conn = wf_conn
    sa = conn._conn
    # engagement start 2 weeks before the digest week → deployment-week 3 (< 5) → preview.
    _seed_week(sa, "rm_org", engagement_start=datetime(2026, 5, 4, tzinfo=timezone.utc))

    fake = FakeDelivery()
    wf.set_delivery_factory(lambda org_id: fake)

    runner = WorkflowRunner(registry.get("autocm_weekly_digest"))
    run_id = runner.run("rm_org", {"week_start": "2026-05-18"}, conn=conn)
    assert run_id

    status = conn.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()["status"]
    assert status == "completed"
    # preview-first: operator received, founder did NOT.
    assert len(fake.operator) == 1
    assert len(fake.founder) == 0
    # the time-saved headline (3 auto * 2.0 = 6 minutes) is in the delivered body.
    assert "6 minutes" in fake.operator[0]


def test_workflow_delivers_to_founder_from_week_5(wf_conn):
    conn = wf_conn
    sa = conn._conn
    # engagement start 4 weeks before the digest week → deployment-week 5 → founder.
    _seed_week(sa, "rm_org", engagement_start=datetime(2026, 4, 20, tzinfo=timezone.utc))

    fake = FakeDelivery()
    wf.set_delivery_factory(lambda org_id: fake)

    runner = WorkflowRunner(registry.get("autocm_weekly_digest"))
    run_id = runner.run("rm_org", {"week_start": "2026-05-18"}, conn=conn)
    assert run_id
    assert len(fake.founder) == 1
    assert len(fake.operator) == 1  # copy to operator too


def test_workflow_resolves_client_by_org(wf_conn):
    conn = wf_conn
    sa = conn._conn
    _seed_week(sa, "rm_org", engagement_start=datetime(2026, 5, 4, tzinfo=timezone.utc))
    fake = FakeDelivery()
    wf.set_delivery_factory(lambda org_id: fake)

    runner = WorkflowRunner(registry.get("autocm_weekly_digest"))
    # no explicit client_id → resolved from org.
    run_id = runner.run("rm_org", {"week_start": "2026-05-18"}, conn=conn)
    assert run_id
    assert len(fake.operator) == 1


def test_workflow_no_client_fails_clean(wf_conn):
    from sable_platform.errors import SableError

    runner = WorkflowRunner(registry.get("autocm_weekly_digest"))
    with pytest.raises(SableError):
        runner.run("rm_org", {"week_start": "2026-05-18"}, conn=wf_conn)


def test_default_delivery_factory_is_null(wf_conn):
    """With the default factory (NullDigestDelivery) the workflow still completes."""
    conn = wf_conn
    sa = conn._conn
    _seed_week(sa, "rm_org", engagement_start=datetime(2026, 5, 4, tzinfo=timezone.utc))
    runner = WorkflowRunner(registry.get("autocm_weekly_digest"))
    run_id = runner.run("rm_org", {"week_start": "2026-05-18"}, conn=conn)
    status = conn.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()["status"]
    assert status == "completed"
