"""Tests for _register_actions parsing twitter_strategy_brief + discord_playbook."""
from __future__ import annotations

import datetime
import json
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.workflows.builtins.weekly_client_loop import (
    _register_actions,
    _parse_actions_from_artifact,
)

_TEST_RUN_ID = "test_run"


def _ensure_run_row(conn, run_id=_TEST_RUN_ID, org_id="wf_org"):
    """Insert a workflow_runs row so _get_run_started_at returns a timestamp."""
    started = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR IGNORE INTO workflow_runs (run_id, org_id, workflow_name, status, started_at) "
        "VALUES (?, ?, 'weekly_client_loop', 'running', ?)",
        (run_id, org_id, started),
    )
    conn.commit()


@pytest.fixture
def strategy_brief(tmp_path):
    """Write a mock strategy brief with recommendations."""
    brief = tmp_path / "brief.md"
    brief.write_text(textwrap.dedent("""\
        # Twitter Strategy Brief

        ## Analysis
        Current engagement is strong.

        ## Recommendations
        - Post a thread about community governance
        - Reply to @alice's thread on tokenomics
        - Share the weekly digest in DMs

        ## Notes
        Some extra notes.
    """))
    return str(brief)


@pytest.fixture
def playbook(tmp_path):
    """Write a mock discord playbook with actions."""
    pb = tmp_path / "playbook.md"
    pb.write_text(textwrap.dedent("""\
        # Discord Playbook

        ## Actions
        - Set up a new #governance channel
        - Pin the community rules message

        ## Follow-up
        Monitor activity.
    """))
    return str(pb)


def test_parse_strategy_brief_actions(wf_db, strategy_brief):
    """Parses recommendations from twitter_strategy_brief artifact."""
    _ensure_run_row(wf_db)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'twitter_strategy_brief', ?)",
        (strategy_brief,),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID

    ids = _parse_actions_from_artifact(ctx, "twitter_strategy_brief", "strategy_brief", "post_content")
    assert len(ids) == 3

    # Verify action_type is post_content
    for aid in ids:
        row = wf_db.execute("SELECT action_type, source FROM actions WHERE action_id=?", (aid,)).fetchone()
        assert row["action_type"] == "post_content"
        assert row["source"] == "strategy_brief"


def test_parse_playbook_actions(wf_db, playbook):
    """Parses actions from discord_playbook artifact."""
    _ensure_run_row(wf_db)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'discord_playbook', ?)",
        (playbook,),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID

    ids = _parse_actions_from_artifact(ctx, "discord_playbook", "playbook", "general")
    assert len(ids) == 2

    for aid in ids:
        row = wf_db.execute("SELECT action_type, source FROM actions WHERE action_id=?", (aid,)).fetchone()
        assert row["action_type"] == "general"
        assert row["source"] == "playbook"


def test_register_actions_combines_both(wf_db, strategy_brief, playbook):
    """_register_actions parses both playbook and strategy brief."""
    _ensure_run_row(wf_db)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'discord_playbook', ?)",
        (playbook,),
    )
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'twitter_strategy_brief', ?)",
        (strategy_brief,),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID
    ctx.input_data = {}

    result = _register_actions(ctx)
    assert result.output["actions_created"] == 5  # 2 playbook + 3 strategy


def test_register_actions_no_artifacts(wf_db):
    """_register_actions returns empty when no artifacts exist."""
    _ensure_run_row(wf_db)
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID
    ctx.input_data = {}

    result = _register_actions(ctx)
    assert result.output["actions_created"] == 0
    assert result.output["action_ids"] == []


def test_parse_missing_file(wf_db):
    """Handles missing file gracefully."""
    _ensure_run_row(wf_db)
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'discord_playbook', '/nonexistent/path.md')",
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = _TEST_RUN_ID

    ids = _parse_actions_from_artifact(ctx, "discord_playbook", "playbook", "general")
    assert ids == []
