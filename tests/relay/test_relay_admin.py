"""C2.3b tests — admin: /register-operator (3 modes) + /bind-chat.

No real Telegram/Discord; DB work runs against the in-memory ``sa_conn`` schema.
Each command returns a result object the listener uses to reply OUTSIDE the txn.

Coverage (per MEGAPLAN C2.3b exit — the 3 register-operator resolution modes
audited in isolation, role-gated, NO bare-handle resolution):
  * mode 1 (numeric tg_user_id), mode 2 (self-claim via recently-seen identity),
    mode 3 (forwarded-message from.id) each grant the role.
  * a bare @handle with NO resolution path is rejected (no getUserByUsername).
  * self-claim with zero / ambiguous recent identities reports the error.
  * authorization is role-gated: a non-admin /register-operator and /bind-chat are
    rejected; an audit row is written on success.
  * /bind-chat binds the current chat for a client (honoring the active-binding
    indexes) and rejects an unknown client / bad role.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.handlers import admin


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, '{}')"),
        {"o": org_id},
    )


def _grant_admin(conn, org_id, tg_user_id, handle="boss"):
    mid = relay_db.auto_create_member_identity(conn, "telegram", str(tg_user_id), handle=handle)
    conn.execute(
        text("INSERT INTO relay_member_roles (member_id, org_id, role) VALUES (:m, :o, 'admin')"),
        {"m": mid, "o": org_id},
    )
    return mid


def _audit_count(conn, action):
    return conn.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE action = :a AND source = 'relay'"),
        {"a": action},
    ).fetchone()[0]


# ==========================================================================
# /register-operator — the three resolution modes
# ==========================================================================
def test_register_operator_numeric_mode(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_tg_user_id="500", role="sable_operator",
    )
    assert res.code == admin.REGISTER_OK
    assert res.mode == admin.MODE_NUMERIC
    # The target now holds the operator role.
    assert relay_db.is_relay_operator(sa_conn, res.target_member_id, "orgA") is True
    # And an audit row was written (in the same txn).
    assert _audit_count(sa_conn, "relay.register_operator") == 1


def test_register_operator_self_claim_mode(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    # The target DMed /whoami earlier → a recent TG identity exists.
    target = relay_db.auto_create_member_identity(
        sa_conn, "telegram", "600", handle="claire"
    )
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_handle="@claire", role="sable_operator",
    )
    assert res.code == admin.REGISTER_OK
    assert res.mode == admin.MODE_SELF_CLAIM
    assert res.target_member_id == target
    assert relay_db.is_relay_operator(sa_conn, target, "orgA") is True


def test_register_operator_forwarded_mode(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        forwarded_from_user_id="700", target_display_handle="dave",
        role="sable_operator",
    )
    assert res.code == admin.REGISTER_OK
    assert res.mode == admin.MODE_FORWARDED
    assert relay_db.is_relay_operator(sa_conn, res.target_member_id, "orgA") is True


def test_register_operator_bare_handle_rejected(sa_conn):
    """A bare @handle with NO resolution path is rejected (no getUserByUsername)."""
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        # No tg_user_id / forwarded / recently-seen handle.
        role="sable_operator",
    )
    assert res.code == admin.REGISTER_BARE_HANDLE
    # No grant, no audit.
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_member_roles WHERE role='sable_operator'")
    ).fetchone()[0] == 0
    assert _audit_count(sa_conn, "relay.register_operator") == 0


def test_register_operator_self_claim_no_recent_identity(sa_conn):
    """A handle that never DMed the bot resolves to nothing (NOT bare-handle scan)."""
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_handle="@ghost", role="sable_operator",
    )
    assert res.code == admin.REGISTER_NO_MATCH


def test_register_operator_self_claim_stale_identity_no_match(sa_conn):
    """An identity last seen >7d ago does not resolve (recent-window guard)."""
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "600", handle="claire")
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sa_conn.execute(
        text("UPDATE relay_member_identities SET linked_at = :t WHERE member_id = :m"),
        {"t": stale, "m": mid},
    )
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_handle="@claire", role="sable_operator",
    )
    assert res.code == admin.REGISTER_NO_MATCH


def test_register_operator_self_claim_ambiguous(sa_conn):
    """Two recent identities sharing a handle → ambiguous; report candidates."""
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    relay_db.auto_create_member_identity(sa_conn, "telegram", "601", handle="dup")
    relay_db.auto_create_member_identity(sa_conn, "telegram", "602", handle="dup")
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_handle="@dup", role="sable_operator",
    )
    assert res.code == admin.REGISTER_AMBIGUOUS
    assert set(res.candidates) == {"601", "602"}


def test_register_operator_non_admin_rejected(sa_conn):
    """A non-admin caller cannot register operators (§8)."""
    _seed(sa_conn)
    # caller 9 is only a sable_operator, not an admin.
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "9", handle="op")
    sa_conn.execute(
        text("INSERT INTO relay_member_roles (member_id, org_id, role) VALUES (:m, 'orgA', 'sable_operator')"),
        {"m": mid},
    )
    sa_conn.commit()

    res = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="9", admin_handle="op",
        target_tg_user_id="500", role="sable_operator",
    )
    assert res.code == admin.REGISTER_NOT_AUTHORIZED
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_member_roles WHERE member_id != :m"),
        {"m": mid},
    ).fetchone()[0] == 0


def test_register_operator_idempotent_already(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()
    kw = dict(
        org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_tg_user_id="500", role="sable_operator",
    )
    first = admin.register_operator(sa_conn, **kw)
    second = admin.register_operator(sa_conn, **kw)
    assert first.code == admin.REGISTER_OK
    assert second.code == admin.REGISTER_ALREADY
    # one role row only.
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_member_roles WHERE member_id = :m AND role='sable_operator'"),
        {"m": first.target_member_id},
    ).fetchone()[0] == 1


def test_register_operator_admin_role_grant(sa_conn):
    """The grantable set includes 'admin' (chain-of-trust); other roles rejected."""
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    ok = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_tg_user_id="500", role="admin",
    )
    assert ok.code == admin.REGISTER_OK
    assert relay_db.member_has_role(sa_conn, ok.target_member_id, "orgA", "admin")

    bad = admin.register_operator(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        target_tg_user_id="501", role="client_team",
    )
    assert bad.code == admin.REGISTER_BAD_ARGS


# ==========================================================================
# /bind-chat
# ==========================================================================
def test_bind_chat_binds_current_chat(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    res = admin.bind_chat(
        sa_conn, org_id="orgA", platform="telegram", chat_id="-700",
        role="operator", admin_external_user_id="1", admin_handle="boss",
    )
    assert res.code == admin.BIND_OK
    assert res.binding_id is not None
    # The operator chat now resolves.
    assert relay_db.get_operator_chat(sa_conn, "orgA", "telegram") == "-700"
    assert _audit_count(sa_conn, "relay.bind_chat") == 1


def test_bind_chat_repoints_role(sa_conn):
    """Binding a new operator chat displaces the old one (active-role unique index)."""
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    admin.bind_chat(
        sa_conn, org_id="orgA", platform="telegram", chat_id="-700",
        role="operator", admin_external_user_id="1", admin_handle="boss",
    )
    admin.bind_chat(
        sa_conn, org_id="orgA", platform="telegram", chat_id="-800",
        role="operator", admin_external_user_id="1", admin_handle="boss",
    )
    # Only the new chat is the active operator binding.
    assert relay_db.get_operator_chat(sa_conn, "orgA", "telegram") == "-800"
    active = sa_conn.execute(
        text(
            "SELECT COUNT(*) FROM relay_chat_bindings "
            "WHERE org_id='orgA' AND platform='telegram' AND role='operator' AND status='active'"
        )
    ).fetchone()[0]
    assert active == 1


def test_bind_chat_non_admin_rejected(sa_conn):
    _seed(sa_conn)
    relay_db.auto_create_member_identity(sa_conn, "telegram", "9", handle="rando")
    sa_conn.commit()

    res = admin.bind_chat(
        sa_conn, org_id="orgA", platform="telegram", chat_id="-700",
        role="operator", admin_external_user_id="9", admin_handle="rando",
    )
    assert res.code == admin.BIND_NOT_AUTHORIZED
    assert relay_db.get_operator_chat(sa_conn, "orgA", "telegram") is None


def test_bind_chat_unknown_client_rejected(sa_conn):
    # orgZ has an orgs row but NO relay_clients row — relay_member_roles.org_id
    # FKs relay_clients, so an org with no client can have no admin grant either;
    # the unknown-client check fires before the (impossible) admin-gate.
    sa_conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES ('orgZ', 'Z')"))
    relay_db.auto_create_member_identity(sa_conn, "telegram", "1", handle="boss")
    sa_conn.commit()

    res = admin.bind_chat(
        sa_conn, org_id="orgZ", platform="telegram", chat_id="-700",
        role="operator", admin_external_user_id="1", admin_handle="boss",
    )
    assert res.code == admin.BIND_UNKNOWN_CLIENT


def test_bind_chat_bad_role_rejected(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()

    res = admin.bind_chat(
        sa_conn, org_id="orgA", platform="telegram", chat_id="-700",
        role="superadmin", admin_external_user_id="1", admin_handle="boss",
    )
    assert res.code == admin.BIND_BAD_ROLE
