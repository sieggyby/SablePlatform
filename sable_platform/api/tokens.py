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
        expires_at = (
            _dt.datetime.now(_dt.timezone.utc)
            + _dt.timedelta(days=expires_in_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")

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
    if row["expires_at"]:
        # Lexicographic compare on ISO timestamps works because of the format.
        now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        if row["expires_at"] <= now_iso:
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


def touch_last_used(conn: Connection, token_id: str) -> None:
    """Update last_used_at. Best-effort — failure does not block requests."""
    try:
        conn.execute(
            text(
                "UPDATE api_tokens SET last_used_at=CURRENT_TIMESTAMP"
                " WHERE token_id=:tid"
            ),
            {"tid": token_id},
        )
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def revoke_token(conn: Connection, token_id: str) -> bool:
    """Soft-revoke a token. Returns True if a row was affected."""
    result = conn.execute(
        text(
            "UPDATE api_tokens SET enabled=0,"
            " revoked_at=CURRENT_TIMESTAMP WHERE token_id=:tid AND enabled=1"
        ),
        {"tid": token_id},
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
