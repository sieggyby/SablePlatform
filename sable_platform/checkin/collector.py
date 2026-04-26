"""Step 1 of client_checkin_loop: collect inputs.

Pulls the latest cult_grader run, this-week's actions, the most recent
metric_snapshot (for WoW baseline), and the latest strategy brief artifact.

The Tier 1 / Tier 2 schema mapping lives here in one place — if cult_grader
output keys change, this is the single point of update.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db import snapshots as snapshot_store


# Tier 1 — TIG's stated 5 metrics. Discord fields are proxies: cult_grader tracks
# pulse (active posters + retention movement), not literal joins. Labels in render.py
# call this out so we don't claim something we're not measuring.
TIER1_KEYS = (
    "fletcher_followers",
    "tig_followers",
    "discord_active_posters_weekly",
    "discord_retention_delta",
    "twitter_mentions",
)

# Tier 2 — Sable-influenceable leading indicators.
TIER2_KEYS = (
    "team_reply_rate",
    "lateral_reply_count",
    "recurring_engaged_accounts",
    "named_subsquads_publicly",
)


@dataclass
class CheckinInputs:
    org_id: str
    run_date: str  # ISO date (YYYY-MM-DD) the check-in is FOR (the Friday)
    tier1: dict[str, Any] = field(default_factory=dict)
    tier2: dict[str, Any] = field(default_factory=dict)
    previous_metrics: dict[str, Any] = field(default_factory=dict)  # last week's snapshot, or {}
    previous_snapshot_date: str | None = None
    cult_grader_meta: dict[str, Any] = field(default_factory=dict)  # run_meta.json subset
    actions_this_week: list[dict[str, Any]] = field(default_factory=list)
    strategy_brief_path: str | None = None

    def as_metrics_payload(self) -> dict[str, Any]:
        """Snapshot-friendly dict — what gets persisted to metric_snapshots."""
        return {
            "tier1": self.tier1,
            "tier2": self.tier2,
            "cult_grader_run_id": self.cult_grader_meta.get("run_id"),
            "cult_grader_run_date": self.cult_grader_meta.get("run_date"),
        }


def _resolve_cult_grader_run_dir(cult_grader_repo: Path, project_slug: str) -> Path | None:
    runs_dir = cult_grader_repo / "diagnostics" / project_slug / "runs"
    if not runs_dir.exists():
        return None
    latest = runs_dir / "latest"
    if latest.exists():
        return latest.resolve() if latest.is_symlink() else latest
    dated = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir() and not d.name.startswith("_")),
        reverse=True,
    )
    return dated[0] if dated else None


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def extract_tier1_tier2(
    cult_grader_run_dir: Path,
    discord_pulse: dict | None = None,
) -> tuple[dict, dict, dict]:
    """Project cult_grader output onto Tier 1/2 keys.

    Twitter fields come from the cult_grader run dir's computed_metrics +
    raw_twitter. Discord fields come from the latest discord_pulse_runs row
    in sable.db (passed in by the caller as `discord_pulse`); pulse data may
    be None when the bot is freshly added and no pulse has run yet, or when
    no row exists for this org.
    """
    computed = _read_json(cult_grader_run_dir / "computed_metrics.json")
    raw_twitter = _read_json(cult_grader_run_dir / "raw_twitter.json")
    run_meta = _read_json(cult_grader_run_dir / "run_meta.json")

    twitter = computed.get("twitter") or {}
    team_followers = raw_twitter.get("team_follower_counts") or {}
    pulse = discord_pulse or {}

    tier1 = {
        "fletcher_followers": team_followers.get("dr_johnfletcher"),
        "tig_followers": twitter.get("follower_count"),
        "discord_active_posters_weekly": pulse.get("weekly_active_posters"),
        "discord_retention_delta": pulse.get("retention_delta"),
        "twitter_mentions": twitter.get("unique_mentioners_count"),
    }
    tier2 = {
        "team_reply_rate": twitter.get("team_reply_rate"),
        "lateral_reply_count": twitter.get("lateral_reply_count"),
        "recurring_engaged_accounts": twitter.get("recurring_engaged_accounts"),
        # Subsquad public-naming count is not directly produced by cult_grader.
        # Track manually for now; the renderer surfaces it as "—" when None.
        "named_subsquads_publicly": None,
    }
    return tier1, tier2, run_meta


def get_latest_discord_pulse(conn, org_id: str) -> dict | None:
    """Fetch the most recent discord_pulse_runs row for an org as a dict."""
    row = conn.execute(
        text(
            """
            SELECT run_date, weekly_active_posters, wow_retention_rate, echo_rate,
                   retention_delta, echo_rate_delta, avg_silence_gap_hours
            FROM discord_pulse_runs
            WHERE org_id = :org_id
            ORDER BY run_date DESC LIMIT 1
            """
        ),
        {"org_id": org_id},
    ).fetchone()
    if not row:
        return None
    return dict(row._mapping)


def collect_actions_this_week(
    conn: Connection,
    org_id: str,
    *,
    since: str | None,
) -> list[dict[str, Any]]:
    """Return action rows touched (created/claimed/completed/skipped) since `since`.

    `since` is an ISO timestamp string. If None, we just return the most recent
    20 actions. The check-in should be honest about activity, so completed and
    skipped both count toward "what Sable did this week".
    """
    if since:
        rows = conn.execute(
            text(
                """
                SELECT action_id, title, status, source, action_type,
                       created_at, claimed_at, completed_at, skipped_at, outcome_notes
                FROM actions
                WHERE org_id = :org_id
                  AND (created_at >= :since
                       OR claimed_at >= :since
                       OR completed_at >= :since
                       OR skipped_at >= :since)
                ORDER BY COALESCE(completed_at, claimed_at, created_at) DESC
                LIMIT 50
                """
            ),
            {"org_id": org_id, "since": since},
        ).fetchall()
    else:
        rows = conn.execute(
            text(
                """
                SELECT action_id, title, status, source, action_type,
                       created_at, claimed_at, completed_at, skipped_at, outcome_notes
                FROM actions
                WHERE org_id = :org_id
                ORDER BY created_at DESC LIMIT 20
                """
            ),
            {"org_id": org_id},
        ).fetchall()
    return [dict(r._mapping) for r in rows]


def latest_strategy_brief_path(conn: Connection, org_id: str) -> str | None:
    row = conn.execute(
        text(
            """
            SELECT path FROM artifacts
            WHERE org_id = :org_id AND artifact_type = 'twitter_strategy_brief'
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"org_id": org_id},
    ).fetchone()
    return row[0] if row and row[0] else None


