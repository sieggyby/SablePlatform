"""Phase -1 NULO voice-spike harness (MEGAPLAN C4.2).

The load-bearing voice-viability gate that runs the bimodal NULO drafter over a
fixed pack of >=50 representative messages, drafting in the *classifier-selected*
register per message (calm vs reactive via the production
:func:`sable_platform.autocm.classifier.register.choose_register`), scores every
draft against the ported ``spike/scorer.py`` predicate subset, and ENFORCES the
two-part engineering exit gate as a REAL assertion (NOT an advisory log line):

  (1) ``aggregate['pass_rate'] >= 0.75`` — the headline acceptance threshold
      (the same 0.75 the donor ``run_spike.py`` exits 0/1 on); AND
  (2) ``min(calm_pass_rate, reactive_pass_rate) >= 0.60`` — the NET-NEW
      per-register floor C4.2 must ADD (no single register may collapse while the
      aggregate still clears 0.75). This replaces the unmeasurable "failures
      clustered" vibe criterion.

The harness drives the LLM through the C3.1 seam using a deterministic FAKE
provider (:class:`FakeSpikeLLMProvider`) — NO real ``anthropic``, NO network — so
it runs unmodified in CI. The fake returns canned, register-appropriate
``{"register","draft","reasoning"}`` JSON; the scorer then measures
register-SELECTION (did the production register chooser pick the expected register)
+ guardrail/voice compliance (the mechanical ``scorer.py`` predicates).

The harness is the auto-checkable engineering gate ONLY. The separate, explicitly
human "Lex sign-off" program gate (the 30-sample "Lex pack" artifact this harness
produces) is NOT part of the tests-green chunk gate — the two are not conflated
(MEGAPLAN C4.2 exit/audit, two distinct gates).

NO migration: the harness is entirely in-process (a fixed message pack +
deterministic provider + scorer); it persists nothing to the DB.
"""
from __future__ import annotations

from sable_platform.autocm.spike.messages import (
    LEX_PACK,
    SPIKE_MESSAGES,
    SpikeMessage,
    lex_pack,
    spike_pack,
)
from sable_platform.autocm.spike.provider import FakeSpikeLLMProvider
from sable_platform.autocm.spike.runner import (
    AGGREGATE_PASS_RATE_FLOOR,
    PER_REGISTER_PASS_RATE_FLOOR,
    GateResult,
    SpikeResult,
    aggregate_scores,
    evaluate_gate,
    run_spike,
)
from sable_platform.autocm.spike.scorer import Score, score_response

__all__ = [
    "SpikeMessage",
    "SPIKE_MESSAGES",
    "LEX_PACK",
    "spike_pack",
    "lex_pack",
    "FakeSpikeLLMProvider",
    "Score",
    "score_response",
    "aggregate_scores",
    "run_spike",
    "evaluate_gate",
    "SpikeResult",
    "GateResult",
    "AGGREGATE_PASS_RATE_FLOOR",
    "PER_REGISTER_PASS_RATE_FLOOR",
]
