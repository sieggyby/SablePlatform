"""CLI: the Content Deck claim-due publish worker (Content Deck Phase 4, migration 077).

``deck publish-due`` is the out-of-band claim-due worker for ``content_publish_jobs``: it flips
every SCHEDULED job whose ``publish_at`` has passed to ``'due'`` for OPERATOR HAND-OFF (composeUrl +
media download -- there is NO auto-send in v1). Without this worker (or its systemd timer) a
scheduled candidate sits ``'scheduled'`` forever and never surfaces to the operator.

It makes **NO external API call** (no Telegram / Discord / SocialData / HTTP): it only runs
``claim_due_jobs()`` inside a single ``immediate_txn`` and drains via ``count_due_jobs()`` until the
scheduled-due set is empty, then prints the counts. ``claim_due_jobs`` is single-flight (atomic
conditional UPDATE per job), so two concurrent workers never double-release the same job.

Connection model mirrors ``relay_cmds``: the content_publish helpers + ``immediate_txn`` take a raw
SQLAlchemy ``Connection`` (not the sqlite3-compatible ``get_db()``), resolved from the same target
``main.py`` resolves (``SABLE_DATABASE_URL`` -> ``SABLE_DB_PATH`` -> ``~/.sable/sable.db``). The CLI
is gated by ``SABLE_OPERATOR_ID`` (the suite-wide operator-identity gate enforced in ``main.py``).
"""
from __future__ import annotations

import json

import click

from sable_platform.relay.bot.txn import immediate_txn

# A hard bound on the drain loop. Each batch strictly shrinks the scheduled-due set (it flips up to
# ``limit`` rows to 'due' or cancels them), so this is never reached in practice -- it only exists so
# a pathological state can never hang the timer.
_MAX_DRAIN_BATCHES = 10_000


def _connect():
    """Open a raw SQLAlchemy ``Connection`` to the configured platform DB (caller closes it)."""
    from sable_platform.cli.main import _resolve_cli_database_target
    from sable_platform.db.engine import get_engine

    target = _resolve_cli_database_target(None)
    return get_engine(target.connection_url).connect()


@click.group("deck")
def deck() -> None:
    """Content Deck release substrate (Phase 4) — the claim-due publish worker."""


@deck.command("publish-due")
@click.option("--limit", default=50, show_default=True, type=click.IntRange(min=1),
              help="Max jobs claimed per batch (the drain loops until none remain).")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the counts as JSON.")
def publish_due(limit: int, as_json: bool) -> None:
    """Flip every due content_publish_job to 'due' for operator hand-off (NO external calls).

    Drains the whole scheduled-due backlog: claims a batch (single-flight atomic UPDATE), then
    re-checks ``count_due_jobs()`` and repeats until the scheduled-due set is empty. Reports the
    number of jobs claimed and the number of batches run. Read/flip only — never sends anything.
    """
    from sable_platform.db.content_publish import claim_due_jobs, count_due_jobs

    conn = _connect()
    try:
        claimed_total = 0
        batches = 0
        with immediate_txn(conn):
            while batches < _MAX_DRAIN_BATCHES:
                batch = claim_due_jobs(conn, limit=limit)
                claimed_total += len(batch)
                batches += 1
                if count_due_jobs(conn) == 0:
                    break
            else:
                # Defensive only (the scheduled-due set strictly shrinks each batch): never hang
                # the timer -- surface it and let the immediate_txn roll back the partial drain.
                raise click.ClickException(
                    f"deck publish-due: drain did not converge after {_MAX_DRAIN_BATCHES} batches"
                )
    finally:
        conn.close()

    result = {"claimed": claimed_total, "batches": batches}
    if as_json:
        click.echo(json.dumps(result))
    elif claimed_total == 0:
        click.echo("No due publish jobs.")
    else:
        click.echo(f"Claimed {claimed_total} publish job(s) for hand-off in {batches} batch(es).")
