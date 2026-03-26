"""Cost logging and budget enforcement for sable.db."""
from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
from pathlib import Path

from sable_platform.errors import SableError, BUDGET_EXCEEDED


def _read_platform_config() -> dict:
    """Read platform config from ~/.sable/config.yaml; return empty dict on failure."""
    try:
        import yaml
    except ImportError:
        return {}
    sable_home = Path(os.environ.get("SABLE_HOME", Path.home() / ".sable"))
    config_path = sable_home / "config.yaml"
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def log_cost(
    conn: sqlite3.Connection,
    org_id: str,
    call_type: str,
    cost_usd: float,
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    call_status: str = "success",
    job_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO cost_events
            (org_id, job_id, call_type, model, input_tokens, output_tokens, cost_usd, call_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (org_id, job_id, call_type, model, input_tokens, output_tokens, cost_usd, call_status),
    )
    conn.commit()


def get_weekly_spend(conn: sqlite3.Connection, org_id: str) -> float:
    """Return total cost_usd for org in the current ISO calendar week (Mon–Sun UTC)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    y, w, _ = now.isocalendar()
    week_start = datetime.datetime.fromisocalendar(y, w, 1).replace(tzinfo=datetime.timezone.utc)
    week_end = week_start + datetime.timedelta(days=7)

    fmt = "%Y-%m-%d %H:%M:%S"
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_usd), 0.0)
        FROM cost_events
        WHERE org_id = ?
          AND created_at >= ?
          AND created_at <  ?
        """,
        (org_id, week_start.strftime(fmt), week_end.strftime(fmt)),
    ).fetchone()
    return float(row[0])


def get_org_cost_cap(conn: sqlite3.Connection, org_id: str) -> float:
    """Return the weekly AI spend cap for the org; falls back to config default."""
    row = conn.execute("SELECT config_json FROM orgs WHERE org_id=?", (org_id,)).fetchone()
    if row:
        try:
            cfg = json.loads(row["config_json"] or "{}")
            cap = cfg.get("max_ai_usd_per_org_per_week")
            if cap is not None:
                return float(cap)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    platform_cfg = _read_platform_config()
    return float(
        platform_cfg.get("platform", {})
        .get("cost_caps", {})
        .get("max_ai_usd_per_org_per_week", 5.00)
    )


def check_budget(conn: sqlite3.Connection, org_id: str) -> tuple[float, float]:
    """Return (weekly_spend, cap). Raises SableError(BUDGET_EXCEEDED) if over cap."""
    spend = get_weekly_spend(conn, org_id)
    cap = get_org_cost_cap(conn, org_id)
    if spend > cap * 0.90:
        logging.getLogger(__name__).warning(
            "Org '%s' AI spend $%.2f is >90%% of weekly cap $%.2f", org_id, spend, cap
        )
    if spend >= cap:
        raise SableError(
            BUDGET_EXCEEDED,
            f"Org '{org_id}' weekly AI spend ${spend:.2f} exceeds cap ${cap:.2f}",
        )
    return spend, cap
