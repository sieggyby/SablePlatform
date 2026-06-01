"""Resolve a stored media reference to an absolute URL.

Stored form (R2): ``'<bucket>/<key>'`` (no scheme, no leading slash).
Resolved form: ``'{base}/<bucket>/<key>'`` where ``base`` is the media proxy
(MEDIA_PROXY_BASE_URL). Already-absolute refs (legacy Drive URLs) pass through.

``base`` is INJECTED by the caller (Slopper reads it from its config; Tracking
from MEDIA_PROXY_BASE_URL) — never imported here, so the lib stays config-free.
"""
from __future__ import annotations


def build_media_url(ref: str, base: str) -> str:
    if not ref:
        return ""
    if ref.startswith(("http://", "https://")):
        return ref  # already absolute (legacy Drive)
    base = (base or "").rstrip("/")
    return f"{base}/{ref}" if base else ref
