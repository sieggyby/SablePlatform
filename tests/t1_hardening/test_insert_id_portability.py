"""T1-PORTABILITY: runtime code should not depend on SQLite-only insert ID APIs."""
from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_does_not_use_last_insert_rowid():
    """last_insert_rowid() is SQLite-only and breaks the Postgres runtime path."""
    offenders: list[str] = []
    for path in (_PROJECT_ROOT / "sable_platform").rglob("*.py"):
        if path.name == "compat_conn.py":
            continue
        if "last_insert_rowid(" in path.read_text():
            offenders.append(str(path.relative_to(_PROJECT_ROOT)))

    assert not offenders, f"SQLite-only last_insert_rowid() usage found: {offenders}"


def test_runtime_does_not_use_cursor_lastrowid():
    """Direct lastrowid usage should stay confined to the compatibility wrapper."""
    offenders: list[str] = []
    for path in (_PROJECT_ROOT / "sable_platform").rglob("*.py"):
        if path.name == "compat_conn.py":
            continue
        if ".lastrowid" in path.read_text():
            offenders.append(str(path.relative_to(_PROJECT_ROOT)))

    assert not offenders, f"Backend-specific .lastrowid usage found: {offenders}"
