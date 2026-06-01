"""AutoCM adversarial (DESIGN §4 ``adversarial/``).

``regression`` — the daily adversarial regression harness (prompt-injection incl.
thread-context-poisoning + author-tag-injection, voice-drift, refusal-bypass) run
against the LIVE pipeline; results → ``autocm_adversarial_runs`` + an
``injection_blocked`` audit row per blocked injection. Full impl = C3.9.
"""
from __future__ import annotations

from .regression import (
    AdversarialCase,
    AdversarialHarness,
    AdversarialResult,
    CaseResult,
    LivePipelineAdversarialHarness,
    NotImplementedAdversarialHarness,
    default_cases,
    record_run,
    run_case,
)

__all__ = [
    "AdversarialCase",
    "AdversarialHarness",
    "AdversarialResult",
    "CaseResult",
    "LivePipelineAdversarialHarness",
    "NotImplementedAdversarialHarness",
    "default_cases",
    "record_run",
    "run_case",
]
