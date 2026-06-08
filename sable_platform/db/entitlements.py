"""Entitlement enforcement (ONBOARDING_PHASE2_PLAN.md P2). DORMANT BY DEFAULT.

`has_entitlement` returns True (ALLOW) UNLESS all hold: (1) the `ENTITLEMENT_ENFORCEMENT`
process-env flag is on (default OFF), (2) the org has ≥1 ACTIVE entitlement row (un-onboarded
orgs have zero → always allowed even with the flag on), and (3) this service_key is not active
for the org. Any exception → ALLOW (fail-open). This DOUBLE GUARD (global flag + per-org
has-active-rows) makes flipping the flag unable to break a live client that hasn't been
explicitly entitled.

The master switch is process-env ONLY — NEVER `orgs.config_json` (a client can write that via
the onboarding CLI, so the master switch must be operator-process-controlled, like
SABLE_OPERATOR_ID). Per-SKU knobs live in `org_entitlements.config_json`; the master switch does
not. `active := status IN ('trial','active')` (the ONE definition, shared with
`onboarding.list_entitlements(active_only=True)`).
"""
from __future__ import annotations

import logging
import os

ACTIVE = ("trial", "active")
log = logging.getLogger(__name__)


def enforcement_enabled() -> bool:
    """The global kill-switch — process env only (default OFF). A client cannot set it."""
    return os.environ.get("ENTITLEMENT_ENFORCEMENT", "").strip().lower() in ("1", "true", "yes", "on")


def has_entitlement(conn, org_id: str, service_key: str) -> bool:
    """True = ALLOW the service to run for this org. See the module docstring for the
    double-guard truth table. Fail-open on any error so a DB blip never starves a client."""
    if not enforcement_enabled():
        return True  # dormant: zero behavior change until an operator flips the flag
    try:
        from sable_platform.db.onboarding import list_entitlements

        active = list_entitlements(conn, org_id, active_only=True)  # status IN ('trial','active')
        if not active:
            return True  # un-/de-onboarded org (0 active rows) → allow (can't break a live client)
        return any(r["service_key"] == service_key for r in active)
    except Exception:
        # Fail-open AND clear a poisoned transaction (T2-3): on Postgres a failed SELECT
        # leaves `conn` in InFailedSqlTransaction, which would cascade to every later query
        # on the same conn (e.g. the rest of a sweep). Roll back best-effort before allowing.
        try:
            conn.rollback()
        except Exception:
            pass
        log.exception(
            "has_entitlement check failed for org=%r sku=%r; allowing (fail-open)", org_id, service_key
        )
        return True


def filter_entitled(conn, org_ids, service_key: str) -> list[str]:
    """Keep only the orgs entitled to ``service_key`` (or ALL when enforcement is off — a
    pure pass-through, so a caller's default result set is unchanged with the flag off)."""
    if not enforcement_enabled():
        return list(org_ids)
    return [o for o in org_ids if has_entitlement(conn, o, service_key)]
