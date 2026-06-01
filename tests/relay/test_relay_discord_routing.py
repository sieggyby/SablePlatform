"""C2.2 Discord low-level dispatch primitive tests.

Exercises ``DiscordListener`` routing directly (no live gateway needed):

  - the 3s-defer pre-step dedupes the interaction and signals should_defer
  - a duplicate interaction id → should_defer False (silent drop)
  - a Discord reaction event (v1.5, no deadline) is deduped on event_id
  - the client is constructed with AllowedMentions.none() as the WIRE default
  - note_send_failure flips the binding only at/over the configured threshold
  - the listener does NOT request the privileged message_content intent
"""
from __future__ import annotations

import discord
from sqlalchemy import text

from sable_platform.relay.bot.discord_app import (
    DiscordListener,
    build_intents,
)


def _seed_org_client_binding(conn, org_id, chat_id):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, 'discord', :c, 'broadcast', 'active')"
        ),
        {"o": org_id, "c": chat_id},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 3s-defer pre-step + dedupe
# ---------------------------------------------------------------------------
def test_new_interaction_signals_defer(sa_conn) -> None:
    listener = DiscordListener(sa_conn)
    res = listener.route_interaction("1234567890123456789")
    assert res.should_defer is True
    rows = sa_conn.exec_driver_sql(
        "SELECT platform, update_id FROM relay_processed_updates"
    ).fetchall()
    assert ("discord", "1234567890123456789") in rows


def test_duplicate_interaction_is_silent(sa_conn) -> None:
    listener = DiscordListener(sa_conn)
    assert listener.route_interaction("999").should_defer is True
    # Redelivered interaction → drop silently (no second defer).
    assert listener.route_interaction("999").should_defer is False


def test_reaction_event_deduped(sa_conn) -> None:
    listener = DiscordListener(sa_conn)
    assert listener.route_reaction_event("evt-1") is True
    assert listener.route_reaction_event("evt-1") is False


# ---------------------------------------------------------------------------
# AllowedMentions.none() wire default + intents
# ---------------------------------------------------------------------------
def test_client_default_allowed_mentions_is_none(sa_conn) -> None:
    listener = DiscordListener(sa_conn)
    am = listener.client.allowed_mentions
    assert am is not None
    assert am.everyone is False
    assert am.users is False
    assert am.roles is False


def test_intents_exclude_privileged_message_content() -> None:
    intents = build_intents()
    assert intents.message_content is False  # privileged — not requested in v1
    assert intents.guilds is True
    assert intents.guild_reactions is True


# ---------------------------------------------------------------------------
# Discord 403/404 binding-flip via note_send_failure (§15.3)
# ---------------------------------------------------------------------------
def test_note_send_failure_below_threshold_no_flip(sa_conn) -> None:
    _seed_org_client_binding(sa_conn, "orgDR", "chan-a")
    listener = DiscordListener(sa_conn)
    assert listener.note_send_failure("orgDR", "chan-a", 3) is False
    status = sa_conn.execute(
        text("SELECT status FROM relay_chat_bindings WHERE chat_id='chan-a'")
    ).fetchone()[0]
    assert status == "active"


def test_note_send_failure_at_threshold_flips(sa_conn) -> None:
    _seed_org_client_binding(sa_conn, "orgDR2", "chan-b")
    listener = DiscordListener(sa_conn)
    assert listener.note_send_failure("orgDR2", "chan-b", 5) is True
    status = sa_conn.execute(
        text("SELECT status FROM relay_chat_bindings WHERE chat_id='chan-b'")
    ).fetchone()[0]
    assert status == "kicked"
