"""CLI commands for SableRelay operator management (MEGAPLAN C2.5).

The operator-facing relay command surface (PLAN §4): ``bind-chat``,
``register-operator``, ``status``, ``pending``, ``enable``, and the relay-level
kill-switch ``disable`` (alias ``pause-org``).

These commands are the out-of-band operator equivalent of the in-chat admin
handlers (``relay/bot/handlers/admin.py``): the in-chat path is admin-gated via
``relay_member_roles``; the CLI path is gated by ``SABLE_OPERATOR_ID`` (the
suite-wide operator-identity gate enforced in ``cli/main.py``), so the CLI does
NOT re-run the in-chat admin role check — the operator running the CLI already
holds platform-level authority.

Connection model: unlike the ``org``/``kol`` commands (which use the sqlite3-
compatible ``get_db()`` ``CompatConnection``), the relay db helpers and the
``immediate_txn`` write boundary take a raw SQLAlchemy ``Connection``. So these
commands acquire one via ``get_engine(...).connect()`` — exactly like the
``db-health`` command in ``main.py`` and the relay feed/listener loops — and own
its lifecycle. Writers (``enable``, ``disable``) wrap their work in a single
``immediate_txn`` so the ``relay_clients`` flip, the publication-job fan-out, and
the audit row commit atomically (PLAN §3.1 / §15.3).
"""
from __future__ import annotations

import json
import os
import sys

import click

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn

_GRANTABLE_OPERATOR_ROLES = ("sable_operator", "admin")
_BINDING_ROLES = ("operator", "shared", "community", "broadcast")


def _operator_actor() -> str:
    """The audit actor for relay CLI writes — the suite operator identity."""
    return os.environ.get("SABLE_OPERATOR_ID", "unknown")


def _connect():
    """Open a raw SQLAlchemy ``Connection`` to the configured platform DB.

    Resolves the same target ``main.py`` resolves (``SABLE_DATABASE_URL`` →
    ``SABLE_DB_PATH`` → ``~/.sable/sable.db``) and returns an open SA connection
    the relay db helpers + ``immediate_txn`` can use directly. The caller owns
    closing it.
    """
    from sable_platform.cli.main import _resolve_cli_database_target
    from sable_platform.db.engine import get_engine

    target = _resolve_cli_database_target(None)
    return get_engine(target.connection_url).connect()


@click.group("relay")
def relay() -> None:
    """Manage the SableRelay substrate (clients, chat bindings, operators)."""


@relay.command("bind-chat")
@click.argument("org_id")
@click.argument("role", type=click.Choice(_BINDING_ROLES))
@click.option("--chat-id", required=True, help="Platform chat id to bind.")
@click.option("--platform", default="telegram", show_default=True,
              type=click.Choice(["telegram", "discord"]),
              help="Chat platform.")
