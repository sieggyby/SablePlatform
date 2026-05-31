"""C2.2 Telegram low-level dispatch primitive tests.

Exercises ``TelegramListener.route_*`` directly with lightweight update fakes
(the routing logic only reads update_id / message_reaction.user / message /
my_chat_member.new_chat_member.status — no live TG connection needed). Covers:

  - dedupe drops a duplicate update_id (reaction)
  - anonymous reaction (user is None) is dropped but still claimed in dedupe
    (so a redelivered anonymous reaction is not re-routed)
  - a new attributable reaction is accepted (the C2.3a quorum tally point)
  - allowed_updates opts into reactions + my_chat_member
  - supergroup migration routes to migrate_chat_binding
  - my_chat_member kicked/left routes to kick_chat_binding; other statuses don't
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import text

from sable_platform.relay.bot.telegram_app import (
    ALLOWED_UPDATES,
    TelegramListener,
)


# ---------------------------------------------------------------------------
# Update fakes (only the fields the routing primitive reads)
# ---------------------------------------------------------------------------
def _reaction_update(update_id, user_id):
    user = None if user_id is None else SimpleNamespace(id=user_id)
    return SimpleNamespace(
        update_id=update_id,
        message_reaction=SimpleNamespace(user=user),
    )


def _migration_update(update_id, old_chat_id, new_chat_id):
    return SimpleNamespace(
        update_id=update_id,
        message=SimpleNamespace(
            chat=SimpleNamespace(id=old_chat_id),
            migrate_to_chat_id=new_chat_id,
        ),
    )


def _my_chat_member_update(update_id, chat_id, status):
    return SimpleNamespace(
        update_id=update_id,
        my_chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            new_chat_member=SimpleNamespace(status=status),
        ),
    )


def _seed_org_client_binding(conn, org_id, chat_id, platform="telegram"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, :p, :c, 'operator', 'active')"
        ),
        {"o": org_id, "p": platform, "c": chat_id},
    )
    conn.commit()


def _listener(conn):
    # No bot_token / application → app is None; routing primitives don't need it.
    return TelegramListener(conn)


# ---------------------------------------------------------------------------
# allowed_updates opt-in
# ---------------------------------------------------------------------------
def test_allowed_updates_includes_reactions_and_my_chat_member() -> None:
    names = {str(u) for u in ALLOWED_UPDATES}
    assert "message_reaction" in names
    assert "my_chat_member" in names
    assert "message" in names


# ---------------------------------------------------------------------------
# Reaction routing: dedupe + anon-drop
# ---------------------------------------------------------------------------
def test_reaction_accepted_when_new_and_attributable(sa_conn) -> None:
    listener = _listener(sa_conn)
    assert listener.route_reaction(_reaction_update(10, user_id=777)) is True
    # The dedupe row was persisted.
    rows = sa_conn.exec_driver_sql(
        "SELECT platform, update_id FROM relay_processed_updates"
    ).fetchall()
    assert ("telegram", "10") in rows


def test_reaction_duplicate_update_id_dropped(sa_conn) -> None:
    listener = _listener(sa_conn)
    assert listener.route_reaction(_reaction_update(20, user_id=1)) is True
    # Same update_id redelivered → dropped.
    assert listener.route_reaction(_reaction_update(20, user_id=1)) is False


def test_anonymous_reaction_dropped_but_claimed(sa_conn) -> None:
    listener = _listener(sa_conn)
    # Anonymous reaction (user is None) is dropped...
    assert listener.route_reaction(_reaction_update(30, user_id=None)) is False
    # ...but the update_id was still claimed in dedupe (so a redelivery of the
    # same anonymous reaction won't re-enter routing).
    rows = sa_conn.exec_driver_sql(
        "SELECT update_id FROM relay_processed_updates WHERE update_id='30'"
    ).fetchall()
    assert rows == [("30",)]


def test_reaction_with_no_reaction_payload_dropped(sa_conn) -> None:
    listener = _listener(sa_conn)
    empty = SimpleNamespace(update_id=40, message_reaction=None)
    assert listener.route_reaction(empty) is False


# ---------------------------------------------------------------------------
# Supergroup migration routing (§15.3)
# ---------------------------------------------------------------------------
def test_migration_routes_to_binding_repoint(sa_conn) -> None:
    _seed_org_client_binding(sa_conn, "orgTG", "-100")
    listener = _listener(sa_conn)
    assert listener.route_migration(_migration_update(50, -100, -100999)) is True
    new = sa_conn.execute(
        text("SELECT status FROM relay_chat_bindings WHERE chat_id='-100999'")
    ).fetchone()
    assert new is not None and new[0] == "active"


def test_migration_noop_without_migrate_field(sa_conn) -> None:
    listener = _listener(sa_conn)
    no_migrate = SimpleNamespace(
        update_id=60,
        message=SimpleNamespace(chat=SimpleNamespace(id=-1), migrate_to_chat_id=None),
    )
    assert listener.route_migration(no_migrate) is False


# ---------------------------------------------------------------------------
# my_chat_member routing (§15.3 bot-kicked)
# ---------------------------------------------------------------------------
def test_my_chat_member_kicked_flips_binding(sa_conn) -> None:
    _seed_org_client_binding(sa_conn, "orgTGK", "-200")
    listener = _listener(sa_conn)
    assert listener.route_my_chat_member(_my_chat_member_update(70, -200, "kicked")) is True
    status = sa_conn.execute(
        text("SELECT status FROM relay_chat_bindings WHERE chat_id='-200'")
    ).fetchone()[0]
    assert status == "kicked"


def test_my_chat_member_left_flips_binding(sa_conn) -> None:
    _seed_org_client_binding(sa_conn, "orgTGL", "-210")
    listener = _listener(sa_conn)
    assert listener.route_my_chat_member(_my_chat_member_update(80, -210, "left")) is True


def test_my_chat_member_member_status_is_noop(sa_conn) -> None:
    _seed_org_client_binding(sa_conn, "orgTGM", "-220")
    listener = _listener(sa_conn)
    # Still a member / promoted → no binding flip.
    assert listener.route_my_chat_member(_my_chat_member_update(90, -220, "member")) is False
    status = sa_conn.execute(
        text("SELECT status FROM relay_chat_bindings WHERE chat_id='-220'")
    ).fetchone()[0]
    assert status == "active"
