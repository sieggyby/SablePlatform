"""Tests for _register_actions parsing twitter_strategy_brief + discord_playbook."""
from __future__ import annotations

import json
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from sable_platform.workflows.builtins.weekly_client_loop import (
    _register_actions,
    _parse_actions_from_artifact,
)


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
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'twitter_strategy_brief', ?)",
        (strategy_brief,),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = "test_run"

    ids = _parse_actions_from_artifact(ctx, "twitter_strategy_brief", "strategy_brief", "post_content")
    assert len(ids) == 3

    # Verify action_type is post_content
    for aid in ids:
        row = wf_db.execute("SELECT action_type, source FROM actions WHERE action_id=?", (aid,)).fetchone()
        assert row["action_type"] == "post_content"
        assert row["source"] == "strategy_brief"


def test_parse_playbook_actions(wf_db, playbook):
    """Parses actions from discord_playbook artifact."""
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'discord_playbook', ?)",
        (playbook,),
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = "test_run"

    ids = _parse_actions_from_artifact(ctx, "discord_playbook", "playbook", "general")
    assert len(ids) == 2

    for aid in ids:
        row = wf_db.execute("SELECT action_type, source FROM actions WHERE action_id=?", (aid,)).fetchone()
        assert row["action_type"] == "general"
        assert row["source"] == "playbook"


def test_register_actions_combines_both(wf_db, strategy_brief, playbook):
    """_register_actions parses both playbook and strategy brief."""
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
    ctx.run_id = "test_run"
    ctx.input_data = {}

    result = _register_actions(ctx)
    assert result.output["actions_created"] == 5  # 2 playbook + 3 strategy


def test_register_actions_no_artifacts(wf_db):
    """_register_actions returns empty when no artifacts exist."""
    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = "test_run"
    ctx.input_data = {}

    result = _register_actions(ctx)
    assert result.output["actions_created"] == 0
    assert result.output["action_ids"] == []


def test_parse_missing_file(wf_db):
    """Handles missing file gracefully."""
    wf_db.execute(
        "INSERT INTO artifacts (org_id, artifact_type, path) VALUES ('wf_org', 'discord_playbook', '/nonexistent/path.md')",
    )
    wf_db.commit()

    ctx = MagicMock()
    ctx.db = wf_db
    ctx.org_id = "wf_org"
    ctx.run_id = "test_run"

    ids = _parse_actions_from_artifact(ctx, "discord_playbook", "playbook", "general")
    assert ids == []
