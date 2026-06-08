"""Org-row helpers (SP-owned org creation).

Promoted here from ``SableKOL/sable_kol/wizard_orgs.py`` so org creation lives in
the platform that owns the ``orgs`` schema (per SablePlatform CLAUDE.md). Both the
KOL wizard and the community-audit bot create *prospect* orgs the same way, and the
``orgs`` table has NO ``org_type``/``is_active`` column (verified against
``migrations/001_initial.sql`` — Codex round-2 #3), so prospects use:

    status      = 'inactive'   (operator promotes via `sable-platform org config set`)
    config_json = {"org_type": "prospect", "created_via": <caller>, ...}

The prospect AI cost cap is written under the SAME key ``get_org_cost_cap`` reads
(``max_ai_usd_per_org_per_week`` — see ``db/cost.py``) so ``check_budget`` bounds the
prospect org. Do NOT invent a new cap key; ``get_org_cost_cap`` cannot read it.
"""
from __future__ import annotations

import json

from sqlalchemy import text

# Default weekly AI spend cap for an auto-created prospect org. Low by design — a
# self-invite community-audit prospect is not a paying client. Written under the
# existing cap key so cost.get_org_cost_cap reads it without a code change.
PROSPECT_AI_USD_PER_WEEK = 0.50


# --- org config_json validation (shared by `org config set` + `onboard apply`) ------
# Extracted from the org_config_set click command so both the CLI and the onboarding
# `apply` step validate identically (CLIENT_ONBOARDING_PLAN.md §5 step 3).
VALID_SECTORS = {
    "DeFi", "DeSci", "Gaming", "Infrastructure", "L1/L2", "Social", "DAO", "NFT", "AI", "Other",
}
VALID_STAGES = {"pre_launch", "launch", "growth", "mature", "declining"}
_NUMERIC_RANGES: dict[str, tuple[float, float]] = {
    "tracking_stale_days": (1, 365),
    "discord_pulse_stale_days": (1, 365),
    "stuck_run_threshold_hours": (0.5, 168),
    "decay_warning_threshold": (0.0, 1.0),
    "decay_critical_threshold": (0.0, 1.0),
    "bridge_centrality_threshold": (0.0, 1.0),
    "bridge_decay_threshold": (0.0, 1.0),
    "discord_pulse_regression_threshold": (0.0, 1.0),
    "max_ai_usd_per_org_per_week": (0.0, 10000.0),
}


def validate_org_config(key: str, value: str):
    """Validate + coerce a single `config_json` key/value. Returns the parsed value
    (float for numeric keys, str otherwise). Raises ``ValueError`` with a human message
    on an invalid sector/stage/number/out-of-range (the caller echoes + exits)."""
    if key == "sector" and value not in VALID_SECTORS:
        raise ValueError(f"Invalid sector '{value}'. Valid: {', '.join(sorted(VALID_SECTORS))}")
    if key == "stage" and value not in VALID_STAGES:
        raise ValueError(f"Invalid stage '{value}'. Valid: {', '.join(sorted(VALID_STAGES))}")
    if key in _NUMERIC_RANGES:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Key '{key}' expects a numeric value.")
        lo, hi = _NUMERIC_RANGES[key]
        if not (lo <= parsed <= hi):
            raise ValueError(f"Value {parsed} out of range for '{key}' (must be {lo}–{hi}).")
        return parsed
    return value


def set_org_config(conn, org_id: str, key: str, value: str):
    """Validate (via ``validate_org_config``) then merge KEY=VALUE into the org's
    ``config_json``. Raises ``ValueError`` if the org doesn't exist or validation fails.
    Commits. Returns the parsed value."""
    parsed = validate_org_config(key, value)
    row = conn.execute(
        text("SELECT config_json FROM orgs WHERE org_id = :org_id"), {"org_id": org_id}
    ).fetchone()
    if row is None:
        raise ValueError(f"Org '{org_id}' not found.")
    cfg = json.loads(row[0]) if row[0] else {}
    cfg[key] = parsed
    conn.execute(
        text("UPDATE orgs SET config_json = :cfg, updated_at = CURRENT_TIMESTAMP WHERE org_id = :org_id"),
        {"cfg": json.dumps(cfg), "org_id": org_id},
    )
    conn.commit()
    return parsed


