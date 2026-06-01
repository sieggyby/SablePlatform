"""C3.2c — the autocm_kb_refresh SP WorkflowRunner workflow (end-to-end).

The scheduled trigger for the KB freshness sweep is an SP ``WorkflowRunner``
workflow (MEGAPLAN C3.2c: "use SP WorkflowRunner pattern"). This exercises the
builtin wrapper end-to-end: a real org + AutoCM client + a due source flow through
``WorkflowRunner.run`` and the due source is re-indexed.

Offline — FakeHttpFetcher is wired by injecting an embedding provider name of
"fake"; the workflow's extractor uses a real HTTP fetcher by default, so the due
source here is a no-fetch ``doc`` (inline ``fetch_config.text``) — exercising the
durable workflow path without any network.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.workflows import registry
from sable_platform.workflows.engine import WorkflowRunner


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


def test_autocm_kb_refresh_is_registered() -> None:
    assert "autocm_kb_refresh" in registry.list_all()


def test_workflow_refreshes_due_doc_source(wf_conn) -> None:
    conn = wf_conn
    sa = conn._conn  # the underlying SA connection (what the step operates on)
    # AutoCM client for the org
    sa.execute(
        text(
            "INSERT INTO autocm_clients (org_id, autonomy_state, enabled) "
            "VALUES ('rm_org', 'hitl', 1)"
        )
    )
    client_id = sa.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = 'rm_org'")
    ).fetchone()[0]
    # a NEVER-refreshed inline doc source (no network) → due, indexes its text
    sa.execute(
        text(
            "INSERT INTO autocm_kb_sources "
            "(client_id, source_type, refresh_cadence, authority_default, "
            " fetch_config, last_refreshed_at) "
            "VALUES (:c, 'doc', 'weekly', 0.8, :fc, NULL)"
        ),
        {
            "c": client_id,
            "fc": json.dumps({"text": "the robotmoney vault deploys treasury capital"}),
        },
    )
    sa.commit()

    runner = WorkflowRunner(registry.get("autocm_kb_refresh"))
    run_id = runner.run(
        "rm_org", {"client_id": client_id, "embedding_provider": "fake"}, conn=conn
    )
    assert run_id

    # the workflow completed and the due source was indexed
    chunk_ct = sa.execute(
        text("SELECT COUNT(*) FROM autocm_kb_chunks WHERE client_id = :c AND status = 'active'"),
        {"c": client_id},
    ).fetchone()[0]
    assert chunk_ct >= 1
    status = conn.execute(
        "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
    ).fetchone()["status"]
    assert status == "completed"


def test_workflow_no_client_fails_clean(wf_conn) -> None:
    conn = wf_conn
    runner = WorkflowRunner(registry.get("autocm_kb_refresh"))
    # org exists but no AutoCM client seeded → the step raises a clean config error
    from sable_platform.errors import SableError

    with pytest.raises(SableError):
        runner.run("rm_org", {"embedding_provider": "fake"}, conn=conn)


class _FakeCtx:
    """Minimal ctx for exercising the workflow's provider-resolution helpers."""

    def __init__(self, conn, org_id, config):
        self._conn = conn
        self.org_id = org_id
        self.config = config


def _seed_autocm_client(sa, org_id, *, kb_config=None):
    sa.execute(
        text(
            "INSERT INTO autocm_clients (org_id, autonomy_state, enabled, kb_config) "
            "VALUES (:o, 'hitl', 1, :kb)"
        ),
        {"o": org_id, "kb": json.dumps(kb_config if kb_config is not None else {})},
    )


def test_default_embedding_provider_is_not_fake(wf_conn) -> None:
    # BLOCKER fix: a cron run with NO explicit embedding_provider config must NOT
    # build the FakeEmbeddingProvider (which would write meaningless 64-dim vectors
    # and silently corrupt the D-2 cosine leg). With no per-run config and no
    # kb_config preference, the C3.2a voyage default applies.
    from sable_platform.autocm.kb.store import (
        DEFAULT_EMBEDDING_PROVIDER,
        FakeEmbeddingProvider,
        build_embedding_provider,
    )
    from sable_platform.workflows.builtins.autocm_kb_refresh import (
        _resolve_embedding_provider,
    )

    conn = wf_conn
    sa = conn._conn
    _seed_autocm_client(sa, "rm_org")  # no kb_config
    sa.commit()

    resolved = _resolve_embedding_provider(sa, _FakeCtx(sa, "rm_org", {}))
    assert resolved == DEFAULT_EMBEDDING_PROVIDER == "voyage"
    provider = build_embedding_provider(resolved)
    assert not isinstance(provider, FakeEmbeddingProvider)


def test_kb_config_provider_overrides_default(wf_conn) -> None:
    # A client whose manifest-backed kb_config names an embedding provider drives
    # the resolution (without an explicit per-run override).
    from sable_platform.workflows.builtins.autocm_kb_refresh import (
        _resolve_embedding_provider,
    )

    conn = wf_conn
    sa = conn._conn
    _seed_autocm_client(
        sa, "rm_org", kb_config={"kb": {"embedding": {"provider": "fake"}}}
    )
    sa.commit()
    assert _resolve_embedding_provider(sa, _FakeCtx(sa, "rm_org", {})) == "fake"


def test_explicit_config_wins_over_kb_config(wf_conn) -> None:
    from sable_platform.workflows.builtins.autocm_kb_refresh import (
        _resolve_embedding_provider,
    )

    conn = wf_conn
    sa = conn._conn
    _seed_autocm_client(
        sa, "rm_org", kb_config={"kb": {"embedding": {"provider": "voyage"}}}
    )
    sa.commit()
    ctx = _FakeCtx(sa, "rm_org", {"embedding_provider": "fake"})
    assert _resolve_embedding_provider(sa, ctx) == "fake"
