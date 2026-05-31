"""Workflow: autocm_kb_refresh — scheduled per-source KB freshness sweep (C3.2c).

The SP ``WorkflowRunner`` trigger for ``KB_DESIGN §5`` freshness contracts (one of
the four ``SABLE_PLATFORM_INTEGRATION §1`` durable/scheduled WorkflowRunner jobs).
Run on a schedule (cron) per client; finds every ``autocm_kb_sources`` row past
its freshness contract for the org's AutoCM client and re-extracts + re-embeds it
via the C3.2c :class:`~sable_platform.autocm.kb.refresher.KBRefresher`.

Single step (``refresh_due_sources``) so the sweep is the workflow's one durable
unit. The refresher's :data:`Clock` seam defaults to wall-clock in production; the
unit tests drive a fake clock directly against :class:`KBRefresher` (this workflow
wrapper is exercised end-to-end with a real autocm client + due source).

Config:
  * ``client_id`` (optional) — refresh a specific AutoCM client; otherwise the
    org's single AutoCM client (``autocm_clients.org_id == org_id``) is resolved.
  * ``embedding_provider`` (optional) — selects the C3.2a embedding adapter. An
    explicit per-run value wins; otherwise the client's manifest-backed
    ``kb_config`` ``kb.embedding.provider`` is consulted; otherwise the C3.2a
    ``build_embedding_provider`` default (``voyage``) applies. The literal
    ``"fake"`` test double is NEVER the production default — a cron run with no
    explicit config builds the real provider, not ``FakeEmbeddingProvider``
    (which would write meaningless 64-dim vectors and silently corrupt the D-2
    cosine retrieval leg). Tests pass ``embedding_provider="fake"`` explicitly.
"""
from __future__ import annotations

import logging

from sable_platform.autocm.kb.extractor import KBExtractor
from sable_platform.autocm.kb.refresher import KBRefresher
from sable_platform.autocm.kb.store import (
    DEFAULT_EMBEDDING_PROVIDER,
    SQLiteKBStore,
    build_embedding_provider,
)
from sable_platform.autocm.loaders import load_client_config
from sable_platform.errors import INVALID_CONFIG, SableError
from sable_platform.workflows import registry
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition

log = logging.getLogger(__name__)


def _sa_conn(ctx):
    """Return the raw SQLAlchemy Connection backing the workflow ``ctx.db``.

    Builtin workflows receive a ``CompatConnection`` (sqlite3-style shim); the
    AutoCM KB modules speak native SQLAlchemy ``text()`` over a ``Connection``.
    ``CompatConnection._conn`` is that underlying SA connection.
    """
    db = ctx.db
    return getattr(db, "_conn", db)


def _resolve_client_id(conn, ctx) -> int:
    explicit = ctx.config.get("client_id")
    if explicit is not None:
        return int(explicit)
    from sqlalchemy import text

    row = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"),
        {"o": ctx.org_id},
    ).fetchone()
    if row is None:
        raise SableError(
            INVALID_CONFIG,
            f"no AutoCM client for org '{ctx.org_id}' (seed autocm_clients first)",
        )
    return int(row[0])


def _kb_config_provider(conn, org_id) -> str | None:
    """Read ``kb.embedding.provider`` from the client's manifest-backed kb_config.

    The per-client deployment manifest's ``kb`` block is persisted as the
    ``autocm_clients.kb_config`` JSON blob (loaded via ``load_client_config``).
    Returns the configured provider name (nested ``kb.embedding.provider`` or a
    flat ``embedding_provider`` key), or ``None`` if the client carries no KB
    embedding-provider preference (so the C3.2a ``voyage`` default applies).
    """
    cfg = load_client_config(conn, org_id, with_persona=False)
    if cfg is None:
        return None
    kb = cfg.kb_config or {}
    embedding = kb.get("kb", {}).get("embedding", {}) if isinstance(kb.get("kb"), dict) else {}
    provider = embedding.get("provider") if isinstance(embedding, dict) else None
    return provider or kb.get("embedding_provider")


def _resolve_embedding_provider(conn, ctx) -> str:
    """Resolve the embedding-provider name for this refresh run.

    Precedence: an explicit per-run ``embedding_provider`` config wins; otherwise
    the client's manifest-backed ``kb_config`` ``kb.embedding.provider``; otherwise
    the C3.2a :data:`DEFAULT_EMBEDDING_PROVIDER` (``voyage``). The literal test
    double ``"fake"`` is NEVER the implicit default — a production cron run with no
    explicit config builds the real provider, not ``FakeEmbeddingProvider``.
    """
    explicit = ctx.config.get("embedding_provider")
    if explicit:
        return explicit
    return _kb_config_provider(conn, ctx.org_id) or DEFAULT_EMBEDDING_PROVIDER


def _refresh_due_sources(ctx) -> StepResult:
    conn = _sa_conn(ctx)
    client_id = _resolve_client_id(conn, ctx)
    provider_name = _resolve_embedding_provider(conn, ctx)
    embedder = build_embedding_provider(provider_name)
    store = SQLiteKBStore(conn, embedder)
    extractor = KBExtractor()
    refresher = KBRefresher(conn, store, extractor, org_id=ctx.org_id)

    due = refresher.due_sources(client_id)
    refreshed = refresher.refresh_client(client_id)
    conn.commit()
    return StepResult(
        "completed",
        {
            "client_id": client_id,
            "due_count": len(due),
            "refreshed_count": refreshed,
        },
    )


AUTOCM_KB_REFRESH = WorkflowDefinition(
    name="autocm_kb_refresh",
    version="1.0",
    steps=[
        StepDefinition(name="refresh_due_sources", fn=_refresh_due_sources, max_retries=1),
    ],
)

registry.register(AUTOCM_KB_REFRESH)
