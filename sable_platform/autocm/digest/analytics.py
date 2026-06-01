"""Digest analytics (MEGAPLAN C3.7 — DESIGN §4 ``digest/analytics`` / DIGEST §3).

The bot-internal member-analytics signals + the autonomy/voice-drift numbers the
weekly "What Mattered" digest (``digest/weekly``) assembles. Two corpora feed this
module:

  * the **C1.1 ``relay_messages`` inbound-message corpus** — one row PER inbound
    message, INCLUDING the ~70% C3.4a strong-skips with no draft row. Volume +
    member-activity analytics (``volume``, ``score_sentiment``,
    ``frequent_questions``, ``cultist_candidates``, ``topic_clusters``) aggregate
    over THIS corpus, NOT ``autocm_drafts`` alone (a filter-skipped message has no
    draft row but DOES count for volume / member activity);
  * the **058 ``autocm_drafts`` / ``autocm_reviews`` tables** — the auto/HITL/clean
    ratio (``autonomy_ratios``, reusing C3.5a :func:`gather_review_stats`) and the
    voice-drift heavy-edit clustering (``voice_drift``).

**Read-only, dialect-agnostic.** Every helper takes an already-open SQLAlchemy
``Connection`` (the caller owns lifecycle; NO engine is created here) and reads
``relay_messages`` directly over the C1.1 SCHEMA — NOT via ``relay/db.py`` (the
C3.7 corpus-read dependency is on the schema only, intentionally). Timestamps are
computed in Python as UTC ISO-8601 ``...Z`` and bound as parameters (never
``strftime('now')``), so the window SQL runs unchanged on the live Postgres pool.

**Sentiment seam (the only non-deterministic leg — fake in tests).** DIGEST §3
names ``score_sentiment()`` "via the LLM seam". The :class:`SentimentScorer`
Protocol is that seam; the default :class:`KeywordSentimentScorer` is a pure,
deterministic, zero-LLM lexical scorer (so a deployment with the LLM disabled — or
a test — still gets a sentiment number), and :class:`LLMSentimentScorer` wraps the
C3.1 async ``LLMProvider`` for the richer production scoring. Tests inject the
deterministic scorer; NO real Anthropic / network call is ever made here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.autocm.gate.autonomy import gather_review_stats
from sable_platform.autocm.llm import LLMProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pinned thresholds / constants
# ---------------------------------------------------------------------------
#: a stored ``autocm_reviews.edit_diff_size`` ABOVE this is a "heavy edit" — the
#: voice-drift signal (DIGEST §2j / §3: ``edit_diff_size > 30%``). Mirrors the
#: C3.5a HEAVY_EDIT_THRESHOLD so the digest and the autonomy gate agree.
VOICE_DRIFT_THRESHOLD = 0.30
#: how many top FAQ clusters the digest surfaces (DIGEST §2e "Top 5").
DEFAULT_TOP_QUESTIONS = 5
#: how many cultist candidates the digest spotlights (DIGEST §2g "3–5").
DEFAULT_CULTIST_LIMIT = 5
#: a message must look like a question (end in '?' or open with an interrogative)
#: to enter the FAQ-frequency clustering.
_INTERROGATIVE_OPENERS = (
    "how", "what", "why", "when", "where", "who", "which", "can", "does",
    "is", "are", "do", "will", "should", "could", "would",
)
#: a cultist candidate must have asked at least this many substantive questions
#: (the simpler v1 bot-internal signal; v2 reads Cult Grader — DIGEST §2g).
CULTIST_MIN_QUESTIONS = 2
#: a topic cluster (subsquad pollination) needs at least this many DISTINCT
#: members on the same topic to be an intro candidate (DIGEST §2h / Bible §IV).
TOPIC_MIN_MEMBERS = 2


# ---------------------------------------------------------------------------
# Clock + window helpers (injectable; the week window is deterministic in tests)
# ---------------------------------------------------------------------------
def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def week_bounds(week_start: datetime) -> Tuple[str, str]:
    """Return the ``[start, end)`` ISO ``...Z`` bounds for the 7-day digest week.

    ``week_start`` is the inclusive Monday-00:00 anchor (client timezone is the
    caller's concern); the window is exactly 7 days wide and the end bound is
    EXCLUSIVE so a message at exactly the next week's start does not double-count.
    """
    start = week_start
    end = week_start + timedelta(days=7)
    return _iso_z(start), _iso_z(end)


# ---------------------------------------------------------------------------
# Sentiment seam — the only non-deterministic leg (fake in tests)
# ---------------------------------------------------------------------------
class SentimentScorer(Protocol):
    """The digest sentiment seam (DIGEST §3 ``score_sentiment`` "via the LLM seam").

    ``score`` maps a batch of message texts to a single ``[-1.0, 1.0]`` scalar
    (negative → positive). The default impl is deterministic + offline; the LLM
    impl wraps the C3.1 ``LLMProvider``. Both satisfy this Protocol so the digest
    never imports a transport directly.
    """

    def score(self, texts: Sequence[str]) -> float:
        ...


# A small, deterministic lexicon. Deliberately tiny + transparent — this is the
# zero-LLM default + the test double, NOT a sentiment model. The richer scoring is
# LLMSentimentScorer.
_POSITIVE_WORDS = frozenset(
    {
        "love", "great", "amazing", "awesome", "good", "nice", "thanks", "thank",
        "excited", "bullish", "wagmi", "lfg", "gm", "happy", "win", "wins", "best",
        "solid", "clean", "legit", "based", "fire", "huge",
    }
)
_NEGATIVE_WORDS = frozenset(
    {
        "scam", "rug", "rugpull", "dump", "dumping", "bearish", "fud", "fear",
        "broken", "bug", "down", "angry", "hate", "worst", "bad", "dead", "ngmi",
        "panic", "exit", "worried", "concern", "concerned", "slow", "lag",
    }
)
_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _tokens_lower(text_value: Optional[str]) -> List[str]:
    return _TOKEN_RE.findall((text_value or "").lower())


class KeywordSentimentScorer:
    """Deterministic, offline, zero-LLM lexical sentiment scorer (the default).

    Counts positive vs negative lexicon hits across the batch and returns
    ``(pos - neg) / (pos + neg)`` in ``[-1.0, 1.0]`` (``0.0`` when neither fires).
    Pure + deterministic — used as the always-available default AND the test
    double (DIGEST §3 "via the LLM seam [fake in tests]"). NO network.
    """

    def score(self, texts: Sequence[str]) -> float:
        pos = neg = 0
        for t in texts:
            for tok in _tokens_lower(t):
                if tok in _POSITIVE_WORDS:
                    pos += 1
                elif tok in _NEGATIVE_WORDS:
                    neg += 1
        total = pos + neg
        if total == 0:
            return 0.0
        return round((pos - neg) / total, 4)


class LLMSentimentScorer:
    """:class:`SentimentScorer` over the C1 async ``LLMProvider`` seam.

    Production scoring: ships the week's messages as the volatile user turn behind
    a stable cached system block and parses a single scalar out of the completion.
    On ANY failure (``None`` completion / unparseable / no LLM) it falls back to the
    deterministic :class:`KeywordSentimentScorer` so the digest always gets a number
    (the LLM is garnish, never the hot path — D-1/R-4). NEVER used in tests (those
    inject the deterministic scorer); NO network at import time.
    """

    _SYSTEM = (
        "You are a sentiment scorer for a crypto community manager's weekly digest. "
        "Read the messages and reply with ONLY a JSON object {\"sentiment\": <float "
        "in [-1.0, 1.0]>} where -1 is very negative and +1 is very positive."
    )

    def __init__(self, provider: LLMProvider, *, max_messages: int = 200) -> None:
        self._provider = provider
        self._max_messages = max_messages
        self._fallback = KeywordSentimentScorer()

    def score(self, texts: Sequence[str]) -> float:
        sample = list(texts)[: self._max_messages]
        prompt = "<messages>\n" + "\n".join(f"- {t}" for t in sample) + "\n</messages>"
        try:
            raw = _run_sync(self._provider.complete(self._SYSTEM, prompt, max_tokens=32))
        except Exception:  # pragma: no cover - defensive; seam shouldn't raise
            logger.exception("LLMSentimentScorer.score raised; deterministic fallback")
            raw = None
        parsed = _parse_sentiment(raw)
        if parsed is None:
            return self._fallback.score(texts)
        return parsed


def _run_sync(coro):
    """Drive an async-seam coroutine to completion from sync digest code.

    The C3.1 ``LLMProvider.complete`` is ``async``; the digest assembly is
    synchronous (it runs inside a WorkflowRunner step). ``asyncio.run`` drives the
    single completion. The deterministic default scorer (and every test) never
    reaches this path.
    """
    return asyncio.run(coro)


def _parse_sentiment(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        val = float(obj["sentiment"])
    except (ValueError, TypeError, KeyError):
        return None
    return round(max(-1.0, min(1.0, val)), 4)


# ---------------------------------------------------------------------------
# Volume (DIGEST §2b) — over the relay_messages corpus
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VolumeStats:
    """DIGEST §2b volume: message + member counts over the relay_messages corpus.

    ``messages`` is EVERY inbound message in the window (filter-skipped INCLUDED —
    the corpus is the authority, not ``autocm_drafts``). ``new_members`` counts
    distinct members whose FIRST-EVER message for the org fell inside the window.
    ``prev_messages`` is the prior week's message count for the w/w delta.
    """

    messages: int
    distinct_members: int
    new_members: int
    prev_messages: int

    @property
    def wow_pct(self) -> Optional[float]:
        """Week-over-week message-count change as a fraction (None when no prior)."""
        if self.prev_messages == 0:
            return None
        return round((self.messages - self.prev_messages) / self.prev_messages, 4)


def _client_org_id(conn: Connection, client_id: int) -> Optional[str]:
    row = conn.execute(
        text("SELECT org_id FROM autocm_clients WHERE id = :id"), {"id": client_id}
    ).fetchone()
    return row[0] if row is not None else None


def _week_messages(
    conn: Connection, org_id: str, start: str, end: str
) -> List[Dict[str, Optional[str]]]:
    """Fetch the week's relay_messages rows for the org (filter-skipped INCLUDED)."""
    rows = conn.execute(
        text(
            "SELECT id, member_id, external_user_id, text, received_at "
            "FROM relay_messages "
            "WHERE org_id = :org_id AND received_at >= :start AND received_at < :end "
            "ORDER BY received_at, id"
        ),
        {"org_id": org_id, "start": start, "end": end},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def volume(
    conn: Connection, client_id: int, week_start: datetime, *, org_id: Optional[str] = None
) -> VolumeStats:
    """DIGEST §2b volume over the relay_messages corpus (filter-skipped INCLUDED)."""
    if org_id is None:
        org_id = _client_org_id(conn, client_id)
    start, end = week_bounds(week_start)
    prev_start, _ = week_bounds(week_start - timedelta(days=7))

    msgs = _week_messages(conn, org_id or "", start, end)
    messages = len(msgs)
    distinct = {
        (m["member_id"], m["external_user_id"]) for m in msgs
    }

    prev_row = conn.execute(
        text(
            "SELECT COUNT(*) FROM relay_messages "
            "WHERE org_id = :org_id AND received_at >= :ps AND received_at < :start"
        ),
        {"org_id": org_id or "", "ps": prev_start, "start": start},
    ).fetchone()
    prev_messages = int(prev_row[0] or 0)

    # new members: a member whose earliest-ever message for the org is in-window.
    new_members = 0
    for m in msgs:
        mid, ext = m["member_id"], m["external_user_id"]
        first_row = conn.execute(
            text(
                "SELECT MIN(received_at) FROM relay_messages "
                "WHERE org_id = :org_id "
                "  AND ((member_id IS NOT NULL AND member_id = :mid) "
                "       OR (member_id IS NULL AND external_user_id = :ext))"
            ),
            {"org_id": org_id or "", "mid": mid, "ext": ext},
        ).fetchone()
        first_seen = first_row[0] if first_row else None
        if first_seen is not None and start <= first_seen < end:
            # count each distinct member once: only when THIS row is their first.
            if m["received_at"] == first_seen:
                new_members += 1

    return VolumeStats(
        messages=messages,
        distinct_members=len(distinct),
        new_members=new_members,
        prev_messages=prev_messages,
    )


# ---------------------------------------------------------------------------
# Autonomy ratios (DIGEST §2c) — reuse C3.5a gather_review_stats
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AutonomyRatios:
    """DIGEST §2c autonomy scoreboard: auto / HITL / escalated / clean ratios.

    Computed over ``autocm_drafts`` (the draft surface — a filter-skipped message
    has no draft row, so it is correctly excluded from the AUTONOMY denominator,
    which is "drafts produced", unlike the corpus-wide Volume number). The clean
    ratio reuses C3.5a :func:`gather_review_stats` so the digest reads the SAME
    quantity the autonomy gate computes (no re-derive drift).
    """

    total_drafts: int
    auto_handled: int
    hitl_handled: int
    escalated: int
    clean_approvals: int
    reviewed: int

    @property
    def auto_pct(self) -> float:
        return round(self.auto_handled / self.total_drafts, 4) if self.total_drafts else 0.0

    @property
    def hitl_pct(self) -> float:
        return round(self.hitl_handled / self.total_drafts, 4) if self.total_drafts else 0.0

    @property
    def escalated_pct(self) -> float:
        return round(self.escalated / self.total_drafts, 4) if self.total_drafts else 0.0

    @property
    def clean_approval_rate(self) -> float:
        return round(self.clean_approvals / self.reviewed, 4) if self.reviewed else 0.0


# autocm_drafts.status buckets (058 CHECK). auto_sent → auto-handled; the
# operator-reviewed terminal states → HITL-handled; escalated/suppressed → escalated.
_AUTO_STATUSES = ("auto_sent",)
_HITL_STATUSES = ("approved", "rejected", "published", "hitl_pending")
_ESCALATED_STATUSES = ("escalated", "suppressed")


def autonomy_ratios(
    conn: Connection, client_id: int, week_start: datetime
) -> AutonomyRatios:
    """DIGEST §2c auto/HITL/escalated + clean-approval ratios for the digest week.

    Buckets the week's ``autocm_drafts`` by ``status`` (auto_sent → auto; the
    operator-reviewed terminal states → HITL; escalated/suppressed → escalated) and
    pulls the clean-approval count from ``autocm_reviews`` over the same window —
    the clean count is computed via the row-level ``is_clean_approval`` flag the
    C3.5b review write path set (the SAME quantity C3.5a's
    :func:`gather_review_stats` aggregates), so the digest and the autonomy gate
    never disagree.
    """
    start, end = week_bounds(week_start)
    rows = conn.execute(
        text(
            "SELECT status, COUNT(*) AS n FROM autocm_drafts "
            "WHERE client_id = :c AND created_at >= :start AND created_at < :end "
            "GROUP BY status"
        ),
        {"c": client_id, "start": start, "end": end},
    ).fetchall()
    by_status = {r[0]: int(r[1]) for r in rows}
    total = sum(by_status.values())
    auto = sum(by_status.get(s, 0) for s in _AUTO_STATUSES)
    hitl = sum(by_status.get(s, 0) for s in _HITL_STATUSES)
    escalated = sum(by_status.get(s, 0) for s in _ESCALATED_STATUSES)

    rev = conn.execute(
        text(
            "SELECT COUNT(*) AS n, COALESCE(SUM(is_clean_approval), 0) AS clean "
            "FROM autocm_reviews "
            "WHERE client_id = :c AND reviewed_at >= :start AND reviewed_at < :end"
        ),
        {"c": client_id, "start": start, "end": end},
    ).fetchone()
    reviewed = int(rev[0] or 0)
    clean = int(rev[1] or 0)

    return AutonomyRatios(
        total_drafts=total,
        auto_handled=auto,
        hitl_handled=hitl,
        escalated=escalated,
        clean_approvals=clean,
        reviewed=reviewed,
    )


def category_clean_rates(
    conn: Connection, client_id: int, categories: Sequence[str]
) -> Dict[str, float]:
    """Per-category clean-approval rate via C3.5a :func:`gather_review_stats` reuse.

    Returns ``{category: clean_approval_rate}`` over each category's full review
    history (the autonomy-scoreboard "per-category breakdown", DIGEST §2c). Reuses
    the C3.5a aggregator verbatim so "close to flipping autonomous" reads the exact
    quantity the promotion gate uses.
    """
    out: Dict[str, float] = {}
    for cat in categories:
        stats = gather_review_stats(conn, client_id, cat)
        rate = (
            stats.clean_approval_count / stats.sample_count
            if stats.sample_count
            else 0.0
        )
        out[cat] = round(rate, 4)
    return out


# ---------------------------------------------------------------------------
# FAQ frequency (DIGEST §2e) — clustered over the relay_messages corpus
# ---------------------------------------------------------------------------
def _looks_like_question(text_value: Optional[str]) -> bool:
    if not text_value:
        return False
    stripped = text_value.strip()
    if stripped.endswith("?"):
        return True
    toks = _tokens_lower(stripped)
    return bool(toks) and toks[0] in _INTERROGATIVE_OPENERS


def _normalize_question(text_value: str) -> str:
    """Normalize a question for clustering: lowercase content tokens, drop '?'.

    A deterministic, transparent clustering key — NOT semantic similarity. Two
    questions cluster iff their normalized content-token sequence matches (the v1
    FAQ-frequency signal; richer semantic clustering is a later refinement).
    """
    toks = _tokens_lower(text_value)
    return " ".join(toks)


def frequent_questions(
    conn: Connection,
    client_id: int,
    week_start: datetime,
    *,
    org_id: Optional[str] = None,
    top_n: int = DEFAULT_TOP_QUESTIONS,
) -> List[Tuple[str, int]]:
    """DIGEST §2e top question clusters over the relay_messages corpus.

    Clusters the week's question-shaped messages (over the FULL corpus, INCLUDING
    filter-skipped ones — a question that was strong-skipped still counts toward
    "most-asked") by normalized content tokens and returns the ``top_n`` clusters as
    ``(representative_text, count)`` sorted by count desc then text for a
    deterministic order. The representative is the first-seen raw text in the cluster.
    """
    if org_id is None:
        org_id = _client_org_id(conn, client_id)
    start, end = week_bounds(week_start)
    msgs = _week_messages(conn, org_id or "", start, end)

    counts: Dict[str, int] = {}
    representative: Dict[str, str] = {}
    for m in msgs:
        body = m["text"]
        if not _looks_like_question(body):
            continue
        key = _normalize_question(body)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        representative.setdefault(key, body.strip())

    ranked = sorted(
        counts.items(), key=lambda kv: (-kv[1], representative[kv[0]])
    )
    return [(representative[k], n) for k, n in ranked[:top_n]]


# ---------------------------------------------------------------------------
# Sentiment (DIGEST §2d) — over the week's corpus, through the seam
# ---------------------------------------------------------------------------
def score_sentiment(
    conn: Connection,
    client_id: int,
    week_start: datetime,
    scorer: SentimentScorer,
    *,
    org_id: Optional[str] = None,
) -> float:
    """DIGEST §2d top-line community mood over the week's relay_messages corpus.

    Pulls the week's message texts (FULL corpus) and scores them through the
    injected :class:`SentimentScorer` seam (the deterministic
    :class:`KeywordSentimentScorer` by default / in tests; the
    :class:`LLMSentimentScorer` in production). Returns a ``[-1.0, 1.0]`` scalar.
    An empty week is neutral (``0.0``).
    """
    if org_id is None:
        org_id = _client_org_id(conn, client_id)
    start, end = week_bounds(week_start)
    msgs = _week_messages(conn, org_id or "", start, end)
    texts = [m["text"] for m in msgs if m["text"]]
    if not texts:
        return 0.0
    return scorer.score(texts)


# ---------------------------------------------------------------------------
# Community-health delta (DIGEST §2a leg C) — sentiment + retention + new-engagement
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CommunityHealth:
    """DIGEST §2a leg C: the composite community-health signal + its w/w delta.

    ``score`` is the composite (sentiment + new-engagement) for the week; ``delta``
    is ``score - prev_score`` (the headline's ``+0.07``-shaped number). Both legs
    are bounded; the delta is the digest headline's community-health figure.
    """

    score: float
    prev_score: float

    @property
    def delta(self) -> float:
        return round(self.score - self.prev_score, 4)


def _health_score(sentiment: float, new_members: int, total_members: int) -> float:
    """Composite health: sentiment plus a bounded new-engagement bonus.

    Deterministic given its inputs (sentiment comes from the seam). The
    new-engagement leg is ``new/total`` capped at the sentiment's own scale, so a
    healthy week with positive sentiment + fresh joiners scores above a flat week.
    """
    engagement = (new_members / total_members) if total_members else 0.0
    return round(max(-1.0, min(1.0, sentiment + min(engagement, 0.5))), 4)


def community_health_delta(
    conn: Connection,
    client_id: int,
    week_start: datetime,
    scorer: SentimentScorer,
    *,
    org_id: Optional[str] = None,
) -> CommunityHealth:
    """DIGEST §2a leg C: community-health composite + week-over-week delta.

    Scores the current week and the prior week (same composite — sentiment via the
    seam + the new-engagement leg over the corpus) and returns both so the headline
    can print the delta. The prior-week sentiment also runs through the SAME scorer
    so the delta is apples-to-apples.
    """
    if org_id is None:
        org_id = _client_org_id(conn, client_id)
    cur_vol = volume(conn, client_id, week_start, org_id=org_id)
    cur_sent = score_sentiment(conn, client_id, week_start, scorer, org_id=org_id)
    cur = _health_score(cur_sent, cur_vol.new_members, cur_vol.distinct_members)

    prev_week = week_start - timedelta(days=7)
    prev_vol = volume(conn, client_id, prev_week, org_id=org_id)
    prev_sent = score_sentiment(conn, client_id, prev_week, scorer, org_id=org_id)
    prev = _health_score(prev_sent, prev_vol.new_members, prev_vol.distinct_members)

    return CommunityHealth(score=cur, prev_score=prev)


# ---------------------------------------------------------------------------
# Voice-drift (DIGEST §2j) — heavy-edits over autocm_reviews, clustered
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceDriftCluster:
    """One voice-drift cluster: heavy-edited drafts grouped by category × register.

    DIGEST §2j: drafts operators edited heavily this week — clustered by category
    and (bimodal-specific) register so the digest can say "edits clustered in
    mechanics, register=calm". ``count`` is the number of heavy edits in the cluster.
    """

    category: Optional[str]
    register: Optional[str]
    count: int


def voice_drift(
    conn: Connection, client_id: int, week_start: datetime
) -> List[VoiceDriftCluster]:
    """DIGEST §2j heavy-edit voice-drift clusters for the digest week.

    Selects ``autocm_reviews`` rows in the window whose stored
    ``edit_diff_size`` exceeds :data:`VOICE_DRIFT_THRESHOLD` (the heavy-edit
    signal — the SAME 30% boundary the C3.5a clean-approval rejection uses), joins
    to ``autocm_drafts`` for the category + register, and groups by
    ``(category, register)``. Returns clusters sorted by count desc then category so
    the digest's "which register did edits cluster in?" line is deterministic.
    """
    start, end = week_bounds(week_start)
    rows = conn.execute(
        text(
            "SELECT d.category AS category, d.register AS register, COUNT(*) AS n "
            "FROM autocm_reviews r "
            "JOIN autocm_drafts d ON d.id = r.draft_id "
            "WHERE r.client_id = :c AND r.reviewed_at >= :start AND r.reviewed_at < :end "
            "  AND r.edit_diff_size > :thr "
            "GROUP BY d.category, d.register "
            "ORDER BY n DESC, d.category"
        ),
        {"c": client_id, "start": start, "end": end, "thr": VOICE_DRIFT_THRESHOLD},
    ).fetchall()
    return [
        VoiceDriftCluster(category=r[0], register=r[1], count=int(r[2])) for r in rows
    ]


# ---------------------------------------------------------------------------
# Cultist candidates (DIGEST §2g) — simpler v1 bot-internal signals
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CultistCandidate:
    """A high-fit community member worth recognizing (DIGEST §2g, v1 signals).

    ``handle`` is the member's display name / external id; ``question_count`` is
    the substantive questions they asked this week; ``message_count`` is their total
    in-window activity. v1 uses these simpler bot-internal signals; v2 reads Cult
    Grader (DIGEST §8). ``member_id`` / ``external_user_id`` identify them for the
    ``[Recognize]`` button target.
    """

    handle: str
    member_id: Optional[int]
    external_user_id: Optional[str]
    question_count: int
    message_count: int


def cultist_candidates(
    conn: Connection,
    client_id: int,
    week_start: datetime,
    *,
    org_id: Optional[str] = None,
    limit: int = DEFAULT_CULTIST_LIMIT,
    min_questions: int = CULTIST_MIN_QUESTIONS,
) -> List[CultistCandidate]:
    """DIGEST §2g cultist-candidate spotlight over the relay_messages corpus.

    The v1 bot-internal signal: a member who asked >= ``min_questions`` substantive
    (question-shaped) messages this week is a candidate. Computed over the FULL
    corpus (filter-skipped INCLUDED — a member's substantive questions count even
    when the bot strong-skipped them). Returns up to ``limit`` candidates sorted by
    question_count desc then message_count desc then handle. Distinct members are
    keyed by ``member_id`` when linked, else ``external_user_id``.
    """
    if org_id is None:
        org_id = _client_org_id(conn, client_id)
    start, end = week_bounds(week_start)
    msgs = _week_messages(conn, org_id or "", start, end)

    # aggregate per distinct member.
    agg: Dict[Tuple[Optional[int], Optional[str]], Dict[str, int]] = {}
    for m in msgs:
        key = (m["member_id"], m["external_user_id"])
        bucket = agg.setdefault(key, {"questions": 0, "messages": 0})
        bucket["messages"] += 1
        if _looks_like_question(m["text"]):
            bucket["questions"] += 1

    candidates: List[CultistCandidate] = []
    for (member_id, external_user_id), bucket in agg.items():
        if bucket["questions"] < min_questions:
            continue
        handle = _resolve_handle(conn, member_id, external_user_id)
        candidates.append(
            CultistCandidate(
                handle=handle,
                member_id=member_id,
                external_user_id=external_user_id,
                question_count=bucket["questions"],
                message_count=bucket["messages"],
            )
        )
    candidates.sort(
        key=lambda c: (-c.question_count, -c.message_count, c.handle)
    )
    return candidates[:limit]


def _resolve_handle(
    conn: Connection, member_id: Optional[int], external_user_id: Optional[str]
) -> str:
    """Best-effort display handle for a member (display_name → identity → ext id)."""
    if member_id is not None:
        row = conn.execute(
            text("SELECT display_name FROM relay_members WHERE id = :id"),
            {"id": member_id},
        ).fetchone()
        if row is not None and row[0]:
            return str(row[0])
        ident = conn.execute(
            text(
                "SELECT handle FROM relay_member_identities "
                "WHERE member_id = :id AND handle IS NOT NULL ORDER BY linked_at LIMIT 1"
            ),
            {"id": member_id},
        ).fetchone()
        if ident is not None and ident[0]:
            return str(ident[0])
        return f"member:{member_id}"
    return external_user_id or "unknown"


# ---------------------------------------------------------------------------
# Topic clusters / subsquad pollination (DIGEST §2h) — members per topic
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TopicCluster:
    """A topic multiple members engaged this week — a subsquad-pollination pair.

    DIGEST §2h / Bible §IV: members who asked about the same topic this week are
    intro candidates. ``topic`` is the shared normalized token key; ``handles`` are
    the distinct members on it (>= :data:`TOPIC_MIN_MEMBERS`). The operator handles
    the actual intro; the bot only surfaces the pattern.
    """

    topic: str
    handles: Tuple[str, ...]

    @property
    def member_count(self) -> int:
        return len(self.handles)


# topic-bearing tokens: drop very common stopwords so two messages cluster on the
# SUBSTANTIVE shared term, not on "the"/"how"/etc.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "is", "are", "do", "does", "how", "what", "why", "when",
        "where", "who", "which", "can", "will", "should", "could", "would", "to",
        "of", "in", "on", "for", "and", "or", "i", "you", "it", "this", "that",
        "with", "about", "my", "we", "be", "get", "got", "any", "there", "your",
    }
)


