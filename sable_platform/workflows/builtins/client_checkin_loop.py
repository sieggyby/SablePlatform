"""Workflow: client_checkin_loop — weekly Friday client-facing check-in.

7 steps: collect → deltas → render → synthesize → assemble → snapshot → send.

Step boundaries follow the engine's JSON-serialization constraint by
checkpointing intermediate state to `~/sable-vault/<org>/checkins/<DATE>/`.
Each step writes its output to disk; the next step reads it. The vault
directory IS the durable checkpoint — restart-safe and human-inspectable.

Telegram delivery is gated by ``orgs.config_json.checkin_enabled`` and
``client_telegram_chat_id``. ``dry_run: true`` in workflow config skips
synthesize (uses canned text) AND skips send.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sable_platform.checkin.collector import CheckinInputs, collect_inputs
from sable_platform.checkin.deltas import DeltaReport, MetricDelta, compute_deltas
from sable_platform.checkin.render import render_data_sections
from sable_platform.checkin.synthesize import (
    SYSTEM_PROMPT,
    SynthesisResult,
    synthesize as synthesize_call,
)
from sable_platform.db.cost import log_cost
from sable_platform.db import snapshots as snapshot_store
from sable_platform.errors import SableError, INVALID_CONFIG, STEP_EXECUTION_ERROR
from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.workflows import registry


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault checkpoint helpers
# ---------------------------------------------------------------------------

DEFAULT_VAULT_ROOT = Path.home() / "sable-vault"
DEFAULT_PROJECT_SLUG_BY_ORG = {"tig": "the-innovation-game_tigfoundation"}


def _vault_root(ctx) -> Path:
    explicit = ctx.config.get("vault_root")
    if explicit:
        return Path(explicit)
    return DEFAULT_VAULT_ROOT


def _checkin_dir(ctx) -> Path:
    run_date = ctx.config.get("run_date") or _dt.date.today().isoformat()
    base = _vault_root(ctx) / ctx.org_id / "checkins" / run_date
    base.mkdir(parents=True, exist_ok=True)
    return base


def _project_slug(ctx) -> str:
    explicit = ctx.config.get("project_slug")
    if explicit:
        return explicit
    return DEFAULT_PROJECT_SLUG_BY_ORG.get(ctx.org_id, ctx.org_id)


def _cult_grader_repo(ctx) -> Path:
    explicit = ctx.config.get("cult_grader_repo")
    if explicit:
        return Path(explicit)
    env = os.environ.get("SABLE_CULT_GRADER_PATH")
    if not env:
        raise SableError(INVALID_CONFIG, "SABLE_CULT_GRADER_PATH not set")
    return Path(env)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 1 — collect_inputs
# ---------------------------------------------------------------------------

def _collect_inputs(ctx) -> StepResult:
    run_date = ctx.config.get("run_date") or _dt.date.today().isoformat()
    inputs = collect_inputs(
        ctx.db,
        ctx.org_id,
        run_date=run_date,
        cult_grader_repo=_cult_grader_repo(ctx),
        project_slug=_project_slug(ctx),
    )
    out_path = _checkin_dir(ctx) / "_collected.json"
    _write_json(out_path, asdict(inputs))
    return StepResult(
        "completed",
        {
            "checkin_dir": str(_checkin_dir(ctx)),
            "collected_path": str(out_path),
            "run_date": run_date,
            "previous_snapshot_date": inputs.previous_snapshot_date,
        },
    )


# ---------------------------------------------------------------------------
# Step 2 — compute_deltas
# ---------------------------------------------------------------------------

def _compute_deltas(ctx) -> StepResult:
    collected_path = Path(ctx.input_data["collected_path"])
    raw = _read_json(collected_path)
    deltas = compute_deltas(
        raw.get("tier1") or {},
        raw.get("tier2") or {},
        raw.get("previous_metrics") or {},
    )
    out_path = Path(ctx.input_data["checkin_dir"]) / "_deltas.json"
    _write_json(out_path, deltas.as_dict())
    return StepResult("completed", {"deltas_path": str(out_path)})


# ---------------------------------------------------------------------------
# Step 3 — render_data_sections
# ---------------------------------------------------------------------------

def _render_data_sections(ctx) -> StepResult:
    raw_inputs = _read_json(Path(ctx.input_data["collected_path"]))
    raw_deltas = _read_json(Path(ctx.input_data["deltas_path"]))

    inputs = CheckinInputs(**raw_inputs)
    deltas = DeltaReport(
        tier1=[MetricDelta(**d) for d in raw_deltas.get("tier1", [])],
        tier2=[MetricDelta(**d) for d in raw_deltas.get("tier2", [])],
    )

    sections = render_data_sections(inputs, deltas)
    out_path = Path(ctx.input_data["checkin_dir"]) / "_data_sections.json"
    _write_json(out_path, sections)
    return StepResult("completed", {"data_sections_path": str(out_path)})


# ---------------------------------------------------------------------------
# Step 4 — synthesize_prose
# ---------------------------------------------------------------------------

_DRY_RUN_SUMMARY = (
    "## SUMMARY\n"
    "- (dry-run) synthesize step skipped — no Anthropic call made.\n"
    "- See data tables below for this week's numbers.\n"
)
_DRY_RUN_DEEP_DIVE = (
    "## DEEP_DIVE\n\n"
    "### Tier 1\n_(dry-run placeholder)_\n\n"
    "### Tier 2\n_(dry-run placeholder)_\n\n"
    "### Tier 3\n_(dry-run placeholder)_"
)


def _synthesize_prose(ctx) -> StepResult:
    raw_inputs = _read_json(Path(ctx.input_data["collected_path"]))
    raw_deltas = _read_json(Path(ctx.input_data["deltas_path"]))
    sections = _read_json(Path(ctx.input_data["data_sections_path"]))

    if ctx.config.get("dry_run"):
        synth_path = Path(ctx.input_data["checkin_dir"]) / "_synthesis.json"
        _write_json(synth_path, {
            "summary_prose": _DRY_RUN_SUMMARY,
            "deep_dive_prose": _DRY_RUN_DEEP_DIVE,
            "model": "dry_run",
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "cost_usd": 0.0,
            "raw_text": _DRY_RUN_SUMMARY + "\n---\n" + _DRY_RUN_DEEP_DIVE,
        })
        return StepResult("completed", {"synthesis_path": str(synth_path), "synthesis_dry_run": True})

    inputs = CheckinInputs(**raw_inputs)
    deltas = DeltaReport(
        tier1=[MetricDelta(**d) for d in raw_deltas.get("tier1", [])],
        tier2=[MetricDelta(**d) for d in raw_deltas.get("tier2", [])],
    )

    result: SynthesisResult = synthesize_call(inputs, deltas, sections)

    log_cost(
        ctx.db,
        ctx.org_id,
        call_type="checkin_synthesize",
        cost_usd=result.cost_usd,
        model=result.model,
        input_tokens=result.input_tokens + result.cache_read_input_tokens + result.cache_creation_input_tokens,
        output_tokens=result.output_tokens,
        call_status="success",
    )

    synth_path = Path(ctx.input_data["checkin_dir"]) / "_synthesis.json"
    _write_json(synth_path, asdict(result))
    return StepResult(
        "completed",
        {
            "synthesis_path": str(synth_path),
            "synthesis_cost_usd": result.cost_usd,
            "synthesis_model": result.model,
        },
    )


# ---------------------------------------------------------------------------
# Step 5 — assemble_artifact
# ---------------------------------------------------------------------------

def _assemble_artifact(ctx) -> StepResult:
    sections = _read_json(Path(ctx.input_data["data_sections_path"]))
    synth = _read_json(Path(ctx.input_data["synthesis_path"]))

    summary_md = (
        f"{synth['summary_prose']}\n\n"
        f"---\n\n"
        f"{sections['header']}\n"
    )
    deep_dive_md = (
        f"{synth['deep_dive_prose']}\n\n"
        f"---\n\n"
        f"{sections['header']}\n\n"
        f"{sections['tier1_table']}\n\n"
        f"{sections['tier2_table']}\n\n"
        f"{sections['tier3_table']}\n"
    )

    checkin_dir = Path(ctx.input_data["checkin_dir"])
    summary_path = checkin_dir / "summary.md"
    deep_dive_path = checkin_dir / "deep_dive.md"
    summary_path.write_text(summary_md, encoding="utf-8")
    deep_dive_path.write_text(deep_dive_md, encoding="utf-8")

    return StepResult(
        "completed",
        {
            "summary_path": str(summary_path),
            "deep_dive_path": str(deep_dive_path),
            "summary_chars": len(summary_md),
            "deep_dive_chars": len(deep_dive_md),
        },
    )


# ---------------------------------------------------------------------------
# Step 6 — snapshot_metrics
# ---------------------------------------------------------------------------

def _snapshot_metrics(ctx) -> StepResult:
    """Persist this Friday's metrics so next Friday has a WoW baseline.

    Skipped in dry_run so smoke tests don't poison the real WoW chain —
    otherwise a `--dry-run` for next Friday would land a snapshot that the
    next real run would treat as its prior baseline.
    """
    if ctx.config.get("dry_run"):
        return StepResult(
            "completed",
            {"snapshot_id": None, "skipped_reason": "dry_run"},
        )

    raw_inputs = _read_json(Path(ctx.input_data["collected_path"]))
    inputs = CheckinInputs(**raw_inputs)
    snapshot_id = snapshot_store.upsert_metric_snapshot(
        ctx.db,
        ctx.org_id,
        inputs.run_date,
        inputs.as_metrics_payload(),
        source="pipeline",
    )
    ctx.db.commit()
    return StepResult(
        "completed",
        {"snapshot_id": snapshot_id, "snapshot_date": inputs.run_date},
    )


# ---------------------------------------------------------------------------
# Step 7 — notify_and_send
# ---------------------------------------------------------------------------

def _send_telegram_message(token: str, chat_id: str, body: str) -> str | None:
    """Send a single TG message. Returns error string or None.

    Mirrors alert_delivery._send_telegram but without the alerts-specific
    HTML/severity wrapper. Telegram message limit is 4096 chars; we truncate
    with a trailing notice rather than fail.
    """
    if len(body) > 4090:
        body = body[:4090] + "…(trim)"
    try:
        data = json.dumps({"chat_id": chat_id, "text": body}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"URLError: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return str(e)


_TRUTHY_STRINGS = frozenset({"true", "yes", "1", "on"})


def _coerce_bool(value) -> bool:
    """`org config set` stores all values as strings. Accept bool or "true"/"false"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return bool(value)


