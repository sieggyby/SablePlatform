"""Client-onboarding CRUD (migration 073: client_intake / client_accounts /
client_docs / org_entitlements). The data layer under the `onboard` CLI + the pure
status/scaffold core (docs/CLIENT_ONBOARDING_PLAN.md).

OPS-ONLY: these tables hold client PII + commercial state. They are read by /ops + the
CLI only; SableWeb's `assembleClientData()` must NEVER join them (PLAN §1.6).

Writers commit (matching the rest of `db/*`). The org row must already exist (FK ->
orgs) — `onboard init` upserts a draft org via `orgs.upsert_client_org` BEFORE the
manifest header (PLAN §1.0), so callers never hit the FK.
"""
from __future__ import annotations

import json

from sqlalchemy import text


def _iso_z() -> str:
    # Match the migration's strftime('%Y-%m-%dT%H:%M:%SZ','now') column default shape.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result.fetchall()]


def _one(result) -> dict | None:
    row = result.fetchone()
    return dict(row._mapping) if row is not None else None


# --- client_intake (the manifest header) -----------------------------------
_INTAKE_FIELDS = (
    "manifest_status",
    "primary_contact_name",
    "primary_contact_email",
    "primary_contact_telegram",
    "website_url",
    "notes",
)


def upsert_intake(conn, org_id: str, **fields) -> None:
    """Create or update the manifest header. Only the keys in ``fields`` are written
    (unknown keys raise). On a new row, `manifest_status` defaults to 'draft'."""
    bad = set(fields) - set(_INTAKE_FIELDS)
    if bad:
        raise ValueError(f"unknown client_intake field(s): {sorted(bad)}")
    existing = get_intake(conn, org_id)
    if existing is None:
        cols = ["org_id", *fields.keys()]
        placeholders = ", ".join(f":{c}" for c in cols)
        conn.execute(
            text(f"INSERT INTO client_intake ({', '.join(cols)}) VALUES ({placeholders})"),
            {"org_id": org_id, **fields},
        )
    elif fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        conn.execute(
            text(
                f"UPDATE client_intake SET {sets}, updated_at = :now WHERE org_id = :org_id"
            ),
            {"org_id": org_id, "now": _iso_z(), **fields},
        )
    conn.commit()


def get_intake(conn, org_id: str) -> dict | None:
    return _one(
        conn.execute(
            text("SELECT * FROM client_intake WHERE org_id = :org_id"), {"org_id": org_id}
        )
    )


def set_manifest_status(conn, org_id: str, status: str) -> None:
    if status not in ("draft", "ready", "applied"):
        raise ValueError(f"invalid manifest_status: {status!r}")
    conn.execute(
        text(
            "UPDATE client_intake SET manifest_status = :s, updated_at = :now "
            "WHERE org_id = :org_id"
        ),
        {"s": status, "now": _iso_z(), "org_id": org_id},
    )
    conn.commit()


# --- client_accounts (the unified handle registry) -------------------------
def add_account(
    conn,
    org_id: str,
    platform: str,
    handle: str,
    role: str,
    *,
    controlled: bool = False,
    display_name: str | None = None,
    bio: str | None = None,
    notes: str | None = None,
) -> None:
    """Upsert a handle on the natural key (org_id, platform, handle). Re-adding the same
    handle updates its role + controlled flag; the optional metadata (display_name/bio/
    notes) is COALESCE-preserved -- a re-add to correct a role never silently nulls an
    existing bio. Pass an empty string (not None) to deliberately clear a metadata field."""
    conn.execute(
        text(
            "INSERT INTO client_accounts "
            "(org_id, platform, handle, role, controlled, display_name, bio, notes) "
            "VALUES (:org_id, :platform, :handle, :role, :controlled, :display_name, :bio, :notes) "
            "ON CONFLICT (org_id, platform, handle) DO UPDATE SET "
            "  role = excluded.role, controlled = excluded.controlled, "
            "  display_name = COALESCE(excluded.display_name, client_accounts.display_name), "
            "  bio = COALESCE(excluded.bio, client_accounts.bio), "
            "  notes = COALESCE(excluded.notes, client_accounts.notes)"
        ),
        {
            "org_id": org_id,
            "platform": platform,
            "handle": handle,
            "role": role,
            "controlled": 1 if controlled else 0,
            "display_name": display_name,
            "bio": bio,
            "notes": notes,
        },
    )
    conn.commit()


