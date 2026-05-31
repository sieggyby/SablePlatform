"""C2.1 query-helper tests for sable_platform.relay.db.

Exercises the helpers against a temp sable.db (the conftest ``sa_conn``
fixture = in-memory SQLite engine with the full platform schema, incl. the
057 relay_* tables) and a file-backed temp db. Covers:
  - the COALESCE(relay_clients.x_handle_override, orgs.twitter_handle) resolver
  - the role-gating helper over relay_member_roles (incl. admin subsumption)
  - client lookups + member identity resolution
"""
from __future__ import annotations

from sqlalchemy import create_engine, event, text

from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.relay import db as relay_db


def _seed_org(conn, org_id: str, twitter_handle: str | None) -> None:
    conn.execute(
        text(
            "INSERT INTO orgs (org_id, display_name, twitter_handle) "
            "VALUES (:org_id, :name, :handle)"
        ),
        {"org_id": org_id, "name": org_id, "handle": twitter_handle},
    )


def _seed_relay_client(conn, org_id: str, *, enabled: int = 1, override: str | None = None) -> None:
    conn.execute(
        text(
            "INSERT INTO relay_clients (org_id, enabled, x_handle_override) "
            "VALUES (:org_id, :enabled, :override)"
        ),
        {"org_id": org_id, "enabled": enabled, "override": override},
    )


def _seed_member(conn, display_name: str) -> int:
    conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES (:name)"),
        {"name": display_name},
    )
    return conn.execute(
        text("SELECT id FROM relay_members WHERE display_name = :name"),
        {"name": display_name},
    ).fetchone()[0]


def _grant_role(conn, member_id: int, org_id: str, role: str) -> None:
    conn.execute(
        text(
            "INSERT INTO relay_member_roles (member_id, org_id, role) "
            "VALUES (:m, :o, :r)"
        ),
        {"m": member_id, "o": org_id, "r": role},
    )


# ------------------------------------------------------------------
# COALESCE x-handle resolver
# ------------------------------------------------------------------
def test_resolve_x_handle_falls_back_to_org_twitter_handle(sa_conn) -> None:
    _seed_org(sa_conn, "orgA", "orgA_on_x")
    _seed_relay_client(sa_conn, "orgA", override=None)
    sa_conn.commit()
    assert relay_db.resolve_x_handle(sa_conn, "orgA") == "orgA_on_x"


def test_resolve_x_handle_prefers_override(sa_conn) -> None:
    _seed_org(sa_conn, "orgB", "orgB_default")
    _seed_relay_client(sa_conn, "orgB", override="orgB_override")
    sa_conn.commit()
    assert relay_db.resolve_x_handle(sa_conn, "orgB") == "orgB_override"


def test_resolve_x_handle_none_when_neither_set(sa_conn) -> None:
    _seed_org(sa_conn, "orgC", None)
    _seed_relay_client(sa_conn, "orgC", override=None)
    sa_conn.commit()
    assert relay_db.resolve_x_handle(sa_conn, "orgC") is None


def test_resolve_x_handle_none_when_no_relay_client(sa_conn) -> None:
    _seed_org(sa_conn, "orgD", "orgD_on_x")
    sa_conn.commit()
    # No relay_clients row → the JOIN yields nothing.
    assert relay_db.resolve_x_handle(sa_conn, "orgD") is None


def test_resolve_x_handle_against_temp_file_db(tmp_path) -> None:
    """Same resolver, but against a file-backed temp sable.db (not in-memory)."""
    db_file = tmp_path / "sable.db"
    engine = create_engine(f"sqlite:///{db_file}")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    sa_metadata.create_all(engine)
    with engine.connect() as conn:
        _seed_org(conn, "fileorg", "file_handle")
        _seed_relay_client(conn, "fileorg", override="file_override")
        conn.commit()
        assert relay_db.resolve_x_handle(conn, "fileorg") == "file_override"
    engine.dispose()


# ------------------------------------------------------------------
# Role gating
# ------------------------------------------------------------------
def test_member_has_role_true_for_granted_role(sa_conn) -> None:
    _seed_org(sa_conn, "orgR", None)
    _seed_relay_client(sa_conn, "orgR")
    mid = _seed_member(sa_conn, "op1")
    _grant_role(sa_conn, mid, "orgR", "sable_operator")
    sa_conn.commit()
    assert relay_db.member_has_role(sa_conn, mid, "orgR", "sable_operator") is True
    assert relay_db.is_relay_operator(sa_conn, mid, "orgR") is True


