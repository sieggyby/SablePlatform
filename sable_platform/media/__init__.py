"""Shared media layer — canonical R2 storage + URL + registry for the Sable suite.

See docs/SHARED_MEDIA_LAYER_PLAN_V1.md. Consumed by SableSlopper and SableTracking
so there is one implementation of media upload, the '<bucket>/<key>' reference
convention, URL resolution, and the media_assets registry.
"""
from __future__ import annotations

from sable_platform.media.r2_store import R2Store
from sable_platform.media.registry import (
    find_by_sha,
    get_asset,
    list_assets,
    register_asset,
)
from sable_platform.media.sanitize import (
    FilenameRejected,
    _safe_filename,
    _safe_key,
)
from sable_platform.media.signing import sign_media_url, verify_media_signature
from sable_platform.media.urls import build_media_url

__all__ = [
    "R2Store",
    "build_media_url",
    "sign_media_url",
    "verify_media_signature",
    "register_asset",
    "find_by_sha",
    "get_asset",
    "list_assets",
    "FilenameRejected",
    "_safe_filename",
    "_safe_key",
]