@click.option("--title", default=None, help="Optional chat title for the chat-id surface row.")
def relay_bind_chat(org_id: str, role: str, chat_id: str, platform: str, title: str | None) -> None:
    """Bind a chat as operator/shared/community/broadcast for a relay client.

    ORG_ID must already be a relay client (run `relay enable ORG_ID` first).
    Re-points the role / displaces any other role on the chat per the partial
    unique indexes. Writes an audit row. Idempotent on an identical binding.
    """
    conn = _connect()
    try:
        if not relay_db.relay_client_exists(conn, org_id):
            click.echo(
                f"Org '{org_id}' is not a relay client. "
                f"Run `sable-platform relay enable {org_id}` first.",
                err=True,
            )
            sys.exit(1)
        with immediate_txn(conn):
            binding_id = relay_db.bind_chat(
                conn,
                org_id=org_id,
                platform=platform,
                chat_id=chat_id,
                role=role,
                title=title,
            )
            relay_db.write_relay_audit(
                conn,
                actor=_operator_actor(),
                action="relay.bind_chat",
                org_id=org_id,
                entity_id=str(binding_id),
                detail={
                    "platform": platform,
                    "chat_id": chat_id,
                    "role": role,
                    "via": "cli",
                },
            )
        click.echo(
            f"Bound {platform} chat '{chat_id}' as '{role}' for '{org_id}' "
            f"(binding id {binding_id})."
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@relay.command("register-operator")
@click.argument("org_id")
@click.option("--tg-user-id", required=True,
              help="Telegram numeric user id of the member to grant the role to.")
@click.option("--role", default="sable_operator", show_default=True,
              type=click.Choice(_GRANTABLE_OPERATOR_ROLES),
              help="Relay role to grant.")
@click.option("--handle", default=None, help="Display handle for the member (display-only).")
def relay_register_operator(org_id: str, tg_user_id: str, role: str, handle: str | None) -> None:
    """Grant a relay role (sable_operator/admin) to a Telegram member for a client.

    Resolves (or auto-creates, for audit only) the member from the Telegram
    numeric user id, grants ROLE for ORG_ID, and writes an audit row — all in one
    transaction. This is the CLI equivalent of the in-chat `/register-operator`
    numeric mode; the operator running the CLI is already platform-authorized via
    SABLE_OPERATOR_ID, so no in-chat admin role check is re-run.
    """
    conn = _connect()
    try:
        if not relay_db.relay_client_exists(conn, org_id):
            click.echo(
                f"Org '{org_id}' is not a relay client. "
                f"Run `sable-platform relay enable {org_id}` first.",
                err=True,
            )
            sys.exit(1)
        with immediate_txn(conn):
            member_id = relay_db.auto_create_member_identity(
                conn, "telegram", str(tg_user_id), handle=handle
            )
            granted = relay_db.grant_member_role(conn, member_id, org_id, role)
            relay_db.write_relay_audit(
                conn,
                actor=_operator_actor(),
                action="relay.register_operator",
                org_id=org_id,
                entity_id=str(member_id),
                detail={
                    "role": role,
                    "granted": granted,
                    "tg_user_id": str(tg_user_id),
                    "via": "cli",
                },
            )
        if granted:
            click.echo(
                f"Granted '{role}' to member {member_id} (tg:{tg_user_id}) for '{org_id}'."
            )
        else:
            click.echo(
                f"Member {member_id} (tg:{tg_user_id}) already holds '{role}' for '{org_id}'."
            )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@relay.command("enable")
@click.argument("org_id")
def relay_enable(org_id: str) -> None:
    """Enable a relay client (admit ORG_ID to the poller).

    Creates the relay_clients row if absent and sets enabled=1, re-admitting the
    org to the poller's per-enabled-client loop. Writes an audit row. ORG_ID must
    already exist in `orgs`.
    """
    conn = _connect()
    try:
        if not relay_db.org_exists(conn, org_id):
            click.echo(
                f"Org '{org_id}' not found in orgs. "
                f"Create it first: `sable-platform org create {org_id} --name ...`.",
                err=True,
            )
            sys.exit(1)
        with immediate_txn(conn):
            created = relay_db.ensure_relay_client(conn, org_id, enabled=0)
            relay_db.set_relay_client_enabled(conn, org_id, enabled=1)
            relay_db.write_relay_audit(
                conn,
                actor=_operator_actor(),
                action="relay.enable",
                org_id=org_id,
                detail={"created": created, "via": "cli"},
            )
        verb = "Created and enabled" if created else "Enabled"
        click.echo(f"{verb} relay client '{org_id}' (poller will pick it up).")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@relay.command("disable")
@click.argument("org_id")
def relay_disable(org_id: str) -> None:
    """Kill-switch: disable a relay client and halt its in-flight publishing.

    In ONE transaction: set relay_clients.enabled=0 (stops the poller) AND mark
    every pending/retry/claimed relay_publication_jobs row for ORG_ID to
    state='dead' with last_error='org disabled by operator' (stops the publisher).
    'dead' is the only CHECK-allowed halted value in the §3.1 set — no 'halted'
    state exists. Writes an audit row. Different blast radius than AutoCM's
    /pause-client: this halts mirror/quorum publishing at the substrate.
    """
    conn = _connect()
    try:
        if not relay_db.relay_client_exists(conn, org_id):
            click.echo(f"Org '{org_id}' is not a relay client (nothing to disable).", err=True)
            sys.exit(1)
        with immediate_txn(conn):
            relay_db.set_relay_client_enabled(conn, org_id, enabled=0)
            killed = relay_db.kill_org_inflight_jobs(
                conn, org_id, last_error="org disabled by operator"
            )
            relay_db.write_relay_audit(
                conn,
                actor=_operator_actor(),
                action="relay.disable",
                org_id=org_id,
                detail={"jobs_killed": killed, "via": "cli"},
            )
        click.echo(
            f"Disabled relay client '{org_id}' — poller stopped and "
            f"{killed} in-flight publication job(s) marked dead."
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()


# `pause-org` is an operator-friendly alias for the kill-switch (PLAN §4 names
# both `disable` and `pause-org`); it shares the same callback/behaviour.
relay.add_command(relay_disable, name="pause-org")


@relay.command("status")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON.")
def relay_status(org_id: str, as_json: bool) -> None:
    """Show relay bot-health for a client (PLAN §4 `/status`).

    Reports the enabled flag, poll cursor, last error, and counts of active
    bindings / open submissions / in-flight publication jobs.
    """
    conn = _connect()
    try:
        status = relay_db.get_relay_status(conn, org_id)
    finally:
        conn.close()

    if status is None:
        click.echo(f"Org '{org_id}' is not a relay client.", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(status, default=str))
        return

    click.echo(f"relay client '{org_id}':")
    click.echo(f"  enabled            : {'yes' if status['enabled'] else 'no'}")
    click.echo(f"  last_polled_at     : {status['last_polled_at'] or '(never)'}")
    click.echo(f"  last_seen_x_id     : {status['last_seen_x_id'] or '(none)'}")
    click.echo(f"  last_error         : {status['last_error'] or '(none)'}")
    click.echo(f"  active_bindings    : {status['active_bindings']}")
    click.echo(f"  pending_submissions: {status['pending_submissions']}")
    click.echo(f"  inflight_jobs      : {status['inflight_jobs']}")


@relay.command("pending")
@click.argument("org_id")
@click.option("--limit", default=50, show_default=True, type=click.IntRange(min=1),
              help="Max submissions to list.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON.")
def relay_pending(org_id: str, limit: int, as_json: bool) -> None:
    """List open submissions awaiting quorum for a client (PLAN §4 `/pending`)."""
    conn = _connect()
    try:
        rows = relay_db.list_pending_submissions(conn, org_id, limit=limit)
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(rows, default=str))
        return

    if not rows:
        click.echo(f"No pending submissions for '{org_id}'.")
        return

    click.echo(f"{'ID':<6}  {'STATUS':<18}  {'ROLE':<9}  {'AUTHOR':<18}  X_ID")
    click.echo("-" * 72)
    for r in rows:
        click.echo(
            f"{r['id']:<6}  {r['status']:<18}  {r['source_role']:<9}  "
            f"{('@' + (r['x_author_handle'] or '?')):<18}  {r['x_id']}"
        )
