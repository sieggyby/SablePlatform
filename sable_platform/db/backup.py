"""SQLite online backup for sable.db."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Backup files match: sable_YYYYMMDDTHHMMSSz[_label].db
_BACKUP_PATTERN = "sable_[0-9]*T[0-9]*Z*.db"
_LABEL_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def backup_database(
    source_path: Path,
    dest_dir: Path,
    *,
    label: str | None = None,
    max_backups: int = 10,
) -> Path:
    """Create a backup of sable.db using SQLite's online backup API.

    Uses ``sqlite3.Connection.backup()`` which safely copies from a live WAL-mode
    database without requiring the caller to hold a lock for the full duration.

    Args:
        source_path: Path to the live sable.db file.
        dest_dir: Directory to write the backup into.
        label: Optional label appended to the backup filename.  Must contain only
            alphanumeric characters, underscores, and hyphens.
        max_backups: Retain at most this many backups in *dest_dir* (0 = unlimited).
            Oldest backups (by filename sort) are removed first.

    Returns:
        Path to the newly created backup file.

    Raises:
        FileNotFoundError: If *source_path* does not exist.
        ValueError: If *label* contains invalid characters.
        sqlite3.Error: On backup failure.
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Source database not found: {source_path}")

    if label and not _LABEL_RE.match(label):
        raise ValueError(
            f"Label must contain only alphanumeric, underscore, or hyphen characters: {label!r}"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{label}" if label else ""
    dest_path = dest_dir / f"sable_{timestamp}{suffix}.db"

    # Use SQLite online backup API — safe for WAL-mode databases.
    src_conn = sqlite3.connect(str(source_path))
    try:
        dst_conn = sqlite3.connect(str(dest_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    except Exception:
        # Clean up partial backup file on failure to prevent corrupt orphans
        # from consuming a prune slot.
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        src_conn.close()

    # Prune old backups if max_backups is set.
    if max_backups > 0:
        _prune_old_backups(dest_dir, max_backups)

    return dest_path


def _prune_old_backups(dest_dir: Path, max_backups: int) -> list[Path]:
    """Remove oldest backups exceeding *max_backups*.  Returns list of removed paths."""
    backups = sorted(dest_dir.glob(_BACKUP_PATTERN))
    removed: list[Path] = []
    while len(backups) > max_backups:
        oldest = backups.pop(0)
        oldest.unlink(missing_ok=True)
        removed.append(oldest)
    return removed


def get_backup_size(path: Path) -> str:
    """Return human-readable file size."""
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
