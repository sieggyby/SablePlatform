"""Per-(operator, org, ISO-week) dollar budget for on-demand meme production (mig 078).

The deck "Generate" button (SableWeb -> Slopper ``POST /api/v1/meme/produce``) spends one paid
``meme_ideate`` Claude call per batch. This ledger caps that spend at a configurable default of
**$5.00 per OPERATOR, per CLIENT (org), per ISO calendar week** (Mon-Sun UTC). The produced
candidates themselves land in the SHARED org deck, so the BUDGET is per-operator while the OUTPUT
bank is shared across the client's operators.

Reserve-then-reconcile (mirrors ``replies.reserve_generation``, but in dollars + per-week +
org-scoped): the caller atomically ``reserve_meme_spend`` an ESTIMATE before the call -- if that
reservation would breach the cap it is refunded immediately and ``allowed`` is False (nothing is
produced, nothing spent) -- then ``reconcile_meme_spend`` adjusts the reservation down to the
call's ACTUAL cost afterwards. Because the estimate is always >= a real call's cost, reconcile
only ever LOWERS the recorded spend, so the cap is a true (race-safe) ceiling rather than a
check-then-act TOCTOU. The ISO-week boundary matches ``cost.get_weekly_spend``.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

# Float-dust tolerance: a reconcile that lands within this of 0 is clamped to 0 (never written as a
# tiny negative that would trip the spend_usd >= 0 CHECK), but anything more negative FAILS CLOSED.
_SPEND_EPS = 1e-9


def _require_finite(value, name: str, *, positive: bool) -> float:
    """Coerce ``value`` to a FINITE float and enforce its sign domain. ``positive=True`` -> must be
    > 0 (an estimate is a real held reservation); otherwise must be >= 0 (a cap / actual cost).
    Rejects None, non-numeric, NaN, and +/-inf with ValueError so a bad input can never corrupt
    ``spend_usd`` or mint budget credit (the cost ceiling is only a ceiling if its inputs are sane)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"meme_budget: {name} must be a finite number, got {value!r}")
    if not math.isfinite(f):
        raise ValueError(f"meme_budget: {name} must be finite, got {f!r}")
    if positive and f <= 0:
        raise ValueError(f"meme_budget: {name} must be > 0, got {f!r}")
    if not positive and f < 0:
        raise ValueError(f"meme_budget: {name} must be >= 0, got {f!r}")
    return f

# Default per-operator weekly cap (USD). Overridable per org via
# orgs.config_json["max_meme_usd_per_operator_per_week"].
DEFAULT_MEME_WEEKLY_CAP_USD = 5.00
# Reserved up front per produce, then reconciled to the real cost. MUST sit comfortably ABOVE the
# worst-case meme_ideate call so (a) concurrent reservations can't slip extra calls past the cap
# and (b) reconcile only ever LOWERS the recorded spend -> the cap is a true ceiling. Worst case is
# the Opus 4.8 ideation (generator.IDEATION_MODEL, $5/$25 per Mtok, max_tokens=3072): output ceiling
# ~$0.077 + a prompt-cache MISS on the large system prefix (~10-15k tok at 1.25x create) ~$0.06-0.10
# => ~$0.15-0.18 real. 0.30 keeps the estimate >= actual with headroom (reconcile only lowers); the
# refund makes a typical ~$0.15 call cheap. A call somehow exceeding 0.30 is the only overshoot path,
# bounded to one call (the next reserve sees the true higher spend and refuses).
DEFAULT_PRODUCE_ESTIMATE_USD = 0.30
_CAP_CONFIG_KEY = "max_meme_usd_per_operator_per_week"


