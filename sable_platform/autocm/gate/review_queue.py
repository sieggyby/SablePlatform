"""HITL review surface (DESIGN §4 ``gate/review_queue``) — the seam + the C3.5b TG flow.

The ``HITLReviewSurface`` interface is the productization seam (Q12): the v1
white-glove tier posts review items to the per-client TG operator chat
(:class:`TelegramReviewSurface`); a managed/self-host tier swaps in a web dashboard
(:class:`WebDashboardReviewSurface`, stub) WITHOUT a rewrite of the pipeline — the
gate/confidence stage only ever talks to the abstract surface.

The TG impl RIDES the C2.7 relay primitives:
  * ``RelayHandlerRegistry.get_operator_chat`` / ``provision_operator_chat`` —
    resolve / provision the per-client operator chat (the HITL surface DB side).
  * a transport :class:`BotSender` posts the review message with inline
    [Approve][Edit][Reject][Punt to founder][Why this routing?] buttons; the
    inline-button callbacks route back through C2.7's callback router
    (``RelayHandlerRegistry.register_callback_handler`` / ``dispatch_callback``)
    keyed on the :data:`CALLBACK_PREFIX` data prefix.

C3.1 fixed the interface + the TG impl's surface RESOLUTION over C2.7 + the
web-dashboard stub. **C3.5b** (this chunk) adds the load-bearing happy-path flow:
the review-queue message anatomy (HITL_UX §1), the button POST + callback wiring
(HITL_UX §2), the stateful [Edit] flow (HITL_UX §2 — operator-provided edited text
recorded with ``edit_diff_ratio``; heavy edit > 0.30 flagged), the decision
recording into ``autocm_reviews`` WITH the ``is_clean_approval`` flag the C3.5a
autonomy sweep reads, and the 15-minute stale auto-expiration (HITL_UX §3 — an
untouched tier-2 draft is marked expired, the bot posts NOTHING, and a "missed
window" note is recorded).

**Scope boundary (C3.5b/C3.6 — no duplicated enqueue).** C3.5b records the
operator decision + the ``final_text`` (the approved/edited reply) into
``autocm_drafts``/``autocm_reviews`` + the audit trail; it does NOT itself touch
the relay outbox. The actual ``[Approve]`` → ``relay_publication_jobs`` ENQUEUE is
owned by C3.6 (the publisher, downstream). So this module cannot publish on its
own — it never calls the transport's send for the PUBLIC reply, only for the
operator-chat queue message / its status updates.

**Prompt-injection surface (SAFETY §2 / CLASSIFIER §3).** The review-queue message
renders ``display_name`` / handle / user-controlled fields for the HUMAN operator
only — these are NEVER interpolated into any LLM/classifier payload as a
trusted instruction-level token. This module does no LLM work at all (it is a pure
render + DB-record path); the guarantee here is that nothing it reads from
``relay_members`` / the source message ever flows back into a model prompt.

**Outbound-mirror safety (relay §15.2).** The no-LLM guarantee above is only HALF
the user-field safety story. The other half is the transport mirror: the rendered
body interleaves the verbatim user substrings with bot markup and is posted to the
operator chat. :func:`render_review_message` does NOT escape, so it targets
PLAIN-TEXT delivery — the :class:`BotSender` contract requires plain text (no parse
mode; Discord ``AllowedMentions.none()``) so user substrings are inert, OR escaping
via ``escape_telegram_text`` / ``escape_discord`` if a parse-mode sender is ever
used. See the :class:`BotSender` docstring; do not read the "no-LLM" guarantee as
covering the outbound render.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.autocm.gate.autonomy import (
    HEAVY_EDIT_THRESHOLD,
    edit_diff_ratio,
    is_heavy_edit,
)
from sable_platform.db.audit import log_audit
from sable_platform.relay.bot.registry import CallbackEvent, RelayHandlerRegistry

# The HITL review actions an operator can take (HITL_UX §2). These mirror the
# autocm_reviews.decision CHECK set in 058 (approve / edit / reject /
# punt_to_founder) plus the read-only "why" action (no decision row).
REVIEW_ACTIONS = ("approve", "edit", "reject", "punt", "why")

# Map a button action → the autocm_reviews.decision CHECK value (058). 'why' is
# read-only (no decision recorded) and 'punt' maps to the schema's
# 'punt_to_founder'.
ACTION_TO_DECISION = {
    "approve": "approve",
    "edit": "edit",
    "reject": "reject",
    "punt": "punt_to_founder",
}

# Inline-button callback-data prefix. The registry routes every callback whose
# data starts with this to the review-queue controller (C2.7 longest-prefix
# routing). Data shape: ``autocm:review:<action>:<draft_id>``.
CALLBACK_PREFIX = "autocm:review:"

# The 15-minute tier-2 resolution SLA (HITL_UX §3): after this an untouched draft
# is auto-expired — the bot posts NOTHING and a "missed window" note is recorded.
STALE_AFTER_MINUTES = 15

# log_audit verbs (audit-everything; source="sable-autocm").
AUDIT_SOURCE = "sable-autocm"
ACTION_REVIEW_DECISION = "hitl_review_decision"
ACTION_REVIEW_EXPIRED = "hitl_review_expired"

# The draft statuses this flow writes (subset of the 058 autocm_drafts CHECK set).
STATUS_HITL_PENDING = "hitl_pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_ESCALATED = "escalated"
STATUS_SUPPRESSED = "suppressed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The review item (carried to the surface) + the rendered message anatomy
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReviewItem:
    """One draft posted to the HITL surface for an operator decision.

    Carries the SAFETY §5 audit field set the review must persist on decision
    (source message, cited chunks, draft text, category/tier/confidence) PLUS the
    human-only display fields the queue-message anatomy renders (HITL_UX §1):
    ``source_text`` (the quoted user message), ``source_username`` /
    ``source_sent_at`` (the "Source (TG · user @username · 19:42 UTC)" header), the
    ``client_label``, and the classifier ``reasoning`` shown by [Why this routing?].

    SAFETY §2 / CLASSIFIER §3: ``source_username`` and ``source_text`` are
    USER-CONTROLLED and rendered for the human operator ONLY — this module never
    feeds them into an LLM/classifier payload. That no-LLM guarantee does NOT by
    itself make the user fields safe on the OUTBOUND mirror: the rendered body is
    plain-text-only (un-escaped), so the :class:`BotSender` MUST satisfy relay §15.2
    (plain-text send / ``AllowedMentions.none()``, or escape if parse-mode) — see
    the :class:`BotSender` contract.
    """

    draft_id: int
    org_id: str
    source_message_row_id: int
    draft_text: str
    category: str
    tier: int
    confidence: float
    register: str = "calm"
    cited_chunk_ids: List[int] = field(default_factory=list)
    # human-only display fields (HITL_UX §1 anatomy)
    client_label: Optional[str] = None
    source_text: Optional[str] = None
    source_username: Optional[str] = None
    source_sent_at: Optional[str] = None
    # [Why this routing?] payload (classifier reasoning + category state)
    reasoning: Optional[str] = None
    category_state: Optional[str] = None
    # sensitive-draft marker (HITL_UX §4) — a fired hard-refusal pattern
    refusal_pattern: Optional[str] = None


@dataclass(frozen=True)
class KBSourceLine:
    """One '• [chunk title] — [source URL, last refreshed]' line (HITL_UX §1)."""

    chunk_id: int
    title: str
    source_url: Optional[str]
    last_refreshed_at: Optional[str]


def fetch_kb_source_lines(
    conn: Connection, chunk_ids: Sequence[int]
) -> List[KBSourceLine]:
    """Resolve the cited chunk ids → the HITL_UX §1 'KB sources used' lines.

    Joins ``autocm_kb_chunks`` to ``autocm_kb_sources`` for each cited chunk and
    derives a human-readable title from the chunk's ``chunk_metadata`` JSON
    (``title`` key) when present, else the source type + a short snippet of the
    chunk text. Preserves the cited order; silently skips an id that no longer
    resolves (a chunk removed/superseded since drafting). This is a render-only
    read — none of it flows into any LLM payload.
    """
    lines: List[KBSourceLine] = []
    for cid in chunk_ids:
        row = conn.execute(
            text(
                "SELECT c.id, c.chunk_text, c.chunk_metadata, "
                "       s.source_type, s.source_url, s.last_refreshed_at "
                "FROM autocm_kb_chunks c "
                "JOIN autocm_kb_sources s ON s.id = c.source_id "
                "WHERE c.id = :cid"
            ),
            {"cid": cid},
        ).fetchone()
        if row is None:
            continue
        m = row._mapping
        title = ""
        meta_raw = m["chunk_metadata"]
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
                if isinstance(meta, dict):
                    title = str(meta.get("title") or "").strip()
            except (ValueError, TypeError):
                title = ""
        if not title:
            snippet = (m["chunk_text"] or "").strip().replace("\n", " ")
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            source_type = m["source_type"] or "kb"
            title = f"{source_type}: {snippet}" if snippet else str(source_type)
        lines.append(
            KBSourceLine(
                chunk_id=int(m["id"]),
                title=title,
                source_url=m["source_url"],
                last_refreshed_at=m["last_refreshed_at"],
            )
        )
    return lines


def render_review_message(item: ReviewItem, kb_lines: Sequence[KBSourceLine]) -> str:
    """Render the HITL_UX §1 (or §4) review-queue message body.

    Reproduces the documented anatomy: the status header line (🟡 DRAFT or, when a
    hard-refusal pattern fired, the 🔴 SENSITIVE DRAFT header per §4), the quoted
    source message, the NULO draft, and the 'KB sources used' list. The inline
    buttons are attached by the surface, not part of this text body.

    All user-controlled fields (``source_username`` / ``source_text``) are rendered
    verbatim for the human operator — they are NEVER passed to any model.

    OUTBOUND-SAFETY (relay §15.2). This body is built for PLAIN-TEXT delivery: it
    does NO escaping of the verbatim user substrings, so the :class:`BotSender` that
    posts it MUST satisfy the §15.2 invariant — send plain text (no parse mode;
    Discord ``AllowedMentions.none()``) so ``<script>`` / ``@everyone`` in
    ``source_text`` are inert, OR (if a parse-mode sender is ever used) escape the
    user substrings via ``escape_telegram_text`` / ``escape_discord`` first. See the
    :class:`BotSender` contract.
    """
    client = item.client_label or item.org_id
    if item.refusal_pattern:
        header = (
            f"🔴 SENSITIVE DRAFT — {client} · {item.category} · conf {item.confidence:.2f}"
        )
    else:
        header = (
            f"🟡 DRAFT — {client} · {item.category} · conf {item.confidence:.2f}"
        )

    src_who = f"user @{item.source_username}" if item.source_username else "user"
    src_when = f" · {item.source_sent_at}" if item.source_sent_at else ""
    source_block = (
        f"Source (TG · {src_who}{src_when}):\n"
        f"> {item.source_text or ''}"
    )

    draft_block = f"Draft reply (NULO):\n> {item.draft_text or ''}"

    if kb_lines:
        kb_body = "\n".join(
            "• {title} — {url}{refreshed}".format(
                title=ln.title,
                url=ln.source_url or "(no source url)",
                refreshed=(
                    f", last refreshed {ln.last_refreshed_at}"
                    if ln.last_refreshed_at
                    else ""
                ),
            )
            for ln in kb_lines
        )
        kb_block = "KB sources used:\n" + kb_body
    else:
        kb_block = "KB sources used:\n• (none)"

    parts = [header, "", source_block, "", draft_block, "", kb_block]
    if item.refusal_pattern:
        parts.append("")
        parts.append(f"⚠️ Hard-refusal triggered: {item.refusal_pattern}")
        parts.append(
            "The draft is a calibrated refusal. Verify tone before posting."
        )
    return "\n".join(parts)


def build_review_buttons(draft_id: int) -> List[Tuple[str, str]]:
    """Build the inline-keyboard (label, callback_data) pairs (HITL_UX §1 / §2).

    Five buttons in the documented order; each callback data is
    ``autocm:review:<action>:<draft_id>`` so the C2.7 registry routes them to the
    review-queue controller by :data:`CALLBACK_PREFIX`.
    """
    return [
        ("Approve", f"{CALLBACK_PREFIX}approve:{draft_id}"),
        ("Edit", f"{CALLBACK_PREFIX}edit:{draft_id}"),
        ("Reject", f"{CALLBACK_PREFIX}reject:{draft_id}"),
        ("Punt to founder", f"{CALLBACK_PREFIX}punt:{draft_id}"),
        ("Why this routing?", f"{CALLBACK_PREFIX}why:{draft_id}"),
    ]


def parse_callback_data(data: str) -> Optional[Tuple[str, int]]:
    """Parse ``autocm:review:<action>:<draft_id>`` → ``(action, draft_id)``.

    Returns ``None`` for data that does not match the review-queue shape (so a
    foreign callback that slipped past the prefix router is ignored, not crashed
    on).
    """
    if not data.startswith(CALLBACK_PREFIX):
        return None
    rest = data[len(CALLBACK_PREFIX):]
    parts = rest.split(":")
    if len(parts) != 2:
        return None
    action, raw_id = parts
    if action not in REVIEW_ACTIONS:
        return None
    try:
        draft_id = int(raw_id)
    except ValueError:
        return None
    return action, draft_id


# ---------------------------------------------------------------------------
# Transport seam: the bot that actually posts to the operator chat
# ---------------------------------------------------------------------------
@runtime_checkable
class BotSender(Protocol):
    """The transport the surface uses to post to the OPERATOR chat (not the public chat).

    A real impl wraps the PTB ``Bot`` send/edit calls; tests inject a fake that
    records calls (NO real telegram/network). Methods return a surface-specific
    message handle (TG message id as a string). This seam ONLY ever writes to the
    operator chat — the C3.6 publisher owns the public reply outbox enqueue, so
    nothing here can publish a public reply.

    OUTBOUND-SAFETY CONTRACT (relay §15.2). :func:`render_review_message` mirrors
    USER-CONTROLLED fields (``source_username`` / ``source_text``) into the operator
    chat verbatim, interleaved with bot markup (the 🟡/🔴 header, ``> `` quote
    prefixes, ``•`` KB bullets, the ⚠️ hard-refusal line). The relay-wide §15.2
    invariant is that every outbound send neutralizes user substrings. A
    ``BotSender`` impl MUST satisfy that invariant under whatever mode it actually
    sends in:

      * **PLAIN TEXT (the v1 contract).** Send with NO parse mode — Telegram
        ``parse_mode=None``, Discord no Markdown/HTML interpretation — AND, on
        Discord, ``allowed_mentions=discord.AllowedMentions.none()`` (see
        :func:`sable_platform.relay.bot.escaping.discord_allowed_mentions`). In plain
        text a user substring like ``<script>`` or ``@everyone`` is inert: there is
        no markup to inject into and (with AllowedMentions.none()) no mention
        resolves. The rendered body is built for THIS mode and the FakeBot path
        records it verbatim. THIS IS THE EXPECTED v1 MODE.
      * **If a real impl chooses HTML/Markdown parse mode** (natural given the bot
        markup), it MUST first route every user substring through
        :func:`sable_platform.relay.bot.escaping.escape_telegram_text` /
        :func:`escape_discord` before interpolation, exactly as the relay does —
        otherwise it reintroduces the §15.2 HTML-injection vector into the per-client
        operator chat. ``render_review_message`` does NOT escape (it targets plain
        text), so a parse-mode sender that skips escaping is a §15.2 violation.

    The C3.6 real ``BotSender`` (not built yet) is bound by this contract.
    """

    def send_message(
        self, chat_id: str, body: str, *, buttons: Optional[Sequence[Tuple[str, str]]] = None
    ) -> str: ...

    def edit_message(self, chat_id: str, handle: str, body: str) -> None: ...

    def send_reply(self, chat_id: str, body: str, *, reply_to: Optional[str] = None) -> str: ...


# ---------------------------------------------------------------------------
# autocm_reviews / autocm_drafts record helpers
# ---------------------------------------------------------------------------
def _client_id_for_org(conn: Connection, org_id: str) -> Optional[int]:
    row = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"),
        {"o": org_id},
    ).fetchone()
    return int(row[0]) if row is not None else None


def already_reviewed(conn: Connection, draft_id: int) -> bool:
    """True iff a decision row already exists for this draft (dedupe backstop).

    The C2.7 callback router already drops a redelivered ``CallbackQuery`` (same
    ``update_id``) — this is the SECOND backstop against a double-apply: a callback
    that arrives under a FRESH update_id (so the router's update_id dedupe does not
    catch it) still records exactly one decision, because the controller refuses to
    act on a draft that already has an ``autocm_reviews`` decision row.

    This is a read-then-write guard, NOT a DB-enforced unique constraint
    (``autocm_reviews`` has only a non-unique index on ``draft_id``, 058). It is
    TOCTOU-safe under the ACTUAL architecture — the single long-lived connection +
    synchronous callback dispatch: the whole ``on_callback`` (read + record +
    commit) runs to completion before the next callback is dispatched, and the C2.7
    update_id claim serializes redeliveries. The guarantee therefore rests on that
    single-connection / synchronous-dispatch model and would NOT survive concurrent
    writers in a hypothetical multi-process listener deployment. If a future tier
    ever runs multiple listener processes, add a UNIQUE index on
    ``autocm_reviews(draft_id)`` (a future migration) to make the dedupe durable
    independent of the connection model.
    """
    row = conn.execute(
        text("SELECT 1 FROM autocm_reviews WHERE draft_id = :d LIMIT 1"),
        {"d": draft_id},
    ).fetchone()
    return row is not None


def _load_draft(conn: Connection, draft_id: int) -> Optional[dict]:
    row = conn.execute(
        text(
            "SELECT id, client_id, source_message_id, category, tier, register, "
            "       draft_text, confidence, cited_chunk_ids, status, created_at "
            "FROM autocm_drafts WHERE id = :d"
        ),
        {"d": draft_id},
    ).fetchone()
    return dict(row._mapping) if row is not None else None


def record_review_decision(
    conn: Connection,
    *,
    draft_id: int,
    client_id: int,
    reviewer: Optional[str],
    decision: str,
    draft_text: Optional[str] = None,
    edited_text: Optional[str] = None,
    note: Optional[str] = None,
    org_id: Optional[str] = None,
    source_message_id: Optional[int] = None,
    cited_chunk_ids: Optional[Sequence[int]] = None,
    category: Optional[str] = None,
    tier: Optional[int] = None,
    confidence: Optional[float] = None,
) -> int:
    """Insert one ``autocm_reviews`` row + the SAFETY §5 audit row; return review id.

    Computes the load-bearing ``is_clean_approval`` flag the C3.5a autonomy sweep
    reads (``gather_review_stats`` SUMs ``is_clean_approval``), so the quantity is
    consistent end-to-end:

      * ``approve``           → clean (edit_diff_size 0.0);
      * ``edit``              → ``edit_diff_size = edit_diff_ratio(draft, edited)``;
                                clean ONLY iff that ratio is ``<= 0.30`` (a light
                                touch-up). A heavy edit (> 0.30) is NOT clean
                                (flagged for the digest voice-drift watch);
      * ``reject`` / ``punt_to_founder`` → never clean (edit_diff_size 0.0).

    Also writes the SAFETY §5 audit field set (``source_message_id``, cited
    ``chunk_ids``, ``draft_text``, ``final_text`` (the edit delta when edited),
    reviewer, tier/category/confidence) onto the audit trail — every operator
    decision is audited.
    """
    edit_diff_size = 0.0
    final_text: Optional[str] = None
    if decision == "edit":
        edit_diff_size = edit_diff_ratio(draft_text, edited_text)
        final_text = edited_text
        is_clean = not is_heavy_edit(edit_diff_size)
    elif decision == "approve":
        is_clean = True
        final_text = draft_text
    else:  # reject / punt_to_founder
        is_clean = False

    # Bind reviewed_at EXPLICITLY in _iso_z (...T...Z) form — do NOT rely on the
    # column DEFAULT. On Postgres the Alembic default is func.now() over a TEXT
    # column, which renders SPACE-separated ('2026-05-31 12:00:00+00', no T/Z) and
    # is NOT lexically comparable to the _iso_z :since bound the C3.5a rolling-7d
    # sweep (gather_review_stats) uses on the autocm_reviews leg. A space-form value
    # sorts below any T-prefixed :since (0x20 < 0x54), so a default-written row would
    # be silently dropped from the rolling window and the auto-demotion safety
    # mechanism would never see recent reviews. The 058 migration docstring pins this
    # exact contract for the C3.5b write path; kb/refresher binds the same way.
    row = conn.execute(
        text(
            "INSERT INTO autocm_reviews "
            "(draft_id, client_id, reviewer, decision, edited_text, edit_diff_size, "
            " is_clean_approval, note, reviewed_at) "
            "VALUES (:d, :c, :rev, :dec, :edited, :eds, :clean, :note, :rev_at) "
            "RETURNING id"
        ),
        {
            "d": draft_id,
            "c": client_id,
            "rev": reviewer,
            "dec": decision,
            "edited": edited_text,
            "eds": edit_diff_size,
            "clean": 1 if is_clean else 0,
            "note": note,
            "rev_at": _iso_z(_utc_now()),
        },
    ).fetchone()
    review_id = int(row[0])

    log_audit(
        conn,
        actor=reviewer or AUDIT_SOURCE,
        action=ACTION_REVIEW_DECISION,
        org_id=org_id,
        entity_id=str(draft_id),
        detail={
            "draft_id": draft_id,
            "decision": decision,
            "reviewer": reviewer,
            "source_message_id": source_message_id,
            "chunk_ids": list(cited_chunk_ids or []),
            "draft_text": draft_text,
            "final_text": final_text,
            "edit_diff_size": round(edit_diff_size, 4),
            "is_clean_approval": is_clean,
            "heavy_edit": is_heavy_edit(edit_diff_size) if decision == "edit" else False,
            "category": category,
            "tier": tier,
            "confidence": confidence,
        },
        source=AUDIT_SOURCE,
    )
    return review_id


def _set_draft_status(
    conn: Connection, draft_id: int, status: str, *, resolved: bool = True
) -> None:
    if resolved:
        conn.execute(
            text(
                "UPDATE autocm_drafts SET status = :s, resolved_at = :now WHERE id = :d"
            ),
            {"s": status, "now": _iso_z(_utc_now()), "d": draft_id},
        )
    else:
        conn.execute(
            text("UPDATE autocm_drafts SET status = :s WHERE id = :d"),
            {"s": status, "d": draft_id},
        )


# ---------------------------------------------------------------------------
# HITLReviewSurface ABC + impls
# ---------------------------------------------------------------------------
class HITLReviewSurface(ABC):
    """The seam: post a :class:`ReviewItem` to the human-in-the-loop surface.

    Implementations: :class:`TelegramReviewSurface` (v1, over C2.7) and
    :class:`WebDashboardReviewSurface` (v2 stub). The pipeline depends on this ABC
    only — the surface is config-selected per deployment tier.
    """

    @abstractmethod
    def post_review(self, item: ReviewItem) -> str:
        """Post a review item; return a surface-specific handle (e.g. TG message id).

        Raises if the surface is not provisioned (rather than silently dropping the
        HITL queue — SAFETY §5 observability).
        """
        ...

    @abstractmethod
    def is_available(self, org_id: str) -> bool:
        """True iff the surface is provisioned/reachable for this client."""
        ...


class TelegramReviewSurface(HITLReviewSurface):
    """v1 HITL surface: the per-client TG operator chat, over the C2.7 registry.

    Resolves the operator chat through :class:`RelayHandlerRegistry`
    (``get_operator_chat`` / ``provision_operator_chat``) — the C2.7 HITL surface
    primitive — and posts the review-queue message + inline buttons via an injected
    :class:`BotSender` (C3.5b). The inline-button callbacks route back through
    C2.7's callback router; the :class:`ReviewQueueController` registers the
    callback handler and applies the operator's decision.

    The ``bot`` sender is OPTIONAL so the C3.1 resolution path (provision/resolve,
    no posting) keeps working: with no sender, an attempted post on an
    unprovisioned chat still raises ``RuntimeError`` (never silently drops), and a
    post on a provisioned chat with no sender raises ``RuntimeError`` (a real send
    requires a transport) — it never silently no-ops.
    """

    def __init__(
        self,
        registry: RelayHandlerRegistry,
        *,
        platform: str = "telegram",
        bot: Optional[BotSender] = None,
        conn: Optional[Connection] = None,
    ) -> None:
        self._registry = registry
        self._platform = platform
        self._bot = bot
        # connection for the KB-source render join; defaults to the registry's conn.
        self._conn = conn if conn is not None else getattr(registry, "_conn", None)

    def ensure_provisioned(self, org_id: str, chat_id: str, *, title: Optional[str] = None) -> str:
        """Idempotently provision the operator chat (C2.7 ``provision_operator_chat``)."""
        return self._registry.provision_operator_chat(
            org_id, chat_id, platform=self._platform, title=title
        )

    def is_available(self, org_id: str) -> bool:
        return self._registry.get_operator_chat(org_id, platform=self._platform) is not None

    def resolve_chat(self, org_id: str) -> Optional[str]:
        return self._registry.get_operator_chat(org_id, platform=self._platform)

    def post_review(self, item: ReviewItem) -> str:
        chat_id = self._registry.get_operator_chat(item.org_id, platform=self._platform)
        if chat_id is None:
            raise RuntimeError(
                f"HITL operator chat not provisioned for org {item.org_id!r} "
                f"(platform={self._platform}); provision it before queuing reviews"
            )
        if self._bot is None:
            raise RuntimeError(
                "TelegramReviewSurface has no BotSender wired; inject one to post "
                "review-queue messages (C3.5b)"
            )
        kb_lines: List[KBSourceLine] = []
        if self._conn is not None and item.cited_chunk_ids:
            kb_lines = fetch_kb_source_lines(self._conn, item.cited_chunk_ids)
        body = render_review_message(item, kb_lines)
        buttons = build_review_buttons(item.draft_id)
        return self._bot.send_message(chat_id, body, buttons=buttons)


class WebDashboardReviewSurface(HITLReviewSurface):
    """v2 HITL surface STUB: a managed/self-host web dashboard.

    Demonstrates the seam swaps cleanly to a non-TG surface without a pipeline
    rewrite. Not built in v1 — every method raises. Present so the seam has a
    second (stub) impl and the interface test can assert both satisfy the ABC.
    """

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = base_url

    def is_available(self, org_id: str) -> bool:
        return False

    def post_review(self, item: ReviewItem) -> str:
        raise NotImplementedError("web-dashboard HITL surface is a v2 tier (stub)")


# ---------------------------------------------------------------------------
# The review-queue controller — the C3.5b button/callback flow
# ---------------------------------------------------------------------------
@dataclass
class _PendingEdit:
    """One in-flight [Edit] awaiting the operator's edited-text reply."""

    draft_id: int
    org_id: str
    chat_id: str
    queue_handle: Optional[str]
    started_at: datetime


