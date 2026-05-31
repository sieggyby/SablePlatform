"""Signed media URLs — HMAC-SHA256 over '<bucket>/<key>:<exp>'.

The apps MINT signed URLs at read time; the Cloudflare Worker media proxy VERIFIES
the same signature (WebCrypto) before streaming the private R2 object. Signing the
ref+exp only (NOT the Range header) lets a browser reuse one signed URL across the
range requests it issues while seeking a <video>.

This module is the canonical signature definition + test vector for the Worker.
Pure stdlib (hmac/hashlib/time); secret is injected, never read from config here.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode


def _sig(ref: str, exp: int, secret: str) -> str:
    return hmac.new(secret.encode(), f"{ref}:{exp}".encode(), hashlib.sha256).hexdigest()


def sign_media_url(ref: str, base: str, secret: str, ttl: int = 3600, now: float | None = None) -> str:
    """Return a signed proxy URL for a stored '<bucket>/<key>' ref.

    Empty ref -> "". Already-absolute (legacy Drive) refs pass through unchanged.
    If secret/base is empty, falls back to an UNSIGNED url (build_media_url semantics)
    so callers degrade gracefully before the proxy/secret exist.
    """
    if not ref:
        return ""
    if ref.startswith(("http://", "https://")):
        return ref
    b = (base or "").rstrip("/")
    if not (secret and b):
        return f"{b}/{ref}" if b else ref
    exp = int((now if now is not None else time.time())) + ttl
    qs = urlencode({"exp": exp, "sig": _sig(ref, exp, secret)})
    return f"{b}/{ref}?{qs}"


def verify_media_signature(ref: str, exp: str | int, sig: str, secret: str,
                           now: float | None = None) -> bool:
    """Verify a signed-ref request. Constant-time; rejects expired or tampered.

    This is the reference implementation the Worker mirrors in TypeScript.
    """
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(now if now is not None else time.time()):
        return False
    return hmac.compare_digest(_sig(ref, exp_i, secret), sig or "")
