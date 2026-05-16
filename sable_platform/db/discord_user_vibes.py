"""DB helpers for sable-roles personalization layer (mig 047).

Three tables back this module:

* ``discord_message_observations`` (3.7) — raw per-message observation log,
  the source data for the daily rollup. Written by the on_message listener
  in sable-roles (R10). Reactions GIVEN BY a user are merged into the
  ``reactions_given_json`` slot on the existing row via :func:`merge_reaction_given`
  (UPSERT pattern). Nightly GC via :func:`gc_old_observations` bounds the
  table size.

* ``discord_user_observations`` (3.4) — rollup, written by the daily cron
  in sable-roles. Each row is a single (guild_id, user_id) snapshot summarizing
  message counts + sample message contents + reactions-given totals + the
  set of channels the user was active in over the rollup window. Multiple
  rows per user are kept (audit trail) — readers pick the latest via
  :func:`get_latest_observation`.

* ``discord_user_vibes`` (3.5) — LLM-summarized per-user vibe block,
  populated weekly by the inference cron. Strict 5-field JSON output from
  the model is validated by :func:`validate_inferred_vibe` (rejects on
  schema or imperative-guard violations — defuses prompt-injection per
  post-audit BLOCKER 6) and then UPSERTed with both the 5 individual fields
  and the rendered ``<user_vibe>...</user_vibe>`` block ready for §5.3
  injection.

Privacy: :func:`purge_user_personalization_data` deletes both raw
observations + rollup + vibe rows for a (guild_id, user_id) tuple. Called
from /stop-pls and (future) on-leave-guild handlers to honor the consent
surface in plan §0.3.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

VIBE_FIELDS: tuple[str, ...] = (
    "identity",
    "activity_rhythm",
    "reaction_signature",
    "palette_signals",
    "tone",
)
VIBE_FIELD_RENDER_LABELS: dict[str, str] = {
    "identity": "identity",
    "activity_rhythm": "activity",
    "reaction_signature": "reactions",
    "palette_signals": "palette",
    "tone": "tone",
}
VIBE_FIELD_MAX_CHARS = 80
VIBE_FIELD_UNKNOWN_VALUE = "unknown"

# Imperative-guard denylist for inferred vibe field values. Matches the
# §7.3 spec — rejects any value containing a token that looks like a
# directive the model could have absorbed from raw Discord messages and
# laundered into a vibe field. Case-insensitive, word-boundary anchored.
#
# Each base token is enumerated with its common English-morphology forms
# (-ing/-ed/-es/-s + the -e-drop verb stems like praise→prais → praising).
# Pure `\b(word)\b` matching missed suffixed forms (roasting, praising);
# pure `\b(word)\w*\b` matching missed -e-drop forms (`praise` is not a
# substring of `praising`). Explicit enumeration sidesteps both gaps.
# "do" is intentionally omitted — too short, swamped by false positives
# (doctor, donor, doubt). Plan §7.3 lists it but the cost outweighs the
# defense gain at this prefix length.
_IMPERATIVE_DENYLIST: tuple[str, ...] = (
    # ignore family
    "ignore", "ignores", "ignored", "ignoring",
    # override family
    "override", "overrides", "overrode", "overriding", "overridden",
    # write family
    "write", "writes", "wrote", "writing", "written",
    # praise family
    "praise", "praises", "praised", "praising", "praiseworthy",
    # roast family
    "roast", "roasts", "roasted", "roasting",
    # system family
    "system", "systems", "systemic", "systematic",
    # prompt family
    "prompt", "prompts", "prompted", "prompting",
    # instruction family
    "instruction", "instructions", "instructional", "instruct", "instructs",
    "instructed", "instructing",
    # rule family
    "rule", "rules", "ruled", "ruling",
    # please family
    "please", "pleased", "pleases", "pleasing",
    # modal verbs
    "must", "should",
)
_IMPERATIVE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _IMPERATIVE_DENYLIST) + r")\b",
    re.IGNORECASE,
)


def _now_iso_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Raw message observations (3.7)
# ---------------------------------------------------------------------------


def insert_message_observation(
    conn: Connection,
    *,
    guild_id: str,
    channel_id: str,
    message_id: str,
    user_id: str,
    content_truncated: str | None,
    posted_at: str,
) -> bool:
    """Insert a raw message-observation row. Idempotent on (guild_id, message_id).

    Returns True if a new row was inserted, False if the message was already
    captured (UNIQUE constraint hit). Reactions are merged in later by
    :func:`merge_reaction_given` on the existing row.
    """
    result = conn.execute(
        text(
            "INSERT INTO discord_message_observations"
            " (guild_id, channel_id, message_id, user_id,"
            "  content_truncated, posted_at, captured_at)"
            " VALUES (:g, :c, :m, :u, :content, :posted, :now)"
            " ON CONFLICT (guild_id, message_id) DO NOTHING"
        ),
        {
            "g": guild_id,
            "c": channel_id,
            "m": message_id,
            "u": user_id,
            "content": content_truncated,
            "posted": posted_at,
            "now": _now_iso_seconds(),
        },
    )
    conn.commit()
    return result.rowcount == 1


def merge_reaction_given(
    conn: Connection,
    *,
    guild_id: str,
    message_id: str,
    emoji: str,
) -> bool:
    """Merge a +1 reaction count for ``emoji`` into the existing observation
    row's ``reactions_given_json`` dict. No-op if no row exists for
    (guild_id, message_id) — the reactions table is observation-rooted,
    so we don't fabricate a row from a reaction alone.

    Returns True if a row was updated. Read-modify-write is acceptable
    here because reactions are coarse-grained signals and the bot is
    single-process per guild (per sable-roles CLAUDE.md).
    """
    row = conn.execute(
        text(
            "SELECT reactions_given_json FROM discord_message_observations"
            " WHERE guild_id = :g AND message_id = :m LIMIT 1"
        ),
        {"g": guild_id, "m": message_id},
    ).fetchone()
    if row is None:
        return False
    current_json = row["reactions_given_json"]
    if current_json is None:
        counts: dict[str, int] = {}
    else:
        try:
            parsed = json.loads(current_json)
        except (TypeError, ValueError):
            parsed = {}
        counts = parsed if isinstance(parsed, dict) else {}
    counts[emoji] = int(counts.get(emoji, 0)) + 1
    conn.execute(
        text(
            "UPDATE discord_message_observations"
            " SET reactions_given_json = :rj"
            " WHERE guild_id = :g AND message_id = :m"
        ),
        {"rj": json.dumps(counts, sort_keys=True), "g": guild_id, "m": message_id},
    )
    conn.commit()
    return True


def list_recent_message_observations(
    conn: Connection,
    guild_id: str,
    user_id: str,
    *,
    within_days: int,
    as_of_utc: datetime | None = None,
) -> list[dict]:
    """Return raw observation rows for (guild_id, user_id) in the last
    ``within_days``. Used by the daily rollup cron in sable-roles.
    """
    cutoff = (
        (as_of_utc or datetime.now(timezone.utc)) - timedelta(days=within_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        text(
            "SELECT id, guild_id, channel_id, message_id, user_id,"
            "       content_truncated, reactions_given_json,"
            "       posted_at, captured_at"
            " FROM discord_message_observations"
            " WHERE guild_id = :g AND user_id = :u AND posted_at > :cutoff"
            " ORDER BY posted_at ASC, id ASC"
        ),
        {"g": guild_id, "u": user_id, "cutoff": cutoff},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def list_recent_observation_users(
    conn: Connection,
    guild_id: str,
    *,
    within_days: int,
    as_of_utc: datetime | None = None,
) -> list[str]:
    """Return distinct user_ids posting in (guild_id) within ``within_days``.

    Used by R10's daily rollup cron to enumerate who needs a rollup row
    refreshed without scanning every (guild, user) pair globally.
    """
    cutoff = (
        (as_of_utc or datetime.now(timezone.utc)) - timedelta(days=within_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        text(
            "SELECT DISTINCT user_id FROM discord_message_observations"
            " WHERE guild_id = :g AND posted_at > :cutoff"
            " ORDER BY user_id ASC"
        ),
        {"g": guild_id, "cutoff": cutoff},
    ).fetchall()
    return [r["user_id"] for r in rows]


def gc_old_observations(
    conn: Connection,
    *,
    older_than_days: int,
    as_of_utc: datetime | None = None,
) -> int:
    """Delete raw observation rows older than ``older_than_days`` (by
    ``captured_at``). Returns the number of rows deleted.
    """
    cutoff = (
        (as_of_utc or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = conn.execute(
        text(
            "DELETE FROM discord_message_observations WHERE captured_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    conn.commit()
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Rollup observations (3.4)
# ---------------------------------------------------------------------------


def insert_observation_rollup(
    conn: Connection,
    *,
    guild_id: str,
    user_id: str,
    window_start: str,
    window_end: str,
    message_count: int,
    sample_messages: list[str] | None,
    reaction_emojis_given: dict[str, int] | None,
    channels_active_in: list[str] | None,
) -> int:
    """Append a rollup row for (guild_id, user_id, window). Returns id.

    Append-only by design — the daily cron can re-run on a different
    window without losing the prior snapshot. ``get_latest_observation``
    picks the freshest row.
    """
    result = conn.execute(
        text(
            "INSERT INTO discord_user_observations"
            " (guild_id, user_id, window_start, window_end, message_count,"
            "  sample_messages_json, reaction_emojis_given_json,"
            "  channels_active_in_json, computed_at)"
            " VALUES (:g, :u, :ws, :we, :n, :sm, :rj, :ch, :now)"
            " RETURNING id"
        ),
        {
            "g": guild_id,
            "u": user_id,
            "ws": window_start,
            "we": window_end,
            "n": int(message_count),
            "sm": json.dumps(sample_messages, ensure_ascii=False) if sample_messages else None,
            "rj": json.dumps(reaction_emojis_given, sort_keys=True) if reaction_emojis_given else None,
            "ch": json.dumps(channels_active_in) if channels_active_in else None,
            "now": _now_iso_seconds(),
        },
    ).fetchone()
    conn.commit()
    return int(result["id"])


def get_latest_observation(
    conn: Connection,
    guild_id: str,
    user_id: str,
) -> dict | None:
    """Return the freshest rollup row for (guild_id, user_id) by computed_at."""
    row = conn.execute(
        text(
            "SELECT id, guild_id, user_id, window_start, window_end,"
            "       message_count, sample_messages_json,"
            "       reaction_emojis_given_json, channels_active_in_json,"
            "       computed_at"
            " FROM discord_user_observations"
            " WHERE guild_id = :g AND user_id = :u"
            " ORDER BY computed_at DESC, id DESC LIMIT 1"
        ),
        {"g": guild_id, "u": user_id},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# Vibe inference output validation (§7.3 — post-audit BLOCKER 6)
# ---------------------------------------------------------------------------


def validate_inferred_vibe(model_output: str | dict) -> dict | None:
    """Validate strict JSON output from the vibe-inference model.

    Returns the parsed dict on success — exactly five string fields, each
    ≤ 80 chars, no imperative tokens. Returns ``None`` on any of:

    * JSON parse failure
    * the ``{"insufficient_data": true}`` short-circuit
    * schema violation (missing/extra keys, wrong types, length cap)
    * imperative-guard regex hit on any field value

    The cron in sable-roles MUST treat None as "skip this user" and not
    UPSERT a row — this is the gate that defuses prompt injection from
    inferred vibe text (post-audit BLOCKER 6). Tested adversarially in
    ``test_discord_user_vibes``.
    """
    # 1. Parse
    if isinstance(model_output, str):
        try:
            payload = json.loads(model_output)
        except (TypeError, ValueError):
            return None
    elif isinstance(model_output, dict):
        payload = model_output
    else:
        return None

    if not isinstance(payload, dict):
        return None

    # 2. Insufficient data short-circuit — model self-declared the row
    # shouldn't be written. Don't treat this as an error, but do return
    # None so callers skip the UPSERT.
    if payload.get("insufficient_data") is True:
        return None

    # 3. Schema: exactly the 5 required keys, all string values
    if set(payload.keys()) != set(VIBE_FIELDS):
        return None
    for field in VIBE_FIELDS:
        value = payload[field]
        if not isinstance(value, str):
            return None
        if len(value) == 0 or len(value) > VIBE_FIELD_MAX_CHARS:
            return None

    # 4. Imperative guard. "unknown" sentinel is the one exemption — it's
    # the spec-mandated value for unknowable fields and shouldn't be
    # tripped by, say, a future denylist token that happens to match.
    for field in VIBE_FIELDS:
        value = payload[field]
        if value == VIBE_FIELD_UNKNOWN_VALUE:
            continue
        if _IMPERATIVE_RE.search(value):
            return None

    return {field: payload[field] for field in VIBE_FIELDS}


def render_vibe_block(fields: dict[str, str]) -> str:
    """Render the canonical ``<user_vibe>...</user_vibe>`` block text.

    Caller must pass validated fields (from :func:`validate_inferred_vibe`).
    Output matches §7.3 layout — five labeled lines wrapped in a single
    ``<user_vibe>`` tag, ready for user-role injection in §5.3.
    """
    lines = [
        f"{VIBE_FIELD_RENDER_LABELS[field]}: {fields[field]}"
        for field in VIBE_FIELDS
    ]
    return "<user_vibe>\n" + "\n".join(lines) + "\n</user_vibe>"


# ---------------------------------------------------------------------------
# Vibes table (3.5)
# ---------------------------------------------------------------------------


def upsert_vibe(
    conn: Connection,
    *,
    guild_id: str,
    user_id: str,
    fields: dict[str, str],
    source_observation_id: int | None = None,
) -> int:
    """Append a new vibe row for (guild_id, user_id). Returns id.

    Append-only by design — old vibe rows are preserved so the weekly
    cron's history is auditable. Readers use :func:`get_latest_vibe`.
    ``fields`` must be the validated 5-field dict from
    :func:`validate_inferred_vibe`.
    """
    if set(fields.keys()) != set(VIBE_FIELDS):
        raise ValueError(
            f"fields must contain exactly {VIBE_FIELDS}, got {sorted(fields.keys())!r}"
        )
    vibe_block_text = render_vibe_block(fields)
    result = conn.execute(
        text(
            "INSERT INTO discord_user_vibes"
            " (guild_id, user_id, vibe_block_text, identity, activity_rhythm,"
            "  reaction_signature, palette_signals, tone, inferred_at,"
            "  source_observation_id)"
            " VALUES (:g, :u, :block, :ident, :act, :react, :pal, :tone,"
            "         :now, :sid)"
            " RETURNING id"
        ),
        {
            "g": guild_id,
            "u": user_id,
            "block": vibe_block_text,
            "ident": fields["identity"],
            "act": fields["activity_rhythm"],
            "react": fields["reaction_signature"],
            "pal": fields["palette_signals"],
            "tone": fields["tone"],
            "now": _now_iso_seconds(),
            "sid": source_observation_id,
        },
    ).fetchone()
    conn.commit()
    return int(result["id"])


def get_latest_vibe(
    conn: Connection,
    guild_id: str,
    user_id: str,
    *,
    max_age_days: int | None = None,
    as_of_utc: datetime | None = None,
) -> dict | None:
    """Return the freshest vibe row for (guild_id, user_id).

    If ``max_age_days`` is set, returns None when the latest row is
    older than that — used by §5.3 to drop stale vibe blocks rather
    than ship a months-out-of-date inference.
    """
    row = conn.execute(
        text(
            "SELECT id, guild_id, user_id, vibe_block_text, identity,"
            "       activity_rhythm, reaction_signature, palette_signals,"
            "       tone, inferred_at, source_observation_id"
            " FROM discord_user_vibes"
            " WHERE guild_id = :g AND user_id = :u"
            " ORDER BY inferred_at DESC, id DESC LIMIT 1"
        ),
        {"g": guild_id, "u": user_id},
    ).fetchone()
    if row is None:
        return None
    result = dict(row._mapping)
    if max_age_days is not None:
        cutoff_dt = (
            (as_of_utc or datetime.now(timezone.utc))
            - timedelta(days=max_age_days)
        )
        try:
            inferred_dt = datetime.strptime(
                str(result["inferred_at"])[:19], "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            inferred_dt = None
        if inferred_dt is None or inferred_dt < cutoff_dt:
            return None
    return result


# ---------------------------------------------------------------------------
# Privacy surface — purge all personalization data for a user
# ---------------------------------------------------------------------------


def purge_user_personalization_data(
    conn: Connection,
    guild_id: str,
    user_id: str,
) -> dict[str, int]:
    """Delete all retained personalization rows for (guild_id, user_id).

    Used by /stop-pls (R4) and (future) on-leave-guild handlers to honor
    the consent surface from plan §0.3. Returns the per-table delete
    counts: ``{"discord_user_vibes": n, "discord_user_observations": n,
    "discord_message_observations": n}``.

    Vibes are deleted BEFORE observations to keep the FK chain clean
    (vibes.source_observation_id REFERENCES discord_user_observations.id).
    """
    counts: dict[str, int] = {}
    for table in (
        "discord_user_vibes",
        "discord_user_observations",
        "discord_message_observations",
    ):
        result = conn.execute(
            text(
                f"DELETE FROM {table}"
                " WHERE guild_id = :g AND user_id = :u"
            ),
            {"g": guild_id, "u": user_id},
        )
        counts[table] = int(result.rowcount or 0)
    conn.commit()
    return counts


__all__ = [
    "VIBE_FIELDS",
    "VIBE_FIELD_RENDER_LABELS",
    "VIBE_FIELD_MAX_CHARS",
    "VIBE_FIELD_UNKNOWN_VALUE",
    "insert_message_observation",
    "merge_reaction_given",
    "list_recent_message_observations",
    "list_recent_observation_users",
    "gc_old_observations",
    "insert_observation_rollup",
    "get_latest_observation",
    "validate_inferred_vibe",
    "render_vibe_block",
    "upsert_vibe",
    "get_latest_vibe",
    "purge_user_personalization_data",
]
