"""Cost logging and budget enforcement for sable.db."""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

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
    conn: Connection,
    org_id: str,
    call_type: str,
    cost_usd: float,
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    call_status: str = "success",
    job_id: str | None = None,
    operator_id: str | None = None,
) -> None:
    """Append one billed call to the ``cost_events`` ledger.

    ``operator_id`` (mig 081) is the acting operator's stable SableWeb SESSION
    identity (``operator_arf`` …), stamped only when a logged-in human initiated
    the spend — NOT the persona X-handle (personas are shared across humans).
    None = unattributed (system paths: workflows, ambient producers, timers).
    """
    conn.execute(
        text(
            "INSERT INTO cost_events"
            " (org_id, job_id, call_type, model, input_tokens, output_tokens, cost_usd,"
            " call_status, operator_id)"
            " VALUES (:org_id, :job_id, :call_type, :model, :input_tokens, :output_tokens,"
            " :cost_usd, :call_status, :operator_id)"
        ),
        {
            "org_id": org_id,
            "job_id": job_id,
            "call_type": call_type,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "call_status": call_status,
            "operator_id": operator_id,
        },
    )
    conn.commit()


def reserve_image_spend(
    conn: Connection,
    org_id: str,
    est: float,
    *,
    call_type: str = "meme_image",
    model: str | None = None,
) -> int:
    """Insert a HELD ``cost_events`` reservation (cost_usd=est, call_status='reserved') and return
    its ``event_id``. ``get_weekly_spend``/``get_daily_spend`` SUM regardless of status, so the hold
    is immediately visible to a concurrent reserve.

    The caller MUST run this INSIDE a serialized (``BEGIN IMMEDIATE`` / SERIALIZABLE) transaction
    together with the weekly/daily cap re-check, so concurrent paid renders serialize and only those
    that fit under the cap can take a hold (closes the check-then-spend race). Does NOT commit — the
    caller's serialized txn owns the boundary. Pair with ``release_image_reservation`` (refund/finalize)."""
    row = conn.execute(
        text(
            "INSERT INTO cost_events (org_id, call_type, model, cost_usd, call_status) "
            "VALUES (:org_id, :call_type, :model, :cost_usd, 'reserved') RETURNING event_id"
        ),
        {"org_id": org_id, "call_type": call_type, "model": model, "cost_usd": float(est)},
    ).fetchone()
    if row is None:  # INSERT ... RETURNING must yield a row
        raise SableError(BUDGET_EXCEEDED, "reserve_image_spend: INSERT ... RETURNING yielded no row")
    return int(row[0])


def release_image_reservation(conn: Connection, event_id: int) -> None:
    """Delete a held reservation (only a still-'reserved' row), refunding the hold. Used after the
    paid call resolves: on a NOT-charged failure it leaves no residual spend; on success / a charged
    failure the caller deletes the hold and then logs the real ``meme_image`` outcome via
    ``log_cost`` (so the final ledger row is exactly the charge, never double-counted). Commits."""
    conn.execute(
        text("DELETE FROM cost_events WHERE event_id = :e AND call_status = 'reserved'"),
        {"e": int(event_id)},
    )
    conn.commit()