class ReviewQueueController:
    """Wires the C3.5b review-queue flow onto the C2.7 callback registry.

    Posts a review item (via :class:`TelegramReviewSurface`), registers the
    inline-button callback handler (prefix :data:`CALLBACK_PREFIX`), and applies
    each operator decision:

      * **[Approve]** — record ``approve`` (clean) into ``autocm_reviews``, mark the
        draft ``approved`` (the C3.6 publisher reads approved drafts and enqueues —
        this controller never enqueues), update the queue message to
        "✅ APPROVED & POSTED".
      * **[Edit]** — start a stateful edit: prompt the operator to reply with the
        edited text. When the edited text arrives (``submit_edit``), record ``edit``
        with ``edit_diff_ratio``; a heavy edit (> 0.30) is flagged; mark the draft
        ``approved`` with the edited final text.
      * **[Reject]** — record ``reject`` (never clean), mark the draft ``rejected``;
        the bot posts NOTHING (public).
      * **[Punt to founder]** — record ``punt_to_founder``, mark the draft
        ``escalated``, insert an ``autocm_escalations`` row; the bot posts NOTHING
        (public).
      * **[Why this routing?]** — reply in the operator chat with the classifier
        reasoning + confidence + category state (read-only; NO decision row).

    Dedupe (no double-apply on redelivery): the C2.7 callback router already drops
    a redelivered ``CallbackQuery``; this controller additionally refuses to act on
    a draft that already has a decision row (:func:`already_reviewed`), so a
    second action on the same draft is a safe no-op.

    SAFETY §2 / CLASSIFIER §3: the controller renders ``display_name`` / handle /
    user text for the human operator only. It does no LLM work — nothing it reads
    flows into a model prompt.
    """

    def __init__(
        self,
        conn: Connection,
        surface: TelegramReviewSurface,
        *,
        reviewer: Optional[str] = None,
    ) -> None:
        self._conn = conn
        self._surface = surface
        self._reviewer = reviewer
        self._bot = surface._bot
        # in-flight [Edit] sessions, keyed by draft_id.
        self._pending_edits: Dict[int, _PendingEdit] = {}
        # remember the posted queue-message handle per draft (for status updates).
        self._queue_handles: Dict[int, Tuple[str, str]] = {}  # draft_id -> (chat_id, handle)
        # remember the classifier reasoning per draft so [Why this routing?] can
        # surface it (HITL_UX §2). The reasoning is carried on the in-memory
        # ReviewItem at post time but NOT persisted to autocm_drafts (no reasoning
        # column in 058), so it is stashed here keyed by draft_id — same in-memory
        # pattern as _queue_handles. This is render-only operator-facing text; it is
        # NEVER fed into an LLM/classifier payload (SAFETY §2 / CLASSIFIER §3).
        self._reasoning: Dict[int, str] = {}  # draft_id -> classifier reasoning

    # -- registration ---------------------------------------------------------
    def register(self, registry: RelayHandlerRegistry) -> None:
        """Register the inline-button callback handler on the C2.7 registry."""
        registry.register_callback_handler(self.on_callback, prefix=CALLBACK_PREFIX)

    # -- posting --------------------------------------------------------------
    def post(self, item: ReviewItem) -> str:
        """Post the review-queue message and remember its handle for status updates."""
        handle = self._surface.post_review(item)
        chat_id = self._surface.resolve_chat(item.org_id)
        if chat_id is not None:
            self._queue_handles[item.draft_id] = (chat_id, handle)
        if item.reasoning:
            self._reasoning[item.draft_id] = item.reasoning
        return handle

    # -- callback routing (C2.7) ----------------------------------------------
    def on_callback(self, event: CallbackEvent) -> None:
        """Apply one inline-button press (routed here by the C2.7 registry)."""
        parsed = parse_callback_data(event.data)
        if parsed is None:
            return
        action, draft_id = parsed
        reviewer = event.external_user_id or self._reviewer

        if action == "why":
            self._handle_why(draft_id, event)
            return

        # Dedupe backstop: a draft already decided is a no-op (no double-apply).
        if already_reviewed(self._conn, draft_id):
            return

        if action == "edit":
            self._begin_edit(draft_id, event)
            return

        self._apply_terminal(action, draft_id, reviewer, event)

    # -- terminal decisions (approve / reject / punt) -------------------------
    def _apply_terminal(
        self, action: str, draft_id: int, reviewer: Optional[str], event: CallbackEvent
    ) -> None:
        draft = _load_draft(self._conn, draft_id)
        if draft is None:
            return
        client_id = int(draft["client_id"])
        org_id = event.org_id or self._org_id_for_client(client_id)
        cited = self._parse_cited(draft.get("cited_chunk_ids"))
        decision = ACTION_TO_DECISION[action]

        record_review_decision(
            self._conn,
            draft_id=draft_id,
            client_id=client_id,
            reviewer=reviewer,
            decision=decision,
            draft_text=draft.get("draft_text"),
            org_id=org_id,
            source_message_id=draft.get("source_message_id"),
            cited_chunk_ids=cited,
            category=draft.get("category"),
            tier=draft.get("tier"),
            confidence=draft.get("confidence"),
        )

        if action == "approve":
            # Mark approved — the C3.6 publisher reads approved drafts and enqueues.
            # This controller NEVER touches the relay outbox (C3.5b/C3.6 boundary).
            _set_draft_status(self._conn, draft_id, STATUS_APPROVED)
            self._update_queue(draft_id, reviewer, "✅ APPROVED & POSTED")
        elif action == "reject":
            # Bot posts NOTHING; the draft is dropped.
            _set_draft_status(self._conn, draft_id, STATUS_REJECTED)
            self._update_queue(draft_id, reviewer, "🚫 REJECTED — nothing posted")
        elif action == "punt":
            _set_draft_status(self._conn, draft_id, STATUS_ESCALATED)
            self._insert_escalation(draft_id, client_id, draft.get("source_message_id"))
            self._update_queue(draft_id, reviewer, "📨 PUNTED TO FOUNDER — nothing posted")
        self._conn.commit()

    # -- [Edit] two-step flow -------------------------------------------------
    def _begin_edit(self, draft_id: int, event: CallbackEvent) -> None:
        """Tap [Edit] → prompt the operator to reply with the edited text (HITL_UX §2)."""
        draft = _load_draft(self._conn, draft_id)
        if draft is None:
            return
        client_id = int(draft["client_id"])
        org_id = event.org_id or self._org_id_for_client(client_id)
        chat_id = self._surface.resolve_chat(org_id) if org_id else None
        queue_handle = self._queue_handles.get(draft_id, (None, None))[1]
        self._pending_edits[draft_id] = _PendingEdit(
            draft_id=draft_id,
            org_id=org_id or "",
            chat_id=chat_id or "",
            queue_handle=queue_handle,
            started_at=_utc_now(),
        )
        if self._bot is not None and chat_id is not None:
            self._bot.send_reply(
                chat_id,
                f"✏️ Reply to this message with the edited text for draft #{draft_id}.",
                reply_to=queue_handle,
            )

    def has_pending_edit(self, draft_id: int) -> bool:
        return draft_id in self._pending_edits

    def submit_edit(
        self, draft_id: int, edited_text: str, *, reviewer: Optional[str] = None
    ) -> Optional[int]:
        """Complete a pending [Edit] with the operator's edited text (HITL_UX §2).

        Records an ``edit`` review with ``edit_diff_size = edit_diff_ratio(draft,
        edited)``; a heavy edit (> 0.30) is flagged (``is_clean_approval=0``) for the
        digest voice-drift watch, a light edit is a clean approval. Marks the draft
        ``approved`` carrying the edited final text. Returns the review row id, or
        ``None`` if there was no pending edit / the draft already had a decision.

        SCOPE NOTE: this is the SECOND leg of the [Edit] two-step. Resolving an
        arriving operator reply (``reply_to == _begin_edit``'s ``queue_handle``) back
        to the matching pending-edit ``draft_id`` and invoking ``submit_edit`` is the
        LISTENER loop's job (the relay message handler, a later chunk), NOT this
        controller's — so that reply→submit_edit join is intentionally not wired or
        tested here. ``_begin_edit`` stashes the ``queue_handle`` on the pending-edit
        session for that future resolver.
        """
        if draft_id not in self._pending_edits:
            return None
        if already_reviewed(self._conn, draft_id):
            self._pending_edits.pop(draft_id, None)
            return None
        draft = _load_draft(self._conn, draft_id)
        if draft is None:
            self._pending_edits.pop(draft_id, None)
            return None
        client_id = int(draft["client_id"])
        org_id = self._pending_edits[draft_id].org_id or self._org_id_for_client(client_id)
        cited = self._parse_cited(draft.get("cited_chunk_ids"))
        rev = reviewer or self._reviewer

        review_id = record_review_decision(
            self._conn,
            draft_id=draft_id,
            client_id=client_id,
            reviewer=rev,
            decision="edit",
            draft_text=draft.get("draft_text"),
            edited_text=edited_text,
            org_id=org_id,
            source_message_id=draft.get("source_message_id"),
            cited_chunk_ids=cited,
            category=draft.get("category"),
            tier=draft.get("tier"),
            confidence=draft.get("confidence"),
        )
        # The edited final text is persisted on the review row (edited_text); the
        # draft moves to 'approved' so the C3.6 publisher enqueues the EDITED reply.
        _set_draft_status(self._conn, draft_id, STATUS_APPROVED)
        ratio = edit_diff_ratio(draft.get("draft_text"), edited_text)
        flag = " (heavy edit ⚠️)" if is_heavy_edit(ratio) else ""
        self._update_queue(draft_id, rev, f"✅ EDITED & POSTED{flag}")
        self._pending_edits.pop(draft_id, None)
        self._conn.commit()
        return review_id

    # -- [Why this routing?] (read-only) --------------------------------------
    def _handle_why(self, draft_id: int, event: CallbackEvent) -> None:
        """Reply with the classifier reasoning + confidence + category state.

        Read-only — NO decision row, the draft stays pending (HITL_UX §2). Surfaces
        all three documented components: the classifier ``reasoning`` (stashed in
        memory at post time — there is no autocm_drafts reasoning column), the
        ``confidence``, and the live category-state for the client × category so the
        operator sees why it routed to HITL vs auto.
        """
        draft = _load_draft(self._conn, draft_id)
        if draft is None or self._bot is None:
            return
        client_id = int(draft["client_id"])
        org_id = event.org_id or self._org_id_for_client(client_id)
        chat_id = self._surface.resolve_chat(org_id) if org_id else None
        if chat_id is None:
            return
        cat = draft.get("category")
        state = self._category_state(client_id, cat)
        conf = draft.get("confidence")
        conf_str = f"{conf:.2f}" if conf is not None else "n/a"
        reasoning = self._reasoning.get(draft_id) or "(not recorded)"
        body = (
            f"Why this routing? draft #{draft_id}\n"
            f"category: {cat} (tier {draft.get('tier')})\n"
            f"confidence: {conf_str}\n"
            f"reasoning: {reasoning}\n"
            f"category state for this client: {state}"
        )
        queue_handle = self._queue_handles.get(draft_id, (None, None))[1]
        self._bot.send_reply(chat_id, body, reply_to=queue_handle)

    # -- 15-min stale auto-expiration (HITL_UX §3) ----------------------------
    def expire_stale_reviews(
        self,
        client_id: int,
        *,
        now: Optional[datetime] = None,
        org_id: Optional[str] = None,
        stale_after_minutes: int = STALE_AFTER_MINUTES,
    ) -> List[int]:
        """Auto-expire tier-2 drafts untouched for ``stale_after_minutes`` (HITL_UX §3).

        For every ``hitl_pending`` draft of the client older than the SLA with NO
        decision row, mark it ``suppressed`` (the bot posts NOTHING) and write a
        "missed window" audit note (``ACTION_REVIEW_EXPIRED``) — better to silently
        miss than to post stale. NO ``final_text`` is recorded (nothing was posted).
        A draft that already has a decision row is skipped (it was resolved in time).

        Returns the list of expired draft ids. The clock is injectable so the SLA
        window is deterministic under test.
        """
        now = now or _utc_now()
        cutoff = _iso_z(now - timedelta(minutes=stale_after_minutes))
        if org_id is None:
            org_id = self._org_id_for_client(client_id)

        rows = self._conn.execute(
            text(
                "SELECT d.id, d.category, d.tier, d.source_message_id "
                "FROM autocm_drafts d "
                "WHERE d.client_id = :c AND d.status = :pending "
                "  AND d.created_at <= :cutoff "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM autocm_reviews r WHERE r.draft_id = d.id) "
                "ORDER BY d.id"
            ),
            {"c": client_id, "pending": STATUS_HITL_PENDING, "cutoff": cutoff},
        ).fetchall()

        expired: List[int] = []
        for r in rows:
            draft_id = int(r[0])
            _set_draft_status(self._conn, draft_id, STATUS_SUPPRESSED)
            log_audit(
                self._conn,
                actor=AUDIT_SOURCE,
                action=ACTION_REVIEW_EXPIRED,
                org_id=org_id,
                entity_id=str(draft_id),
                detail={
                    "draft_id": draft_id,
                    "category": r[1],
                    "tier": r[2],
                    "source_message_id": r[3],
                    "note": "missed window — draft auto-expired, nothing posted",
                    "stale_after_minutes": stale_after_minutes,
                },
                source=AUDIT_SOURCE,
            )
            # drop any dangling pending-edit session / stashed reasoning for an
            # expired draft.
            self._pending_edits.pop(draft_id, None)
            self._reasoning.pop(draft_id, None)
            expired.append(draft_id)
        self._conn.commit()
        return expired

    # -- small helpers --------------------------------------------------------
    def _update_queue(self, draft_id: int, reviewer: Optional[str], status_line: str) -> None:
        """Edit the queue message in place to the resolved-status line (HITL_UX §2)."""
        entry = self._queue_handles.get(draft_id)
        if entry is None or self._bot is None:
            return
        chat_id, handle = entry
        who = f" · {reviewer}" if reviewer else ""
        self._bot.edit_message(
            chat_id, handle, f"{status_line}{who} · {_iso_z(_utc_now())}"
        )

    def _insert_escalation(
        self, draft_id: int, client_id: int, source_message_id: Optional[int]
    ) -> None:
        self._conn.execute(
            text(
                "INSERT INTO autocm_escalations "
                "(client_id, draft_id, source_message_id, reason) "
                "VALUES (:c, :d, :smi, :reason)"
            ),
            {
                "c": client_id,
                "d": draft_id,
                "smi": source_message_id,
                "reason": "operator punt to founder (HITL [Punt to founder])",
            },
        )

    def _org_id_for_client(self, client_id: int) -> Optional[str]:
        row = self._conn.execute(
            text("SELECT org_id FROM autocm_clients WHERE id = :id"),
            {"id": client_id},
        ).fetchone()
        return row[0] if row is not None else None

    def _category_state(self, client_id: int, category: Optional[str]) -> str:
        if not category:
            return "unknown"
        row = self._conn.execute(
            text(
                "SELECT state FROM autocm_category_state "
                "WHERE client_id = :c AND category = :cat"
            ),
            {"c": client_id, "cat": category},
        ).fetchone()
        return row[0] if row is not None else "hitl (default)"

    @staticmethod
    def _parse_cited(raw: Optional[str]) -> List[int]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if isinstance(value, list):
            out: List[int] = []
            for v in value:
                try:
                    out.append(int(v))
                except (ValueError, TypeError):
                    continue
            return out
        return []


__all__ = [
    "HITLReviewSurface",
    "ReviewItem",
    "TelegramReviewSurface",
    "WebDashboardReviewSurface",
    "ReviewQueueController",
    "BotSender",
    "KBSourceLine",
    "REVIEW_ACTIONS",
    "ACTION_TO_DECISION",
    "CALLBACK_PREFIX",
    "STALE_AFTER_MINUTES",
    "HEAVY_EDIT_THRESHOLD",
    "ACTION_REVIEW_DECISION",
    "ACTION_REVIEW_EXPIRED",
    "fetch_kb_source_lines",
    "render_review_message",
    "build_review_buttons",
    "parse_callback_data",
    "record_review_decision",
    "already_reviewed",
]
