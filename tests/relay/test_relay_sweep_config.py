"""Migration 062 — ``/sweep-config`` bot command tests.

Mirrors the test_relay_admin.py pattern (no real Telegram; DB work against the
in-memory ``sa_conn`` schema). Covers the admin-gate, the happy path (config
written + audit row), partial updates, the unknown-client path, the no-fields /
bad-value guards, the lexicon seed, and the C2.7 registry routing (verb-scoped).
The cost cap is NOT settable here — that single-cap-source invariant is asserted.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.handlers import sweep_config
from sable_platform.relay.bot.registry import RelayHandlerRegistry


def _seed(conn, *, org_id="orgA", twitter_handle="orgA_on_x"):
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name, twitter_handle) VALUES (:o, :o, :h)"),
        {"o": org_id, "h": twitter_handle},
    )
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
# Admin gate
# ==========================================================================
def test_sweep_config_rejects_non_admin(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    res = sweep_config.set_sweep_config(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="999", admin_handle="rando",
        topic_queries=["swarm intelligence"],
    )
    assert res.code == sweep_config.SWEEP_CONFIG_NOT_AUTHORIZED
    # Nothing was written.
    assert relay_db.get_sweep_config(sa_conn, "orgA") is None
    assert _audit_count(sa_conn, "relay.sweep_config") == 0


def test_sweep_config_unknown_client(sa_conn):
    # org exists but NO relay_clients row.
    sa_conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES ('orgN', 'orgN')"))
    sa_conn.commit()
    res = sweep_config.set_sweep_config(
        sa_conn, org_id="orgN", platform="telegram",
        admin_external_user_id="1", topic_queries=["x"],
    )
    assert res.code == sweep_config.SWEEP_CONFIG_UNKNOWN_CLIENT


# ==========================================================================
# Happy path
# ==========================================================================
def test_sweep_config_admin_sets_fields_and_audits(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()
    res = sweep_config.set_sweep_config(
        sa_conn, org_id="orgA", platform="telegram",
        admin_external_user_id="1", admin_handle="boss",
        mention_handles=["@tig", "@tigfoundation"],
        topic_queries=["swarm intelligence", "LLMs good at math"],
        from_set=["@keone"],
        operator_handles=["@arf", "@monasex_1"],
        enabled=True,
        expiry_hours=48,
    )
    assert res.code == sweep_config.SWEEP_CONFIG_OK
    assert res.created is True
    cfg = relay_db.get_sweep_config(sa_conn, "orgA")
    assert json.loads(cfg["mention_handles"]) == ["@tig", "@tigfoundation"]
    assert json.loads(cfg["topic_queries"]) == ["swarm intelligence", "LLMs good at math"]
    assert json.loads(cfg["from_set"]) == ["@keone"]
    assert json.loads(cfg["operator_handles"]) == ["@arf", "@monasex_1"]
    assert cfg["enabled"] == 1
    assert cfg["expiry_hours"] == 48
    # An audit row was written in the same txn.
    assert _audit_count(sa_conn, "relay.sweep_config") == 1


def test_sweep_config_partial_update_preserves_other_fields(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()
    sweep_config.set_sweep_config(
        sa_conn, org_id="orgA", platform="telegram", admin_external_user_id="1",
        admin_handle="boss", topic_queries=["swarm"], enabled=True,
    )
    # A second call setting ONLY enabled=False leaves topic_queries intact.
    res = sweep_config.set_sweep_config(
        sa_conn, org_id="orgA", platform="telegram", admin_external_user_id="1",
        admin_handle="boss", enabled=False,
    )
    assert res.code == sweep_config.SWEEP_CONFIG_OK
    assert res.created is False
    cfg = relay_db.get_sweep_config(sa_conn, "orgA")
    assert json.loads(cfg["topic_queries"]) == ["swarm"]  # preserved
    assert cfg["enabled"] == 0


def test_sweep_config_lexicon_seeds_own_handle_on_create(sa_conn):
    """On first creation with no explicit mention_handles, the org's own resolved
    X handle is seeded as the default mention target."""
    _seed(sa_conn, twitter_handle="orgA_on_x")
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()
    res = sweep_config.set_sweep_config(
        sa_conn, org_id="orgA", platform="telegram", admin_external_user_id="1",
        admin_handle="boss", topic_queries=["swarm"],  # no mention_handles
    )
    assert res.code == sweep_config.SWEEP_CONFIG_OK
    cfg = relay_db.get_sweep_config(sa_conn, "orgA")
    assert json.loads(cfg["mention_handles"]) == ["orgA_on_x"]  # seeded


# ==========================================================================
# Guards
# ==========================================================================
def test_sweep_config_no_fields(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()
    res = sweep_config.set_sweep_config(
        sa_conn, org_id="orgA", platform="telegram", admin_external_user_id="1",
        admin_handle="boss",
    )
    assert res.code == sweep_config.SWEEP_CONFIG_NO_FIELDS


def test_sweep_config_bad_expiry(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1)
    sa_conn.commit()
    res = sweep_config.set_sweep_config(
        sa_conn, org_id="orgA", platform="telegram", admin_external_user_id="1",
        admin_handle="boss", expiry_hours=0,
    )
    assert res.code == sweep_config.SWEEP_CONFIG_BAD_VALUE
    assert relay_db.get_sweep_config(sa_conn, "orgA") is None  # nothing written


def test_sweep_config_no_cap_field():
    """The single-cap-source invariant: the cost cap is NOT a settable field of
    /sweep-config (it lives in relay_clients.config.polling)."""
    import inspect
    sig = inspect.signature(sweep_config.set_sweep_config)
    params = set(sig.parameters)
    assert not any("cap" in p or "cost" in p or "budget" in p for p in params)
    assert "daily_cost_cap_usd" not in params


# ==========================================================================
# Registry routing (C2.7 command path)
# ==========================================================================
def test_sweep_config_registry_routing_happy_path(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 42, handle="boss")
    sa_conn.commit()
    reg = RelayHandlerRegistry(sa_conn)
    sweep_config.register(reg)
    assert reg.has_command_handler is True  # the per-verb handler is registered

    routed = reg.dispatch_command(
        platform="telegram", update_id="cmd-1",
        text="/sweep-config topic_queries=swarm,algorithms enabled=on expiry_hours=24",
        org_id="orgA", chat_id="c", external_user_id="42",
    )
    assert routed is True
    cfg = relay_db.get_sweep_config(sa_conn, "orgA")
    assert cfg is not None
    assert json.loads(cfg["topic_queries"]) == ["swarm", "algorithms"]
    assert cfg["enabled"] == 1
    assert cfg["expiry_hours"] == 24


def test_sweep_config_registry_routing_non_admin_noop(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    reg = RelayHandlerRegistry(sa_conn)
    sweep_config.register(reg)
    routed = reg.dispatch_command(
        platform="telegram", update_id="cmd-2",
        text="/sweep-config topic_queries=swarm",
        org_id="orgA", chat_id="c", external_user_id="777",  # not an admin
    )
    # The command was routed (registry's job) but the gate rejected it — no config.
    assert routed is True
    assert relay_db.get_sweep_config(sa_conn, "orgA") is None
