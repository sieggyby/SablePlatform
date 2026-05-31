"""SableRelay SQLAlchemy table models (mirrors migration 057_relay.sql).

The ``relay_*`` family is defined canonically — bare-imports style,
``server_default=func.now()``, named indexes — on the shared platform
``MetaData`` in ``sable_platform.db.schema`` (where 057 landed alongside the
SQL migration, per the SP dual-migration convention). Redefining the same
table names on that same ``MetaData`` would raise ``InvalidRequestError``, so
this module is the relay-namespaced **re-export** of the canonical objects:
the single source of truth stays in ``db/schema.py``, and relay code imports
its tables from ``sable_platform.relay.schema`` for a clean module boundary.

Every Table here is the *same object* registered on ``metadata`` — so
``relay/db.py`` query helpers and the listener address the canonical schema,
and a parity test can assert the relay names map 1:1 onto migration 057.
"""
from __future__ import annotations

from sable_platform.db.schema import (
    metadata,
    relay_chat_bindings,
    relay_chats,
    relay_clients,
    relay_member_identities,
    relay_member_preferences,
    relay_member_roles,
    relay_members,
    relay_messages,
    relay_processed_updates,
    relay_publication_jobs,
    relay_publications,
    relay_reply_notifications,
    relay_reply_opportunities,
    relay_reply_opportunity_targets,
    relay_submission_reactions,
    relay_submissions,
    relay_tweets,
)

# The canonical relay table names (mirrors 057_relay.sql CREATE TABLE order).
RELAY_TABLES = (
    relay_clients,
    relay_chats,
    relay_chat_bindings,
    relay_members,
    relay_member_identities,
    relay_member_roles,
    relay_member_preferences,
    relay_tweets,
    relay_messages,
    relay_submissions,
    relay_submission_reactions,
    relay_publication_jobs,
    relay_publications,
    relay_reply_opportunities,
    relay_reply_opportunity_targets,
    relay_reply_notifications,
    relay_processed_updates,
)

__all__ = [
    "metadata",
    "RELAY_TABLES",
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
]
