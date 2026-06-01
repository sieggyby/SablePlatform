"""Shared seed helpers for the C3.10 multi-tenant / cost-isolation tests.

These build a fully-wired AutoCM client (org + relay_client + autocm_client +
persona + KB source/chunks + constants + a relay chat & inbound message) so a
single inbound message can be driven through the whole pipeline (KB retrieval →
classifier → drafter → gate → publisher). Two such clients can be seeded in the
SAME in-memory db to assert per-client isolation (no KB/persona/constants/draft
bleed) and per-client cost attribution (no cost bleed).

NO real Anthropic / network: every LLM path uses a Null or in-test FAKE provider,
and embeddings use the deterministic FakeEmbeddingProvider. The relay tables are
seeded via the public ``relay.db`` helpers so the publisher's destination
resolution works end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import text

from sable_platform.autocm.kb.constants import upsert_constants
from sable_platform.autocm.kb.store import FakeEmbeddingProvider, SQLiteKBStore
from sable_platform.relay import db as relay_db


@dataclass
class SeededClient:
    """Everything one seeded AutoCM tenant exposes to a test."""

    org_id: str
    client_id: int
    persona_id: int
    source_id: int
    chat_row_id: int
    message_row_id: int
    chunk_ids: List[int] = field(default_factory=list)
    constants: Dict[str, str] = field(default_factory=dict)


def seed_org(conn, org_id: str) -> None:
    """Insert the base org + relay_clients row (relay_chats FKs relay_clients)."""
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id}
    )
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )


def seed_persona(
    conn,
    *,
    name: str,
    calm_prompt: str,
    reactive_prompt: str,
    calibration_set: str = "{}",
    config: str = "{}",
) -> int:
    """Insert one autocm_personas row; return its id."""
    row = conn.execute(
        text(
            "INSERT INTO autocm_personas "
            "(name, description, calm_prompt, reactive_prompt, calibration_set, config) "
            "VALUES (:n, :d, :cp, :rp, :cs, :cfg) RETURNING id"
        ),
        {
            "n": name,
            "d": f"persona for {name}",
            "cp": calm_prompt,
            "rp": reactive_prompt,
            "cs": calibration_set,
            "cfg": config,
        },
    ).fetchone()
    return int(row[0])


def seed_client_row(
    conn, org_id: str, *, persona_id: int, display_name: str
) -> int:
    """Insert one autocm_clients row bound to a persona; return its id."""
    row = conn.execute(
        text(
            "INSERT INTO autocm_clients "
            "(org_id, persona_id, display_name, autonomy_state, incident_active, enabled) "
            "VALUES (:o, :p, :dn, 'hitl', 0, 1) RETURNING id"
        ),
        {"o": org_id, "p": persona_id, "dn": display_name},
    ).fetchone()
    return int(row[0])


def seed_kb_source(conn, client_id: int, *, authority: float = 0.8) -> int:
    """Insert one autocm_kb_sources row for a client; return its id."""
    row = conn.execute(
        text(
            "INSERT INTO autocm_kb_sources (client_id, source_type, authority_default) "
            "VALUES (:c, 'doc', :a) RETURNING id"
        ),
        {"c": client_id, "a": authority},
    ).fetchone()
    return int(row[0])


def seed_full_client(
    conn,
    *,
    org_id: str,
    display_name: str,
    calm_prompt: str,
    reactive_prompt: str,
    kb_bodies: List[str],
    constants: Optional[Dict[str, str]] = None,
    inbound_text: str = "how does the vault actually work?",
    chat_external_id: Optional[str] = None,
    msg_external_id: Optional[str] = None,
    authority: float = 0.8,
) -> SeededClient:
    """Seed ONE complete AutoCM tenant in the current db and return its handles.

    Wires: org + relay_client, persona, autocm_client, a KB source with the
    embedded+indexed ``kb_bodies`` chunks (FakeEmbeddingProvider), the per-client
    slot-fill ``constants``, and a relay chat + inbound message the pipeline will
    answer. Everything is client_id-scoped so two tenants in one db stay isolated.
    """
    seed_org(conn, org_id)
    persona_id = seed_persona(
        conn, name=f"{display_name}-NULO", calm_prompt=calm_prompt, reactive_prompt=reactive_prompt
    )
    client_id = seed_client_row(
        conn, org_id, persona_id=persona_id, display_name=display_name
    )
    source_id = seed_kb_source(conn, client_id, authority=authority)
    conn.commit()

    # KB chunks (embed → index via the deterministic fake embedder).
    store = SQLiteKBStore(conn, FakeEmbeddingProvider())
    chunk_ids: List[int] = []
    for body in kb_bodies:
        chunk_ids.extend(
            store.index_source(
                org_id=org_id, client_id=client_id, source_id=source_id, body=body
            )
        )

    # per-client slot-fill constants (irreducibles).
    consts = constants or {}
    if consts:
        upsert_constants(conn, client_id, consts)

    # the relay chat + inbound message the public reply will target.
    chat_external = chat_external_id or f"-100{abs(hash(org_id)) % 100000}"
    msg_external = msg_external_id or f"{org_id}-msg-1"
    chat_row_id = relay_db.upsert_chat(
        conn, org_id, chat_external, platform="telegram", title=f"{display_name} community"
    )
    msg_row_id = relay_db.persist_inbound_message(
        conn,
        org_id=org_id,
        chat_row_id=chat_row_id,
        platform="telegram",
        external_message_id=msg_external,
        external_user_id="curious_degen",
        text_body=inbound_text,
    )
    conn.commit()

    return SeededClient(
        org_id=org_id,
        client_id=client_id,
        persona_id=persona_id,
        source_id=source_id,
        chat_row_id=int(chat_row_id),
        message_row_id=int(msg_row_id),
        chunk_ids=chunk_ids,
        constants=consts,
    )


def insert_draft(
    conn,
    *,
    client_id: int,
    source_message_id: Optional[int],
    source_chat_id: Optional[int],
    draft_text: str,
    category: str = "mechanics",
    tier: int = 2,
    register: str = "calm",
    confidence: float = 0.72,
    cited: str = "[]",
    status: str = "hitl_pending",
) -> int:
    """Insert one autocm_drafts row; return its id."""
    row = conn.execute(
        text(
            "INSERT INTO autocm_drafts "
            "(client_id, source_message_id, source_chat_id, category, tier, register, "
            " draft_text, confidence, cited_chunk_ids, status) "
            "VALUES (:c, :sm, :sc, :cat, :tier, :reg, :dt, :conf, :cited, :st) "
            "RETURNING id"
        ),
        {
            "c": client_id,
            "sm": source_message_id,
            "sc": source_chat_id,
            "cat": category,
            "tier": tier,
            "reg": register,
            "dt": draft_text,
            "conf": confidence,
            "cited": cited,
            "st": status,
        },
    ).fetchone()
    return int(row[0])
