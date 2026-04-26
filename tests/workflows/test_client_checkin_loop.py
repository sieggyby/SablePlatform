"""Integration tests for client_checkin_loop workflow."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sable_platform.checkin.synthesize import SynthesisResult
from sable_platform.db import snapshots as snapshot_store
from sable_platform.db.workflow_store import get_workflow_run, get_workflow_steps
from sable_platform.workflows import registry
from sable_platform.workflows.builtins.client_checkin_loop import (
    CLIENT_CHECKIN_LOOP,
    _DRY_RUN_SUMMARY,
)
from sable_platform.workflows.engine import WorkflowRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORG_ID = "wf_org"
PROJECT_SLUG = "wf_project"


def _seed_cult_grader(repo: Path) -> Path:
    run_dir = repo / "diagnostics" / PROJECT_SLUG / "runs" / "2026-04-30"
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
        "team_follower_counts": {"dr_johnfletcher": 3457},
    }))
    (run_dir / "run_meta.json").write_text(json.dumps({
        "run_id": "wf_run_abc", "run_date": "2026-04-30",
    }))
    return run_dir


def _stub_synth():
    return SynthesisResult(
        summary_prose="## SUMMARY\n- bullet one referencing 282 recurring accounts\n- bullet two",
        deep_dive_prose="## DEEP_DIVE\n### Tier 1\n...\n### Tier 2\n...\n### Tier 3\n...",
        model="claude-opus-4-7",
        input_tokens=400,
        output_tokens=200,
        cache_creation_input_tokens=600,
        cache_read_input_tokens=0,
        cost_usd=0.025,
        raw_text="(stub)",
    )


@pytest.fixture
def cult_grader_repo(tmp_path):
    repo = tmp_path / "cult_grader"
    repo.mkdir()
    _seed_cult_grader(repo)
    return repo


@pytest.fixture
def vault_root(tmp_path):
    root = tmp_path / "sable_vault"
    root.mkdir()
    return root


@pytest.fixture
def base_config(cult_grader_repo, vault_root):
    return {
        "org_id": ORG_ID,
        "run_date": "2026-05-01",
        "project_slug": PROJECT_SLUG,
        "cult_grader_repo": str(cult_grader_repo),
        "vault_root": str(vault_root),
    }


# ---------------------------------------------------------------------------
# Workflow registration
# ---------------------------------------------------------------------------

def test_workflow_is_registered():
    assert "client_checkin_loop" in registry.list_all()
    wf = registry.get("client_checkin_loop")
    assert wf.name == "client_checkin_loop"
    step_names = [s.name for s in wf.steps]
    assert step_names == [
        "collect_inputs", "compute_deltas", "render_data_sections",
        "synthesize_prose", "assemble_artifact",
        "snapshot_metrics", "notify_and_send",
    ]


# ---------------------------------------------------------------------------
# Dry run end-to-end
# ---------------------------------------------------------------------------

def test_dry_run_writes_artifacts_skips_send(wf_db, base_config, vault_root):
    config = {**base_config, "dry_run": True}
    runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
    run_id = runner.run(ORG_ID, config, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    steps = {s["step_name"]: s["status"] for s in get_workflow_steps(wf_db, run_id)}
    assert all(s == "completed" for s in steps.values()), steps

    checkin_dir = vault_root / ORG_ID / "checkins" / "2026-05-01"
    assert (checkin_dir / "_collected.json").exists()
    assert (checkin_dir / "_deltas.json").exists()
    assert (checkin_dir / "_data_sections.json").exists()
    assert (checkin_dir / "_synthesis.json").exists()
    assert (checkin_dir / "summary.md").exists()
    assert (checkin_dir / "deep_dive.md").exists()

    # Dry run synthesis should be the canned placeholder
    synth = json.loads((checkin_dir / "_synthesis.json").read_text())
    assert synth["model"] == "dry_run"
    assert synth["cost_usd"] == 0.0
    assert _DRY_RUN_SUMMARY.strip() in (checkin_dir / "summary.md").read_text()


def test_dry_run_skips_metric_snapshot(wf_db, base_config):
    """dry_run must NOT write to metric_snapshots — otherwise smoke tests
    poison the WoW baseline chain for subsequent real runs."""
    config = {**base_config, "dry_run": True}
    runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
    runner.run(ORG_ID, config, conn=wf_db)

    snap = snapshot_store.get_snapshot(wf_db, ORG_ID, "2026-05-01")
    assert snap is None


def test_real_run_writes_metric_snapshot(wf_db, base_config):
    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ):
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        runner.run(ORG_ID, base_config, conn=wf_db)

    snap = snapshot_store.get_snapshot(wf_db, ORG_ID, "2026-05-01")
    assert snap is not None
    assert snap["source"] == "pipeline"
    assert snap["metrics"]["tier1"]["tig_followers"] == 8538
    assert snap["metrics"]["cult_grader_run_id"] == "wf_run_abc"


def test_dry_run_skips_send(wf_db, base_config):
    config = {**base_config, "dry_run": True}
    runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
    run_id = runner.run(ORG_ID, config, conn=wf_db)

    notify_step = next(
        s for s in get_workflow_steps(wf_db, run_id) if s["step_name"] == "notify_and_send"
    )
    output = json.loads(notify_step["output_json"])
    assert output["sent"] is False
    assert output["reason"] == "dry_run"


# ---------------------------------------------------------------------------
# Real-synth path (mocked) end-to-end with WoW deltas
# ---------------------------------------------------------------------------

def test_full_run_with_previous_snapshot_and_mocked_synth(wf_db, base_config, vault_root):
    # Seed last week's snapshot so deltas have a real baseline
    snapshot_store.upsert_metric_snapshot(
        wf_db, ORG_ID, "2026-04-24",
        {
            "tier1": {"tig_followers": 8500, "fletcher_followers": 3450,
                      "discord_joins": None, "discord_velocity": None,
                      "twitter_mentions": 1800},
            "tier2": {"team_reply_rate": 0.005, "lateral_reply_count": 0,
                      "recurring_engaged_accounts": 280, "named_subsquads_publicly": None},
        },
        source="pipeline",
    )
    wf_db.commit()

    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ):
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(ORG_ID, base_config, conn=wf_db)

    run = get_workflow_run(wf_db, run_id)
    assert run["status"] == "completed"

    checkin_dir = vault_root / ORG_ID / "checkins" / "2026-05-01"
    deep_dive = (checkin_dir / "deep_dive.md").read_text()
    assert "▲" in deep_dive  # WoW arrows present
    assert "Tier 1" in deep_dive
    assert "2026-04-24" in deep_dive  # baseline date in header

    synth = json.loads((checkin_dir / "_synthesis.json").read_text())
    assert synth["model"] == "claude-opus-4-7"
    assert synth["cost_usd"] == 0.025

    # Cost was logged
    cost_row = wf_db.execute(
        "SELECT cost_usd, call_type FROM cost_events WHERE org_id=?", (ORG_ID,),
    ).fetchone()
    assert cost_row is not None
    assert cost_row["call_type"] == "checkin_synthesize"
    assert float(cost_row["cost_usd"]) == 0.025


# ---------------------------------------------------------------------------
# Notify gate behavior
# ---------------------------------------------------------------------------

def test_notify_skipped_when_checkin_not_enabled(wf_db, base_config):
    """Org with no config_json → checkin_enabled is False → send is skipped."""
    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ):
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(ORG_ID, base_config, conn=wf_db)

    notify_step = next(
        s for s in get_workflow_steps(wf_db, run_id) if s["step_name"] == "notify_and_send"
    )
    output = json.loads(notify_step["output_json"])
    assert output["sent"] is False
    assert output["reason"] == "checkin_disabled"


def test_notify_skipped_when_no_chat_id(wf_db, base_config):
    """checkin_enabled=True but no client_telegram_chat_id."""
    wf_db.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"checkin_enabled": True}), ORG_ID),
    )
    wf_db.commit()

    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ):
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(ORG_ID, base_config, conn=wf_db)

    notify_step = next(
        s for s in get_workflow_steps(wf_db, run_id) if s["step_name"] == "notify_and_send"
    )
    output = json.loads(notify_step["output_json"])
    assert output["reason"] == "no_client_telegram_chat_id"


def test_notify_calls_telegram_when_fully_configured(wf_db, base_config, monkeypatch):
    wf_db.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"checkin_enabled": True, "client_telegram_chat_id": "-5050566880"}), ORG_ID),
    )
    wf_db.commit()
    monkeypatch.setenv("SABLE_TELEGRAM_BOT_TOKEN", "fake_token_for_test")

    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ), patch(
        "sable_platform.workflows.builtins.client_checkin_loop._send_telegram_message",
        return_value=None,
    ) as mock_send:
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(ORG_ID, base_config, conn=wf_db)

    notify_step = next(
        s for s in get_workflow_steps(wf_db, run_id) if s["step_name"] == "notify_and_send"
    )
    output = json.loads(notify_step["output_json"])
    assert output["sent"] is True
    assert output["chat_id"] == "-5050566880"
    assert mock_send.call_count == 2  # summary + deep_dive
    # First arg is bot token
    assert mock_send.call_args_list[0].args[0] == "fake_token_for_test"
    assert mock_send.call_args_list[0].args[1] == "-5050566880"


def test_notify_accepts_string_true_from_cli_set(wf_db, base_config, monkeypatch):
    """`org config set` stores all values as strings — "true" must enable, "false" must not."""
    wf_db.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"checkin_enabled": "true", "client_telegram_chat_id": "-5050566880"}), ORG_ID),
    )
    wf_db.commit()
    monkeypatch.setenv("SABLE_TELEGRAM_BOT_TOKEN", "fake_token")

    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ), patch(
        "sable_platform.workflows.builtins.client_checkin_loop._send_telegram_message",
        return_value=None,
    ) as mock_send:
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(ORG_ID, base_config, conn=wf_db)

    notify_step = next(
        s for s in get_workflow_steps(wf_db, run_id) if s["step_name"] == "notify_and_send"
    )
    assert json.loads(notify_step["output_json"])["sent"] is True
    assert mock_send.call_count == 2


def test_notify_treats_string_false_as_disabled(wf_db, base_config):
    wf_db.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"checkin_enabled": "false", "client_telegram_chat_id": "-5050566880"}), ORG_ID),
    )
    wf_db.commit()

    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ):
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(ORG_ID, base_config, conn=wf_db)

    notify_step = next(
        s for s in get_workflow_steps(wf_db, run_id) if s["step_name"] == "notify_and_send"
    )
    output = json.loads(notify_step["output_json"])
    assert output["sent"] is False
    assert output["reason"] == "checkin_disabled"


def test_notify_does_not_send_deep_dive_if_summary_fails(wf_db, base_config, monkeypatch):
    wf_db.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"checkin_enabled": True, "client_telegram_chat_id": "-5050566880"}), ORG_ID),
    )
    wf_db.commit()
    monkeypatch.setenv("SABLE_TELEGRAM_BOT_TOKEN", "fake_token")

    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=_stub_synth(),
    ), patch(
        "sable_platform.workflows.builtins.client_checkin_loop._send_telegram_message",
        return_value="HTTP 429",
    ) as mock_send:
        runner = WorkflowRunner(CLIENT_CHECKIN_LOOP)
        run_id = runner.run(ORG_ID, base_config, conn=wf_db)

    notify_step = next(
        s for s in get_workflow_steps(wf_db, run_id) if s["step_name"] == "notify_and_send"
    )
    output = json.loads(notify_step["output_json"])
    assert output["sent"] is False
    assert output["summary_error"] == "HTTP 429"
    assert mock_send.call_count == 1
