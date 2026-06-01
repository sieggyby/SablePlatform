"""Filename / key sanitization for media stored in R2.

Ported verbatim from the SableTracking v5-audited spec (R2_MIGRATION_PLAN_V5 §1.2)
so there is ONE canonical implementation. Pure + sync — no I/O, no deps.

Defends against: path traversal, NUL/separator injection, CRLF, and
double-extension content-type spoofing (e.g. ``clip.mp4.html``).
"""
from __future__ import annotations

import urllib.parse


class FilenameRejected(ValueError):
    """Raised when a filename/key cannot be safely stored."""


# Media MIMEs only — non-media (documents, octet-stream) pass through unchanged.
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/tiff": ".tiff", "image/svg+xml": ".svg",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-msvideo": ".avi",
    "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/wav": ".wav",
    "audio/x-m4a": ".m4a",
}

# Extensions a browser may execute/render inline — stripped from media filenames.
_BROWSER_RENDERABLE_EXTENSIONS = frozenset({
    ".html", ".htm", ".svg", ".xml", ".xhtml", ".js", ".mjs", ".css",
})


def _safe_key(part: str) -> str:
    """Sanitize a single path component (folder segment)."""
    if not part or part in (".", ".."):
        raise FilenameRejected(f"Invalid path component: {part!r}")
    if any(c in part for c in ("\x00", "/", "\\")):
        raise FilenameRejected(f"Path component contains forbidden chars: {part!r}")
    encoded = urllib.parse.quote(part, safe="-_.")
    if len(encoded) > 100:
        raise FilenameRejected(f"Path component too long: {len(encoded)} > 100")
    return encoded


def _safe_filename(filename: str, declared_mime: str) -> str:
    """Sanitize a leaf filename, enforcing a MIME-consistent extension.

    For media MIMEs the declared MIME must be known, and any
    browser-renderable extension chain is stripped and replaced with the
    canonical extension for the MIME (double-extension spoofing defense).
    Non-media MIMEs pass through with their original extension.
    """
    if not filename:
        raise FilenameRejected("Empty filename")
    if any(c in filename for c in ("\x00", "/", "\\", "\r", "\n")) or ".." in filename:
        raise FilenameRejected(f"Filename contains forbidden chars: {filename!r}")

    is_media = declared_mime.split("/", 1)[0] in ("image", "video", "audio")
    if is_media:
        canonical_ext = _MIME_TO_EXT.get(declared_mime)
        if canonical_ext is None:
            raise FilenameRejected(f"Unsupported media MIME: {declared_mime!r}")
        # strip the entire trailing chain of browser-renderable extensions
        stem = filename
        while True:
            lower = stem.lower()
            for ext in _BROWSER_RENDERABLE_EXTENSIONS:
                if lower.endswith(ext):
                    stem = stem[: -len(ext)]
                    break
            else:
                break
        # drop any remaining real extension, append the canonical one
        dot = stem.rfind(".")
        if dot > 0:
            stem = stem[:dot]
        safe = stem + canonical_ext
    else:
        safe = filename

    encoded = urllib.parse.quote(safe, safe="-_.")
    if len(encoded) > 200:
        raise FilenameRejected(f"Filename too long: {len(encoded)} > 200")
    return encoded