def test_member_has_role_false_without_grant(sa_conn) -> None:
    _seed_org(sa_conn, "orgR2", None)
    _seed_relay_client(sa_conn, "orgR2")
    mid = _seed_member(sa_conn, "lurker")
    sa_conn.commit()
    assert relay_db.member_has_role(sa_conn, mid, "orgR2", "sable_operator") is False
    assert relay_db.is_relay_operator(sa_conn, mid, "orgR2") is False


def test_admin_subsumes_other_roles(sa_conn) -> None:
    _seed_org(sa_conn, "orgR3", None)
    _seed_relay_client(sa_conn, "orgR3")
    mid = _seed_member(sa_conn, "boss")
    _grant_role(sa_conn, mid, "orgR3", "admin")
    sa_conn.commit()
    # admin grant alone satisfies sable_operator and client_team checks.
    assert relay_db.member_has_role(sa_conn, mid, "orgR3", "sable_operator") is True
    assert relay_db.member_has_role(sa_conn, mid, "orgR3", "client_team") is True
    assert relay_db.is_relay_operator(sa_conn, mid, "orgR3") is True


def test_role_is_org_scoped(sa_conn) -> None:
    _seed_org(sa_conn, "orgX", None)
    _seed_org(sa_conn, "orgY", None)
    _seed_relay_client(sa_conn, "orgX")
    _seed_relay_client(sa_conn, "orgY")
    mid = _seed_member(sa_conn, "scopedop")
    _grant_role(sa_conn, mid, "orgX", "sable_operator")
    sa_conn.commit()
    assert relay_db.is_relay_operator(sa_conn, mid, "orgX") is True
    # Same member, different org → no role.
    assert relay_db.is_relay_operator(sa_conn, mid, "orgY") is False


def test_member_has_role_rejects_unknown_role(sa_conn) -> None:
    _seed_org(sa_conn, "orgZ", None)
    _seed_relay_client(sa_conn, "orgZ")
    mid = _seed_member(sa_conn, "someone")
    sa_conn.commit()
    import pytest

    with pytest.raises(ValueError):
        relay_db.member_has_role(sa_conn, mid, "orgZ", "superuser")


def test_list_member_roles_returns_all_grants(sa_conn) -> None:
    _seed_org(sa_conn, "orgM", None)
    _seed_relay_client(sa_conn, "orgM")
    mid = _seed_member(sa_conn, "multi")
    _grant_role(sa_conn, mid, "orgM", "sable_operator")
    _grant_role(sa_conn, mid, "orgM", "client_team")
    sa_conn.commit()
    roles = relay_db.list_member_roles(sa_conn, mid, "orgM")
    assert roles == ["client_team", "sable_operator"]


# ------------------------------------------------------------------
# Client lookups + identity resolution
# ------------------------------------------------------------------
def test_get_relay_client_returns_row_dict(sa_conn) -> None:
    _seed_org(sa_conn, "orgC1", "handleC1")
    _seed_relay_client(sa_conn, "orgC1", enabled=1, override="ovr")
    sa_conn.commit()
    row = relay_db.get_relay_client(sa_conn, "orgC1")
    assert row is not None
    assert row["org_id"] == "orgC1"
    assert row["enabled"] == 1
    assert row["x_handle_override"] == "ovr"
    # default columns from 057 are present
    assert row["polling_interval_seconds"] == 300


def test_get_relay_client_none_for_unknown(sa_conn) -> None:
    assert relay_db.get_relay_client(sa_conn, "nope") is None


def test_list_enabled_clients(sa_conn) -> None:
    _seed_org(sa_conn, "on1", None)
    _seed_org(sa_conn, "on2", None)
    _seed_org(sa_conn, "off1", None)
    _seed_relay_client(sa_conn, "on1", enabled=1)
    _seed_relay_client(sa_conn, "on2", enabled=1)
    _seed_relay_client(sa_conn, "off1", enabled=0)
    sa_conn.commit()
    assert relay_db.list_enabled_clients(sa_conn) == ["on1", "on2"]


def test_resolve_member_id(sa_conn) -> None:
    mid = _seed_member(sa_conn, "ident")
    sa_conn.execute(
        text(
            "INSERT INTO relay_member_identities "
            "(member_id, platform, external_user_id, handle) "
            "VALUES (:m, 'telegram', '55555', 'identguy')"
        ),
        {"m": mid},
    )
    sa_conn.commit()
    assert relay_db.resolve_member_id(sa_conn, "telegram", "55555") == mid
    assert relay_db.resolve_member_id(sa_conn, "telegram", "00000") is None
    assert relay_db.resolve_member_id(sa_conn, "discord", "55555") is None
