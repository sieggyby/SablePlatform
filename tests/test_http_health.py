"""Tests for SP-OBS Phase 2: /health HTTP endpoint."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from tests.conftest import make_test_conn


def _make_conn():
    return make_test_conn()


class _MockHeaders:
    """Minimal dict-like headers stub for BaseHTTPRequestHandler."""
    def __init__(self, headers: dict):
        self._h = {k.lower(): v for k, v in headers.items()}

    def get(self, key: str, default=None):
        return self._h.get(key.lower(), default)


def _invoke_handler(
    path: str,
    conn,
    headers: dict | None = None,
    monkeypatch=None,
    token: str = "test_token",
) -> tuple[int, dict | None]:
    """Instantiate _HealthHandler and call do_GET, return (status_code, body_or_None)."""
    from sable_platform.http_health import _HealthHandler

    response_buf = io.BytesIO()

    class _FakeWFile:
        def write(self, data: bytes) -> None:
            response_buf.write(data)

    handler = _HealthHandler.__new__(_HealthHandler)
    handler.path = path
    handler.wfile = _FakeWFile()
    handler.headers = _MockHeaders(headers or {})
    handler._token = token

    status_sent = []
    headers_sent = {}

    def _send_response(code):
        status_sent.append(code)

    def _send_header(name, value):
        headers_sent[name] = value

    def _end_headers():
        pass

    handler.send_response = _send_response
    handler.send_header = _send_header
    handler.end_headers = _end_headers

    with patch("sable_platform.http_health.get_db", return_value=conn):
        handler.do_GET()

    code = status_sent[0] if status_sent else None
    body = None
    raw = response_buf.getvalue()
    if raw:
        try:
            body = json.loads(raw)
        except Exception:
            pass
    return code, body


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

def test_health_no_auth_returns_401():
    """GET /health without Authorization header returns 401."""
    conn = _make_conn()
    code, body = _invoke_handler("/health", conn, headers={})
    assert code == 401
    assert body is None


def test_health_wrong_token_returns_401():
    """GET /health with wrong bearer token returns 401."""
    conn = _make_conn()
    code, body = _invoke_handler("/health", conn, headers={"Authorization": "Bearer wrong_token"})
    assert code == 401
    assert body is None


def test_health_endpoint_returns_200():
    """GET /health with correct bearer token returns 200 and a JSON body with 'ok' key."""
    conn = _make_conn()
    code, body = _invoke_handler(
        "/health", conn,
        headers={"Authorization": "Bearer test_token"},
        token="test_token",
    )
    assert code == 200
    assert body is not None
    assert "ok" in body
    assert body["ok"] is True


def test_health_unknown_path_returns_404():
    """GET /other with correct auth returns 404."""
    conn = _make_conn()
    code, body = _invoke_handler(
        "/other", conn,
        headers={"Authorization": "Bearer test_token"},
        token="test_token",
    )
    assert code == 404
    assert body is None


def test_health_serve_requires_token_env(monkeypatch):
    """serve_health() raises RuntimeError if SABLE_HEALTH_TOKEN is not set."""
    import os
    monkeypatch.delenv("SABLE_HEALTH_TOKEN", raising=False)
    from sable_platform.http_health import serve_health
    with pytest.raises(RuntimeError, match="SABLE_HEALTH_TOKEN"):
        serve_health(port=19999)
