"""DB helpers for the community-audit bot (sable-audit), migration 067.

SP owns the ``community_audit_*`` tables; sable-audit is a thin client that imports
these helpers (the sable-roles model). All SQL is ``text()``-wrapped with ``:named``
params; rows are read positionally / via ``_row_to_dict`` (CompatConnection-safe).
Helpers take an open connection first and commit themselves unless noted.

Key design (see sable-audit/PLAN.md §6.1, R3-N2): the contributor leaderboard score
is DERIVED by COUNT over the reaction-existence ledger — ADD upserts a ledger row,
REMOVE deletes it, so reaction removal correctly decrements. ``community_audit_member_scores``
is a materialized cache, always recomputable from the ledger, never an authoritative
monotonic counter.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text


def _iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


# ---------------------------------------------------------------------------
# Guilds — one row per joined guild. org_id is NULL until consent.
# ---------------------------------------------------------------------------
def record_guild_join(conn, guild_id: str, invited_by: str | None = None) -> None:
    """Idempotently record a guild the bot joined. Re-invite reuses the row and
    preserves any prior consent/org (only refreshes invited_by + updated_at)."""
    conn.execute(
        text(
            "INSERT INTO community_audit_guilds (guild_id, invited_by, updated_at) "
            "VALUES (:guild_id, :invited_by, :now) "
            "ON CONFLICT (guild_id) DO UPDATE SET "
            "  invited_by = COALESCE(excluded.invited_by, community_audit_guilds.invited_by), "
            "  updated_at = excluded.updated_at"
        ),
        {"guild_id": guild_id, "invited_by": invited_by, "now": _iso_z()},
    )
    conn.commit()


def get_guild(conn, guild_id: str) -> dict | None:
    row = conn.execute(
        text("SELECT * FROM community_audit_guilds WHERE guild_id = :guild_id"),
        {"guild_id": guild_id},
    ).fetchone()
    return _row_to_dict(row)


def set_consent(conn, guild_id: str, org_id: str) -> None:
    """Record disclosure acceptance: stamp consent_at + bind the org_id (the
    prospect org created via orgs.upsert_prospect_org, or a claimed client org).
    Only sets consent_at the first time (idempotent re-consent keeps the original)."""
    now = _iso_z()
    conn.execute(
        text(
            "UPDATE community_audit_guilds SET "
            "  org_id = :org_id, "
            "  consent_at = COALESCE(consent_at, :now), "
            "  updated_at = :now "
            "WHERE guild_id = :guild_id"
        ),
        {"guild_id": guild_id, "org_id": org_id, "now": now},
    )
    conn.commit()


def mark_audited(conn, guild_id: str) -> None:
    now = _iso_z()
    conn.execute(
        text(
            "UPDATE community_audit_guilds SET last_audit_at = :now, updated_at = :now "
            "WHERE guild_id = :guild_id"
        ),
        {"guild_id": guild_id, "now": now},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Runs / findings / checks / settings
# ---------------------------------------------------------------------------
def create_run(conn, guild_id: str, kind: str, tier: str = "free") -> int:
    """Create an audit run (kind: 'metadata' | 'deep'). Returns the run_id."""
    result = conn.execute(
        text(
            "INSERT INTO community_audit_runs (guild_id, kind, tier, started_at, created_at) "
            "VALUES (:guild_id, :kind, :tier, :now, :now) RETURNING id"
        ),
        {"guild_id": guild_id, "kind": kind, "tier": tier, "now": _iso_z()},
    )
    run_id = result.fetchone()[0]
    conn.commit()
    return int(run_id)


def finish_run(
    conn,
    run_id: int,
    *,
    status: str = "ok",
    overall_grade: str | None = None,
    category_grades_json: str = "{}",
    messages_analyzed: int = 0,
    channels_active: int = 0,
    channels_dead: int = 0,
    span_start: str | None = None,
) -> None:
    """Finalize a run. overall_grade is NULL unless the grade-suppression rule
    (PLAN §C5) is satisfied — callers pass None when a category is insufficient."""
    conn.execute(
        text(
            "UPDATE community_audit_runs SET "
            "  status = :status, overall_grade = :overall_grade, "
            "  category_grades_json = :category_grades_json, "
            "  messages_analyzed = :messages_analyzed, channels_active = :channels_active, "
            "  channels_dead = :channels_dead, span_start = :span_start, finished_at = :now "
            "WHERE id = :run_id"
        ),
        {
            "run_id": run_id,
            "status": status,
            "overall_grade": overall_grade,
            "category_grades_json": category_grades_json,
            "messages_analyzed": messages_analyzed,
            "channels_active": channels_active,
            "channels_dead": channels_dead,
            "span_start": span_start,
            "now": _iso_z(),
        },
    )
    conn.commit()


def add_finding(
    conn,
    run_id: int,
    *,
    category: str,
    type: str,
    title: str,
    severity: str = "info",
    plain_detail: str | None = None,
    message_ref: str | None = None,
    confidence: float | None = None,
) -> int:
    """Record one plain-language finding. message_ref is a jump-link, never the
    verbatim message text (free-tier privacy, PLAN R4)."""
    result = conn.execute(
        text(
            "INSERT INTO community_audit_findings "
            "(run_id, category, severity, type, title, plain_detail, message_ref, confidence) "
            "VALUES (:run_id, :category, :severity, :type, :title, :plain_detail, "
            ":message_ref, :confidence) RETURNING id"
        ),
        {
            "run_id": run_id,
            "category": category,
            "severity": severity,
            "type": type,
            "title": title,
            "plain_detail": plain_detail,
            "message_ref": message_ref,
            "confidence": confidence,
        },
    )
    finding_id = result.fetchone()[0]
    conn.commit()
    return int(finding_id)


def record_security_check(
    conn, run_id: int, check_key: str, status: str, detail: str | None = None
) -> None:
    """status in ('pass','warn','fail')."""
    conn.execute(
        text(
            "INSERT INTO community_audit_security_checks (run_id, check_key, status, detail) "
            "VALUES (:run_id, :check_key, :status, :detail)"
        ),
        {"run_id": run_id, "check_key": check_key, "status": status, "detail": detail},
    )
    conn.commit()


def save_settings_snapshot(conn, run_id: int, **fields) -> None:
    """Upsert the Identity & Polish snapshot for a run (one per run)."""
    cols = {
        "boost_level": 0,
        "boost_count": 0,
        "custom_emoji_count": 0,
        "soundboard_count": 0,
        "vanity_url": None,
        "has_banner": 0,
        "has_icon": 0,
        "verification_level": None,
        "description": None,
        "raw_json": "{}",
    }
    cols.update({k: v for k, v in fields.items() if k in cols})
    params = {"run_id": run_id, **cols}
    conn.execute(
        text(
            "INSERT INTO community_audit_settings_snapshot "
            "(run_id, boost_level, boost_count, custom_emoji_count, soundboard_count, "
            " vanity_url, has_banner, has_icon, verification_level, description, raw_json) "
            "VALUES (:run_id, :boost_level, :boost_count, :custom_emoji_count, "
            ":soundboard_count, :vanity_url, :has_banner, :has_icon, :verification_level, "
            ":description, :raw_json) "
            "ON CONFLICT (run_id) DO UPDATE SET "
            "  boost_level = excluded.boost_level, boost_count = excluded.boost_count, "
            "  custom_emoji_count = excluded.custom_emoji_count, "
            "  soundboard_count = excluded.soundboard_count, vanity_url = excluded.vanity_url, "
            "  has_banner = excluded.has_banner, has_icon = excluded.has_icon, "
            "  verification_level = excluded.verification_level, "
            "  description = excluded.description, raw_json = excluded.raw_json"
        ),
        params,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Reaction ledger + DERIVED leaderboard (R3-N2)
# ---------------------------------------------------------------------------
def add_reaction(
    conn, guild_id: str, post_id: str, reactor_id: str, emoji: str, author_id: str
) -> None:
    """ADD: a reaction now exists. Idempotent — duplicate gateway/process deliveries
    are no-ops (PK = guild_id, post_id, reactor_id, emoji)."""
    conn.execute(
        text(
            "INSERT INTO community_audit_reaction_ledger "
            "(guild_id, post_id, reactor_id, emoji, author_id) "
            "VALUES (:guild_id, :post_id, :reactor_id, :emoji, :author_id) "
            "ON CONFLICT (guild_id, post_id, reactor_id, emoji) DO NOTHING"
        ),
        {
            "guild_id": guild_id,
            "post_id": post_id,
            "reactor_id": reactor_id,
            "emoji": emoji,
            "author_id": author_id,
        },
    )
    conn.commit()


def remove_reaction(
    conn, guild_id: str, post_id: str, reactor_id: str, emoji: str
) -> None:
    """REMOVE: the reaction no longer exists. Deleting the ledger row correctly
    decrements any derived score (the whole point of the ledger model)."""
    conn.execute(
        text(
            "DELETE FROM community_audit_reaction_ledger "
            "WHERE guild_id = :guild_id AND post_id = :post_id "
            "AND reactor_id = :reactor_id AND emoji = :emoji"
        ),
        {"guild_id": guild_id, "post_id": post_id, "reactor_id": reactor_id, "emoji": emoji},
    )
    conn.commit()


def reactions_received(conn, guild_id: str, author_id: str) -> int:
    """Derived: count of live reactions an author has received (decrements on
    removal because removal deletes the ledger row)."""
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM community_audit_reaction_ledger "
            "WHERE guild_id = :guild_id AND author_id = :author_id"
        ),
        {"guild_id": guild_id, "author_id": author_id},
    ).fetchone()
    return int(row[0]) if row else 0


def top_contributors(conn, guild_id: str, limit: int = 25) -> list[dict]:
    """Derived leaderboard: authors ranked by live reactions-received. The
    'who's who' surface — always correct from the ledger, no monotonic counter."""
    rows = conn.execute(
        text(
            "SELECT author_id, COUNT(*) AS reactions_received "
            "FROM community_audit_reaction_ledger "
            "WHERE guild_id = :guild_id "
            "GROUP BY author_id "
            "ORDER BY reactions_received DESC, author_id ASC "
            "LIMIT :limit"
        ),
        {"guild_id": guild_id, "limit": limit},
    ).fetchall()
    return [
        {"author_id": r[0], "reactions_received": int(r[1])} for r in rows
    ]


