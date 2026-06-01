"""C2.5 CLI tests for the `sable-platform relay` command surface.

Each subcommand is invoked against a file-backed temp sable.db (resolved via
``SABLE_DB_PATH``, the same way ``main.py`` resolves the CLI target). No real
Telegram/Discord/network is touched — these commands only read/write the
``relay_*`` tables. The kill-switch (``disable`` / ``pause-org``) is asserted to
both flip ``relay_clients.enabled=0`` AND mark in-flight publication jobs
``state='dead'`` (with the §3.1 ``last_error``) in one transaction + write an
audit row.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, text

from sable_platform.cli.relay_cmds import (
    relay_bind_chat,
    relay_disable,
    relay_enable,
    relay_pending,
    relay_register_operator,
    relay_status,
)
from sable_platform.cli.main import cli
from sable_platform.db.connection import ensure_schema


@pytest.fixture
def relay_db_path(tmp_path, monkeypatch):
    """A fresh file-backed sable.db wired to the CLI via SABLE_DB_PATH."""
    db_path = str(tmp_path / "relay.db")
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=ON")
    ensure_schema(raw)
    raw.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES ('acme', 'Acme')"
    )
    raw.commit()
    raw.close()
    monkeypatch.setenv("SABLE_DB_PATH", db_path)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "tester")
    return db_path


def _engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}")


def _seed_tweet(db_path: str, x_id: str, handle: str = "alice") -> int:
    eng = _engine(db_path)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO relay_tweets (x_id, x_author_handle, text) "
                "VALUES (:x, :h, 'hi')"
            ),
            {"x": x_id, "h": handle},
        )
        return c.execute(
            text("SELECT id FROM relay_tweets WHERE x_id = :x"), {"x": x_id}
        ).fetchone()[0]


def _seed_job(db_path: str, org_id: str, tweet_id: int, state: str, chat: str = "d1") -> int:
    eng = _engine(db_path)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO relay_publication_jobs "
                "(org_id, tweet_id, destination_platform, destination_chat_id, state) "
                "VALUES (:o, :t, 'discord', :c, :s)"
            ),
            {"o": org_id, "t": tweet_id, "c": chat, "s": state},
        )
        return c.execute(
            text("SELECT id FROM relay_publication_jobs ORDER BY id DESC LIMIT 1")
        ).fetchone()[0]


def _query(db_path: str, sql: str, params: dict | None = None):
    eng = _engine(db_path)
    with eng.connect() as c:
        return c.execute(text(sql), params or {}).fetchall()


# ------------------------------------------------------------------
# enable
# ------------------------------------------------------------------
def test_enable_creates_relay_client_and_audit(relay_db_path):
    r = CliRunner().invoke(relay_enable, ["acme"])
    assert r.exit_code == 0, r.output
    assert "enabled" in r.output.lower()
    rows = _query(relay_db_path, "SELECT enabled FROM relay_clients WHERE org_id='acme'")
    assert rows[0][0] == 1
    audit = _query(
        relay_db_path,
        "SELECT action, source FROM audit_log WHERE action='relay.enable'",
    )
    assert audit and audit[0][1] == "relay"


def test_enable_unknown_org_fails(relay_db_path):
    r = CliRunner().invoke(relay_enable, ["ghost"])
    assert r.exit_code != 0
    assert "not found" in r.output


def test_enable_is_idempotent(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    r = CliRunner().invoke(relay_enable, ["acme"])
    assert r.exit_code == 0
    rows = _query(relay_db_path, "SELECT COUNT(*) FROM relay_clients WHERE org_id='acme'")
    assert rows[0][0] == 1


# ------------------------------------------------------------------
# bind-chat
# ------------------------------------------------------------------
def test_bind_chat_creates_active_binding_and_audit(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    r = CliRunner().invoke(
        relay_bind_chat,
        ["acme", "operator", "--chat-id", "-100", "--platform", "telegram"],
    )
    assert r.exit_code == 0, r.output
    rows = _query(
        relay_db_path,
        "SELECT role, status FROM relay_chat_bindings "
        "WHERE org_id='acme' AND chat_id='-100'",
    )
    assert rows[0][0] == "operator" and rows[0][1] == "active"
    audit = _query(
        relay_db_path, "SELECT 1 FROM audit_log WHERE action='relay.bind_chat'"
    )
    assert audit


def test_bind_chat_requires_relay_client(relay_db_path):
    # 'acme' org exists but is not yet a relay client (never enabled)
    r = CliRunner().invoke(
        relay_bind_chat,
        ["acme", "operator", "--chat-id", "-100"],
    )
    assert r.exit_code != 0
    assert "not a relay client" in r.output


def test_bind_chat_invalid_role_rejected(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    r = CliRunner().invoke(
        relay_bind_chat, ["acme", "bogus", "--chat-id", "-100"]
    )
    # click.Choice rejects before our code runs
    assert r.exit_code != 0


# ------------------------------------------------------------------
# register-operator
# ------------------------------------------------------------------
def test_register_operator_grants_role_and_audit(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    r = CliRunner().invoke(
        relay_register_operator,
        ["acme", "--tg-user-id", "555", "--role", "sable_operator", "--handle", "alice"],
    )
    assert r.exit_code == 0, r.output
    assert "Granted" in r.output
    rows = _query(
        relay_db_path,
        "SELECT role FROM relay_member_roles WHERE org_id='acme'",
    )
    assert rows[0][0] == "sable_operator"
    audit = _query(
        relay_db_path, "SELECT 1 FROM audit_log WHERE action='relay.register_operator'"
    )
    assert audit


def test_register_operator_idempotent(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    CliRunner().invoke(
        relay_register_operator, ["acme", "--tg-user-id", "555", "--role", "admin"]
    )
    r = CliRunner().invoke(
        relay_register_operator, ["acme", "--tg-user-id", "555", "--role", "admin"]
    )
    assert r.exit_code == 0
    assert "already holds" in r.output
    rows = _query(
        relay_db_path,
        "SELECT COUNT(*) FROM relay_member_roles WHERE org_id='acme' AND role='admin'",
    )
    assert rows[0][0] == 1


def test_register_operator_requires_relay_client(relay_db_path):
    r = CliRunner().invoke(
        relay_register_operator, ["acme", "--tg-user-id", "555"]
    )
    assert r.exit_code != 0
    assert "not a relay client" in r.output


# ------------------------------------------------------------------
# status
# ------------------------------------------------------------------
def test_status_json_and_text(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    rj = CliRunner().invoke(relay_status, ["acme", "--json"])
    assert rj.exit_code == 0
    payload = json.loads(rj.output)
    assert payload["org_id"] == "acme" and payload["enabled"] == 1
    rt = CliRunner().invoke(relay_status, ["acme"])
    assert rt.exit_code == 0
    assert "enabled" in rt.output


def test_status_unknown_org_fails(relay_db_path):
    r = CliRunner().invoke(relay_status, ["ghost"])
    assert r.exit_code != 0
    assert "not a relay client" in r.output


# ------------------------------------------------------------------
# pending
# ------------------------------------------------------------------
def test_pending_empty(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    r = CliRunner().invoke(relay_pending, ["acme"])
    assert r.exit_code == 0
    assert "No pending submissions" in r.output


def test_pending_lists_open_submissions(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    t = _seed_tweet(relay_db_path, "px1", "bob")
    eng = _engine(relay_db_path)
    with eng.begin() as c:
        c.execute(text("INSERT INTO relay_members (display_name) VALUES ('op')"))
        mid = c.execute(text("SELECT id FROM relay_members LIMIT 1")).fetchone()[0]
        c.execute(
            text(
                "INSERT INTO relay_submissions "
                "(org_id, tweet_id, submitter_id, source_chat_id, source_message_id, "
                " source_role, status, expires_at) "
                "VALUES ('acme', :t, :m, 'c', 'm', 'operator', 'pending', "
                " '2099-01-01T00:00:00Z')"
            ),
            {"t": t, "m": mid},
        )
    r = CliRunner().invoke(relay_pending, ["acme", "--json"])
    assert r.exit_code == 0
    rows = json.loads(r.output)
    assert len(rows) == 1
    assert rows[0]["x_id"] == "px1" and rows[0]["status"] == "pending"


# ------------------------------------------------------------------
# disable / pause-org — the kill-switch
# ------------------------------------------------------------------
def test_disable_halts_poller_and_inflight_jobs_atomically(relay_db_path):
    CliRunner().invoke(relay_enable, ["acme"])
    t = _seed_tweet(relay_db_path, "k1")
    j_pending = _seed_job(relay_db_path, "acme", t, "pending", chat="d1")
    j_retry = _seed_job(relay_db_path, "acme", t, "retry", chat="d2")
    j_claimed = _seed_job(relay_db_path, "acme", t, "claimed", chat="d3")
    j_done = _seed_job(relay_db_path, "acme", t, "done", chat="d4")

    r = CliRunner().invoke(relay_disable, ["acme"])
    assert r.exit_code == 0, r.output
    assert "3 in-flight" in r.output

    # poller gate flipped
    enabled = _query(relay_db_path, "SELECT enabled FROM relay_clients WHERE org_id='acme'")[0][0]
    assert enabled == 0

    # in-flight jobs dead with the §3.1 last_error
    for jid in (j_pending, j_retry, j_claimed):
        state, last_error = _query(
            relay_db_path,
            "SELECT state, last_error FROM relay_publication_jobs WHERE id=:i",
            {"i": jid},
        )[0]
        assert state == "dead"
        assert last_error == "org disabled by operator"
    # terminal job untouched
    assert _query(
        relay_db_path, "SELECT state FROM relay_publication_jobs WHERE id=:i", {"i": j_done}
    )[0][0] == "done"

    # audit row written
    audit = _query(
        relay_db_path,
        "SELECT detail_json, source FROM audit_log WHERE action='relay.disable'",
    )
    assert audit
    assert audit[0][1] == "relay"
    assert json.loads(audit[0][0])["jobs_killed"] == 3


def test_pause_org_is_alias_for_disable(relay_db_path):
    """`relay pause-org` is registered as an alias of the kill-switch."""
    CliRunner().invoke(relay_enable, ["acme"])
    t = _seed_tweet(relay_db_path, "k2")
    _seed_job(relay_db_path, "acme", t, "pending")
    # Invoke through the top-level cli group to exercise the registered alias name.
    r = CliRunner().invoke(cli, ["relay", "pause-org", "acme"])
    assert r.exit_code == 0, r.output
    enabled = _query(relay_db_path, "SELECT enabled FROM relay_clients WHERE org_id='acme'")[0][0]
    assert enabled == 0
    states = {
        row[0]
        for row in _query(
            relay_db_path, "SELECT state FROM relay_publication_jobs WHERE org_id='acme'"
        )
    }
    assert states == {"dead"}


def test_disable_unknown_relay_client_fails(relay_db_path):
    r = CliRunner().invoke(relay_disable, ["acme"])  # org exists but not a relay client
    assert r.exit_code != 0
    assert "not a relay client" in r.output


def test_relay_group_registered_in_main_cli():
    """The relay group is wired into the top-level cli (PLAN §5.3)."""
    assert "relay" in cli.commands
    sub = cli.commands["relay"].commands
    for name in ("bind-chat", "register-operator", "status", "pending", "enable",
                 "disable", "pause-org"):
        assert name in sub, f"missing relay subcommand: {name}"
