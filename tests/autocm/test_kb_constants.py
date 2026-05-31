"""C3.2a — kb.constants tests.

Exit-criterion coverage (MEGAPLAN C3.2a): slot-fill constants bypass the LLM —
the ``autocm_kb_constants`` registry is populated and looked up via the VENDORED
``sable_pulse_core.slotfill`` engine (the core slot-fill bridge; NOT reimplemented).
The write path (upsert) + the deterministic lookup leg are both exercised.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform._vendor.sable_pulse_core import SlotFillKB
from sable_platform.autocm.kb.constants import (
    ConstantsKB,
    build_slotfill_kb,
    upsert_constant,
    upsert_constants,
)


def _seed_client(conn, org_id: str) -> int:
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, autonomy_state, enabled) "
            "VALUES (:o, 'hitl', 1)"
        ),
        {"o": org_id},
    )
    return conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]


def test_upsert_then_slotfill_lookup(sa_org) -> None:
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    upsert_constant(
        conn, client_id, "contract_address", "0xC0FFEE",
        description="RM token contract", updated_by="seed",
    )
    conn.commit()

    kb = build_slotfill_kb(conn, client_id)
    assert isinstance(kb, SlotFillKB)
    # zero-LLM literal lookup of the irreducible
    assert kb.constant("contract_address") == "0xC0FFEE"
    # the VENDORED router maps free text → the right key (not reimplemented here)
    assert kb.match_slotfill("what's the contract address") == (
        "contract_address",
        "0xC0FFEE",
    )


def test_upsert_is_idempotent_updates_value(sa_org) -> None:
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    upsert_constant(conn, client_id, "audit_url", "https://old.example/audit")
    upsert_constant(conn, client_id, "audit_url", "https://new.example/audit")
    conn.commit()

    rows = conn.execute(
        text(
            "SELECT value FROM autocm_kb_constants "
            "WHERE client_id = :c AND key = 'audit_url'"
        ),
        {"c": client_id},
    ).fetchall()
    # composite PK (client_id, key) → exactly ONE row, the updated value
    assert len(rows) == 1
    assert rows[0][0] == "https://new.example/audit"


def test_bulk_upsert_and_facade_lookup(sa_org) -> None:
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    upsert_constants(
        conn,
        client_id,
        {
            "contract_address": "0xABCDEF",
            "audit_url": "https://example/audit.pdf",
            "official_twitter": "@robotmoney",
        },
        updated_by="manifest",
    )
    conn.commit()

    facade = ConstantsKB.load(conn, client_id)
    assert facade.client_id == client_id
    assert facade.constant("audit_url") == "https://example/audit.pdf"
    # vendored slot-fill routing via the facade
    assert facade.match_slotfill("drop the ca") == ("contract_address", "0xABCDEF")
    assert facade.match_slotfill("who audited the project") == (
        "audit_url",
        "https://example/audit.pdf",
    )


def test_per_client_isolation(sa_org) -> None:
    conn, org_id = sa_org
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES ('org2', 'Org Two')")
    )
    client_a = _seed_client(conn, org_id)
    client_b = _seed_client(conn, "org2")
    upsert_constant(conn, client_a, "contract_address", "0xAAA")
    upsert_constant(conn, client_b, "contract_address", "0xBBB")
    conn.commit()

    kb_a = build_slotfill_kb(conn, client_a)
    kb_b = build_slotfill_kb(conn, client_b)
    assert kb_a.constant("contract_address") == "0xAAA"
    assert kb_b.constant("contract_address") == "0xBBB"


def test_glossary_leg_optional_passthrough(sa_org) -> None:
    conn, org_id = sa_org
    client_id = _seed_client(conn, org_id)
    upsert_constant(conn, client_id, "contract_address", "0xC0FFEE")
    conn.commit()
    # C3.1 accepts an optional glossary so the bridge is complete now; C3.2c
    # populates it from definitional chunks. Passthrough is delegated to the
    # vendored engine.
    facade = ConstantsKB.load(
        conn, client_id, glossary={"erc-4626": "a tokenized vault standard"}
    )
    assert facade.match_glossary("what is erc-4626") == (
        "erc-4626",
        "a tokenized vault standard",
    )
