"""CLI tests for `sable-platform allowlist …` (mig 075)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from sable_platform.cli.allowlist_cmds import allowlist
from sable_platform.db import allowlist as al
from sable_platform.db.connection import get_db


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("SABLE_DB_PATH", str(tmp_path / "sable.db"))
    monkeypatch.setenv("SABLE_OPERATOR_ID", "tester")
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    runner = CliRunner()
    return lambda *a: runner.invoke(allowlist, list(a))


def test_add_list_disable_enable_rm(run):
    assert run("add", "Op@Sable.io", "--role", "operator", "--operator-id", "op1",
               "--assigned-orgs", "tig,solstitch").exit_code == 0
    out = run("list").output
    assert "op@sable.io" in out and "operator" in out and "tig,solstitch" in out
    assert run("disable", "op@sable.io").exit_code == 0
    assert "(disabled)" in run("list").output
    assert run("enable", "op@sable.io").exit_code == 0
    assert run("rm", "op@sable.io").exit_code == 0
    conn = get_db()
    try:
        assert al.get_entry(conn, "op@sable.io") is None
    finally:
        conn.close()


def test_add_validation_error(run):
    r = run("add", "c@y.io", "--role", "client")  # client needs --org
    assert r.exit_code == 1 and "requires --org" in r.output


def test_disable_nonexistent_exits_1(run):
    assert run("disable", "ghost@y.io").exit_code == 1


def test_list_json(run):
    run("add", "a@y.io", "--role", "admin", "--operator-id", "a")
    data = json.loads(run("list", "--json").output)
    assert data[0]["email"] == "a@y.io" and data[0]["role"] == "admin"