def week_iso(now: Optional[datetime] = None) -> str:
    """'YYYY-Www' ISO-week bucket (UTC, Mon-Sun), matching cost.get_weekly_spend's week."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _stamp(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def meme_weekly_cap(conn, org_id: str) -> float:
    """Per-operator weekly meme cap for ``org_id``: the orgs.config_json override or the default.
    A negative / non-numeric override is ignored (falls back to the default)."""
    row = conn.execute(
        text("SELECT config_json FROM orgs WHERE org_id = :o"), {"o": org_id}
    ).fetchone()
    if row and row[0]:
        try:
            cfg = json.loads(row[0])
            v = cfg.get(_CAP_CONFIG_KEY)
            # A finite, non-negative numeric override only -- json.loads accepts NaN/Infinity by
            # default, so guard isfinite too (a non-finite cap would void the ceiling).
            if (isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and v >= 0):
                return float(v)
        except (ValueError, TypeError):
            pass
    return DEFAULT_MEME_WEEKLY_CAP_USD


def _current(conn, operator_handle: str, org_id: str, wk: str) -> tuple[float, int]:
    row = conn.execute(
        text("SELECT spend_usd, runs FROM operator_meme_budget "
             "WHERE operator_handle = :h AND org_id = :o AND week_iso = :w"),
        {"h": operator_handle, "o": org_id, "w": wk},
    ).fetchone()
    if not row:
        return (0.0, 0)
    return (float(row[0] or 0.0), int(row[1] or 0))


def operator_meme_status(conn, operator_handle: str, org_id: str, *,
                         cap: Optional[float] = None, now: Optional[datetime] = None) -> dict:
    """Read-only budget readout for (operator, org, current ISO week). NEVER mutates.

    ``{operator, org, week, spend_usd, cap_usd, remaining_usd, runs, allowed}`` where ``allowed``
    is True iff there is budget left (spend < cap)."""
    wk = week_iso(now)
    cap = meme_weekly_cap(conn, org_id) if cap is None else float(cap)
    spend, runs = _current(conn, operator_handle, org_id, wk)
    return {
        "operator": operator_handle, "org": org_id, "week": wk,
        "spend_usd": round(spend, 4), "cap_usd": round(cap, 2),
        "remaining_usd": round(max(0.0, cap - spend), 4), "runs": runs,
        "allowed": spend < cap,
    }


def reserve_meme_spend(conn, operator_handle: str, org_id: str, *,
                       estimate: float = DEFAULT_PRODUCE_ESTIMATE_USD,
                       cap: Optional[float] = None, now: Optional[datetime] = None) -> dict:
    """Atomically reserve ``estimate`` against this week's (operator, org) budget.

    Mirrors ``replies.reserve_generation``: a SINGLE upsert (``INSERT ... ON CONFLICT DO UPDATE ...
    RETURNING``) increments the spend AND reads back the NEW total in one statement, so concurrent
    reservations cannot both pass a stale read (no TOCTOU) -- it is the RETURNING value, not a
    separate read-after-write, that decides the cap. If the NEW total exceeds the cap the
    reservation (and its run) is refunded and ``allowed=False`` is returned. The caller MUST commit.
    Returns the post-reserve status dict with ``allowed`` and ``estimate`` (the amount actually
    held; 0.0 when blocked)."""
    wk = week_iso(now)
    stamp = _stamp(now)
    # Validate inputs BEFORE touching the ledger: a finite cap >= 0 and a finite estimate > 0. A
    # negative / NaN / inf estimate could otherwise mint budget credit (negative spend) or void the
    # cap; reject it up front so the reserve-then-reconcile ceiling stays a true ceiling.
    cap = meme_weekly_cap(conn, org_id) if cap is None else _require_finite(cap, "cap", positive=False)
    est = _require_finite(estimate, "estimate", positive=True)

    row = conn.execute(
        text("INSERT INTO operator_meme_budget "
             "  (operator_handle, org_id, week_iso, spend_usd, runs, updated_at) "
             "VALUES (:h, :o, :w, :amt, 1, :now) "
             "ON CONFLICT(operator_handle, org_id, week_iso) DO UPDATE SET "
             "  spend_usd = operator_meme_budget.spend_usd + :amt, "
             "  runs = operator_meme_budget.runs + 1, "
             "  updated_at = :now "
             "RETURNING spend_usd, runs"),
        {"h": operator_handle, "o": org_id, "w": wk, "amt": est, "now": stamp},
    ).fetchone()
    # The post-increment total comes from the write statement's RETURNING -- atomic with the
    # increment, NOT a separate read -- so the cap decision has no read-after-write TOCTOU
    # (mirrors replies.reserve_generation; race-safe without depending on caller isolation level).
    spend = float(row[0] or 0.0)
    if spend > cap:
        # Over the cap -> release the reservation we just took (and its run) so the stored spend
        # reflects only real, allowed produces.
        conn.execute(
            text("UPDATE operator_meme_budget SET "
                 "  spend_usd = operator_meme_budget.spend_usd - :amt, "
                 "  runs = operator_meme_budget.runs - 1, updated_at = :now "
                 "WHERE operator_handle = :h AND org_id = :o AND week_iso = :w"),
            {"h": operator_handle, "o": org_id, "w": wk, "amt": est, "now": stamp},
        )
        st = operator_meme_status(conn, operator_handle, org_id, cap=cap, now=now)
        st["allowed"] = False
        st["estimate"] = 0.0
        return st

    st = operator_meme_status(conn, operator_handle, org_id, cap=cap, now=now)
    st["allowed"] = True
    st["estimate"] = est
    return st


def reconcile_meme_spend(conn, operator_handle: str, org_id: str, *,
                         estimate: float, actual: float, now: Optional[datetime] = None) -> dict:
    """Adjust a held reservation from ``estimate`` to the call's ``actual`` cost (delta = actual -
    estimate, normally negative). Call after a successful ``reserve`` (allowed=True) + produce;
    ``actual=0.0`` fully unwinds the estimate (the producer was disabled / no call happened). The
    net effect across reserve+reconcile is exactly +``actual`` so stored spend stays >= 0. The
    caller MUST commit. Returns the fresh status dict.

    FAIL CLOSED: inputs must be a finite estimate > 0 and a finite actual >= 0, and the reconcile
    REQUIRES an existing reservation row for (operator, org, week) -- a reconcile is only ever valid
    after a reserve created that row. A reconcile with NO matching row (caller skipped reserve, or is
    reconciling the wrong key) raises (no write): without that guard an ``actual >= estimate`` against
    an absent (0,0) row would compute a non-negative ``new_spend``, match ZERO rows in the UPDATE, and
    return a clean zero-spend status -- silently DROPPING the spend and weakening the cap. It also
    REFUSES if it would drive ``spend_usd`` below 0 (e.g. a double-reconcile) -- a repeated/mismatched
    reconcile would otherwise mint negative spend = budget credit. The caller (serve layer) treats a
    raised reconcile as a held reservation (logged), never a silent corruption."""
    wk = week_iso(now)
    stamp = _stamp(now)
    est = _require_finite(estimate, "estimate", positive=True)
    act = _require_finite(actual, "actual", positive=False)
    delta = act - est
    # Read-then-write is safe: reconcile runs inside the caller's serialized (immediate) txn, so no
    # concurrent reserve can interleave. REQUIRE the reservation row to EXIST first -- ``_current``
    # collapses an absent row to (0.0, 0), so it cannot distinguish "no reserve" from "reserved $0";
    # query the row directly and fail closed when it is missing (else an actual >= estimate would
    # silently vanish against a zero-row UPDATE).
    row = conn.execute(
        text("SELECT spend_usd FROM operator_meme_budget "
             "WHERE operator_handle = :h AND org_id = :o AND week_iso = :w"),
        {"h": operator_handle, "o": org_id, "w": wk},
    ).fetchone()
    if row is None:
        raise ValueError(
            "reconcile_meme_spend: no reservation row for "
            f"({operator_handle!r}, {org_id!r}, {wk}) -- refusing "
            "(reconcile without a matching reserve, or wrong key?)"
        )
    spend_now = float(row[0] or 0.0)
    new_spend = spend_now + delta
    # Refuse anything that would push spend below 0 (with a float-dust tolerance), and clamp a
    # sub-epsilon residual to exactly 0 so the >= 0 CHECK holds.
    if new_spend < -_SPEND_EPS:
        raise ValueError(
            "reconcile_meme_spend: would drive spend_usd below 0 "
            f"(current={spend_now}, delta={delta}) -- refusing (double-reconcile or no reserve?)"
        )
    new_spend = max(0.0, new_spend)
    res = conn.execute(
        text("UPDATE operator_meme_budget SET spend_usd = :s, updated_at = :now "
             "WHERE operator_handle = :h AND org_id = :o AND week_iso = :w"),
        {"h": operator_handle, "o": org_id, "w": wk, "s": new_spend, "now": stamp},
    )
    if (res.rowcount or 0) == 0:
        # The row existed at the SELECT above but the guarded UPDATE matched nothing -- the
        # reservation vanished mid-reconcile. Fail closed rather than silently drop the spend.
        raise ValueError(
            "reconcile_meme_spend: guarded UPDATE affected 0 rows for "
            f"({operator_handle!r}, {org_id!r}, {wk}) -- reservation row vanished mid-reconcile"
        )
    return operator_meme_status(conn, operator_handle, org_id, now=now)