def _topic_tokens(text_value: Optional[str]) -> List[str]:
    """Substantive (non-stopword, len>=3) tokens — the topic-clustering vocabulary."""
    return [
        t for t in _tokens_lower(text_value) if t not in _STOPWORDS and len(t) >= 3
    ]


def topic_clusters(
    conn: Connection,
    client_id: int,
    week_start: datetime,
    *,
    org_id: Optional[str] = None,
    min_members: int = TOPIC_MIN_MEMBERS,
) -> List[TopicCluster]:
    """DIGEST §2h subsquad pollination: topics >= ``min_members`` distinct members hit.

    Over the week's corpus, maps each substantive topic token to the distinct
    members who used it, and returns the topics touched by at least ``min_members``
    DISTINCT members (the intro-candidate / bridge-node pattern). Sorted by member
    count desc then topic for determinism. Operator handles the intro; this only
    surfaces the pattern.
    """
    if org_id is None:
        org_id = _client_org_id(conn, client_id)
    start, end = week_bounds(week_start)
    msgs = _week_messages(conn, org_id or "", start, end)

    # topic -> ordered distinct member handles (insertion order preserved).
    topic_members: Dict[str, List[str]] = {}
    topic_seen_keys: Dict[str, set] = {}
    for m in msgs:
        key = (m["member_id"], m["external_user_id"])
        handle = _resolve_handle(conn, m["member_id"], m["external_user_id"])
        for tok in set(_topic_tokens(m["text"])):
            seen = topic_seen_keys.setdefault(tok, set())
            if key in seen:
                continue
            seen.add(key)
            topic_members.setdefault(tok, []).append(handle)

    clusters = [
        TopicCluster(topic=t, handles=tuple(h))
        for t, h in topic_members.items()
        if len(h) >= min_members
    ]
    clusters.sort(key=lambda c: (-c.member_count, c.topic))
    return clusters


__all__ = [
    # thresholds
    "VOICE_DRIFT_THRESHOLD",
    "DEFAULT_TOP_QUESTIONS",
    "DEFAULT_CULTIST_LIMIT",
    "CULTIST_MIN_QUESTIONS",
    "TOPIC_MIN_MEMBERS",
    # window
    "week_bounds",
    # sentiment seam
    "SentimentScorer",
    "KeywordSentimentScorer",
    "LLMSentimentScorer",
    # volume
    "VolumeStats",
    "volume",
    # autonomy ratios (reuse gather_review_stats)
    "AutonomyRatios",
    "autonomy_ratios",
    "category_clean_rates",
    # FAQ frequency
    "frequent_questions",
    # sentiment + health
    "score_sentiment",
    "CommunityHealth",
    "community_health_delta",
    # voice drift
    "VoiceDriftCluster",
    "voice_drift",
    # member analytics
    "CultistCandidate",
    "cultist_candidates",
    "TopicCluster",
    "topic_clusters",
]