def _read_org_checkin_config(ctx) -> tuple[bool, str | None]:
    row = ctx.db.execute(
        "SELECT config_json FROM orgs WHERE org_id=?", (ctx.org_id,),
    ).fetchone()
    if not row or not row["config_json"]:
        return False, None
    try:
        cfg = json.loads(row["config_json"])
    except (json.JSONDecodeError, TypeError):
        return False, None
    enabled = _coerce_bool(cfg.get("checkin_enabled"))
    chat_id = cfg.get("client_telegram_chat_id")
    return enabled, chat_id


def _notify_and_send(ctx) -> StepResult:
    summary_path = Path(ctx.input_data["summary_path"])
    deep_dive_path = Path(ctx.input_data["deep_dive_path"])
    summary = summary_path.read_text(encoding="utf-8")
    deep_dive = deep_dive_path.read_text(encoding="utf-8")

    if ctx.config.get("dry_run"):
        return StepResult("completed", {"sent": False, "reason": "dry_run"})

    enabled, chat_id = _read_org_checkin_config(ctx)
    if not enabled:
        return StepResult("completed", {"sent": False, "reason": "checkin_disabled"})
    if not chat_id:
        return StepResult("completed", {"sent": False, "reason": "no_client_telegram_chat_id"})

    token = os.environ.get("SABLE_TELEGRAM_BOT_TOKEN")
    if not token:
        return StepResult("completed", {"sent": False, "reason": "no_bot_token"})

    err1 = _send_telegram_message(token, str(chat_id), summary)
    err2 = _send_telegram_message(token, str(chat_id), deep_dive) if not err1 else None

    return StepResult(
        "completed",
        {
            "sent": err1 is None and err2 is None,
            "summary_error": err1,
            "deep_dive_error": err2,
            "chat_id": str(chat_id),
        },
    )


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

CLIENT_CHECKIN_LOOP = WorkflowDefinition(
    name="client_checkin_loop",
    version="1.0",
    steps=[
        StepDefinition(name="collect_inputs", fn=_collect_inputs, max_retries=1),
        StepDefinition(name="compute_deltas", fn=_compute_deltas, max_retries=0),
        StepDefinition(name="render_data_sections", fn=_render_data_sections, max_retries=0),
        # No timeout_seconds: the engine's threaded timeout would put log_cost
        # on a background thread and SQLite connections aren't thread-safe.
        # The Anthropic SDK enforces its own request timeout.
        StepDefinition(name="synthesize_prose", fn=_synthesize_prose, max_retries=2,
                       retry_delay_seconds=10),
        StepDefinition(name="assemble_artifact", fn=_assemble_artifact, max_retries=0),
        StepDefinition(name="snapshot_metrics", fn=_snapshot_metrics, max_retries=1),
        StepDefinition(name="notify_and_send", fn=_notify_and_send, max_retries=1),
    ],
)

registry.register(CLIENT_CHECKIN_LOOP)