def list_accounts(
    conn, org_id: str, *, platform: str | None = None, controlled_only: bool = False
) -> list[dict]:
    sql = "SELECT * FROM client_accounts WHERE org_id = :org_id"
    params: dict = {"org_id": org_id}
    if platform is not None:
        sql += " AND platform = :platform"
        params["platform"] = platform
    if controlled_only:
        sql += " AND controlled = 1"
    sql += " ORDER BY platform, handle"
    return _rows(conn.execute(text(sql), params))


def remove_account(conn, org_id: str, platform: str, handle: str) -> None:
    conn.execute(
        text(
            "DELETE FROM client_accounts "
            "WHERE org_id = :org_id AND platform = :platform AND handle = :handle"
        ),
        {"org_id": org_id, "platform": platform, "handle": handle},
    )
    conn.commit()


# --- client_docs (explainer/bio/voice pointers) ----------------------------
def add_doc(
    conn, org_id: str, kind: str, label: str, location: str, *, notes: str | None = None
) -> int:
    """Append a doc pointer. Returns the new row id. (Docs are not deduped — a project
    may have several explainers; `scaffold` registers local files idempotently by
    checking `list_docs` first.)"""
    result = conn.execute(
        text(
            "INSERT INTO client_docs (org_id, kind, label, location, notes) "
            "VALUES (:org_id, :kind, :label, :location, :notes) RETURNING id"
        ),
        {"org_id": org_id, "kind": kind, "label": label, "location": location, "notes": notes},
    )
    doc_id = result.fetchone()[0]
    conn.commit()
    return int(doc_id)


def list_docs(conn, org_id: str, *, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM client_docs WHERE org_id = :org_id"
    params: dict = {"org_id": org_id}
    if kind is not None:
        sql += " AND kind = :kind"
        params["kind"] = kind
    sql += " ORDER BY id"
    return _rows(conn.execute(text(sql), params))


def remove_doc(conn, doc_id: int) -> None:
    conn.execute(text("DELETE FROM client_docs WHERE id = :id"), {"id": doc_id})
    conn.commit()


# --- org_entitlements (the SKU/entitlement ledger -- STATE only, never $) ---
def set_entitlement(
    conn,
    org_id: str,
    service_key: str,
    *,
    tier: str | None = None,
    status: str = "active",
    started_at: str | None = None,
    ended_at: str | None = None,
    config: dict | None = None,
    notes: str | None = None,
) -> None:
    """Upsert an entitlement on (org_id, service_key). `status` ∈
    {trial, active, paused, ended}. `config` is a per-service knob dict (NOT money)."""
    if status not in ("trial", "active", "paused", "ended"):
        raise ValueError(f"invalid entitlement status: {status!r}")
    conn.execute(
        text(
            "INSERT INTO org_entitlements "
            "(org_id, service_key, tier, status, started_at, ended_at, config_json, notes) "
            "VALUES (:org_id, :service_key, :tier, :status, :started_at, :ended_at, "
            ":config_json, :notes) "
            "ON CONFLICT (org_id, service_key) DO UPDATE SET "
            "  tier = excluded.tier, status = excluded.status, "
            "  started_at = excluded.started_at, ended_at = excluded.ended_at, "
            "  config_json = excluded.config_json, notes = excluded.notes, updated_at = :now"
        ),
        {
            "org_id": org_id,
            "service_key": service_key,
            "tier": tier,
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at,
            "config_json": json.dumps(config or {}),
            "notes": notes,
            "now": _iso_z(),
        },
    )
    conn.commit()


def list_entitlements(conn, org_id: str, *, active_only: bool = False) -> list[dict]:
    """All entitlements for an org. `active_only` returns the live ones (status ∈
    {trial, active}) — the set `onboard status` reasons over for required inputs."""
    sql = "SELECT * FROM org_entitlements WHERE org_id = :org_id"
    if active_only:
        sql += " AND status IN ('trial', 'active')"
    sql += " ORDER BY service_key"
    return _rows(conn.execute(text(sql), {"org_id": org_id}))


def remove_entitlement(conn, org_id: str, service_key: str) -> None:
    conn.execute(
        text(
            "DELETE FROM org_entitlements WHERE org_id = :org_id AND service_key = :service_key"
        ),
        {"org_id": org_id, "service_key": service_key},
    )
    conn.commit()
