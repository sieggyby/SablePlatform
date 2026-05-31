"""AutoCM adversarial (DESIGN §4 ``adversarial/``).

``regression`` — the daily adversarial regression harness (prompt-injection,
voice-drift, refusal-bypass), results → ``autocm_adversarial_runs``. Skeleton;
full impl = C3.9.
"""
from __future__ import annotations

from .regression import AdversarialHarness, NotImplementedAdversarialHarness

__all__ = ["AdversarialHarness", "NotImplementedAdversarialHarness"]
