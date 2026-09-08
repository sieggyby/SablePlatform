"""The `alias` parameter on `active_predicate`, and the same-day expiry it has to survive.

The older expiry tests in `test_tags.py` all use `2000-01-01T00:00:00`. A value on a
DIFFERENT date is decided by the date halves, so those tests pass under the broken text
compare and the fixed timestamp compare alike. The separator defect only shows when the
date halves are EQUAL. Every expiry test in this file is built on that case.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from sable_platform.db.tags import active_predicate, add_tag, get_entities_by_tag
from sable_platform.db.connection import ensure_schema

CANONICAL = "%Y-%m-%dT%H:%M:%SZ"


def _expired_today() -> str:
    """A canonical `T...Z` timestamp 2 seconds in the past, on the SAME UTC date as now.

    Same date is the whole point. `'2026-09-07T12:00:00Z' > '2026-09-07 12:00:02'` is true
    as text, because `T` is 0x54 and the space SQLite writes is 0x20. As timestamps it is
    false. Wait out a midnight crossing rather than emit a value on the previous date.
    """
    now = datetime.now(timezone.utc)
    while (now - timedelta(seconds=2)).date() != now.date():
        time.sleep(1)
        now = datetime.now(timezone.utc)
    return (now - timedelta(seconds=2)).strftime(CANONICAL)


def _live_later() -> str:
    """A canonical `T...Z` timestamp two days out. Comfortably not expired."""
    return (datetime.now(timezone.utc) + timedelta(days=2)).strftime(CANONICAL)


def _insert_entity(conn, org_id, entity_id="ent_alias"):
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status) VALUES (?, ?, ?, ?)",
        (entity_id, org_id, "Test Entity", "confirmed"),
    )
    conn.commit()
    return entity_id


# ---------------------------------------------------------------------------
# The alias parameter itself
# ---------------------------------------------------------------------------

class TestAliasParameter:
    @pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
    def test_no_alias_qualifies_nothing(self, dialect):
        """The default stays unqualified, so every existing caller keeps working."""
        pred = active_predicate(dialect)
        assert "t." not in pred
        assert pred.startswith("is_current = 1 AND (expires_at IS NULL")

    @pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
    def test_alias_qualifies_every_column_reference(self, dialect):
        """No BARE column may survive. A bare one is ambiguous inside a JOIN."""
        pred = active_predicate(dialect, "t")
        assert pred.count("expires_at") == pred.count("t.expires_at")
        assert pred.count("is_current") == pred.count("t.is_current")
        assert pred.count("t.is_current") == 1

    @pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
    def test_alias_matches_the_replace_hack_it_replaced(self, dialect):
        """Pins the refactor of `get_entities_by_tag` as behaviour-preserving."""
        hack = (active_predicate(dialect)
                .replace("is_current", "t.is_current")
                .replace("expires_at", "t.expires_at"))
        assert active_predicate(dialect, "t") == hack

    def test_alias_rejects_a_non_identifier(self):
        """The alias is interpolated into SQL, so it is unsafe by default."""
        with pytest.raises(ValueError):
            active_predicate("sqlite", "t; DROP TABLE entity_tags --")

    @pytest.mark.parametrize("alias", ["t", "et", "_x", "tag2"])
    def test_alias_accepts_a_plain_identifier(self, alias):
        """must_not_fire: the guard must not reject a legitimate alias."""
        pred = active_predicate("sqlite", alias)
        assert pred.startswith(f"{alias}.is_current = 1")


# ---------------------------------------------------------------------------
# Behaviour through real SQL, on the same-day case
# ---------------------------------------------------------------------------

class TestSameDayExpiryThroughAlias:
    def test_drops_a_same_day_expired_canonical_tag(self, org_db):
        """The defect: a tag that expired 2 seconds ago still came back as active."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", expires_at=_expired_today())
        assert get_entities_by_tag(conn, org_id, "cultist") == []

    def test_keeps_a_live_tag(self, org_db):
        """must_not_fire: the fix must not throw away tags that have NOT expired."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", expires_at=_live_later())
        results = get_entities_by_tag(conn, org_id, "cultist")
        assert [r["entity_id"] for r in results] == [eid]

    def test_keeps_a_tag_with_no_expiry(self, org_db):
        """must_not_fire: a NULL expiry is open-ended, not expired."""
        conn, org_id = org_db
        eid = _insert_entity(conn, org_id)
        add_tag(conn, eid, "cultist", expires_at=None)
        results = get_entities_by_tag(conn, org_id, "cultist")
        assert [r["entity_id"] for r in results] == [eid]


@pytest.fixture
def joined_tags():
    """Build migrated SQLite tables with conflicting tag states on both sides of a join."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('alias_org', 'Alias Org')")
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name, status) "
        "VALUES ('alias_entity', 'alias_org', 'Alias Entity', 'confirmed')"
    )
    conn.executemany(
        "INSERT INTO entity_tags (tag_id, entity_id, tag, is_current, expires_at) "
        "VALUES (?, 'alias_entity', ?, ?, ?)",
        [
            (1, "open", 1, None),
            (2, "future", 1, _live_later()),
            (3, "expired", 1, _expired_today()),
            (4, "deactivated_open", 0, None),
            (5, "deactivated_future", 0, _live_later()),
            (6, "shadow", 0, _expired_today()),
        ],
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("alias", ["t", "et", "_x", "tag2"])
def test_join_filters_the_aliased_tags(joined_tags, alias):
    """Both tables expose predicate columns; only the requested alias controls tag activity."""
    rows = joined_tags.execute(
        f"SELECT {alias}.tag FROM entity_tags {alias} "
        f"JOIN entity_tags shadow ON shadow.entity_id = {alias}.entity_id "
        f"WHERE shadow.tag = 'shadow' AND {active_predicate('sqlite', alias)} "
        f"ORDER BY {alias}.tag"
    ).fetchall()
    assert rows == [("future",), ("open",)]


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
@pytest.mark.parametrize("alias", ["t.", "schema.t", "two words", "1tag", "t--", '"t"'])
def test_alias_rejects_sql_fragments(dialect, alias):
    with pytest.raises(ValueError, match="alias must be a plain SQL identifier"):
        active_predicate(dialect, alias)


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_alias_rejects_a_trailing_newline(dialect):
    """Both dialects must reject a newline at alias validation."""
    with pytest.raises(ValueError, match="alias must be a plain SQL identifier"):
        active_predicate(dialect, "t\n")
