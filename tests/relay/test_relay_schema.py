"""C2.1 schema-mirror tests for sable_platform.relay.schema.

The relay module re-exports the canonical relay_* Table objects from
db/schema.py (single source of truth, shared MetaData). These tests assert the
re-export maps 1:1 onto migration 057's relay_* tables and that no relay Table
was accidentally redefined as a distinct object.
"""
from __future__ import annotations

from sable_platform.db.schema import metadata as canonical_metadata
from sable_platform.relay import schema as relay_schema

# The 17 relay_* tables created by 057_relay.sql.
EXPECTED_RELAY_TABLES = {
    "relay_clients",
    "relay_chats",
    "relay_chat_bindings",
    "relay_members",
    "relay_member_identities",
    "relay_member_roles",
    "relay_member_preferences",
    "relay_tweets",
    "relay_messages",
    "relay_submissions",
    "relay_submission_reactions",
    "relay_publication_jobs",
    "relay_publications",
    "relay_reply_opportunities",
    "relay_reply_opportunity_targets",
    "relay_reply_notifications",
    "relay_processed_updates",
}


def test_relay_schema_exports_all_057_tables() -> None:
    exported = {t.name for t in relay_schema.RELAY_TABLES}
    assert exported == EXPECTED_RELAY_TABLES


def test_relay_tables_are_the_canonical_objects() -> None:
    # Re-export, not redefine: each relay Table must be the same object that is
    # registered on the shared platform MetaData.
    for tbl in relay_schema.RELAY_TABLES:
        assert tbl is canonical_metadata.tables[tbl.name]


def test_relay_schema_shares_platform_metadata() -> None:
    assert relay_schema.metadata is canonical_metadata


def test_member_roles_table_has_named_index_and_composite_pk() -> None:
    tbl = relay_schema.relay_member_roles
    pk_cols = [c.name for c in tbl.primary_key.columns]
    assert pk_cols == ["member_id", "org_id", "role"]
    index_names = {ix.name for ix in tbl.indexes}
    assert "relay_member_roles_by_org_role" in index_names
