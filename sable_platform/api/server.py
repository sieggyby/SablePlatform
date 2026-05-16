"""Stdlib HTTP server for the alert-triage MVP.

Routes (all under /v1):
  GET  /v1/orgs/{org_id}/alerts
  POST /v1/alerts/{alert_id}/acknowledge
  POST /v1/alerts/{alert_id}/resolve
  GET  /openapi.json
  GET  /healthz                  (no auth — basic liveness)

Auth: Authorization: Bearer sp_live_<id>.<secret>
Auth/rate-limit failures are recorded in audit_log with source='api'.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sable_platform.api.openapi import openapi_document
from sable_platform.api.rate_limit import RateLimiter
from sable_platform.api.tokens import TokenContext, touch_last_used, verify_token
from sable_platform.db.alerts import (
    acknowledge_alert,
    get_alert,
    list_alerts,
    resolve_alert,
)
from sable_platform.db.connection import get_db

log = logging.getLogger(__name__)


# Compiled once.
_RE_ALERTS_LIST = re.compile(r"^/v1/orgs/([^/]+)/alerts/?$")
_RE_ACK = re.compile(r"^/v1/alerts/([^/]+)/acknowledge/?$")
_RE_RESOLVE = re.compile(r"^/v1/alerts/([^/]+)/resolve/?$")


@dataclass
class ServerConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8766
    public: bool = False
    rate_limiter: RateLimiter | None = None


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: Any) -> None:
    data = json.dumps(body, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    # X-Forwarded-For is honored only when set by an upstream we trust.
    # For the private MVP we use the socket peer.
    return handler.client_address[0] if handler.client_address else "0.0.0.0"


def _parse_bearer(headers) -> str | None:
    auth = headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip()


def make_handler(config: ServerConfig):
    rate_limiter = config.rate_limiter or RateLimiter()

    class _Handler(BaseHTTPRequestHandler):
        # silence the noisy stdlib access log; we emit our own structured logs.
        def log_message(self, *_) -> None:  # noqa: D401
            return

        # ------------------------------------------------------------------
        # request lifecycle
        # ------------------------------------------------------------------

        def _serve(self, method: str) -> None:
            try:
                self._dispatch(method)
            except Exception:  # noqa: BLE001
                log.exception("unhandled error serving %s %s", method, self.path)
                _json_response(self, 500, {"error": "internal_error"})

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        # ------------------------------------------------------------------
        # routing
        # ------------------------------------------------------------------

        def _dispatch(self, method: str) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            # Unauthenticated routes.
            if method == "GET" and path == "/healthz":
                return _json_response(self, 200, {"ok": True})
            if method == "GET" and path == "/openapi.json":
                return _json_response(self, 200, openapi_document())

            # Everything else requires a token.
            ctx, err = self._authenticate()
            if not ctx:
                return err  # response already sent

            # Determine scope class for rate limiting before further routing.
            scope_class = "read" if method == "GET" else "write"
            allowed, retry = rate_limiter.check(
                token_id=ctx.token_id,
                ip=_client_ip(self),
                scope_class=scope_class,
            )
            if not allowed:
                self.send_response(429)
                self.send_header("Retry-After", str(retry))
                self.send_header("Content-Type", "application/json")
                body = json.dumps({"error": "rate_limited", "retry_after": retry}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # Authenticated routes.
            if method == "GET":
                m = _RE_ALERTS_LIST.match(path)
                if m:
                    return self._handle_list_alerts(ctx, m.group(1), qs)
            if method == "POST":
                m = _RE_ACK.match(path)
                if m:
                    return self._handle_triage(ctx, m.group(1), "ack")
                m = _RE_RESOLVE.match(path)
                if m:
                    return self._handle_triage(ctx, m.group(1), "resolve")

            return _json_response(self, 404, {"error": "not_found"})

        # ------------------------------------------------------------------
        # auth
        # ------------------------------------------------------------------

        def _authenticate(self) -> tuple[TokenContext | None, None]:
            raw = _parse_bearer(self.headers)
            if not raw:
                self._unauthorized()
                return None, None
            conn = get_db()
            try:
                ctx = verify_token(conn, raw)
                if ctx:
                    touch_last_used(conn, ctx.token_id)
                    return ctx, None
            finally:
                conn.close()
            self._unauthorized()
            return None, None

        def _unauthorized(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="sable-platform"')
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"error": "unauthorized"}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ------------------------------------------------------------------
        # handlers
        # ------------------------------------------------------------------

        def _handle_list_alerts(self, ctx: TokenContext, org_id: str, qs: dict) -> None:
            if not ctx.has_scope("read_only") and not ctx.has_scope("write_safe"):
                return _json_response(self, 403, {"error": "scope_required",
                                                   "required": "read_only"})
            if not ctx.can_access_org(org_id):
                # 404 to avoid leaking org existence.
                return _json_response(self, 404, {"error": "not_found"})

            status = (qs.get("status") or ["new"])[0]
            severity = (qs.get("severity") or [None])[0]
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            limit = max(1, min(limit, 200))

            conn = get_db()
            try:
                rows = list_alerts(
                    conn, org_id=org_id, severity=severity, status=status,
                    limit=limit,
                )
                payload = [dict(r) for r in rows]
            finally:
                conn.close()
            return _json_response(self, 200, payload)

        def _handle_triage(self, ctx: TokenContext, alert_id: str, op: str) -> None:
            if not ctx.has_scope("write_safe"):
                return _json_response(self, 403, {"error": "scope_required",
                                                   "required": "write_safe"})

            conn = get_db()
            try:
                row = get_alert(conn, alert_id)
                if not row:
                    return _json_response(self, 404, {"error": "not_found"})
                alert_org = row["org_id"]
                if alert_org and not ctx.can_access_org(alert_org):
                    # Don't leak the alert's existence to wrong-org tokens.
                    return _json_response(self, 404, {"error": "not_found"})

                detail = {"token_id": ctx.token_id, "token_label": ctx.label}
                if op == "ack":
                    new_status = acknowledge_alert(
                        conn, alert_id, ctx.operator_id,
                        source="api", detail_extra=detail,
                    )
                else:
                    new_status = resolve_alert(
                        conn, alert_id, actor=ctx.operator_id,
                        source="api", detail_extra=detail,
                    )
            finally:
                conn.close()
            return _json_response(self, 200, {
                "alert_id": alert_id, "status": new_status,
            })

    return _Handler


def serve(config: ServerConfig) -> None:
    """Block forever, serving the API on the configured bind+port."""
    if config.bind_host == "0.0.0.0" and not config.public:
        raise RuntimeError(
            "Refusing to bind 0.0.0.0 without --public. Default is "
            "127.0.0.1 to keep this a private-network API per TODO_API.md. "
            "Pass --public to override."
        )
    handler = make_handler(config)
    log.info(
        "api server starting on %s:%d (public=%s)",
        config.bind_host, config.port, config.public,
    )
    server = ThreadingHTTPServer((config.bind_host, config.port), handler)
    server.serve_forever()