def collect_inputs(
    conn: Connection,
    org_id: str,
    *,
    run_date: str,
    cult_grader_repo: Path,
    project_slug: str,
) -> CheckinInputs:
    """Top-level collector. Returns a CheckinInputs ready for deltas/render/synthesize."""
    inputs = CheckinInputs(org_id=org_id, run_date=run_date)

    run_dir = _resolve_cult_grader_run_dir(cult_grader_repo, project_slug)
    if run_dir is not None:
        discord_pulse = get_latest_discord_pulse(conn, org_id)
        tier1, tier2, run_meta = extract_tier1_tier2(run_dir, discord_pulse=discord_pulse)
        inputs.tier1 = tier1
        inputs.tier2 = tier2
        inputs.cult_grader_meta = {
            "run_id": run_meta.get("run_id"),
            "run_date": run_meta.get("run_date"),
            "checkpoint_path": str(run_dir),
            "discord_pulse_date": discord_pulse.get("run_date") if discord_pulse else None,
        }

    prev = snapshot_store.get_latest_snapshot(conn, org_id, before_date=run_date)
    if prev:
        inputs.previous_metrics = prev.get("metrics") or {}
        inputs.previous_snapshot_date = prev.get("snapshot_date")

    inputs.actions_this_week = collect_actions_this_week(
        conn, org_id, since=inputs.previous_snapshot_date
    )
    inputs.strategy_brief_path = latest_strategy_brief_path(conn, org_id)

    return inputs