def get_weekly_spend(conn: Connection, org_id: str) -> float:
    """Return total cost_usd for org in the current ISO calendar week (Mon–Sun UTC)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    y, w, _ = now.isocalendar()
    week_start = datetime.datetime.fromisocalendar(y, w, 1).replace(tzinfo=datetime.timezone.utc)
    week_end = week_start + datetime.timedelta(days=7)

    fmt = "%Y-%m-%d %H:%M:%S"
    row = conn.execute(
        text(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total"
            " FROM cost_events"
            " WHERE org_id = :org_id"
            "   AND created_at >= :start"
            "   AND created_at <  :end"
        ),
        {"org_id": org_id, "start": week_start.strftime(fmt), "end": week_end.strftime(fmt)},
    ).fetchone()
    return float(row[0])


def get_org_cost_cap(conn: Connection, org_id: str) -> float:
    """Return the weekly AI spend cap for the org; falls back to config default."""
    row = conn.execute(
        text("SELECT config_json FROM orgs WHERE org_id=:org_id"),
        {"org_id": org_id},
    ).fetchone()
    if row:
        try:
            cfg = json.loads(row["config_json"] or "{}")
            cap = cfg.get("max_ai_usd_per_org_per_week")
            if cap is not None:
                return float(cap)
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            # AttributeError: a non-dict JSON blob ('[]', '"x"') has no .get —
            # degrade to the platform/default cap, never crash a budget gate.
            pass

    platform_cfg = _read_platform_config()
    return float(
        platform_cfg.get("platform", {})
        .get("cost_caps", {})
        .get("max_ai_usd_per_org_per_week", 5.00)
    )


def check_budget(conn: Connection, org_id: str) -> tuple[float, float]:
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


def get_daily_spend(
    conn: Connection,
    org_id: str,
    *,
    call_type: str | None = None,
    call_type_prefix: str | None = None,
    now: datetime.datetime | None = None,
) -> float:
    """Total ``cost_usd`` for ``org_id`` in the current UTC calendar day, optionally filtered
    to one ``call_type`` (e.g. ``'meme_image'``) OR to a ``call_type_prefix`` family (e.g.
    ``'ambient.'`` sums every ``ambient.*`` tag — the same dotted-prefix convention the relay's
    ``relay_socialdata.%`` daily cap uses). The two filters are mutually exclusive. ``now`` is
    injectable for tests. The finer, same-day companion to ``get_weekly_spend`` — closes the
    "weekly-only" image-cap gap."""
    if call_type is not None and call_type_prefix is not None:
        raise ValueError("call_type and call_type_prefix are mutually exclusive")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:  # treat a naive `now` as already-UTC (don't let astimezone() assume local)
        now = now.replace(tzinfo=datetime.timezone.utc)
    day_start = now.astimezone(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day_end = day_start + datetime.timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    sql = (
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total"
        " FROM cost_events"
        " WHERE org_id = :org_id"
        "   AND created_at >= :start"
        "   AND created_at <  :end"
    )
    params = {"org_id": org_id, "start": day_start.strftime(fmt), "end": day_end.strftime(fmt)}
    if call_type is not None:
        sql += "   AND call_type = :ct"
        params["ct"] = call_type
    elif call_type_prefix is not None:
        # LIKE-escape the prefix so a literal '%'/'_' in a tag can never widen the match
        # (dotted tags like 'ambient.' contain neither today; this is belt-and-suspenders).
        esc = call_type_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += "   AND call_type LIKE :ctp ESCAPE '\\'"
        params["ctp"] = f"{esc}%"
    row = conn.execute(text(sql), params).fetchone()
    return float(row[0])


def get_org_image_daily_cap(conn: Connection, org_id: str) -> float:
    """Per-org DAILY image-spend cap (``meme_image``). Resolution order mirrors
    ``get_org_cost_cap``: ``orgs.config_json.max_image_usd_per_org_per_day`` >
    platform ``cost_caps.max_image_usd_per_org_per_day`` > $2.00 default.

    The default is deliberately BELOW the weekly AI-cap default ($5.00/week) so it actually
    BINDS: daily image spend is a subset of weekly AI spend, so a day cap >= the weekly cap
    would be a strict no-op (the weekly reserve would always refuse first). At $2/day a same-day
    render loop is stopped at ~$2 instead of consuming the whole week's budget in one day. (The
    ≤$20 A/B experiment is unaffected — it passes a per-instance override, not this default.)"""
    row = conn.execute(
        text("SELECT config_json FROM orgs WHERE org_id=:org_id"),
        {"org_id": org_id},
    ).fetchone()
    if row:
        try:
            cfg = json.loads(row["config_json"] or "{}")
            cap = cfg.get("max_image_usd_per_org_per_day")
            if cap is not None:
                return float(cap)
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            # AttributeError: a non-dict JSON blob ('[]', '"x"') has no .get —
            # degrade to the platform/default cap, never crash a budget gate.
            pass

    platform_cfg = _read_platform_config()
    return float(
        platform_cfg.get("platform", {})
        .get("cost_caps", {})
        .get("max_image_usd_per_org_per_day", 2.00)
    )


def get_org_ambient_daily_cap(conn: Connection, org_id: str) -> float:
    """Per-org DAILY cap for the AMBIENT deck producers (the ``ambient.*`` cost-tag family —
    the nightly ``sable deck produce-ambient`` batch). Resolution order mirrors
    ``get_org_cost_cap``: ``orgs.config_json.max_ambient_usd_per_org_per_day`` >
    platform ``cost_caps.max_ambient_usd_per_org_per_day`` > $1.00 default.

    The default is deliberately BELOW the weekly AI-cap default ($5.00/week) so it actually
    BINDS (same reasoning as the image cap above): ambient spend is a subset of weekly AI
    spend, and a nightly producer must never be able to consume the whole week's budget in a
    couple of runs. Operator-driven produce (the deck Generate button) is NOT counted against
    this cap — it carries its own controls (``operator_meme_budget`` / ``reserve_generation``)
    and logs non-``ambient.*`` tags."""
    row = conn.execute(
        text("SELECT config_json FROM orgs WHERE org_id=:org_id"),
        {"org_id": org_id},
    ).fetchone()
    if row:
        try:
            cfg = json.loads(row["config_json"] or "{}")
            cap = cfg.get("max_ambient_usd_per_org_per_day")
            if cap is not None:
                return float(cap)
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            # AttributeError: a non-dict JSON blob ('[]', '"x"') has no .get —
            # degrade to the platform/default cap, never crash a budget gate.
            pass

    platform_cfg = _read_platform_config()
    return float(
        platform_cfg.get("platform", {})
        .get("cost_caps", {})
        .get("max_ambient_usd_per_org_per_day", 1.00)
    )
