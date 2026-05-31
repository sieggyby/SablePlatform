"""Adversarial regression harness (DESIGN §4 ``adversarial/regression``).

SKELETON (full impl = C3.9). Daily harness: prompt-injection suite (incl.
thread-context-poisoning + author-tag-injection variants), voice-drift,
refusal-bypass attempts; results → ``autocm_adversarial_runs``. C3.1 fixes the
seam shape only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AdversarialResult:
    suite: str
    passed: int
    failed: int


class AdversarialHarness(Protocol):
    """Run the daily adversarial regression suite for a client."""

    def run_daily(self, client_id: int) -> AdversarialResult:
        ...


class NotImplementedAdversarialHarness:
    """Stub harness — C3.9 replaces it."""

    def run_daily(self, client_id: int) -> AdversarialResult:
        raise NotImplementedError("adversarial regression harness lands in C3.9")


__all__ = ["AdversarialResult", "AdversarialHarness", "NotImplementedAdversarialHarness"]
