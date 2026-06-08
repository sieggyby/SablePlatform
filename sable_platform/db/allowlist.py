"""DB-backed SableWeb allowlist CRUD (migration 075, ONBOARDING_PHASE2_PLAN.md P1).

The operator surface that replaces editing `ALLOWLIST_JSON` + redeploy. SableWeb merges
these rows UNDER env/file (env/file always win) — a row here is ADDITIVE only and can
never escalate above or lock out an env/file user. **AUTH table — OPS-ONLY, never on
/client.** `email` is stored lowercased (mirrors SableWeb's lookup normalization, so a
mixed-case write can't silently miss the lowercased lookup). Writers commit.
"""
from __future__ import annotations

import json

from sqlalchemy import text

ROLES = ("admin", "operator", "client", "client_ops")


def _iso_z() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(result) -> list[dict]:
    out = []
    for r in result.fetchall():
        d = dict(r._mapping)
        if d.get("assigned_orgs"):
            try:
                d["assigned_orgs"] = json.loads(d["assigned_orgs"])
            except (TypeError, ValueError):
                d["assigned_orgs"] = None
        out.append(d)
    return out


def upsert_entry(
    conn,
    email: str,
    role: str,
    *,
    operator_id: str | None = None,
    org: str | None = None,
    assigned_orgs: list[str] | None = None,
    enabled: bool = True,
    notes: str | None = None,
) -> str:
    """Create or update an allowlist entry (upsert on the lowercased email PK). Validates
    role + the role's required fields (admin/operator need operator_id; client/client_ops
    need org). Raises ValueError on invalid input. Returns the normalized email."""
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email is required")
    if role not in ROLES:
        raise ValueError(f"invalid role {role!r}; must be one of {ROLES}")
    if role in ("admin", "operator") and not operator_id:
        raise ValueError(f"role {role!r} requires --operator-id")
    if role in ("client", "client_ops") and not org:
        raise ValueError(f"role {role!r} requires --org")
    aos = json.dumps(list(assigned_orgs)) if assigned_orgs else None
    conn.execute(
        text(
            "INSERT INTO allowlist_entries "
            "(email, role, operator_id, org, assigned_orgs, enabled, notes) "
            "VALUES (:email, :role, :operator_id, :org, :assigned_orgs, :enabled, :notes) "
            "ON CONFLICT (email) DO UPDATE SET "
            "  role = excluded.role, operator_id = excluded.operator_id, org = excluded.org, "
            "  assigned_orgs = excluded.assigned_orgs, enabled = excluded.enabled, "
            "  notes = excluded.notes, updated_at = :now"
        ),
        {
            "email": email,
            "role": role,
            "operator_id": operator_id,
            "org": org,
            "assigned_orgs": aos,
            "enabled": 1 if enabled else 0,
            "notes": notes,
            "now": _iso_z(),
        },
    )
    conn.commit()
    return email


def get_entry(conn, email: str) -> dict | None:
    rows = _rows(
        conn.execute(
            text("SELECT * FROM allowlist_entries WHERE email = :email"),
            {"email": (email or "").strip().lower()},
        )
    )
    return rows[0] if rows else None


def list_entries(conn, *, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM allowlist_entries"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY email"
    return _rows(conn.execute(text(sql)))


def set_enabled(conn, email: str, enabled: bool) -> int:
    """Soft enable/disable. Returns rows affected (0 = no such entry). NB: disabling stops
    NEW logins within SableWeb's cache TTL — it does NOT revoke a live JWT before expiry."""
    result = conn.execute(
        text(
            "UPDATE allowlist_entries SET enabled = :en, updated_at = :now WHERE email = :email"
        ),
        {"en": 1 if enabled else 0, "now": _iso_z(), "email": (email or "").strip().lower()},
    )
    conn.commit()
    return result.rowcount or 0


def remove_entry(conn, email: str) -> int:
    result = conn.execute(
        text("DELETE FROM allowlist_entries WHERE email = :email"),
        {"email": (email or "").strip().lower()},
    )
    conn.commit()
    return result.rowcount or 0
