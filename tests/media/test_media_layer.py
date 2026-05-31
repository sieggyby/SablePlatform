"""Tests for the shared media layer (sable_platform.media)."""
from __future__ import annotations


import pytest

from sable_platform.media import build_media_url
from sable_platform.media.registry import (
    find_by_sha,
    get_asset,
    list_assets,
    register_asset,
)
from sable_platform.media.sanitize import FilenameRejected, _safe_filename, _safe_key


# ---- urls -------------------------------------------------------------------

def test_build_media_url_relative():
    assert build_media_url("bkt/k/clip.mp4", "https://m.x.io") == "https://m.x.io/bkt/k/clip.mp4"


def test_build_media_url_empty_and_absolute():
    assert build_media_url("", "https://m.x.io") == ""
    assert build_media_url("https://drive/x", "https://m.x.io") == "https://drive/x"  # legacy passthrough
    assert build_media_url("bkt/k.mp4", "") == "bkt/k.mp4"  # no base → bare ref


# ---- sanitize ---------------------------------------------------------------

def test_safe_key_rejects_traversal():
    for bad in ("..", ".", "a/b", "a\\b", "x\x00y"):
        with pytest.raises(FilenameRejected):
            _safe_key(bad)


def test_safe_filename_double_extension_defense():
    # a video declared as mp4 but named .mp4.html must lose the renderable ext
    out = _safe_filename("clip.mp4.html", "video/mp4")
    assert out.endswith(".mp4") and "html" not in out


def test_safe_filename_unknown_media_mime_rejected():
    with pytest.raises(FilenameRejected):
        _safe_filename("x.mp4", "video/x-unknown-codec")


# ---- registry (real get_db connection on a temp sqlite file) ---------------

def test_register_asset_idempotent(tmp_path):
    from sable_platform.db.connection import get_db
    path = str(tmp_path / "t.db")
    c = get_db(path)
    try:
        c.execute("INSERT INTO orgs (org_id, display_name) VALUES ('tig','TIG')")
        c.commit()
        a1 = register_asset(c, "tig", "slopper", "clip", "bkt/clip_01.mp4",
                            mime="video/mp4", bytes=123, sha256="abc", caption="hello")
        a2 = register_asset(c, "tig", "slopper", "clip", "bkt/clip_01.mp4",
                            mime="video/mp4", bytes=123, sha256="abc", caption="updated")
        assert a1 == a2  # same (org_id, r2_ref) → same asset, no duplicate
        rows = list_assets(c, "tig")
        assert len(rows) == 1
        assert rows[0]["caption"] == "updated"
        assert get_asset(c, "tig", "bkt/clip_01.mp4")["sha256"] == "abc"
        assert find_by_sha(c, "tig", "abc") == "bkt/clip_01.mp4"
        assert find_by_sha(c, "tig", "nope") is None
    finally:
        c.close()