def upsert_member_score(
    conn,
    guild_id: str,
    member_id: str,
    contribution_score: float,
    *,
    components_json: str = "{}",
    last_active_at: str | None = None,
) -> None:
    """Materialize a derived score for fast reads (always recomputable from the
    ledger). Never treat this as the source of truth."""
    conn.execute(
        text(
            "INSERT INTO community_audit_member_scores "
            "(guild_id, member_id, contribution_score, components_json, last_active_at, updated_at) "
            "VALUES (:guild_id, :member_id, :contribution_score, :components_json, "
            ":last_active_at, :now) "
            "ON CONFLICT (guild_id, member_id) DO UPDATE SET "
            "  contribution_score = excluded.contribution_score, "
            "  components_json = excluded.components_json, "
            "  last_active_at = excluded.last_active_at, updated_at = excluded.updated_at"
        ),
        {
            "guild_id": guild_id,
            "member_id": member_id,
            "contribution_score": contribution_score,
            "components_json": components_json,
            "last_active_at": last_active_at,
            "now": _iso_z(),
        },
    )
    conn.commit()


def list_member_activity(conn, guild_id: str) -> list[dict]:
    """All per-member per-period activity rows for a guild (paid reactivation list)."""
    rows = conn.execute(
        text(
            "SELECT member_id, period, message_count FROM community_audit_member_activity "
            "WHERE guild_id = :guild_id"
        ),
        {"guild_id": guild_id},
    ).fetchall()
    return [
        {"member_id": r[0], "period": r[1], "message_count": int(r[2])} for r in rows
    ]


