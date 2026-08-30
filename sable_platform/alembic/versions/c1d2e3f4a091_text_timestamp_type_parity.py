"""schema.py / PostgreSQL type parity for 19 timestamp columns (migration 091)

DEFECTS_FOUND item 9. ``sable_platform/db/schema.py`` opens with "This module is the single
source of truth for the platform schema". Measured against a database built by this chain,
that was false for 19 columns: ``schema.py`` declares them ``Text`` and PostgreSQL stores
them as ``timestamp with time zone``.

That is not a cosmetic disagreement. This codebase reads through raw ``text()`` queries
everywhere, and those bypass the SQLAlchemy type system, so the same column handed back a
``str`` on SQLite and a ``datetime`` on PostgreSQL. ``api/tokens.py`` hit it as an exception
rather than a wrong answer:

    '<=' not supported between instances of 'datetime.datetime' and 'str'

That fired for every API token carrying an expiry, on PostgreSQL only, and no test saw it
because the suite runs on SQLite.

**Direction: PostgreSQL moves to TEXT, not schema.py to timestamptz.** Every writer of these
columns already binds a canonical ``...T...Z`` string, every reader already treats them as
text, SQLite already stores TEXT, and ``schema.py`` already says ``Text``. The PostgreSQL
type was the outlier. Declaring them ``timestamptz`` in ``schema.py`` would have made the
divergence official and left the dual-dialect return type in place.

The conversion renders through ``AT TIME ZONE 'UTC'`` into the canonical spelling migration
090 established, so these columns land in the same shape as every other TEXT timestamp.
NULL stays NULL.

**This conversion truncates to whole seconds. It does not round.** A stored
``12:00:00.999999+00`` becomes ``12:00:00Z``, not ``12:00:01Z``, so a converted value can
represent an instant up to 999999 microseconds earlier than the one it replaced. Two rows
less than a second apart can land on the same string. ``now()`` supplies microseconds, so
every row written by the old default carries a fractional part and this applies to all of
them.

FIXED WIDTH is what makes a text compare chronological. Whole seconds is one fixed width
among several, and not the only one that works. ``'...T12:00:00.5Z'`` sorts BELOW
``'...T12:00:00Z'``, because ``.`` is ``0x2E`` and ``Z`` is ``0x5A``, so a VARIABLE fraction
breaks the order. A fixed six-digit fraction would hold the order AND keep the microseconds.

Second precision is a choice this codebase already made, not a property it requires:

- Migration 090 canonicalized 233 TEXT columns at second precision.
- 71 call sites in ``sable_platform/`` write the literal ``%Y-%m-%dT%H:%M:%SZ`` themselves.
- SQLite renders at most three fractional digits. ``strftime('%f')`` gives ``SS.SSS``, so a
  six-digit canonical needs string surgery in the SQLite default expression.

Widening the format now reopens all three. This paragraph records the alternative because
it is real, not because it is impossible.

The measured cost of truncating is one query, not a class of them, and that query is fixed
on this branch. A tiebreaker is the right answer to it at ANY precision, because two rows
can share a microsecond as easily as they share a second.

Measured against this schema before accepting it:

- No unique index or key constraint covers any of the 19 columns, so a collapse cannot
  violate one. Those tables do carry unique indexes; none of them names these columns.
- Ten queries order by one of these columns. Nine carry an ``id`` tiebreaker. The tenth,
  ``list_tokens`` in ``sable_platform/api/tokens.py``, did not, and this branch adds
  ``token_id DESC`` to it. A first sweep reported all ten clean, because the sweep itself
  omitted the four ``api_tokens`` columns.
- No caller compares one of these columns for equality as an optimistic-lock token. The two
  lock tokens in this schema, ``discord_state_pins.updated_at`` and
  ``discord_streaks.updated_at``, are not in this list and this migration does not touch
  them.
- ``api_tokens.expires_at`` truncates backward, so an expiry moves up to one second earlier
  and never later. That direction fails closed.

**Not converted: 18 columns where ``schema.py`` says ``Integer`` and PostgreSQL says
``bigint``.** That pair returns a Python ``int`` on both dialects and ``bigint`` is the
better choice for an identity column. It is a declaration that could be tightened, not a
defect, and it is left alone.

SQLite needs nothing. These columns are already TEXT there, and migration 090 canonicalized
their values.

Revision ID: c1d2e3f4a091
Revises: b0c1d2e3f090
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c1d2e3f4a091"
down_revision = "b0c1d2e3f090"
branch_labels = None
depends_on = None


# Pinned copy of sable_platform.db.ts_format, for the same reason migration 090 pins it: a
# migration must keep doing the same thing after the module moves on.
_PG_NOW = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"

# (table, column, has_default). `has_default` records whether schema.py gives the column a
# server default, so the canonical one is restored only where one belongs.
COLUMNS = [
    ("api_tokens", "created_at", True),
    ("api_tokens", "expires_at", False),
    ("api_tokens", "last_used_at", False),
    ("api_tokens", "revoked_at", False),
    ("discord_burn_blocklist", "blocked_at", True),
    ("discord_burn_optins", "opted_in_at", True),
    ("discord_burn_random_log", "roasted_at", True),
    ("discord_invite_snapshot", "captured_at", True),
    ("discord_member_admit", "joined_at", True),
    ("discord_message_observations", "captured_at", True),
    ("discord_message_observations", "posted_at", False),
    ("discord_peer_roast_flags", "flagged_at", True),
    ("discord_peer_roast_tokens", "consumed_at", False),
    ("discord_peer_roast_tokens", "granted_at", True),
    ("discord_team_inviters", "added_at", True),
    ("discord_user_observations", "computed_at", True),
    ("discord_user_observations", "window_end", False),
    ("discord_user_observations", "window_start", False),
    ("discord_user_vibes", "inferred_at", True),
]


def _column_type(bind, table: str, column: str) -> str | None:
    """The column's declared type on this database, or None if it is not there."""
    return bind.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
            "   AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return          # SQLite already stores these as TEXT

    converted = 0
    already_text = 0
    absent = 0
    for table, column, has_default in COLUMNS:
        current = _column_type(bind, table, column)
        if current is None:
            absent += 1
            continue
        if current == "text":
            already_text += 1
            continue
        qt = f'"{table}"'
        qc = f'"{column}"'
        # DROP the default first. It is `now()`, a timestamptz expression, and PostgreSQL
        # will not carry it across a change to text.
        op.execute(f"ALTER TABLE {qt} ALTER COLUMN {qc} DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {qt} ALTER COLUMN {qc} TYPE text"
            f" USING to_char({qc} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
        )
        if has_default:
            op.execute(f"ALTER TABLE {qt} ALTER COLUMN {qc} SET DEFAULT {_PG_NOW}")
        converted += 1

    print(
        f"[091] timestamp type parity: {converted} columns converted to TEXT,"
        f" {already_text} already TEXT, {absent} absent from this database"
    )


def downgrade() -> None:
    """Return the columns to ``timestamp with time zone``.

    The stored values are canonical and carry a ``Z``, so the cast back is unambiguous and
    does not depend on the session timezone. A value that migration 090 could not read is
    the exception: the cast will reject it and the downgrade will fail on that row. That is
    the right failure. A downgrade should not quietly discard a value it cannot convert.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column, has_default in COLUMNS:
        if _column_type(bind, table, column) != "text":
            continue
        qt = f'"{table}"'
        qc = f'"{column}"'
        op.execute(f"ALTER TABLE {qt} ALTER COLUMN {qc} DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {qt} ALTER COLUMN {qc} TYPE timestamptz USING {qc}::timestamptz"
        )
        if has_default:
            op.execute(f"ALTER TABLE {qt} ALTER COLUMN {qc} SET DEFAULT now()")
