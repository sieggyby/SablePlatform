"""Smoke tests for `sable-platform checkin` CLI."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from sable_platform.checkin.synthesize import SynthesisResult
from sable_platform.cli.checkin_cmds import checkin
from tests.conftest import make_test_conn


def _make_org(org_id="tig"):
    conn = make_test_conn(with_org=org_id)
    return conn


def _seed_cult_grader(repo: Path, slug: str = "the-innovation-game_tigfoundation") -> Path:
    run_dir = repo / "diagnostics" / slug / "runs" / "2026-04-30"
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
        "run_id": "cli_run_abc", "run_date": "2026-04-30",
    }))
    return run_dir


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

def test_generate_dry_run_writes_artifacts(tmp_path, monkeypatch):
    conn = _make_org("tig")
    monkeypatch.setattr("sable_platform.cli.checkin_cmds.get_db", lambda: conn)
    cult_repo = tmp_path / "cult"
    cult_repo.mkdir()
    _seed_cult_grader(cult_repo)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)  # so list_cmd default also resolves here

    result = CliRunner().invoke(checkin, [
        "generate",
        "--org", "tig",
        "--date", "2026-05-01",
        "--dry-run",
        "--vault-root", str(vault_root),
        "--cult-grader-repo", str(cult_repo),
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["org_id"] == "tig"
    assert payload["run_date"] == "2026-05-01"

    summary_path = Path(payload["summary_path"])
    deep_dive_path = Path(payload["deep_dive_path"])
    assert summary_path.exists()
    assert deep_dive_path.exists()


def test_generate_invalid_date_fails(monkeypatch):
    conn = _make_org("tig")
    monkeypatch.setattr("sable_platform.cli.checkin_cmds.get_db", lambda: conn)
    result = CliRunner().invoke(checkin, [
        "generate", "--org", "tig", "--date", "not-a-date",
    ])
    assert result.exit_code != 0
    assert "must be YYYY-MM-DD" in result.output


def test_generate_full_synth_invokes_synthesize(tmp_path, monkeypatch):
    """When --dry-run is omitted the synthesize call is made (mocked here)."""
    conn = _make_org("tig")
    monkeypatch.setattr("sable_platform.cli.checkin_cmds.get_db", lambda: conn)
    cult_repo = tmp_path / "cult"
    cult_repo.mkdir()
    _seed_cult_grader(cult_repo)

    stub = SynthesisResult(
        summary_prose="## SUMMARY\n- bullet",
        deep_dive_prose="## DEEP_DIVE\n### Tier 1\n...",
        model="claude-opus-4-7",
        input_tokens=400, output_tokens=200,
        cache_creation_input_tokens=600, cache_read_input_tokens=0,
        cost_usd=0.025, raw_text="x",
    )
    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop.synthesize_call",
        return_value=stub,
    ) as mock_synth:
        result = CliRunner().invoke(checkin, [
            "generate", "--org", "tig", "--date", "2026-05-01",
            "--vault-root", str(tmp_path / "vault"),
            "--cult-grader-repo", str(cult_repo),
        ])

    assert result.exit_code == 0, result.output
    assert mock_synth.call_count == 1


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

def test_send_missing_artifacts_errors(tmp_path, monkeypatch):
    conn = _make_org("tig")
    monkeypatch.setattr("sable_platform.cli.checkin_cmds.get_db", lambda: conn)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    result = CliRunner().invoke(checkin, [
        "send", "--org", "tig", "--date", "2099-12-31",
    ])
    assert result.exit_code == 1
    assert "artifacts not found" in result.output


def test_send_dry_run(tmp_path, monkeypatch):
    conn = _make_org("tig")
    conn.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"checkin_enabled": True, "client_telegram_chat_id": "-5050566880"}), "tig"),
    )
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.checkin_cmds.get_db", lambda: conn)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    out_dir = tmp_path / "sable-vault" / "tig" / "checkins" / "2026-05-01"
    out_dir.mkdir(parents=True)
    (out_dir / "summary.md").write_text("summary body")
    (out_dir / "deep_dive.md").write_text("deep_dive body")

    result = CliRunner().invoke(checkin, [
        "send", "--org", "tig", "--date", "2026-05-01", "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["chat_id"] == "-5050566880"
    assert payload["summary_chars"] == len("summary body")


def test_send_real_invokes_telegram(tmp_path, monkeypatch):
    conn = _make_org("tig")
    conn.execute(
        "UPDATE orgs SET config_json=? WHERE org_id=?",
        (json.dumps({"checkin_enabled": True, "client_telegram_chat_id": "-5050566880"}), "tig"),
    )
    conn.commit()
    monkeypatch.setattr("sable_platform.cli.checkin_cmds.get_db", lambda: conn)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("SABLE_TELEGRAM_BOT_TOKEN", "fake_token")

    out_dir = tmp_path / "sable-vault" / "tig" / "checkins" / "2026-05-01"
    out_dir.mkdir(parents=True)
    (out_dir / "summary.md").write_text("summary body")
    (out_dir / "deep_dive.md").write_text("deep_dive body")

    with patch(
        "sable_platform.workflows.builtins.client_checkin_loop._send_telegram_message",
        return_value=None,
    ) as mock_send:
        result = CliRunner().invoke(checkin, [
            "send", "--org", "tig", "--date", "2026-05-01",
        ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sent"] is True
    assert mock_send.call_count == 2


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_returns_sorted_descending(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    base = tmp_path / "sable-vault" / "tig" / "checkins"
    for date in ("2026-04-17", "2026-04-24", "2026-05-01"):
        d = base / date
        d.mkdir(parents=True)
        (d / "summary.md").write_text("x")
        (d / "deep_dive.md").write_text("x")

    result = CliRunner().invoke(checkin, ["list", "--org", "tig"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    dates = [r["date"] for r in payload["checkins"]]
    assert dates == ["2026-05-01", "2026-04-24", "2026-04-17"]
    assert all(r["summary"] is True for r in payload["checkins"])


def test_list_empty_when_no_vault(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    result = CliRunner().invoke(checkin, ["list", "--org", "tig"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"org": "tig", "checkins": []}