def record_member_activity(
    conn, guild_id: str, member_id: str, period: str, message_count: int
) -> None:
    """Per-member per-period message count (powers the dormant-member reactivation
    list — was-active-then-quiet needs the historical snapshot, not a flag)."""
    conn.execute(
        text(
            "INSERT INTO community_audit_member_activity "
            "(guild_id, member_id, period, message_count, updated_at) "
            "VALUES (:guild_id, :member_id, :period, :message_count, :now) "
            "ON CONFLICT (guild_id, member_id, period) DO UPDATE SET "
            "  message_count = excluded.message_count, updated_at = excluded.updated_at"
        ),
        {
            "guild_id": guild_id,
            "member_id": member_id,
            "period": period,
            "message_count": message_count,
            "now": _iso_z(),
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Rate limits / cost counters (per-guild / per-inviter / global)
# ---------------------------------------------------------------------------
def bump_rate_limit(
    conn, scope: str, key: str, window_start: str, *, count: int = 1, ai_usd: float = 0.0
) -> dict:
    """Increment the (scope, key, window_start) counter and return the new totals.
    scope in ('guild','inviter','global'). Caller compares against its ceiling
    BEFORE spending; this is the durable counter behind PLAN §6.3."""
    conn.execute(
        text(
            "INSERT INTO community_audit_rate_limits "
            "(scope, key, window_start, count, ai_usd, updated_at) "
            "VALUES (:scope, :key, :window_start, :count, :ai_usd, :now) "
            "ON CONFLICT (scope, key, window_start) DO UPDATE SET "
            "  count = community_audit_rate_limits.count + excluded.count, "
            "  ai_usd = community_audit_rate_limits.ai_usd + excluded.ai_usd, "
            "  updated_at = excluded.updated_at"
        ),
        {
            "scope": scope,
            "key": key,
            "window_start": window_start,
            "count": count,
            "ai_usd": ai_usd,
            "now": _iso_z(),
        },
    )
    conn.commit()
    row = conn.execute(
        text(
            "SELECT count, ai_usd FROM community_audit_rate_limits "
            "WHERE scope = :scope AND key = :key AND window_start = :window_start"
        ),
        {"scope": scope, "key": key, "window_start": window_start},
    ).fetchone()
    return {"count": int(row[0]), "ai_usd": float(row[1])}


def get_rate_limit(conn, scope: str, key: str, window_start: str) -> int:
    """Read-only peek at a rate-limit counter (0 if absent). Lets callers check a
    limit BEFORE incrementing (no over-count on a denied attempt)."""
    row = conn.execute(
        text(
            "SELECT count FROM community_audit_rate_limits "
            "WHERE scope = :scope AND key = :key AND window_start = :window_start"
        ),
        {"scope": scope, "key": key, "window_start": window_start},
    ).fetchone()
    return int(row[0]) if row else 0


def get_rate_limit_usd(conn, scope: str, key: str, window_start: str) -> float:
    """Read-only peek at a rate-limit AI-spend counter (0.0 if absent). Backs the
    global daily $ ceiling (PLAN §6.3)."""
    row = conn.execute(
        text(
            "SELECT ai_usd FROM community_audit_rate_limits "
            "WHERE scope = :scope AND key = :key AND window_start = :window_start"
        ),
        {"scope": scope, "key": key, "window_start": window_start},
    ).fetchone()
    return float(row[0]) if row else 0.0


# ---------------------------------------------------------------------------
# Lead capture (mig 070) — non-privileged marketing list (PLAN §1.2)
# ---------------------------------------------------------------------------
def record_lead(
    conn, email: str, *, guild_id: str | None = None, source: str = "audit_page"
) -> int:
    """Record a community-audit lead (email-only marketing capture). NOT the
    allowlist — grants no access. Parameterized; caller validated the email format."""
    result = conn.execute(
        text(
            "INSERT INTO community_audit_leads (email, guild_id, source) "
            "VALUES (:email, :guild_id, :source) RETURNING id"
        ),
        {"email": email, "guild_id": guild_id, "source": source},
    )
    lead_id = result.fetchone()[0]
    conn.commit()
    return int(lead_id)
