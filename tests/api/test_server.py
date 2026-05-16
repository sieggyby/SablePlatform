"""End-to-end tests for the alert-triage HTTP MVP.

Strategy: spin up the ThreadingHTTPServer on an ephemeral port pointed at
a file-backed SQLite DB. The server's get_db() resolves to that file via
SABLE_DB_PATH. Real HTTP requests are issued with stdlib urllib.

Audit findings exercised:
  - bearer auth required (401)
  - token org-scope enforced; cross-org alert returns 404 (not 403, no leak)
  - ack/resolve idempotent (no duplicate audit rows)
  - rate limit triggers 429
  - audit_log records both operator AND token_id
  - api-serve refuses 0.0.0.0 without --public (server.py guard)
  - GET /openapi.json returns spec without auth
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sable_platform.api.openapi import openapi_document
from sable_platform.api.rate_limit import RateLimitConfig, RateLimiter
from sable_platform.api.server import ServerConfig, make_handler, serve
from sable_platform.api.tokens import issue_token
from sable_platform.db.alerts import create_alert
from sable_platform.db.connection import get_db


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def api_server(tmp_path, monkeypatch):
    db_path = tmp_path / "api_test.db"
    monkeypatch.setenv("SABLE_DB_PATH", str(db_path))
    # Clear the engine cache so SABLE_DB_PATH takes effect.
    from sable_platform.db import engine as _engine_mod
    _engine_mod._engine_cache.clear()

    # Bootstrap the DB.
    conn = get_db()
    conn.close()

    port = _free_port()
    # Very low caps so we can trigger rate limit deterministically in tests.
    rl = RateLimiter(RateLimitConfig(
        read_per_min_token=3, write_per_min_token=2, per_min_ip=1000,
    ))
    config = ServerConfig(bind_host="127.0.0.1", port=port,
                          public=False, rate_limiter=rl)
    handler = make_handler(config)
    server = ThreadingHTTPServer((config.bind_host, config.port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Briefly wait for the socket to accept.
    for _ in range(20):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    yield port, db_path
    server.shutdown()
    server.server_close()
    _engine_mod._engine_cache.clear()


def _http(method: str, port: int, path: str, *, token: str | None = None):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, (json.loads(body) if body else None), dict(e.headers or {})


def _seed_org_and_token(*, orgs, scopes, label="t1"):
    """Seed an org + a token + return (raw_token, org_id)."""
    conn = get_db()
    try:
        for org in orgs:
            existing = conn.execute(
                "SELECT 1 FROM orgs WHERE org_id=?", (org,),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
                    (org, f"Org {org}"),
                )
                conn.commit()
        tid, raw = issue_token(
            conn, label=label, operator_id="op_e2e", created_by="owner",
            org_scopes=orgs, scopes=scopes,
        )
        return raw, tid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_openapi_unauthenticated(api_server):
    port, _ = api_server
    status, body, _ = _http("GET", port, "/openapi.json")
    assert status == 200
    assert body["openapi"].startswith("3.")
    expected_paths = set(openapi_document()["paths"].keys())
    assert set(body["paths"].keys()) == expected_paths


def test_healthz_unauthenticated(api_server):
    port, _ = api_server
    status, body, _ = _http("GET", port, "/healthz")
    assert status == 200
    assert body == {"ok": True}


def test_list_alerts_requires_auth(api_server):
    port, _ = api_server
    status, _body, headers = _http("GET", port, "/v1/orgs/tig/alerts")
    assert status == 401
    assert "Bearer" in (headers.get("WWW-Authenticate") or "")


def test_list_alerts_in_scope_returns_200(api_server):
    port, _ = api_server
    raw, _ = _seed_org_and_token(orgs=["tig"], scopes=["read_only"])
    conn = get_db()
    try:
        create_alert(conn, "test", "warning", "hello", org_id="tig",
                     dedup_key="dk:tig:1")
    finally:
        conn.close()

    status, body, _ = _http("GET", port, "/v1/orgs/tig/alerts", token=raw)
    assert status == 200
    assert isinstance(body, list)
    assert any(a["title"] == "hello" for a in body)


def test_list_alerts_out_of_scope_returns_404(api_server):
    """Wrong-org access returns 404 — no existence leak."""
    port, _ = api_server
    raw, _ = _seed_org_and_token(orgs=["tig"], scopes=["read_only"])
    # Seed a second org with an alert.
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
            ("solstitch", "SolStitch"),
        )
        conn.commit()
        create_alert(conn, "test", "warning", "secret",
                     org_id="solstitch", dedup_key="dk:solstitch:1")
    finally:
        conn.close()

    status, body, _ = _http(
        "GET", port, "/v1/orgs/solstitch/alerts", token=raw,
    )
    assert status == 404
    assert body["error"] == "not_found"


def test_acknowledge_in_scope_idempotent_and_audit(api_server):
    port, _ = api_server
    raw, tid = _seed_org_and_token(
        orgs=["tig"], scopes=["read_only", "write_safe"],
    )
    conn = get_db()
    try:
        aid = create_alert(conn, "test", "warning", "T", org_id="tig",
                           dedup_key="dk:ack:1")
    finally:
        conn.close()

    s1, b1, _ = _http("POST", port, f"/v1/alerts/{aid}/acknowledge", token=raw)
    s2, b2, _ = _http("POST", port, f"/v1/alerts/{aid}/acknowledge", token=raw)
    assert s1 == 200
    assert b1["status"] == "acknowledged"
    assert s2 == 200
    assert b2["status"] == "already_acknowledged"

    # Audit log has exactly one ack row, stamped with operator + token_id.
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT actor, source, detail_json FROM audit_log"
            " WHERE action='alert_acknowledge'"
        ).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["actor"] == "op_e2e"
        assert row["source"] == "api"
        detail = json.loads(row["detail_json"])
        assert detail["alert_id"] == aid
        assert detail["token_id"] == tid
    finally:
        conn.close()


def test_acknowledge_out_of_scope_returns_404(api_server):
    port, _ = api_server
    # Token can only touch 'tig'.
    raw, _ = _seed_org_and_token(orgs=["tig"], scopes=["write_safe"])
    # Create alert in another org.
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
            ("other", "Other"),
        )
        conn.commit()
        aid = create_alert(conn, "test", "warning", "T", org_id="other",
                           dedup_key="dk:cross:1")
    finally:
        conn.close()

    status, body, _ = _http("POST", port, f"/v1/alerts/{aid}/acknowledge", token=raw)
    assert status == 404
    assert body["error"] == "not_found"


def test_resolve_idempotent(api_server):
    port, _ = api_server
    raw, _ = _seed_org_and_token(orgs=["tig"], scopes=["write_safe"])
    conn = get_db()
    try:
        aid = create_alert(conn, "test", "warning", "T", org_id="tig",
                           dedup_key="dk:rs:1")
    finally:
        conn.close()
    s1, b1, _ = _http("POST", port, f"/v1/alerts/{aid}/resolve", token=raw)
    s2, b2, _ = _http("POST", port, f"/v1/alerts/{aid}/resolve", token=raw)
    assert s1 == 200 and b1["status"] == "resolved"
    assert s2 == 200 and b2["status"] == "already_resolved"


def test_write_requires_write_safe_scope(api_server):
    port, _ = api_server
    raw, _ = _seed_org_and_token(orgs=["tig"], scopes=["read_only"])
    conn = get_db()
    try:
        aid = create_alert(conn, "t", "warning", "T", org_id="tig",
                           dedup_key="dk:scope:1")
    finally:
        conn.close()
    status, body, _ = _http("POST", port, f"/v1/alerts/{aid}/acknowledge", token=raw)
    assert status == 403
    assert body["error"] == "scope_required"
    assert body["required"] == "write_safe"


def test_rate_limit_triggers_429(api_server):
    port, _ = api_server
    raw, _ = _seed_org_and_token(orgs=["tig"], scopes=["read_only"])
    # Server fixture caps read_per_min_token at 3.
    for _ in range(3):
        status, _b, _h = _http("GET", port, "/v1/orgs/tig/alerts", token=raw)
        assert status == 200
    status, body, headers = _http("GET", port, "/v1/orgs/tig/alerts", token=raw)
    assert status == 429
    assert body["error"] == "rate_limited"
    assert "Retry-After" in headers


def test_revoked_token_immediately_blocked(api_server):
    port, _ = api_server
    raw, tid = _seed_org_and_token(orgs=["tig"], scopes=["read_only"])
    # Confirm working.
    s, _b, _ = _http("GET", port, "/v1/orgs/tig/alerts", token=raw)
    assert s == 200
    # Revoke.
    conn = get_db()
    try:
        from sable_platform.api.tokens import revoke_token
        revoke_token(conn, tid)
    finally:
        conn.close()
    status, body, _ = _http("GET", port, "/v1/orgs/tig/alerts", token=raw)
    assert status == 401
    assert body["error"] == "unauthorized"


def test_serve_refuses_public_without_flag():
    """Sanity: serve() rejects 0.0.0.0 without --public, even if a free
    port is available. Doesn't actually bind."""
    cfg = ServerConfig(bind_host="0.0.0.0", port=1, public=False)
    with pytest.raises(RuntimeError, match="Refusing to bind 0.0.0.0"):
        serve(cfg)