def upsert_prospect_org(
    conn,
    *,
    org_id: str,
    display_name: str,
    twitter_handle: str | None = None,
    created_via: str = "community_audit",
    config_extra: dict | None = None,
) -> None:
    """Insert (or update) a prospect org row, idempotently.

    New row: ``status='inactive'`` + ``config_json`` with ``org_type='prospect'``,
    ``created_via``, and the prospect cap. Existing row: preserve the operator-set
    ``status`` and any existing config keys (``setdefault``), only refresh the
    provenance keys + ``twitter_handle`` (NULL-safe via COALESCE). Caller does not
    need to manage the transaction — this commits.
    """
    row = conn.execute(
        text("SELECT config_json, status FROM orgs WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).fetchone()

    if row is None:
        cfg = {
            "org_type": "prospect",
            "created_via": created_via,
            "max_ai_usd_per_org_per_week": PROSPECT_AI_USD_PER_WEEK,
        }
        if config_extra:
            cfg.update(config_extra)
        conn.execute(
            text(
                "INSERT INTO orgs (org_id, display_name, twitter_handle, status, config_json) "
                "VALUES (:org_id, :display_name, :twitter_handle, 'inactive', :config_json)"
            ),
            {
                "org_id": org_id,
                "display_name": display_name,
                "twitter_handle": twitter_handle,
                "config_json": json.dumps(cfg),
            },
        )
        conn.commit()
        return

    # Org already exists (re-consent, or a since-promoted client) — preserve
    # operator-set status/config, only refresh provenance + handle.
    cfg = json.loads(row[0] or "{}")
    cfg.setdefault("org_type", "prospect")
    cfg.setdefault("created_via", created_via)
    cfg.setdefault("max_ai_usd_per_org_per_week", PROSPECT_AI_USD_PER_WEEK)
    if config_extra:
        for k, v in config_extra.items():
            cfg.setdefault(k, v)
    conn.execute(
        text(
            "UPDATE orgs SET "
            "  twitter_handle = COALESCE(:twitter_handle, twitter_handle), "
            "  config_json = :config_json, "
            "  updated_at = CURRENT_TIMESTAMP "
            "WHERE org_id = :org_id"
        ),
        {
            "org_id": org_id,
            "twitter_handle": twitter_handle,
            "config_json": json.dumps(cfg),
        },
    )
    conn.commit()


def upsert_client_org(
    conn,
    *,
    org_id: str,
    display_name: str,
    status: str | None = None,
    twitter_handle: str | None = None,
    discord_server_id: str | None = None,
    created_via: str = "onboarding",
    config_extra: dict | None = None,
) -> None:
    """Create or update a CLIENT org row, idempotently. The canonical writer for the
    onboarding flow (docs/CLIENT_ONBOARDING_PLAN.md §1.0).

    This is NOT ``upsert_prospect_org`` and must not be confused with it: that one
    force-stamps ``status='inactive'`` + a $0.50/week prospect cap
    (``max_ai_usd_per_org_per_week``), which would silently cap a PAYING client. This
    one stamps ``org_type='client'`` and NO cost cap (the client's cap is set
    separately via the validated ``org config set`` path during ``apply``).

    ``status`` semantics (COALESCE-on-update so it never accidentally downgrades):
      - new row: ``status`` if given, else ``'inactive'`` (a draft created by
        ``onboard init`` before ``apply`` activates it).
      - existing row: ``status`` is changed ONLY if explicitly passed (None preserves
        the operator-set status) — so ``apply`` passes ``'active'`` to go live, while a
        re-run of ``init`` (status=None) never deactivates an already-live org.

    ``twitter_handle``/``discord_server_id`` are FILL-ONLY on an existing row
    (``COALESCE(existing, :new)``) — the registry projection FILLS a NULL handle but
    NEVER overwrites a handle a live org already has (audit T1-A: prevents ``apply`` from
    silently flipping e.g. ``RobotMoneyAgent`` -> ``@RobotMoneyAgent``). To deliberately
    change a set handle, use ``onboard set``/``org`` explicitly. Commits.
    """
    row = conn.execute(
        text("SELECT config_json FROM orgs WHERE org_id = :org_id"),
        {"org_id": org_id},
    ).fetchone()

    if row is None:
        cfg = {"org_type": "client", "created_via": created_via}
        if config_extra:
            cfg.update(config_extra)
        conn.execute(
            text(
                "INSERT INTO orgs "
                "(org_id, display_name, twitter_handle, discord_server_id, status, config_json) "
                "VALUES (:org_id, :display_name, :twitter_handle, :discord_server_id, "
                ":status, :config_json)"
            ),
            {
                "org_id": org_id,
                "display_name": display_name,
                "twitter_handle": twitter_handle,
                "discord_server_id": discord_server_id,
                "status": status or "inactive",
                "config_json": json.dumps(cfg),
            },
        )
        conn.commit()
        return

    # Existing row (a prospect being onboarded, or a re-apply): flip org_type to client,
    # set display_name + handles (NULL-safe), change status only if explicitly given.
    cfg = json.loads(row[0] or "{}")
    was_prospect = cfg.get("org_type") == "prospect"
    cfg["org_type"] = "client"
    cfg.setdefault("created_via", created_via)
    if was_prospect:
        # Converting a prospect -> client: drop the auto-stamped $0.50 prospect throttle
        # (`upsert_prospect_org` set it). Leaving it would silently cap a PAYING client,
        # since cost.get_org_cost_cap reads this key. The client's real cap is set via the
        # validated `org config set` path during `apply`. Only cleared on the prospect
        # flip -- an existing client's operator-set cap is never touched.
        cfg.pop("max_ai_usd_per_org_per_week", None)
    if config_extra:
        for k, v in config_extra.items():
            cfg.setdefault(k, v)
    conn.execute(
        text(
            "UPDATE orgs SET "
            "  display_name = :display_name, "
            "  status = COALESCE(:status, status), "
            # FILL-ONLY: keep an existing non-NULL handle; only fill a NULL one (audit T1-A).
            "  twitter_handle = COALESCE(twitter_handle, :twitter_handle), "
            "  discord_server_id = COALESCE(discord_server_id, :discord_server_id), "
            "  config_json = :config_json, "
            "  updated_at = CURRENT_TIMESTAMP "
            "WHERE org_id = :org_id"
        ),
        {
            "org_id": org_id,
            "display_name": display_name,
            "status": status,
            "twitter_handle": twitter_handle,
            "discord_server_id": discord_server_id,
            "config_json": json.dumps(cfg),
        },
    )
    conn.commit()
