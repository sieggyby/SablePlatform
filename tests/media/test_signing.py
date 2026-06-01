"""Tests for the shared media URL signer (canonical spec for the Worker verifier)."""
from __future__ import annotations

from sable_platform.media.signing import sign_media_url, verify_media_signature

SECRET = "test-secret-0123456789"
REF = "sable-tig/tig/quote_card/world_changed.mp4"
BASE = "https://media.sable.tools"


def _parse(url):
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(url).query)
    return q["exp"][0], q["sig"][0]


def test_sign_then_verify_roundtrip():
    url = sign_media_url(REF, BASE, SECRET, ttl=3600, now=1000)
    assert url.startswith(f"{BASE}/{REF}?")
    exp, sig = _parse(url)
    assert verify_media_signature(REF, exp, sig, SECRET, now=1000) is True


def test_expired_rejected():
    url = sign_media_url(REF, BASE, SECRET, ttl=10, now=1000)
    exp, sig = _parse(url)
    assert verify_media_signature(REF, exp, sig, SECRET, now=2000) is False  # 1010 < 2000


def test_tampered_key_rejected():
    url = sign_media_url(REF, BASE, SECRET, ttl=3600, now=1000)
    exp, sig = _parse(url)
    assert verify_media_signature("sable-tig/tig/quote_card/OTHER.mp4", exp, sig, SECRET, now=1000) is False


def test_tampered_sig_rejected():
    url = sign_media_url(REF, BASE, SECRET, ttl=3600, now=1000)
    exp, _ = _parse(url)
    assert verify_media_signature(REF, exp, "deadbeef", SECRET, now=1000) is False


def test_wrong_secret_rejected():
    url = sign_media_url(REF, BASE, SECRET, ttl=3600, now=1000)
    exp, sig = _parse(url)
    assert verify_media_signature(REF, exp, sig, "other-secret", now=1000) is False


def test_empty_and_absolute_passthrough():
    assert sign_media_url("", BASE, SECRET) == ""
    assert sign_media_url("https://drive/x", BASE, SECRET) == "https://drive/x"


def test_unsigned_fallback_when_no_secret():
    # before the proxy/secret exist, degrade to an unsigned url (bare ref join)
    assert sign_media_url(REF, BASE, "", now=1000) == f"{BASE}/{REF}"
    assert sign_media_url(REF, "", "", now=1000) == REF
