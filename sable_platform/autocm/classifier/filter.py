"""Heuristic engagement filter + stateful pre-filter + injection hardening.

(MEGAPLAN C3.4a — the security-sensitive, runtime-state, zero-LLM half of the
classifier; DESIGN §4 ``classifier/filter``, CLASSIFIER §1, LATENCY §2, SAFETY §2/§3.)

Two layers, both ZERO-LLM:

  (i)  **Pure vendored-core heuristic** — :func:`assess_engagement` over the
       vendored ``sable_pulse_core.engagement.assess`` (text-only
       strong-engage / strong-skip / ambiguous, no runtime state, no LLM,
       eliminates ~70% of TG traffic before any model call). This is the D-1
       reuse wired in C3.1; C3.4a builds the stateful gate ON it.

  (ii) **AutoCM-side stateful pre-filter** — :func:`prefilter` runs BEFORE the
       core heuristic and consults AutoCM/relay RUNTIME STATE the dependency-light
       core cannot read. It enforces the CLASSIFIER §1 / LATENCY §2 strong-skips
       that need state, plus the classifier-stage half of SAFETY §3's two-stage
       scan and the CLASSIFIER §4 non-English HITL floor.

The six pre-filter rules (each fires with ZERO LLM calls):

  (a) author in ``autocm_flagged_users`` (auto-silenced) → DROP
  (b) bot-account author                                 → DROP
  (c) another member replied in chat within 60s          → DROP (no pile-on)
  (d) founder / tier-2 team posted in chat within 5 min  → DROP (pre-emption)
  (e) SAFETY §3 hard-block trigger (doxxing/PII request)  → BLOCK (early-detect)
  (f) non-English message                                → HITL (tier-2, never auto)

ORDERING (CLASSIFIER §1 / C3.4a spec, explicit): the SAFETY §3 early-detect (e)
runs BEFORE the budget-skip and the non-English (f) branches, so an injection /
hard-block attempt is flagged + audited at the engage stage REGARDLESS of whether
the downstream C3.5a LLM drafter is ever reached (both fall-through paths skip the
LLM tier classifier). The full rule order is (a)→(b)→(c)→(d)→(e)→budget→(f)→proceed.

**Prompt-injection hardening (SAFETY §2 + CLASSIFIER §3):** every untrusted string
that will enter the downstream (C3.4b) classifier LLM call — ``{message}`` AND
``{author_tags}`` AND ``{thread_context}`` — is XML-delimiter-wrapped here, with the
wrapper-closing tag escaped/stripped from the input so the user content can never
break out of its delimiter. The wrapping is OWNED here and consumed by C3.4b.
``display_name`` / handle from ``relay_members`` is never interpolated as a trusted
instruction-level token — only inside the delimited author block.

No telegram / anthropic import at module top: the stateful layer reaches DB state
through :mod:`sable_platform.autocm.db` named helpers (SQL behind functions), never
an LLM. Deterministic: same state + same text → same decision.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.engine import Connection

# D-1 reuse: the vendored deterministic engagement + safety engines (NOT the
# sibling repo). The pre-filter's SAFETY §3 early-detect uses the same vendored
# bank the late C3.5a gate uses, so engage-stage and gate-stage detection cannot
# diverge.
from sable_platform._vendor.sable_pulse_core import (
    EngagementResult,
    RefusalMatch,
    assess,
)
# The early-detect (rule e) must catch a §3 content-block / prompt-injection
# trigger even when it CO-FIRES with a higher-precedence §1 hard-refusal. The
# public ``check_refusal`` returns only the single highest-precedence match
# (CATEGORIES order: price/financial/… BEFORE prompt_injection BEFORE §3 blocks),
# so it would MASK a doxxing/PII/injection attempt hidden behind a price/financial
# trigger. We therefore run a §3-block-only + injection-only scan over the vendored
# bank's pattern tables DIRECTLY (read-only — these are NOT edited in place, the
# vendored-drift gate still holds), independent of the single-winner precedence.
from sable_platform._vendor.sable_pulse_core import safety as _vendored_safety
from sable_platform.autocm import db as autocm_db  # named SQL helpers (SQL behind functions)


class FilterDecision:
    """The engage/skip/ambiguous decision constants (mirrors the vendored engine)."""

    ENGAGE = "engage"
    SKIP = "skip"
    AMBIGUOUS = "ambiguous"

    ALL = (ENGAGE, SKIP, AMBIGUOUS)


def assess_engagement(
    text: str,
    *,
    is_reply_to_bot: bool,
    is_mention: bool,
    bot_username: Optional[str],
) -> EngagementResult:
    """Run the deterministic text-only engagement heuristic (vendored ``assess``).

    Returns the vendored :class:`EngagementResult` (``decision`` ∈
    :data:`FilterDecision.ALL`). This is the pure layer (i): no runtime state, no
    LLM. The stateful layer (ii) — :func:`prefilter` — runs BEFORE this and may
    short-circuit on state the text-only heuristic cannot see.
    """
    return assess(
        text,
        is_reply_to_bot=is_reply_to_bot,
        is_mention=is_mention,
        bot_username=bot_username,
    )


# ---------------------------------------------------------------------------
# Stateful pre-filter (layer ii) — CLASSIFIER §1 / LATENCY §2 / SAFETY §3 / §4
# ---------------------------------------------------------------------------


class PreFilterAction:
    """Terminal action of the stateful pre-filter.

    ``DROP``    — strong-skip: do nothing, no LLM, no draft (rules a–d).
    ``BLOCK``   — SAFETY §3 hard-block caught at engage stage (rule e): the bot
                  refuses/suppresses; this is audited as an attempt and NEVER
                  consumes an LLM classify/draft.
    ``HITL``    — route to the human review queue (tier-2), never auto-answer
                  (rule f, non-English).
    ``PROCEED`` — no stateful skip fired; fall through to the text-only heuristic
                  (layer i) which yields engage / skip / ambiguous.
    """

    DROP = "drop"
    BLOCK = "block"
    HITL = "hitl"
    PROCEED = "proceed"

    #: actions that mean "no downstream LLM tier-classify / draft happens"
    NO_LLM = (DROP, BLOCK, HITL)


@dataclass(frozen=True)
class PreFilterContext:
    """The runtime facts the stateful pre-filter needs, supplied by the caller.

    Structural flags (``is_bot`` / ``is_reply_to_bot`` / ``is_mention``) come from
    the relay listener's parse of the platform update payload (Telegram exposes
    ``from.is_bot`` directly), exactly like the existing engagement structural
    flags — the pre-filter does NOT itself classify identity, it consumes the
    listener's resolved facts + the DB state. DB-backed rules use the named
    :mod:`sable_platform.autocm.db` helpers, scoped by ``client_id`` / ``org_id``.
    """

    client_id: int
    org_id: str
    chat_row_id: int
    is_bot: bool = False
    is_reply_to_bot: bool = False
    is_mention: bool = False
    member_id: Optional[int] = None
    external_user_id: Optional[str] = None
    bot_username: Optional[str] = None
    budget_exhausted: bool = False
    # window knobs (defaults are the CLASSIFIER §1 locked windows)
    reply_window_seconds: int = 60
    preemption_window_minutes: int = 5


@dataclass(frozen=True)
class PreFilterDecision:
    """The stateful pre-filter outcome.

    ``action`` ∈ :class:`PreFilterAction`. ``rule`` names the firing rule
    (``flagged_user`` / ``bot_account`` / ``recent_reply`` / ``team_preemption`` /
    ``safety_block`` / ``non_english`` / ``proceed``) for the audit row. When
    ``action == BLOCK`` the fired :class:`RefusalMatch` is attached so the audit
    can log the category/trigger and the reactive register is forced (SAFETY §0).
    ``engagement`` carries the layer-(i) :class:`EngagementResult` ONLY when the
    pre-filter fell through to PROCEED (else ``None`` — no heuristic was run
    because the stateful gate already decided).
    """

    action: str
    rule: str
    reason: str
    safety_match: Optional[RefusalMatch] = None
    engagement: Optional[EngagementResult] = None
    # convenience: the downstream register hint for a BLOCK (always reactive).
    register: Optional[str] = None

    @property
    def consumes_llm(self) -> bool:
        """True iff a downstream LLM call (engage-check or tier-classify) happens.

        The stateful actions (DROP / BLOCK / HITL) ALL short-circuit before any
        LLM. On PROCEED, the layer-(i) heuristic verdict decides (CLASSIFIER §0/§1):

          * ``skip``      → no LLM (a strong-skip the heuristic resolved cheaply);
          * ``engage``    → LLM (strong-engage proceeds directly to the C3.4b
                            tier+category classify call);
          * ``ambiguous`` → LLM (the C3.4b engage-check is the deciding stage).

        So strong-skips — whether stateful (a–d, budget) or text-only — are the
        zero-LLM paths, and only PROCEED+engage / PROCEED+ambiguous reach a model.
        """
        if self.action != PreFilterAction.PROCEED:
            return False
        if self.engagement is None:  # defensive: PROCEED always carries one
            return True
        return self.engagement.decision != FilterDecision.SKIP


# A SAFETY §3 content-block is the engage-stage early-detect target (rule e).
# The §1 hard-refusals (price/financial/legal…) are CHARGED content the vendored
# engagement heuristic deliberately keeps as ENGAGE so the late C3.5a safety gate
# sees them in voice; only the §3 universal CONTENT BLOCKS (doxxing / PII /
# allegations / adult / OFAC / competitor-disparage) are dropped pre-LLM here,
# because the bot must never even classify/draft on them. Prompt injection (§1) is
# ALSO early-blocked here: an injection attempt must be flagged + audited at engage
# time and must NOT reach the LLM (SAFETY §2).
_EARLY_BLOCK_KINDS = ("content_block",)
_EARLY_BLOCK_CATEGORIES = ("prompt_injection",)

# The prompt-injection slice of the vendored §1 hard-refusal bank, isolated so the
# engage-stage early-detect can scan it independent of ``check_refusal``'s
# single-winner precedence (read-only view of the vendored table — never edited).
_INJECTION_PATTERNS = [
    (cat, pat)
    for (cat, pat) in _vendored_safety._HARD_PATTERNS
    if cat == "prompt_injection"
]


def _early_block_match(text: str) -> Optional[RefusalMatch]:
    """Return the rule-(e) early-block :class:`RefusalMatch` for ``text``, or None.

    The early-detect target is the SAFETY §3 universal CONTENT BLOCKS plus §1
    prompt-injection. Crucially this is computed INDEPENDENT of ``check_refusal``'s
    CATEGORIES-order single-winner precedence: a §3 block or an injection trigger
    that CO-FIRES with a higher-precedence §1 hard-refusal (price/financial/…)
    would otherwise be MASKED (``check_refusal`` would report the price/financial
    category and ``_is_early_block`` would fall through to PROCEED). We instead scan
    the §3 block table and the prompt-injection slice of the §1 table DIRECTLY.

    Precedence within the early-block set mirrors SAFETY §2's ordering intent:
    prompt-injection (an adversarial event that must be audited AS injection) is
    reported ahead of a co-firing §3 content block.
    """
    body = text or ""
    if not body:
        return None
    inj = _vendored_safety._first_match(body, _INJECTION_PATTERNS)
    if inj is not None:
        candidate = RefusalMatch(category=inj[0], kind="hard_refusal", trigger=inj[1])
        if _is_early_block(candidate):  # prompt_injection ∈ _EARLY_BLOCK_CATEGORIES
            return candidate
    block = _vendored_safety._first_match(body, _vendored_safety._BLOCK_PATTERNS)
    if block is not None:
        candidate = RefusalMatch(
            category=block[0], kind="content_block", trigger=block[1]
        )
        if _is_early_block(candidate):  # content_block ∈ _EARLY_BLOCK_KINDS
            return candidate
    return None


def _is_early_block(match: RefusalMatch) -> bool:
    """True iff a fired refusal is one the pre-filter blocks BEFORE any LLM.

    The early-block set is the SAFETY §3 universal content blocks (``kind ==
    'content_block'``) plus §1 prompt-injection (``category == 'prompt_injection'``).
    """
    return (
        match.kind in _EARLY_BLOCK_KINDS
        or match.category in _EARLY_BLOCK_CATEGORIES
    )


# Latin-script detector for the non-English HITL floor (rule f). v1 is a
# conservative script check: a message whose LETTER characters are predominantly
# NON-Latin (Cyrillic, CJK, Arabic, Devanagari, …) routes to HITL rather than
# auto-answering (CLASSIFIER §4 — "Non-English messages (v1): punt to HITL;
# v2+ adds a translation layer"). The v2 translation layer stays deferred (§8).
# This intentionally does NOT try to distinguish Latin-script languages (es/fr/
# de) from English — that is a v2 concern; the v1 floor only guarantees that a
# clearly non-Latin-script message never auto-answers.
def _is_probably_non_english(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    non_latin = 0
    for ch in letters:
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = ""
        if not name.startswith("LATIN"):
            non_latin += 1
    # majority of letters are non-Latin script → treat as non-English (v1 floor).
    return non_latin > (len(letters) / 2)


def prefilter(
    conn: Connection,
    text: str,
    ctx: PreFilterContext,
) -> PreFilterDecision:
    """Run the C3.4a stateful pre-filter; ZERO LLM calls on every path.

    Evaluated in the SPEC-MANDATED order so the SAFETY §3 / injection early-detect
    (e) is reached BEFORE the budget-skip and non-English (f) branches:

      (a) flagged-user (auto-silenced)   → DROP
      (b) bot-account                    → DROP
      (c) recent reply within 60s        → DROP (no pile-on)
      (d) team posted within 5 min       → DROP (founder pre-emption)
      (e) SAFETY §3 / injection trigger  → BLOCK (early-detect, audited)
      ──  budget exhausted               → DROP (after e, before f)
      (f) non-English                    → HITL (tier-2, never auto)
      ──  otherwise                      → PROCEED to the text-only heuristic (i)

    The fall-through PROCEED runs :func:`assess_engagement` (layer i) and attaches
    its :class:`EngagementResult` so the caller has the engage/skip/ambiguous
    verdict in one decision object. A PROCEED with an ``ambiguous`` engagement is
    the ONLY path that reaches the C3.4b LLM engage-check.
    """
    body = text or ""

    # (a) auto-silenced flagged user.
    if autocm_db.is_flagged_user(
        conn,
        ctx.client_id,
        member_id=ctx.member_id,
        external_user_id=ctx.external_user_id,
    ):
        return PreFilterDecision(
            action=PreFilterAction.DROP,
            rule="flagged_user",
            reason="author is auto-silenced (autocm_flagged_users)",
        )

    # (b) bot-account identity.
    if ctx.is_bot:
        return PreFilterDecision(
            action=PreFilterAction.DROP,
            rule="bot_account",
            reason="author is a bot account",
        )

    # (c) another community member already replied within the window.
    if autocm_db.member_replied_within(
        conn,
        ctx.chat_row_id,
        seconds=ctx.reply_window_seconds,
        exclude_member_id=ctx.member_id,
        exclude_external_user_id=ctx.external_user_id,
    ):
        return PreFilterDecision(
            action=PreFilterAction.DROP,
            rule="recent_reply",
            reason=(
                f"another member replied within {ctx.reply_window_seconds}s "
                "(no pile-on)"
            ),
        )

    # (d) founder / tier-2 team pre-emption. Excludes the current author (mirroring
    # rule c) so a founder/admin asking in their OWN community chat does not
    # self-suppress a reply to their own just-received message.
    if autocm_db.team_posted_within(
        conn,
        ctx.org_id,
        ctx.chat_row_id,
        minutes=ctx.preemption_window_minutes,
        exclude_member_id=ctx.member_id,
        exclude_external_user_id=ctx.external_user_id,
    ):
        return PreFilterDecision(
            action=PreFilterAction.DROP,
            rule="team_preemption",
            reason=(
                f"founder/team posted within {ctx.preemption_window_minutes}min "
                "(pre-emption)"
            ),
        )

    # (e) SAFETY §3 / injection early-detect — BEFORE budget + non-English.
    # Scanned independent of check_refusal()'s single-winner precedence so a §3
    # content-block or injection trigger that CO-FIRES with a higher-precedence §1
    # hard-refusal (price/financial/…) still early-BLOCKs and is audited under its
    # OWN category (doxxing/pii_request/prompt_injection), never masked.
    match = _early_block_match(body)
    if match is not None:
        return PreFilterDecision(
            action=PreFilterAction.BLOCK,
            rule="safety_block",
            reason=f"SAFETY early-detect: {match.category} ({match.kind})",
            safety_match=match,
            register=match.register,  # always "reactive" (SAFETY §0)
        )

    # budget-skip — after the §3 early-detect (so an injection is still audited),
    # before the non-English HITL floor. A budget-exhausted message must not
    # consume an LLM tier-classify; the deterministic surface carries it (R-4).
    if ctx.budget_exhausted:
        return PreFilterDecision(
            action=PreFilterAction.DROP,
            rule="budget_exhausted",
            reason="LLM budget exhausted; deterministic surface only",
        )

    # (f) non-English → HITL (tier-2), never auto-answer (CLASSIFIER §4).
    if _is_probably_non_english(body):
        return PreFilterDecision(
            action=PreFilterAction.HITL,
            rule="non_english",
            reason="non-English message (v1): route to HITL, never auto-answer",
        )

    # otherwise: fall through to the pure text-only heuristic (layer i).
    engagement = assess_engagement(
        body,
        is_reply_to_bot=ctx.is_reply_to_bot,
        is_mention=ctx.is_mention,
        bot_username=ctx.bot_username,
    )
    return PreFilterDecision(
        action=PreFilterAction.PROCEED,
        rule="proceed",
        reason="no stateful skip; text-only heuristic applied",
        engagement=engagement,
    )


# ---------------------------------------------------------------------------
# Prompt-injection hardening (SAFETY §2 + CLASSIFIER §3) — OWNED here, consumed
# by the C3.4b LLM call. Every untrusted string is XML-delimiter-wrapped with the
# closing tag neutralized so user content can never break out of its delimiter.
# ---------------------------------------------------------------------------

#: the delimiter tags the downstream classifier/drafter prompt expects
#: (CLASSIFIER §3 / SAFETY §2 — `<user_message>`, `<thread>`, `<author>`).
WRAP_TAGS = {
    "message": "user_message",
    "thread_context": "thread",
    "author_tags": "author",
}

# Match ANY opening/closing form of the wrapper tags the user might inject to try
# to break out — `</user_message>`, `<user_message>`, `< / USER_MESSAGE >`, and
# ATTRIBUTED variants like `</user_message foo>` / `<author bar=1>` / `<thread x="y"/>`
# (the `(?:\s[^>]*)?` after the tag-name alternation consumes any attribute payload
# up to the closing bracket). We strip ALL three wrapper tags from EVERY field (not
# just the field's own tag) so a hostile `</thread>` smuggled inside `{author_tags}`
# cannot prematurely close a sibling block either. The pattern is linear (no
# backtracking class is nested) — no ReDoS.
_ALL_TAGS = tuple(sorted(set(WRAP_TAGS.values())))
_WRAPPER_TAG_RE = re.compile(
    r"<\s*/?\s*(?:"
    + "|".join(re.escape(t) for t in _ALL_TAGS)
    + r")(?:\s[^>]*)?\s*/?\s*>",
    re.IGNORECASE,
)


def _neutralize_wrapper_tags(value: str) -> str:
    """Strip any literal wrapper open/close tags from untrusted ``value``.

    This is the break-out defense: a user who types ``</user_message>`` (or any
    casing/spacing variant, an ATTRIBUTED tag like ``</user_message foo>``, or a
    sibling block's tag) into their message / author tags / thread context cannot
    close the delimiter early and inject instruction-level text. The tag tokens are
    removed entirely (replaced with a single space) so no residual ``<...>``
    survives to confuse the model.

    The substitution is LOOPED to a fixpoint: a single non-overlapping ``sub`` pass
    can have a NESTED payload re-form a tag-like token in its output (e.g.
    ``"<<author>author>"`` -> ``"< author>"``, which ``_WRAPPER_TAG_RE`` itself
    still matches). Re-substituting until the string stops changing guarantees the
    output contains NO substring the module's own regex would treat as a wrapper
    tag, so user content truly cannot break out of its delimiter.
    """
    if not value:
        return ""
    out = value
    while True:
        neutralized = _WRAPPER_TAG_RE.sub(" ", out)
        if neutralized == out:
            return out
        out = neutralized


def wrap_user_input(field: str, value: Optional[str]) -> str:
    """Delimiter-wrap one untrusted ``field`` value for the classifier prompt.

    ``field`` ∈ :data:`WRAP_TAGS` (``message`` / ``thread_context`` /
    ``author_tags``). The value is first stripped of any wrapper tags (the
    break-out defense), then wrapped in the field's XML delimiter. A ``None`` /
    empty value yields an empty delimited block (so the prompt template is stable).

    Raises ``ValueError`` on an unknown ``field`` so a template typo fails loudly
    rather than emitting an UNWRAPPED user string into the prompt — the invariant
    is that NO user field ever flows unwrapped.
    """
    if field not in WRAP_TAGS:
        raise ValueError(
            f"unknown wrap field {field!r}; expected one of {sorted(WRAP_TAGS)}"
        )
    tag = WRAP_TAGS[field]
    safe = _neutralize_wrapper_tags(value or "")
    return f"<{tag}>{safe}</{tag}>"


@dataclass(frozen=True)
class WrappedInputs:
    """The three delimiter-wrapped, break-out-safe blocks for the C3.4b prompt.

    Every field here is guaranteed XML-delimiter-wrapped with wrapper tags
    neutralized in the user content — the downstream LLM call interpolates these
    blocks VERBATIM and never an unwrapped user string (the SAFETY §2 invariant).
    """

    message: str
    thread_context: str
    author_tags: str

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "thread_context": self.thread_context,
            "author_tags": self.author_tags,
        }


def wrap_classifier_inputs(
    *,
    message: Optional[str],
    thread_context: Optional[str] = None,
    author_tags: Optional[str] = None,
) -> WrappedInputs:
    """Wrap all three untrusted classifier inputs in one call (C3.4b consumes this).

    The single chokepoint the C3.4b LLM call MUST route through: ``{message}``,
    ``{thread_context}`` (last-5 messages from OTHER members) and ``{author_tags}``
    (``display_name`` / handle from ``relay_members``, never a trusted token) are
    each delimiter-wrapped with wrapper tags neutralized, so none can break out of
    its block. Returns a :class:`WrappedInputs` whose every field is safe to
    interpolate verbatim.
    """
    return WrappedInputs(
        message=wrap_user_input("message", message),
        thread_context=wrap_user_input("thread_context", thread_context),
        author_tags=wrap_user_input("author_tags", author_tags),
    )


__all__ = [
    "EngagementResult",
    "FilterDecision",
    "assess_engagement",
    # stateful pre-filter
    "PreFilterAction",
    "PreFilterContext",
    "PreFilterDecision",
    "prefilter",
    # injection hardening
    "WRAP_TAGS",
    "WrappedInputs",
    "wrap_user_input",
    "wrap_classifier_inputs",
]
