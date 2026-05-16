"""sable-platform api-token / api-serve subcommands.

``api-token issue / list / revoke`` — owner-issued bearer credentials.
``api-serve`` — start the alert-triage HTTP server.

``api-serve`` boots without ``SABLE_OPERATOR_ID`` because callers carry
their own identity in the token. ``api-token issue`` requires the env
var so the issuer is attributable.
"""
from __future__ import annotations

import json
import os
import sys

import click

from sable_platform.api.tokens import (
    ALLOWED_SCOPES,
    issue_token,
    list_tokens,
    revoke_token,
)
from sable_platform.db.connection import get_db


# ---------------------------------------------------------------------------
# api-token group
# ---------------------------------------------------------------------------


@click.group("api-token")
def api_token() -> None:
    """Manage API bearer tokens (owner-only)."""


@api_token.command("issue")
@click.option("--label", required=True,
              help="Short human-readable label (e.g. 'tig-triage-bot').")
@click.option("--operator", "operator_id", required=True,
              help="Operator identity stamped on writes (audit_log.actor).")
@click.option("--orgs", "orgs", required=True,
              help="Comma-separated org_id list, or '*' for owner tokens.")
@click.option("--scopes", default="read_only",
              help=f"Comma-separated scopes from {sorted(ALLOWED_SCOPES)}. "
                   f"Default: read_only.")
@click.option("--expires-in-days", type=int, default=None,
              help="Optional TTL. Recommended for non-owner tokens.")
def api_token_issue(
    label: str,
    operator_id: str,
    orgs: str,
    scopes: str,
    expires_in_days: int | None,
) -> None:
    """Mint a new API token. Prints the secret ONCE."""
    created_by = os.environ.get("SABLE_OPERATOR_ID", "")
    if not created_by or created_by == "unknown":
        click.echo("Error: SABLE_OPERATOR_ID must identify the owner.", err=True)
        sys.exit(1)
    org_list = [o.strip() for o in orgs.split(",") if o.strip()]
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]

    conn = get_db()
    try:
        try:
            token_id, raw = issue_token(
                conn,
                label=label,
                operator_id=operator_id,
                created_by=created_by,
                org_scopes=org_list,
                scopes=scope_list,
                expires_in_days=expires_in_days,
            )
        except ValueError as exc:
            click.echo(f"Issue failed: {exc}", err=True)
            sys.exit(1)
    finally:
        conn.close()

    click.echo("")
    click.echo("==== API TOKEN ISSUED — SAVE THIS NOW ====")
    click.echo(f"token_id: {token_id}")
    click.echo(f"secret:   {raw}")
    click.echo("==========================================")
    click.echo("(secret is shown ONLY once; only the hash is stored)")


@api_token.command("list")
@click.option("--json", "as_json", is_flag=True, default=False)
def api_token_list(as_json: bool) -> None:
    """List tokens (no secrets — only metadata)."""
    conn = get_db()
    try:
        rows = list_tokens(conn)
        payload = [dict(r) for r in rows]
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    if not payload:
        click.echo("No tokens.")
        return
    click.echo(
        f"{'TOKEN_ID':<22} {'LABEL':<24} {'OPERATOR':<16} {'ENABLED':<8} {'EXPIRES'}"
    )
    click.echo("-" * 100)
    for r in payload:
        click.echo(
            f"{r['token_id']:<22} {(r['label'] or '')[:22]:<24} "
            f"{(r['operator_id'] or '')[:14]:<16} {str(bool(r['enabled'])):<8} "
            f"{r['expires_at'] or '-'}"
        )


@api_token.command("revoke")
@click.argument("token_id")
def api_token_revoke(token_id: str) -> None:
    """Soft-revoke a token immediately. Row is retained for audit."""
    conn = get_db()
    try:
        ok = revoke_token(conn, token_id)
    finally:
        conn.close()
    if ok:
        click.echo(f"Revoked {token_id}.")
    else:
        click.echo(f"Token {token_id} not found or already revoked.", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# api-serve
# ---------------------------------------------------------------------------


@click.command("api-serve")
@click.option("--port", default=8766, show_default=True, type=int)
@click.option("--bind", "bind_host", default="127.0.0.1", show_default=True,
              help="Bind interface. Default is loopback for private-network use.")
@click.option("--public", is_flag=True, default=False,
              help="Required to bind a non-loopback interface. Use only when "
                   "fronted by an authenticated reverse proxy or VPN.")
def api_serve(port: int, bind_host: str, public: bool) -> None:
    """Start the alert-triage HTTP API."""
    from sable_platform.api.server import ServerConfig, serve

    config = ServerConfig(bind_host=bind_host, port=port, public=public)
    try:
        serve(config)
    except RuntimeError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\napi server stopped.")
