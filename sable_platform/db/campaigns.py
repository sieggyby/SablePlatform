"""Coordinated reply-campaign CRUD (migration 061) — the "flash mob".

A campaign coordinates several operators replying to ONE target tweet toward a
shared objective. Assignments record who generated / posted for it + the angle
they took, so the generator can DE-DUPE angles across operators (anti bot-swarm)
and outcome tracking can tell whether the target bit.

SablePlatform owns these tables; Slopper + SableWeb reach them through this
in-process helper (not by writing sable.db directly). Platform stores + queries,
does not interpret. Caller commits the transaction (repo convention).

CompatConnection gotcha (see feedback_compatconn_row_access): iterating/unpacking
a Row yields column NAMES, indexing yields values — so every read here maps by
positional index against an explicit column tuple.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _utc_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- campaigns -------------------------------------------------------------

_CAMPAIGN_COLS = (
    "id", "org_id", "target_tweet_id", "target_url", "target_author",
    "objective", "status", "created_by", "created_at", "won_at", "closed_at",
)


def _campaign_dict(row) -> dict[str, Any]:
    return {c: row[i] for i, c in enumerate(_CAMPAIGN_COLS)}


def create_campaign(
    conn: Connection,
    *,
    org_id: str,
    target_tweet_id: str,
    target_url: str | None = None,
    target_author: str | None = None,
    objective: str | None = None,
    created_by: str | None = None,
    now: datetime | None = None,
) -> str:
    """Create an active campaign. Returns the campaign id. Caller commits."""
    cid = uuid.uuid4().hex
    conn.execute(
        text(
            "INSERT INTO reply_campaigns"
            " (id, org_id, target_tweet_id, target_url, target_author, objective,"
            "  status, created_by, created_at)"
            " VALUES (:id, :org, :tid, :url, :author, :obj, 'active', :by, :now)"
        ),
        {"id": cid, "org": org_id, "tid": target_tweet_id, "url": target_url,
         "author": target_author, "obj": objective, "by": created_by, "now": _utc_stamp(now)},
    )
    return cid


def get_campaign(conn: Connection, campaign_id: str) -> dict | None:
    row = conn.execute(
        text(f"SELECT {', '.join(_CAMPAIGN_COLS)} FROM reply_campaigns WHERE id = :id"),
        {"id": campaign_id},
    ).fetchone()
    return _campaign_dict(row) if row else None


def list_campaigns(
    conn: Connection, org_id: str, *, status: str | None = "active", limit: int = 50,
) -> list[dict]:
    """Campaigns for an org, newest first. ``status=None`` returns all statuses;
    the default 'active' backs the shared operator queue."""
    q = f"SELECT {', '.join(_CAMPAIGN_COLS)} FROM reply_campaigns WHERE org_id = :org"
    params: dict[str, Any] = {"org": org_id, "lim": int(limit)}
    if status:
        q += " AND status = :status"
        params["status"] = status
    q += " ORDER BY created_at DESC LIMIT :lim"
    rows = conn.execute(text(q), params).fetchall()
    return [_campaign_dict(r) for r in rows]


def set_status(conn: Connection, campaign_id: str, status: str, *, now: datetime | None = None) -> None:
    """Set campaign status; stamps won_at / closed_at as appropriate. Caller commits."""
    stamp = _utc_stamp(now)
    sets = ["status = :status"]
    params: dict[str, Any] = {"status": status, "id": campaign_id, "now": stamp}
    if status == "won":
        sets.append("won_at = :now")
    elif status == "closed":
        sets.append("closed_at = :now")
    conn.execute(text(f"UPDATE reply_campaigns SET {', '.join(sets)} WHERE id = :id"), params)


# --- assignments -----------------------------------------------------------

_ASSIGN_COLS = (
    "id", "campaign_id", "operator_handle", "suggestion_id", "posted_tweet_id",
    "angle", "status", "created_at", "posted_at",
)


def _assign_dict(row) -> dict[str, Any]:
    return {c: row[i] for i, c in enumerate(_ASSIGN_COLS)}


def add_assignment(
    conn: Connection,
    *,
    campaign_id: str,
    operator_handle: str,
    suggestion_id: str | None = None,
    angle: str | None = None,
    now: datetime | None = None,
) -> str:
    """Record that an operator generated a reply for the campaign (status
    'generated'). Returns the assignment id. Caller commits."""
    aid = uuid.uuid4().hex
    conn.execute(
        text(
            "INSERT INTO reply_campaign_assignments"
            " (id, campaign_id, operator_handle, suggestion_id, angle, status, created_at)"
            " VALUES (:id, :cid, :h, :sid, :angle, 'generated', :now)"
        ),
        {"id": aid, "cid": campaign_id, "h": operator_handle, "sid": suggestion_id,
         "angle": angle, "now": _utc_stamp(now)},
    )
    return aid


def record_post(conn: Connection, *, assignment_id: str, posted_tweet_id: str, now: datetime | None = None) -> None:
    """Mark an assignment as posted (status 'posted' + posted_tweet_id). Caller commits."""
    conn.execute(
        text(
            "UPDATE reply_campaign_assignments"
            " SET status = 'posted', posted_tweet_id = :ptid, posted_at = :now"
            " WHERE id = :id"
        ),
        {"id": assignment_id, "ptid": posted_tweet_id, "now": _utc_stamp(now)},
    )


def list_assignments(conn: Connection, campaign_id: str) -> list[dict]:
    rows = conn.execute(
        text(f"SELECT {', '.join(_ASSIGN_COLS)} FROM reply_campaign_assignments"
             " WHERE campaign_id = :cid ORDER BY created_at ASC"),
        {"cid": campaign_id},
    ).fetchall()
    return [_assign_dict(r) for r in rows]


def list_angles_taken(conn: Connection, campaign_id: str, *, exclude_operator: str | None = None) -> list[str]:
    """The non-empty angles already used in this campaign — fed to the generator so
    the next operator takes a DIFFERENT beat (anti bot-swarm de-dup). Excludes the
    requesting operator's own angles so they only steer away from teammates'."""
    rows = conn.execute(
        text(
            "SELECT angle, operator_handle FROM reply_campaign_assignments"
            " WHERE campaign_id = :cid AND angle IS NOT NULL AND angle != ''"
            " ORDER BY created_at ASC"
        ),
        {"cid": campaign_id},
    ).fetchall()
    out: list[str] = []
    for r in rows:
        angle, op = r[0], r[1]
        if exclude_operator and op == exclude_operator:
            continue
        if angle and str(angle) not in out:
            out.append(str(angle))
    return out


# --- objective-aware outcomes (Phase 4) ------------------------------------


def get_campaign_outcomes(conn: Connection, campaign_id: str) -> dict | None:
    """Objective-aware outcome rollup for a campaign. Returns None if unknown.

    Surfaces the campaign's OBJECTIVE alongside hard, attributable numbers rolled
    up from the assignment→suggestion→outcome chain. The engagement figure is the
    FIXED-AGE reading the posted-reply snapshot job backfills
    (:mod:`sable.quality.reply_outcomes` → ``reply_outcomes.engagement_json`` at
    24h), NOT raw cumulative impressions — and it is averaged ONLY over replies that
    have actually matured to a real reading (a still-maturing ``'{}'`` row is never
    counted as zero, which would understate a young campaign). Reports:

    * ``post_rate`` — coordination follow-through (posted / assigned);
    * ``avg_engagement`` — mean 24h hard-engagement (likes+RT+replies) of MATURED
      replies, or ``None`` when none have matured yet (``measured_count`` says how
      many);
    * ``adoption_rate`` — operators who posted a variant UNEDITED / outcomes (a proxy
      for draft trust).

    Read-only; never returns cost (internal-only per the repo convention). The join
    skips the reply_suggestions hop — ``reply_outcomes.suggestion_id`` equals the
    assignment's ``suggestion_id`` (both are the suggestion PK).
    """
    camp = get_campaign(conn, campaign_id)
    if camp is None:
        return None

    assigns = conn.execute(
        text(
            "SELECT posted_tweet_id, status FROM reply_campaign_assignments"
            " WHERE campaign_id = :cid"
        ),
        {"cid": campaign_id},
    ).fetchall()
    total_assignments = len(assigns)
    # Posted = an assignment that carries a posted tweet id OR is marked posted.
    total_posted = sum(1 for r in assigns if (r[0] or r[1] == "posted"))

    out_rows = conn.execute(
        text(
            "SELECT ro.engagement_json, ro.was_edited, ro.chosen_variant_idx"
            " FROM reply_campaign_assignments a"
            " JOIN reply_outcomes ro ON ro.suggestion_id = a.suggestion_id"
            " WHERE a.campaign_id = :cid AND a.suggestion_id IS NOT NULL"
        ),
        {"cid": campaign_id},
    ).fetchall()

    outcomes_count = len(out_rows)
    measured = 0          # outcomes that have a real engagement reading (not '{}')
    eng_sum = 0
    adopted = 0           # posted a variant unedited
    for r in out_rows:
        ej_raw, was_edited, chosen_idx = r[0], r[1], r[2]
        try:
            ej = json.loads(ej_raw) if ej_raw else {}
        except (TypeError, ValueError):
            ej = {}
        total = ej.get("total")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            measured += 1
            eng_sum += int(total)
        if (not was_edited) and chosen_idx is not None:
            adopted += 1

    return {
        "campaign_id": campaign_id,
        "objective": camp["objective"],
        "status": camp["status"],
        "target_tweet_id": camp["target_tweet_id"],
        "target_url": camp["target_url"],
        "target_author": camp["target_author"],
        "total_assignments": total_assignments,
        "total_posted": total_posted,
        "post_rate": (total_posted / total_assignments) if total_assignments else None,
        "outcomes_count": outcomes_count,
        "measured_count": measured,
        "avg_engagement": (eng_sum / measured) if measured else None,
        "adoption_rate": (adopted / outcomes_count) if outcomes_count else None,
    }
