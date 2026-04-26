"""Tests for sable_platform.checkin.collector."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sable_platform.checkin.collector import (
    CheckinInputs,
    collect_actions_this_week,
    collect_inputs,
    extract_tier1_tier2,
    latest_strategy_brief_path,
)
from sable_platform.db.actions import create_action
from sable_platform.db import snapshots as snapshot_store


# ---------------------------------------------------------------------------
# extract_tier1_tier2 — file fixture only
# ---------------------------------------------------------------------------

def _seed_cult_grader_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "diagnostics" / "the-innovation-game_tigfoundation" / "runs" / "2026-04-30"
    run_dir.mkdir(parents=True)
    (run_dir / "computed_metrics.json").write_text(json.dumps({
        "twitter": {
            "follower_count": 8538,
            "unique_mentioners_count": 1807,
            "team_reply_rate": 0.0047,
            "lateral_reply_count": 0,
            "recurring_engaged_accounts": 282,
        },
    }))
    (run_dir / "raw_twitter.json").write_text(json.dumps({
        "team_follower_counts": {"dr_johnfletcher": 3457, "tigstats": 331},
    }))
    (run_dir / "run_meta.json").write_text(json.dumps({
        "run_id": "run_abc", "run_date": "2026-04-30",
    }))
    return run_dir


def test_extract_tier1_tier2_maps_keys(tmp_path):
    run_dir = _seed_cult_grader_run(tmp_path)
    tier1, tier2, run_meta = extract_tier1_tier2(run_dir)

    assert tier1 == {
        "fletcher_followers": 3457,
        "tig_followers": 8538,
        "discord_joins": None,
        "discord_velocity": None,
        "twitter_mentions": 1807,
    }
    assert tier2 == {
        "team_reply_rate": 0.0047,
        "lateral_reply_count": 0,
        "recurring_engaged_accounts": 282,
        "named_subsquads_publicly": None,
    }
    assert run_meta["run_id"] == "run_abc"


def test_extract_handles_missing_files(tmp_path):
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()
    tier1, tier2, run_meta = extract_tier1_tier2(run_dir)
    assert tier1["tig_followers"] is None
    assert tier2["team_reply_rate"] is None
    assert run_meta == {}


# ---------------------------------------------------------------------------
# DB-backed helpers — use shared in-memory fixtures
# ---------------------------------------------------------------------------

def test_collect_actions_this_week_filters_by_since(org_db):
    conn, org_id = org_db
    aid_old = create_action(conn, org_id, "Old action")
    # Backdate the old action so it falls before the cutoff
    conn.execute(
        "UPDATE actions SET created_at='2026-04-01 12:00:00' WHERE action_id=?",
        (aid_old,),
    )
    conn.commit()
    aid_new = create_action(conn, org_id, "New action")

    rows = collect_actions_this_week(conn, org_id, since="2026-04-15")
    titles = [r["title"] for r in rows]
    assert "New action" in titles
    assert "Old action" not in titles


def test_collect_actions_no_since_returns_recent(org_db):
    conn, org_id = org_db
    create_action(conn, org_id, "A1")
    create_action(conn, org_id, "A2")
    rows = collect_actions_this_week(conn, org_id, since=None)
    assert len(rows) == 2


def test_latest_strategy_brief_path(org_db):
    conn, org_id = org_db
    conn.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path, stale) VALUES (?, ?, ?, 0)",
        (org_id, "twitter_strategy_brief", "/tmp/old_brief.md"),
    )
    conn.execute(
        """
        INSERT INTO artifacts (org_id, artifact_type, path, stale, created_at)
        VALUES (?, ?, ?, 0, '2030-01-01 00:00:00')
        """,
        (org_id, "twitter_strategy_brief", "/tmp/new_brief.md"),
    )
    conn.commit()

    assert latest_strategy_brief_path(conn, org_id) == "/tmp/new_brief.md"


def test_latest_strategy_brief_path_none_when_missing(org_db):
    conn, org_id = org_db
    assert latest_strategy_brief_path(conn, org_id) is None


# ---------------------------------------------------------------------------
# collect_inputs — full integration
# ---------------------------------------------------------------------------

def test_collect_inputs_full(org_db, tmp_path):
    conn, org_id = org_db
    cult_grader_repo = tmp_path
    _seed_cult_grader_run(cult_grader_repo)

    # Seed last week's snapshot
    snapshot_store.upsert_metric_snapshot(
        conn, org_id, "2026-04-24",
        {
            "tier1": {"tig_followers": 8500, "fletcher_followers": 3450,
                      "discord_joins": None, "discord_velocity": None, "twitter_mentions": 1800},
            "tier2": {"team_reply_rate": 0.005, "lateral_reply_count": 0,
                      "recurring_engaged_accounts": 280, "named_subsquads_publicly": None},
        },
        source="pipeline",
    )
    conn.commit()

    create_action(conn, org_id, "Reply to name-tag holders")

    inputs = collect_inputs(
        conn, org_id,
        run_date="2026-05-01",
        cult_grader_repo=cult_grader_repo,
        project_slug="the-innovation-game_tigfoundation",
    )

    assert isinstance(inputs, CheckinInputs)
    assert inputs.org_id == org_id
    assert inputs.run_date == "2026-05-01"
    assert inputs.tier1["tig_followers"] == 8538
    assert inputs.tier2["team_reply_rate"] == 0.0047
    assert inputs.previous_snapshot_date == "2026-04-24"
    assert inputs.previous_metrics["tier1"]["tig_followers"] == 8500
    assert any(a["title"] == "Reply to name-tag holders" for a in inputs.actions_this_week)
    assert inputs.cult_grader_meta["run_id"] == "run_abc"


def test_collect_inputs_no_cult_grader_dir(org_db, tmp_path):
    conn, org_id = org_db
    inputs = collect_inputs(
        conn, org_id,
        run_date="2026-05-01",
        cult_grader_repo=tmp_path,
        project_slug="missing_slug",
    )
    assert inputs.tier1 == {}
    assert inputs.cult_grader_meta == {}


def test_collect_inputs_no_previous_snapshot(org_db, tmp_path):
    conn, org_id = org_db
    cult_grader_repo = tmp_path
    _seed_cult_grader_run(cult_grader_repo)
    inputs = collect_inputs(
        conn, org_id,
        run_date="2026-05-01",
        cult_grader_repo=cult_grader_repo,
        project_slug="the-innovation-game_tigfoundation",
    )
    assert inputs.previous_metrics == {}
    assert inputs.previous_snapshot_date is None


def test_as_metrics_payload_serializable():
    inputs = CheckinInputs(
        org_id="tig", run_date="2026-05-01",
        tier1={"tig_followers": 8538}, tier2={"team_reply_rate": 0.005},
        cult_grader_meta={"run_id": "r1", "run_date": "2026-04-30"},
    )
    payload = inputs.as_metrics_payload()
    assert payload["tier1"]["tig_followers"] == 8538
    assert payload["cult_grader_run_id"] == "r1"
    # JSON-serializable round-trip
    json.dumps(payload)
