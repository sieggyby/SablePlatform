"""CLI commands for the client_checkin_loop pipeline."""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import click

from sable_platform.db.connection import get_db
from sable_platform.errors import SableError


def _resolve_run_date(date_arg: str | None) -> str:
    if date_arg:
        # Validate ISO format
        try:
            _dt.date.fromisoformat(date_arg)
        except ValueError as exc:
            raise click.BadParameter(f"--date must be YYYY-MM-DD: {exc}") from None
        return date_arg
    return _dt.date.today().isoformat()


def _vault_dir(org: str, run_date: str) -> Path:
    return Path.home() / "sable-vault" / org / "checkins" / run_date


@click.group("checkin")
def checkin() -> None:
    """Generate, send, and inspect weekly client check-ins."""


@checkin.command("generate")
@click.option("--org", required=True, help="Org ID")
@click.option("--date", "date_arg", default=None,
              help="ISO date the check-in is FOR (default: today). Used as snapshot_date and vault folder name.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Skip Anthropic call (use canned placeholder) and skip Telegram send.")
@click.option("--vault-root", default=None,
              help="Override vault root (default: ~/sable-vault/).")
@click.option("--cult-grader-repo", default=None,
              help="Override cult_grader repo path (default: $SABLE_CULT_GRADER_PATH).")
def generate(org: str, date_arg: str | None, dry_run: bool,
             vault_root: str | None, cult_grader_repo: str | None) -> None:
    """Run client_checkin_loop end-to-end and write artifacts to the vault."""
    from sable_platform.workflows.engine import WorkflowRunner
    from sable_platform.workflows.builtins.client_checkin_loop import CLIENT_CHECKIN_LOOP

    run_date = _resolve_run_date(date_arg)
    config: dict = {"org_id": org, "run_date": run_date}
    if dry_run:
        config["dry_run"] = True
    if vault_root:
        config["vault_root"] = vault_root
    if cult_grader_repo:
        config["cult_grader_repo"] = cult_grader_repo

    conn = get_db()
    try:
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(org, config, conn=conn)
    except SableError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()

    out_dir = Path(vault_root or Path.home() / "sable-vault") / org / "checkins" / run_date
    summary_path = out_dir / "summary.md"
    deep_dive_path = out_dir / "deep_dive.md"
    click.echo(json.dumps({
        "run_id": run_id,
        "org_id": org,
        "run_date": run_date,
        "dry_run": bool(dry_run),
        "summary_path": str(summary_path),
        "deep_dive_path": str(deep_dive_path),
    }, indent=2))


@checkin.command("send")
@click.option("--org", required=True, help="Org ID")
@click.option("--date", "date_arg", required=True, help="ISO date of the existing artifact (YYYY-MM-DD)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print what would be sent; make no Telegram call.")
def send(org: str, date_arg: str, dry_run: bool) -> None:
    """Re-send (or preview) the existing summary.md + deep_dive.md to the client TG chat."""
    import os
    from sable_platform.workflows.builtins.client_checkin_loop import _send_telegram_message

    run_date = _resolve_run_date(date_arg)
    out_dir = _vault_dir(org, run_date)
    summary_path = out_dir / "summary.md"
    deep_dive_path = out_dir / "deep_dive.md"

    if not summary_path.exists() or not deep_dive_path.exists():
        click.echo(
            f"Error: artifacts not found at {out_dir}. Run `sable-platform checkin generate --org {org} --date {run_date}` first.",
            err=True,
        )
        sys.exit(1)

    summary = summary_path.read_text(encoding="utf-8")
    deep_dive = deep_dive_path.read_text(encoding="utf-8")

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT config_json FROM orgs WHERE org_id=?", (org,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["config_json"]:
        click.echo(f"Error: org '{org}' has no config_json", err=True)
        sys.exit(1)
    cfg = json.loads(row["config_json"])
    chat_id = cfg.get("client_telegram_chat_id")
    if not chat_id:
        click.echo(f"Error: org '{org}' has no client_telegram_chat_id in config_json", err=True)
        sys.exit(1)

    if dry_run:
        click.echo(json.dumps({
            "dry_run": True,
            "chat_id": str(chat_id),
            "summary_chars": len(summary),
            "deep_dive_chars": len(deep_dive),
        }, indent=2))
        return

    token = os.environ.get("SABLE_TELEGRAM_BOT_TOKEN")
    if not token:
        click.echo("Error: SABLE_TELEGRAM_BOT_TOKEN not set", err=True)
        sys.exit(1)

    err1 = _send_telegram_message(token, str(chat_id), summary)
    err2 = _send_telegram_message(token, str(chat_id), deep_dive) if not err1 else None

    click.echo(json.dumps({
        "sent": err1 is None and err2 is None,
        "chat_id": str(chat_id),
        "summary_error": err1,
        "deep_dive_error": err2,
    }, indent=2))
    if err1 or err2:
        sys.exit(1)


@checkin.command("list")
@click.option("--org", required=True, help="Org ID")
@click.option("--limit", default=10, type=int, help="Max rows to return")
def list_cmd(org: str, limit: int) -> None:
    """List past check-in folders in the vault, newest first."""
    base = Path.home() / "sable-vault" / org / "checkins"
    if not base.exists():
        click.echo(json.dumps({"org": org, "checkins": []}, indent=2))
        return

    rows: list[dict] = []
    for d in sorted(base.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        rows.append({
            "date": d.name,
            "summary": (d / "summary.md").exists(),
            "deep_dive": (d / "deep_dive.md").exists(),
            "synthesis_meta": (d / "_synthesis.json").exists(),
        })
        if len(rows) >= limit:
            break

    click.echo(json.dumps({"org": org, "checkins": rows}, indent=2))
