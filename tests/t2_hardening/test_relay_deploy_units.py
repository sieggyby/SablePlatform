"""C4.3 — relay/autocm deployment units + runbook parity & no-inline-secrets gate.

Asserts the three runtime-role deployment units (relay-bot / relay-poller /
autocm-batch), their compose overlay, and the OPERATIONS_RUNBOOK exist and obey
the MEGAPLAN C4.3 invariants:

  * NO inline secret value in any unit (secrets-in-env only, MEGAPLAN §5).
  * each unit points at its committed run-script entrypoint.
  * the single-replica / sole-RateLimiter invariant is encoded for relay-bot and
    explicitly NOT present in the worker units.
  * the runbook covers the three halt modes, the paused->silent->revealed
    rollout, escalation, rollback, and hosting.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _ROOT / "deploy"
_SCRIPTS = _ROOT / "scripts"
_RUNBOOK = _ROOT / "docs" / "OPERATIONS_RUNBOOK.md"

_UNITS = {
    "relay-bot": _DEPLOY / "sable-platform-relay-bot.service",
    "relay-poller": _DEPLOY / "sable-platform-relay-poller.service",
    "autocm-batch": _DEPLOY / "sable-platform-autocm-batch.service",
}
_TIMER = _DEPLOY / "sable-platform-autocm-batch.timer"
_COMPOSE = _DEPLOY / "docker-compose.relay.yaml"

_RUN_SCRIPTS = {
    "relay-bot": _SCRIPTS / "run_relay_bot.py",
    "relay-poller": _SCRIPTS / "run_relay_poller.py",
    "autocm-batch": _SCRIPTS / "run_autocm_batch.py",
}

# Secret-shaped patterns that must NEVER appear inline in a unit file. We allow
# bare env-var NAMES (RELAY_TG_BOT_TOKEN=) but forbid an = followed by a value.
_INLINE_SECRET_PATTERNS = [
    re.compile(r"ANTHROPIC_API_KEY\s*=\s*\S"),
    re.compile(r"RELAY_TG_BOT_TOKEN\s*=\s*\S"),
    re.compile(r"RELAY_DISCORD_BOT_TOKEN\s*=\s*\S"),
    re.compile(r"SOCIALDATA_API_KEY\s*=\s*\S"),
    re.compile(r"sk-[A-Za-z0-9]"),          # anthropic-style key prefix
    re.compile(r"\d{8,}:[A-Za-z0-9_-]{30,}"),  # telegram bot-token shape
]


@pytest.mark.parametrize("name", list(_UNITS))
def test_unit_file_exists(name):
    assert _UNITS[name].exists(), f"missing deployment unit for {name}"


def test_timer_and_compose_exist():
    assert _TIMER.exists(), "missing autocm-batch timer"
    assert _COMPOSE.exists(), "missing relay compose overlay"


@pytest.mark.parametrize("name", list(_RUN_SCRIPTS))
def test_run_script_exists(name):
    assert _RUN_SCRIPTS[name].exists(), f"missing run script for {name}"


@pytest.mark.parametrize("name", list(_UNITS))
def test_unit_has_no_inline_secret(name):
    """Units carry env-file / env NAMES only — never an inline secret VALUE."""
    content = _UNITS[name].read_text()
    for pat in _INLINE_SECRET_PATTERNS:
        m = pat.search(content)
        assert m is None, f"{name}: inline secret-shaped value: {m.group(0)!r}"


def test_compose_has_no_inline_secret():
    """The compose overlay only interpolates ${VAR}; no inline secret value."""
    content = _COMPOSE.read_text()
    # every credential line must be a ${VAR...} interpolation, never a literal.
    for var in ("ANTHROPIC_API_KEY", "RELAY_TG_BOT_TOKEN", "SOCIALDATA_API_KEY"):
        for line in content.splitlines():
            if var in line and "=" in line:
                assert "${" in line, f"compose: {var} not an env interpolation: {line!r}"
    assert not re.search(r"sk-[A-Za-z0-9]", content)


@pytest.mark.parametrize("name", list(_UNITS))
def test_unit_uses_environment_file(name):
    """Secrets reach the unit via EnvironmentFile (systemd), not inline."""
    content = _UNITS[name].read_text()
    assert "EnvironmentFile=" in content, f"{name}: must source secrets via EnvironmentFile"


@pytest.mark.parametrize("name", list(_UNITS))
def test_unit_points_at_its_run_script(name):
    content = _UNITS[name].read_text()
    script = _RUN_SCRIPTS[name].name
    assert script in content, f"{name}: ExecStart must invoke {script}"


@pytest.mark.parametrize("name", list(_UNITS))
def test_unit_sets_distinct_operator_id(name):
    content = _UNITS[name].read_text()
    assert f"SABLE_OPERATOR_ID={name}" in content, f"{name}: must stamp its operator id"


def test_relay_bot_encodes_single_replica_invariant():
    """relay-bot is the SOLE RateLimiter owner -> must document replica=1 pin."""
    content = _UNITS["relay-bot"].read_text()
    low = content.lower()
    assert "ratelimiter" in low
    assert "replica" in low or "single" in low


def test_worker_units_disclaim_ratelimiter():
    """The worker units must NOT host the in-memory limiter (cost-control invariant)."""
    for name in ("relay-poller", "autocm-batch"):
        content = _UNITS[name].read_text()
        assert "ratelimiter" in content.lower(), (
            f"{name}: must explicitly disclaim the in-memory RateLimiter"
        )


def test_compose_relay_bot_pinned_single_replica():
    content = _COMPOSE.read_text()
    assert "replicas: 1" in content, "compose relay-bot must pin replicas: 1"


# --- run-script seam invariants -------------------------------------------

def test_run_scripts_carry_no_inline_secret():
    for path in _RUN_SCRIPTS.values():
        content = path.read_text()
        assert not re.search(r"sk-[A-Za-z0-9]", content), f"{path.name}: inline key"


def test_relay_poller_transport_seams_fail_loud():
    """Until the prod transports are wired, relay-poller must fail loudly."""
    content = _RUN_SCRIPTS["relay-poller"].read_text()
    assert "NotImplementedError" in content
    assert "_build_socialdata_client" in content
    assert "_build_sender" in content


def test_autocm_batch_runs_the_four_batch_workflows():
    content = _RUN_SCRIPTS["autocm-batch"].read_text()
    for wf in (
        "autocm_kb_refresh",
        "autocm_autonomy_sweep",
        "autocm_weekly_digest",
        "autocm_adversarial_sweep",
    ):
        assert wf in content, f"autocm-batch missing workflow {wf}"


# --- runbook coverage ------------------------------------------------------

def test_runbook_exists():
    assert _RUNBOOK.exists()


def test_runbook_covers_three_halt_modes():
    content = _RUNBOOK.read_text()
    assert "/pause-client" in content                       # AutoCM publishing halt
    assert "disable" in content and "pause-org" in content  # relay substrate halt
    assert "48h" in content and "freeze" in content.lower() # SAFETY §6 freeze


def test_runbook_covers_autonomy_rollout():
    content = _RUNBOOK.read_text().lower()
    assert "paused" in content
    assert "silent" in content
    assert "revealed" in content


def test_runbook_covers_required_sections():
    content = _RUNBOOK.read_text().lower()
    for token in ("secret", "rollback", "escalation", "reconcil", "hetzner", "r2"):
        assert token in content, f"runbook missing coverage of {token!r}"


def test_runbook_states_single_replica_invariant():
    content = _RUNBOOK.read_text().lower()
    assert "replica" in content
    assert "shared-store limiter" in content or "shared store limiter" in content
