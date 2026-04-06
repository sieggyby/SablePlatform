"""Stdlib HTTP health server — GET /health returns check_db_health() as JSON.

Auth: requires a bearer token. Set SABLE_HEALTH_TOKEN in the environment before
starting the server. The server refuses to start without it (fail closed).

    export SABLE_HEALTH_TOKEN=$(openssl rand -hex 32)
    sable-platform health-server
"""
from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from sable_platform.db.connection import get_db
from sable_platform.db.health import check_db_health

log = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    # Token is set on the class by serve_health() before the server starts.
    _token: str = ""

    def do_GET(self) -> None:
        # --- Auth check (fail closed) ---
        auth = getattr(self.headers, "get", lambda k, d=None: None)("Authorization") or ""
        expected = f"Bearer {self._token}"
        if not auth or auth != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="sable-platform"')
            self.end_headers()
            return

        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

        conn = get_db()
        try:
            data = check_db_health(conn)
        finally:
            conn.close()
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_) -> None:  # silence stdlib access log
        pass


def serve_health(port: int = 8765) -> None:
    """Block forever serving GET /health on the given port.

    Requires SABLE_HEALTH_TOKEN to be set in the environment. Raises RuntimeError
    if the env var is absent — the server will not start without a token.
    """
    token = os.environ.get("SABLE_HEALTH_TOKEN", "")
    if not token:
        raise RuntimeError(
            "SABLE_HEALTH_TOKEN is required to start the health server. "
            "Set it to a secret bearer token: export SABLE_HEALTH_TOKEN=$(openssl rand -hex 32)"
        )
    _HealthHandler._token = token
    log.info("Health server starting on port %d (auth: Bearer token required)", port)
    HTTPServer(("", port), _HealthHandler).serve_forever()
