"""API token issuance, verification, and revocation.

Tokens are stored as SHA-256 hashes. The raw secret is returned exactly
once at issuance time. Verification uses ``hmac.compare_digest`` to avoid
timing-side-channel leaks.

Wire format::

    sp_live_<22 base32 chars>

The first 16 characters (the ``sp_live_<8>`` prefix) double as the
``token_id`` so the verifier can look up by primary key without scanning.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.ts_format import ISO_Z_FORMAT, utc_now_iso


# Token format constants.
_PREFIX = "sp_live_"
_TOKEN_ID_LEN = len(_PREFIX) + 8        # "sp_live_xxxxxxxx"
_SECRET_LEN = 22                         # appended after prefix+id; total ~46 chars

# Allowed scopes — keep aligned with TODO_API.md "Permission Model".
ALLOWED_SCOPES = frozenset({
    "read_only",
    "write_safe",
    "spend_request",
    "spend_execute",
})


@dataclass(frozen=True)
class TokenContext:
    """What an authenticated request knows about its caller."""
    token_id: str
    operator_id: str
    label: str
    scopes: frozenset[str]
    org_scopes: frozenset[str]      # may contain "*" for owner tokens

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def can_access_org(self, org_id: str) -> bool:
        return "*" in self.org_scopes or org_id in self.org_scopes


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_token() -> tuple[str, str]:
    """Generate (token_id, raw_token). token_id is the wire prefix
    that survives in the DB. raw_token is the full secret (never stored)."""
    suffix = secrets.token_urlsafe(6)[:8]
    token_id = _PREFIX + suffix
    secret_part = secrets.token_urlsafe(20)[:_SECRET_LEN]
    raw_token = token_id + "." + secret_part
    return token_id, raw_token


def _split_token(raw: str) -> tuple[str, str] | None:
    """Split a wire token into (token_id, full_raw). Returns None if shape is bad."""
    if not raw or "." not in raw:
        return None
    token_id, _ = raw.split(".", 1)
    if not token_id.startswith(_PREFIX):
        return None
    if len(token_id) != _TOKEN_ID_LEN:
        return None
    return token_id, raw


# ---------------------------------------------------------------------------
# Public API — DB layer
# ---------------------------------------------------------------------------


def issue_token(
    conn: Connection,
    *,
    label: str,
    operator_id: str,
    created_by: str,
    org_scopes: list[str],
    scopes: list[str],
    expires_in_days: int | None = None,
) -> tuple[str, str]:
    """Mint a new API token. Returns (token_id, raw_token).

    The raw_token is returned ONCE and is never recoverable. Caller is
    responsible for handing it to the operator (clipboard, secrets store,
    etc.) and discarding it from memory.

    Validates scopes against ``ALLOWED_SCOPES`` and rejects empty
    ``org_scopes`` (a token must be scoped to at least one org or ``["*"]``).
    """
    bad_scopes = set(scopes) - ALLOWED_SCOPES
    if bad_scopes:
        raise ValueError(f"Unknown scope(s): {sorted(bad_scopes)}")
    if not org_scopes:
        raise ValueError("org_scopes must list at least one org_id (or '*')")
    if not scopes:
        raise ValueError("scopes must list at least one scope")
    if not operator_id or operator_id == "unknown":
        raise ValueError("operator_id is required and must not be 'unknown'")
    if not created_by or created_by == "unknown":
        raise ValueError("created_by is required (owner identity)")

    token_id, raw = _make_token()
    token_hash = _hash_token(raw)
    expires_at = None
    if expires_in_days is not None:
        # Canonical, WITH the `Z`. Without it this is a naive string, and
        # `api_tokens.expires_at` is `timestamp with time zone` on PostgreSQL, so the cast
        # resolves it in the SESSION's timezone. A token minted under a non-UTC session
        # would expire hours early or late.
        expires_at = (
            _dt.datetime.now(_dt.timezone.utc)
            + _dt.timedelta(days=expires_in_days)
        ).strftime(ISO_Z_FORMAT)

    conn.execute(
        text(
            "INSERT INTO api_tokens (token_id, token_hash, label, operator_id,"
            " created_by, expires_at, enabled, scopes_json, org_scopes_json)"
            " VALUES (:token_id, :token_hash, :label, :operator_id,"
            " :created_by, :expires_at, 1, :scopes_json, :org_scopes_json)"
        ),
        {
            "token_id": token_id,
            "token_hash": token_hash,
            "label": label,
            "operator_id": operator_id,
            "created_by": created_by,
            "expires_at": expires_at,
            "scopes_json": json.dumps(sorted(set(scopes))),
            "org_scopes_json": json.dumps(sorted(set(org_scopes))),
        },
    )
    conn.commit()
    return token_id, raw


def verify_token(conn: Connection, raw_token: str) -> TokenContext | None:
    """Look up a token by wire prefix, constant-time compare its hash,
    enforce enabled/expiry. Returns a TokenContext on success, None on
    any failure path. Never raises."""
    split = _split_token(raw_token)
    if not split:
        return None
    token_id, full = split

    row = conn.execute(
        text(
            "SELECT token_hash, label, operator_id, enabled, expires_at,"
            " revoked_at, scopes_json, org_scopes_json"
            " FROM api_tokens WHERE token_id=:tid"
        ),
        {"tid": token_id},
    ).fetchone()
    if not row:
        # Constant-time burn to avoid leaking "id present" via timing.
        hmac.compare_digest(
            _hash_token(full),
            "0" * 64,
        )
        return None

    if not int(row["enabled"]):
        return None
    if row["revoked_at"]:
        return None
    if row["expires_at"] and _is_expired(row["expires_at"]):
        return None

    expected = row["token_hash"]
    actual = _hash_token(full)
    if not hmac.compare_digest(expected, actual):
        return None

    try:
        scopes = frozenset(json.loads(row["scopes_json"]))
        org_scopes = frozenset(json.loads(row["org_scopes_json"]))
    except (TypeError, ValueError):
        return None

    return TokenContext(
        token_id=token_id,
        operator_id=row["operator_id"],
        label=row["label"],
        scopes=scopes,
        org_scopes=org_scopes,
    )


def _is_expired(value, *, now: _dt.datetime | None = None) -> bool:
    """True if a stored ``expires_at`` is at or before *now*. Takes either dialect's type.

    ``api_tokens.expires_at`` is TEXT on SQLite and ``timestamp with time zone`` on
    PostgreSQL (DEFECTS_FOUND item 9), so a raw read hands back a ``str`` on one and a
    ``datetime`` on the other. The code this replaces compared the value to an ISO string,
    which RAISED on PostgreSQL for every token that carries an expiry:

        '<=' not supported between instances of 'datetime.datetime' and 'str'

    Measured against PostgreSQL 16 through the real migration chain.

    An unreadable value now FAILS CLOSED, and that is a deliberate change. The old
    lexicographic compare let one through: ``'garbage' <= '2026-...'`` is False, so a
    corrupt expiry read as "not expired". On an authentication check the safe answer to
    "I cannot tell when this expires" is to reject the token.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(value, _dt.datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    else:
        raw = str(value).strip().replace(" ", "T")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            moment = _dt.datetime.fromisoformat(raw)
        except ValueError:
            return True
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment <= now


# `api_tokens` binds a canonical Python timestamp rather than using SQL. That is the one
# form that is correct BOTH before and after migration 091.
#
# DEFECTS_FOUND item 9: these columns are `timestamp with time zone` on PostgreSQL while
# `schema.py` declares them `Text`. Migration 091 converts them, but the code and the
# migration ship together and a deployment can run either first.
#
#   - `CURRENT_TIMESTAMP` writes the SPACE form, which puts two spellings back in a TEXT
#     column.
#   - `to_char(now() ...)` is TEXT and a timestamptz column rejects it outright:
#         column "ts" is of type timestamp with time zone but expression is of type text
#   - a canonical `...T...Z` string works on both. PostgreSQL coerces it to the right
#     instant under any session timezone, measured, and a TEXT column stores it verbatim.


def touch_last_used(conn: Connection, token_id: str) -> None:
    """Update last_used_at. Best-effort — failure does not block requests."""
    try:
        conn.execute(
            text("UPDATE api_tokens SET last_used_at=:now WHERE token_id=:tid"),
            {"now": utc_now_iso(), "tid": token_id},
        )
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def revoke_token(conn: Connection, token_id: str) -> bool:
    """Soft-revoke a token. Returns True if a row was affected."""
    result = conn.execute(
        text(
            "UPDATE api_tokens SET enabled=0,"
            " revoked_at=:now WHERE token_id=:tid AND enabled=1"
        ),
        {"now": utc_now_iso(), "tid": token_id},
    )
    conn.commit()
    return (result.rowcount or 0) > 0


def list_tokens(conn: Connection) -> list:
    return conn.execute(
        text(
            "SELECT token_id, label, operator_id, created_by, created_at,"
            " expires_at, last_used_at, revoked_at, enabled, scopes_json,"
            " org_scopes_json FROM api_tokens ORDER BY created_at DESC"
        )
    ).fetchall()
