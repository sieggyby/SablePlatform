"""Drafter persona (DESIGN §4 ``drafter/persona`` — C3.3 bimodal NULO).

Loads the bimodal NULO prompt (calm + reactive system blocks + the voice
calibration set) into a PROMPT-CACHED structure and selects the register-specific
composer. This is the C3.3 heart:

  * :class:`NuloPersona` holds the two register system blocks + the calibration
    set + the per-client :class:`MantraState` (the ``catchphrase_repetition``
    cadence / repeat-counter — OWNED here per the MEGAPLAN C3.3 scope line).
    :meth:`NuloPersona.from_spec` loads it from a 058 ``autocm_personas`` row
    (the per-client ``calm_prompt`` / ``reactive_prompt`` / ``calibration_set`` /
    ``config``), with :meth:`NuloPersona.default` as the deterministic in-tree
    fallback (the vendored NULO banks) so a client with no seeded persona still
    gets the canonical voice.

  * :meth:`NuloPersona.system_block` returns the register-appropriate system
    string — the LARGE, per-client-STABLE prefix that the C3.1 ``LLMProvider``
    real adapter ships as a single ``cache_control: ephemeral`` content block
    (prompt caching is MANDATORY — claude-api memory + MEGAPLAN §5). The
    calibration examples are folded into this stable prefix so they are cached
    too; only the (delimiter-wrapped) thread context + the message are the
    variable suffix in the user turn.

  * :func:`build_cached_request` proves the cache_control shape against the
    request the real adapter BUILDS (the §6 LLM-seam convention: assert the built
    request, never a live round-trip).

  * The R-4 deterministic fallback (:meth:`NuloPersona.render_fallback`) reuses
    the VENDORED zero-LLM NULO renderer (``render_nulo`` over the packaged
    calm/reactive YAML banks) — so when the provider returns ``None`` (LLM
    disabled / budget exhausted / SDK failure) the deterministic surface carries
    the reply uninterrupted (the LLM is garnish, never the hot path — D-1 / R-4).

VOICE QUALITY is trust-gated by the C4.2 voice spike + Lex sign-off (NOT a build
blocker — MEGAPLAN C3.3 note). C3.3 builds the STRUCTURE correctly; the
mechanical (objective) voice predicates are asserted via the
``spike/scorer.py`` predicate subset ported into the test suite.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from sable_platform._vendor.sable_pulse_core import (
    NULO_CALM,
    NULO_REACTIVE,
    Register,
    load_nulo,
    persona_data_dir,
    render_nulo,
)
from sable_platform.autocm.classifier.register import CALM, REACTIVE, REGISTERS
from sable_platform.autocm.kb.store import KBChunk
from sable_platform.autocm.llm import DEFAULT_MODEL, LLMProvider

# Max output tokens for a drafted reply (tweet-shaped; VOICE.md "three fragments
# comfortable, five verbose"). Generous enough for incident-mode status updates.
DRAFT_MAX_TOKENS = 384

# The vendored NULO bank key (the packaged calm/reactive YAML) — the deterministic
# R-4 fallback and the default-persona prompt anchor.
_VENDOR_PERSONA_KEY = "robotmoney"

# Map a classifier hard-refusal / charged category onto the vendored reactive bank
# template key for the deterministic fallback. Anything not here falls back to the
# generic ``refusal`` template (the bank's `default`-tagged entry).
_CATEGORY_TO_REACTIVE_KEY: Dict[str, str] = {
    "price_prediction": "price_prediction",
    "financial_advice": "financial_advice",
    "personal_portfolio": "personal_portfolio",
    "legal": "legal_regulatory",
    "legal_regulatory": "legal_regulatory",
    "insider_information": "insider_information",
    "partnership_unannounced": "insider_information",
    "prompt_injection": "prompt_injection",
    "prompt_injection_direct": "prompt_injection",
    "prompt_injection_persona_swap": "prompt_injection",
}

# Map a calm category onto the vendored calm bank key for the deterministic
# fallback. Anything not here uses the bank's `default`-tagged calm line.
_CATEGORY_TO_CALM_KEY: Dict[str, str] = {
    "greeting": "greeting",
    "glossary": "glossary_wrap",
    "mechanics": "glossary_wrap",
    "trust": "slotfill_wrap",
    "price": "slotfill_wrap",
    "status": "slotfill_wrap",
}


# ---------------------------------------------------------------------------
# catchphrase_repetition — the per-client mantra cadence / repeat-counter.
# OWNED by C3.3 (MEGAPLAN C3.3 scope + CLASSIFIER §2 catchphrase_repetition row).
# ---------------------------------------------------------------------------
@dataclass
class MantraState:
    """Per-client mantra cadence + repeat-counter for ``catchphrase_repetition``.

    The tier-1 ``catchphrase_repetition`` category (Bible §IX.4 "slow drip … repeat
    until myth") needs per-client STATE: the canonical mantra line, how often it may
    surface (the cadence), and how many times it has been emitted so far (the
    repeat-counter). That state lives on the COMPOSE side (C3.3), not the classifier
    — the classifier only tags the category; the drafter decides whether THIS reply
    is a cadence beat.

    SLOW-DRIP CADENCE — DEFERRED (explicit §8 deferral, Bible §IX.4):
      The *automatic* slow-drip scheduling (only inject the mantra every Nth eligible
      reply, escalating "until myth") is DEFERRED. v1 ships the STATE + the
      :meth:`should_emit` predicate + the :meth:`record_emit` counter so the cadence
      can be turned on without a schema/owner change, but v1's default ``cadence`` is
      ``0`` = "operator/HITL-driven, no auto-drip" — the operator decides each beat.
      This is an EXPLICIT deferral per the MEGAPLAN C3.3 scope line ("if the slow-drip
      cadence is deferred, it is deferred explicitly … with the Bible §IX.4
      citation"). The CATEGORY itself is NOT dropped — it stays tier-1 autonomous in
      the C3.4b registry; only the auto-cadence scheduling is deferred.
    """

    mantra: Optional[str] = None
    #: replies between mantra beats. 0 = no auto-drip (DEFERRED — operator-driven).
    cadence: int = 0
    #: how many times the mantra has been emitted (the "repeat until myth" counter).
    repeat_count: int = 0
    #: eligible replies seen since the last mantra beat (drives the cadence when on).
    since_last: int = 0

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "MantraState":
        """Build from the persona ``config`` blob's ``catchphrase`` section (or empty).

        Shape (all optional): ``{"catchphrase": {"mantra": "...", "cadence": N,
        "repeat_count": N, "since_last": N}}``. A missing / malformed section yields
        the inert default (no mantra, cadence 0 = deferred auto-drip).
        """
        cfg = (config or {}).get("catchphrase") or {}
        if not isinstance(cfg, dict):
            return cls()
        try:
            cadence = int(cfg.get("cadence", 0) or 0)
        except (TypeError, ValueError):
            cadence = 0
        try:
            repeat_count = int(cfg.get("repeat_count", 0) or 0)
        except (TypeError, ValueError):
            repeat_count = 0
        try:
            since_last = int(cfg.get("since_last", 0) or 0)
        except (TypeError, ValueError):
            since_last = 0
        mantra = cfg.get("mantra")
        return cls(
            mantra=mantra if isinstance(mantra, str) and mantra.strip() else None,
            cadence=max(0, cadence),
            repeat_count=max(0, repeat_count),
            since_last=max(0, since_last),
        )

    def should_emit(self) -> bool:
        """True iff THIS eligible reply is a cadence beat (the mantra should surface).

        DEFERRED-cadence semantics: when ``cadence <= 0`` (the v1 default) this is
        always ``False`` — the auto-drip is off, the operator drives the beats. When
        an operator enables a positive cadence, the beat fires once ``since_last`` has
        reached ``cadence`` (and a mantra is configured).
        """
        if not self.mantra or self.cadence <= 0:
            return False
        return self.since_last >= self.cadence

    def record_emit(self) -> None:
        """Record that the mantra was emitted this reply (advance the counter)."""
        self.repeat_count += 1
        self.since_last = 0

    def record_skip(self) -> None:
        """Record an eligible reply that did NOT carry the mantra (advance cadence)."""
        self.since_last += 1


# ---------------------------------------------------------------------------
# DraftRequest / DraftResult — the compose contract (extends the C3.1 skeleton).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DraftRequest:
    """Everything the drafter needs to compose one reply.

    ``register`` is the C3.4b classifier output (calm | reactive). ``category`` is
    the classified category (drives the deterministic-fallback template selection
    and the hard-refusal handling). ``thread_context`` is the last-N=5 turns from
    :mod:`sable_platform.autocm.drafter.thread_context` (delimiter-wrapped before it
    reaches the LLM — SAFETY §2). ``seed`` makes the deterministic fallback
    reproducible per message.
    """

    client_id: int
    text: str
    register: str = CALM
    category: Optional[str] = None
    is_refusal: bool = False
    kb_chunks: List[KBChunk] = field(default_factory=list)
    thread_context: List[str] = field(default_factory=list)
    seed: str = ""


@dataclass(frozen=True)
class DraftResult:
    """A composed draft + the chunk_ids it cited (the C3.5a citation-gate input)."""

    text: str
    register: str
    cited_chunk_ids: List[int] = field(default_factory=list)
    used_llm: bool = True
    reasoning: str = ""


class Drafter(Protocol):
    """Compose a NULO-voice reply for a :class:`DraftRequest`."""

    async def compose(self, request: DraftRequest) -> DraftResult:
        ...


# ---------------------------------------------------------------------------
# NuloPersona — the bimodal prompt + calibration set, prompt-cache structured.
# ---------------------------------------------------------------------------
@dataclass
class NuloPersona:
    """The loaded bimodal NULO persona (calm + reactive prompts + calibration set).

    The two register system blocks are the LARGE, per-client-STABLE cache prefixes;
    the calibration examples are FOLDED INTO the stable prefix (so they cache with
    it) via :meth:`system_block`. The per-client :class:`MantraState` carries the
    ``catchphrase_repetition`` cadence/repeat-counter (C3.3-owned).
    """

    calm_prompt: str
    reactive_prompt: str
    calibration_set: Dict[str, Any] = field(default_factory=dict)
    mantra: MantraState = field(default_factory=MantraState)
    #: the vendored deterministic banks (R-4 fallback); resolved lazily on default().
    _banks: Optional[Dict[str, Register]] = None

    # -- construction -------------------------------------------------------
    @classmethod
    def default(cls) -> "NuloPersona":
        """The canonical in-tree default persona (the vendored NULO banks).

        Used when a client has no seeded ``autocm_personas`` row (the safe floor —
        every client gets the canonical NULO voice). The system blocks are derived
        from the vendored calm/reactive YAML banks so the prompt and the R-4
        deterministic fallback share one source of truth.
        """
        banks = load_nulo(persona_data_dir(_VENDOR_PERSONA_KEY, "nulo"))
        calm = _system_from_bank(banks.get(NULO_CALM), CALM)
        reactive = _system_from_bank(banks.get(NULO_REACTIVE), REACTIVE)
        return cls(
            calm_prompt=calm,
            reactive_prompt=reactive,
            calibration_set={},
            mantra=MantraState(),
            _banks=banks,
        )

    @classmethod
    def from_spec(cls, spec: Any) -> "NuloPersona":
        """Build from a 058 ``autocm_personas`` row (a ``PersonaSpec``).

        Per-client ``calm_prompt`` / ``reactive_prompt`` override the default; a
        missing register prompt falls back to the vendored-derived default block so a
        half-configured persona still composes. ``calibration_set`` + ``config``
        (mantra) come from the spec. ``spec`` is duck-typed (the
        :class:`~sable_platform.autocm.loaders.PersonaSpec` dataclass) so this module
        does not import the loader (keeps the dependency arrow one-way).
        """
        base = cls.default()
        calm = getattr(spec, "calm_prompt", None) or base.calm_prompt
        reactive = getattr(spec, "reactive_prompt", None) or base.reactive_prompt
        calibration = getattr(spec, "calibration_set", None) or {}
        config = getattr(spec, "config", None) or {}
        return cls(
            calm_prompt=calm,
            reactive_prompt=reactive,
            calibration_set=calibration if isinstance(calibration, dict) else {},
            mantra=MantraState.from_config(config if isinstance(config, dict) else {}),
            _banks=base._banks,
        )

    # -- prompt-cached system block ----------------------------------------
    def _base_prompt(self, register: str) -> str:
        return self.reactive_prompt if register == REACTIVE else self.calm_prompt

    def system_block(self, register: str) -> str:
        """Return the register-appropriate STABLE system prefix (the cache prefix).

        The register base prompt + the (per-register) calibration examples folded in
        — one stable string per (client, register). This is the EXACT string the
        real adapter ships as a single ``cache_control: ephemeral`` content block
        (prompt caching mandatory). An unknown register defaults to calm (the safe
        floor — calm is never the wrong register to over-soften toward).
        """
        reg = register if register in REGISTERS else CALM
        base = self._base_prompt(reg)
        examples = _calibration_examples_block(self.calibration_set, reg)
        if examples:
            return f"{base}\n\n{examples}"
        return base

    # -- R-4 deterministic fallback (vendored zero-LLM renderer) -----------
    def _ensure_banks(self) -> Dict[str, Register]:
        if self._banks is None:
            self._banks = load_nulo(persona_data_dir(_VENDOR_PERSONA_KEY, "nulo"))
        return self._banks

    def render_fallback(self, request: DraftRequest) -> str:
        """Deterministic, zero-LLM NULO line for ``request`` (the R-4 fallback).

        Picks the register-appropriate vendored bank template by mapping the
        category to a bank key; reactive hard-refusals map to their calibrated §1
        refusal template, calm categories to their wrap template. Always returns a
        non-empty in-voice line (the bank's `default`-tagged fallback guarantees it),
        so the deterministic surface NEVER returns empty when the LLM is absent.
        """
        banks = self._ensure_banks()
        register = REACTIVE if (request.is_refusal or request.register == REACTIVE) else CALM
        if register == REACTIVE:
            key = _CATEGORY_TO_REACTIVE_KEY.get(request.category or "", "refusal")
        else:
            key = _CATEGORY_TO_CALM_KEY.get(request.category or "", "default")
        slots = _fallback_slots(request)
        line = render_nulo(banks, register, key, slots=slots, seed=request.seed or request.text)
        return line or _LAST_RESORT_LINE


def _system_from_bank(bank: Optional[Register], register: str) -> str:
    """Derive a default system prompt block from a vendored register bank.

    The bank YAML carries the canonical in-voice exemplars; we surface them as the
    register's instruction prefix so the default persona's prompt and the R-4
    fallback render share one source of truth (no second hand-maintained copy).
    """
    label = "REACTIVE (charged contexts — classification-tag HK-47 register)" if register == REACTIVE else "CALM (default — lowercase Bill-Monday register)"
    lines = [
        "You are NULO, an autonomous community management agent for RobotMoney.",
        f"Active register: {label}.",
    ]
    if register == REACTIVE:
        lines += [
            "Lead every message with EXACTLY ONE classification tag "
            "(Statement: / Query: / Answer: / Observation: / Correction: / "
            "Disclosure: / Acknowledgment: / Refusal: / Restatement: / Update:).",
            "Capitalized, period-separated short fragments. No apologies. "
            "Cite on-chain facts before adjectives. Never predict/advise/opine on price.",
        ]
    else:
        lines += [
            "Lowercase (exceptions: proper nouns, hex addresses, acronyms like TVL, ERC-4626).",
            "NO classification tags. Tweet-shaped period-ended short fragments. "
            "No apologies. NEVER use 'meatbag' in calm. Cite a fact before an adjective.",
        ]
    if bank and bank.templates:
        lines.append("In-voice reference lines:")
        for t in bank.templates:
            txt = (t.get("text") or "").strip()
            if txt:
                lines.append(f"- {txt}")
    lines += [
        "",
        "Output ONLY a single JSON object: "
        '{"register": "calm"|"reactive", "draft": "<reply>", "reasoning": "<one sentence>"}. '
        "No markdown, no code fences.",
    ]
    return "\n".join(lines)


def _calibration_examples_block(calibration_set: Dict[str, Any], register: str) -> str:
    """Fold the per-register calibration examples into the cached system prefix.

    The calibration set shape is permissive — a list of ``{register, message, reply}``
    dicts under ``examples`` (or a flat list). Only the examples matching ``register``
    are included so each register's cache prefix carries its OWN anchors. Returns ``""``
    when there is nothing to fold (the system block is then just the base prompt).
    """
    examples = calibration_set.get("examples") if isinstance(calibration_set, dict) else None
    if not isinstance(examples, list) or not examples:
        return ""
    rendered: List[str] = []
    for ex in examples:
        if not isinstance(ex, dict):
            continue
        if (ex.get("register") or CALM) != register:
            continue
        msg = (ex.get("message") or "").strip()
        reply = (ex.get("reply") or ex.get("draft") or "").strip()
        if reply:
            if msg:
                rendered.append(f"USER: {msg}\nNULO: {reply}")
            else:
                rendered.append(f"NULO: {reply}")
    if not rendered:
        return ""
    return "Calibration examples (match this voice exactly):\n" + "\n\n".join(rendered)


def _fallback_slots(request: DraftRequest) -> Dict[str, str]:
    """Best-effort slot dict for the vendored fallback render from the KB chunks.

    The vendored calm wrap templates reference ``{term}``/``{definition}`` or
    ``{label}``/``{value}``; we fill them from the top KB chunk when present so a
    calm informational fallback still carries a fact (cite-before-adjective). Missing
    slots degrade gracefully (the renderer returns the un-filled text).
    """
    slots: Dict[str, str] = {}
    if request.kb_chunks:
        top = request.kb_chunks[0]
        slots["definition"] = top.text
        slots["value"] = top.text
        slots["term"] = request.text.strip()[:48] or "this"
        slots["label"] = request.text.strip()[:48] or "this"
    return slots


# the absolute last-resort line if even the vendored bank somehow renders empty.
_LAST_RESORT_LINE = "noted. the agents are at it. pinned doc likely has what you need."


# ---------------------------------------------------------------------------
# The prompt-cached request builder — proves cache_control via the seam.
# ---------------------------------------------------------------------------
def build_cached_request(
    provider: LLMProvider,
    persona: NuloPersona,
    register: str,
    user_prompt: str,
    *,
    max_tokens: int = DRAFT_MAX_TOKENS,
    model: Optional[str] = None,
) -> dict:
    """Build the EXACT request the adapter would send for a drafter call.

    Routes through the provider's :meth:`build_request` (the C3.1 seam) so the
    ``cache_control: ephemeral`` on the system block is the adapter's, not
    re-implemented here. The cached system block is ``persona.system_block(register)``
    (the stable per-(client,register) prefix); ``user_prompt`` is the variable suffix
    (the delimiter-wrapped thread context + message). PURE — no network, no SDK
    import — so tests assert the cache_control shape against the BUILT request
    (the §6 convention).

    The provider must expose ``build_request`` (the real :class:`AnthropicProvider`
    does). A provider without it (e.g. a bare ``NullLLMProvider``) cannot be
    cache-asserted — but it is never the production path; production selects the
    real adapter, whose ``build_request`` is the asserted contract.
    """
    return provider.build_request(  # type: ignore[attr-defined]
        persona.system_block(register),
        user_prompt,
        max_tokens=max_tokens,
        model=model or DEFAULT_MODEL,
    )


def parse_draft(raw: Optional[str], *, register: str) -> Optional[tuple]:
    """Parse an LLM completion into ``(draft_text, register, reasoning)`` or None.

    The drafter prompt asks for ``{"register", "draft", "reasoning"}`` JSON (the same
    discipline as the spike persona prompt). A ``None`` / empty / non-JSON / no-draft
    completion returns ``None`` so the caller falls back to the deterministic render
    (R-4). The model's emitted ``register`` is honored only if valid; otherwise the
    requested ``register`` stands (the classifier's choice is the floor).
    """
    if not raw:
        return None
    body = raw.strip()
    # tolerate a stray code fence even though the prompt forbids it.
    if body.startswith("```"):
        body = body.strip("`")
        if "\n" in body:
            body = body.split("\n", 1)[1]
    try:
        obj = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    draft = obj.get("draft")
    if not isinstance(draft, str) or not draft.strip():
        return None
    emitted = obj.get("register")
    out_register = emitted if emitted in REGISTERS else register
    reasoning = obj.get("reasoning") if isinstance(obj.get("reasoning"), str) else ""
    return (draft.strip(), out_register, reasoning)


__all__ = [
    "DRAFT_MAX_TOKENS",
    "MantraState",
    "DraftRequest",
    "DraftResult",
    "Drafter",
    "NuloPersona",
    "build_cached_request",
    "parse_draft",
]
