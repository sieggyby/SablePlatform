# Migration 090 and 091 runbook: canonical TEXT timestamps

Read this before you run migration 090 and migration 091 against production.
Run both. Migration 091 fixes a PostgreSQL-only failure in `api/tokens.py`.

## What it does

Migration 090 rewrites every TEXT timestamp into one spelling: `YYYY-MM-DDTHH:MM:SSZ`.

It runs in two phases.

1. It sets the PostgreSQL column default on 150 columns. This is a catalog write. It is fast.
2. It rewrites the data in every TEXT column named like a timestamp.

## Why the schema needed it

Every timestamp in this schema is TEXT. One spelling is safe. Two are not.

The column default rendered `now()` on PostgreSQL and `CURRENT_TIMESTAMP` on SQLite. Both
write a SPACE separator. 61 Python call sites write a `T` instead. Space is `0x20` and `T`
is `0x54`. A text comparison therefore reads the separator before the hour.

Three shapes broke.

- **Compare.** A same-day row lands on the wrong side of any bound.
- **Aggregate.** `MAX` and `MIN` return the lexicographic extreme, so a cast applied
  afterwards converts the wrong row. The number looks real. Nothing raises.
- **Order.** `ORDER BY` ranks every `T` value above every space value.

## Why second precision

Lexicographic order equals chronological order only at a fixed width.

`'...T12:00:00.5Z'` sorts BELOW `'...T12:00:00Z'`, because `.` is `0x2E` and `Z` is `0x5A`.
A backfill that kept sub-second precision would leave the defect in place.

## Lock profile

Read this before you pick a window.

- `SET DEFAULT` takes ACCESS EXCLUSIVE for the length of a catalog write.
- The `UPDATE` takes ROW EXCLUSIVE. It does not block readers.
- Alembic holds ONE transaction for the whole migration.

The `UPDATE` rewrites every non-canonical row. On a large `cost_events` or `relay_messages`
that is minutes, not seconds. Run it in a maintenance window.

## Server version

The backfill asks PostgreSQL whether a value parses before it converts it. Without that
check, one out-of-range value such as `2026-13-01T00:00:00Z` aborts the whole migration.

On PostgreSQL 16 and later it uses `pg_input_is_valid`, which is fast.

On an older server that function does not exist, so the migration creates a session-local
PL/pgSQL helper and uses that instead. It gives the same answers, verified against
`pg_input_is_valid` on all the shapes the backfill can meet. **It is slower**, because the
exception block costs a subtransaction per row. If your server is below 16, widen the
maintenance window.

The migration picks the path itself from `server_version_num`. You do not configure anything.

## Before you run it

1. Take a backup:

       sable-platform backup

2. Count the rows the backfill will rewrite. Run this against a replica or a restored copy:

       SELECT COUNT(*) FROM cost_events
        WHERE created_at !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$';

   Repeat for the largest tables. The number tells you how long the window must be.

3. Confirm the current revision:

       alembic current

## Run it

    alembic upgrade b0c1d2e3f090

The migration prints one line when it finishes:

    [090] canonical timestamps: 150 defaults changed, 233 TEXT timestamp columns scanned,
    N values rewritten, M left unreadable, K canonical-shaped but not a real date,
    12 columns already a real timestamp type, 0 columns absent from this database

## Read the output

**`M left unreadable` is not a failure.** It counts values the backfill refused to convert.
The migration leaves them exactly as they were. Three things land in it:

- a value that is not timestamp-shaped at all,
- a value that is timestamp-SHAPED but not a real date, such as `2026-13-01 00:00:00`. A
  shape is not a date, and `pg_input_is_valid` asks the parser rather than guessing. Without
  that check one bad row aborts the whole migration. The same value written in the canonical
  spelling, `2026-13-01T00:00:00Z`, lands in `K` instead, not here,
- a value padded with a tab or a newline. `TRIM` strips SPACES on both dialects and nothing
  else, and widening it would put this out of step with `compat._pg_instant`, which every
  READ goes through.

**`K canonical-shaped but not a real date` is the one to read carefully.** A value like
`2026-13-01T00:00:00Z` matches the canonical DIGIT SHAPE exactly, so the migration leaves it
alone and `M` cannot see it. Without this separate count the report would say "0 left
unreadable" while corrupt rows sat in the table. If `K` is above zero, those rows need a
person.

### Find which columns hold them

The report gives totals, not names. Run this to get the count per column. It sweeps exactly
what migration 090 sweeps: a TEXT column named `*_at`, `timestamp`, `last_seen` or `at_utc`.

    SELECT c.table_name, c.column_name,
           (xpath('/row/n/text()', query_to_xml(format(
              'SELECT count(*) AS n FROM %I.%I WHERE %I IS NOT NULL'
              '   AND btrim(%I) <> '''' AND %I !~ ''^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$''',
              c.table_schema, c.table_name,
              c.column_name, c.column_name, c.column_name),
              false, true, '')))[1]::text::int AS m_not_canonical,
           (xpath('/row/n/text()', query_to_xml(format(
              'SELECT count(*) AS n FROM %I.%I WHERE %I ~ ''^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'''
              '   AND NOT pg_input_is_valid(%I, ''timestamptz'')',
              c.table_schema, c.table_name, c.column_name, c.column_name),
              false, true, '')))[1]::text::int AS k_bad_date
    FROM information_schema.columns c
    WHERE c.table_schema = 'public'
      AND c.data_type = 'text'
      AND (c.column_name LIKE '%\_at'
           OR c.column_name IN ('timestamp', 'last_seen', 'at_utc'))
    ORDER BY 3 DESC, 4 DESC, 1, 2;

Every row it returns with a nonzero count is a column that needs a person. `m_not_canonical`
is the `M` total for that column. `k_bad_date` is the `K` total.

`k_bad_date` needs PostgreSQL 16 or later, for `pg_input_is_valid`. On an older server,
delete that whole expression and run the `m_not_canonical` half alone. It works on any
version.

Then read the rows in one named column:

If `M` is above zero, find them and look at each one:

    SELECT id, created_at FROM <table>
     WHERE created_at IS NOT NULL AND TRIM(created_at) <> ''
       AND created_at !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$';

To find the `K` rows instead, on PostgreSQL 16 or later:

    SELECT id, created_at FROM <table>
     WHERE created_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       AND NOT pg_input_is_valid(created_at, 'timestamptz');

**`12 columns already a real timestamp type` is expected.** `schema.py` declares those
columns `Text` and PostgreSQL stores them as `timestamp with time zone`. The migration skips
them, because a real timestamp type needs no canonical spelling and `SET DEFAULT to_char(...)`
on one is a hard error. The divergence is DEFECTS_FOUND item 9 and is pinned by
`tests/postgres/test_pg_ts_format.py`.

## Then run migration 091

    alembic upgrade c1d2e3f4a091

Run this in the same window. Migration 091 converts 19 columns that migration 090 left as a
real timestamp type.

**That 19 does not match the 12 in the 090 line above.** 090 counts only the columns it
carries a default for. The other 7 are nullable and have no default, so 090 never names
them. `api_tokens.expires_at` is one of the 7. Migration 091 carries its own list of all 19
and reports against that.

It prints one line:

    [091] timestamp type parity: 19 columns converted to TEXT, 0 already TEXT,
    0 absent from this database

**Do not stop at 090.** Before 091, `api/tokens.py` raises on PostgreSQL for every API token
that carries an expiry:

    '<=' not supported between instances of 'datetime.datetime' and 'str'

The column returns a `datetime` on PostgreSQL and a `str` on SQLite, so the test suite never
saw it. Migration 091 is the fix.

### 091 truncates to whole seconds

The conversion truncates. It does not round. A stored `12:00:00.999999+00` becomes
`12:00:00Z`, never `12:00:01Z`. `now()` writes microseconds, so this applies to every row the
old default created.

A converted value can name an instant up to one second EARLIER than the one it replaced. Two
rows less than a second apart can land on the same string.

FIXED WIDTH is what makes a text compare chronological, and whole seconds is one fixed
width among several. A fixed six-digit fraction would hold the order and keep the
microseconds. Second precision is what this codebase already runs on: 090 canonicalized 233
columns at it, 71 call sites write the format literal themselves, and SQLite renders at most
three fractional digits. Widening the format reopens all three, so this migration keeps it.

Four things were measured against this schema before the truncation was accepted:

- No unique index or key constraint covers any of the 19 columns.
- Ten queries order by one of them. Nine carry an `id` tiebreaker. The tenth, `list_tokens`
  in `sable_platform/api/tokens.py`, did not, and this branch adds `token_id DESC` to it.
- No caller compares one of them for equality as a lock token. The two lock tokens are
  `discord_state_pins.updated_at` and `discord_streaks.updated_at`, and 091 does not touch
  them.
- `api_tokens.expires_at` moves EARLIER, never later, so an expiry fails closed.

A tiebreaker is the right answer at any precision. Two rows can share a microsecond as
easily as they share a second.

### To roll back 091

    alembic downgrade b0c1d2e3f090

The columns return to `timestamp with time zone`. A value migration 090 could not read fails
the cast and stops the downgrade on that row. That is the intended failure. The truncated
microseconds do not come back.

## To roll back 090

    alembic downgrade a9b0c1d2e089

The downgrade restores the `now()` defaults. **It does not restore the old spellings.** The
backfill discards the separator and the offset, and nothing is left to reconstruct them from.

That is the safe direction for the SQL comparisons, which is what the pre-090 code did with
these columns.

It is not a universal claim, and there is one known exception. Pre-090 `api/tokens.py`
compared a stored expiry in PYTHON against a current time it rendered WITHOUT the `Z`. A
canonical value carries one, and `Z` sorts above the empty string, so a token expiring on the
exact second reads as valid for up to one more second. That needs a downgrade of the CODE as
well as this migration, and one second is the whole size of it.

## What migration 090 does NOT do

- It does not change any column TYPE. Native `timestamptz` is the WRONG end state here, and
  the reason is the return type: a raw `text()` query hands back a `datetime` on PostgreSQL
  and a `str` on SQLite, and this codebase reads through raw `text()` everywhere. That breaks
  callers by the hundred. Migration 091 moves the other way for 19 columns, from
  `timestamptz` to canonical TEXT, so both dialects return a `str`.
- It does not add a CHECK constraint. A constraint turns today's silent wrong answer into a
  hard failure for any writer the migration missed. That trade is yours to make.

## One side effect to expect

`discord_state_pins.updated_at` and `discord_streaks` carry a millisecond timestamp that is
used as an OPTIMISTIC LOCK TOKEN, not as a time. The writers compare it for equality.

The backfill rewrites those stored values to second precision, so a client holding a
millisecond token from before the migration loses its lock ONCE and must retry. It cannot
corrupt anything: a lock loss is reported, not silently applied.

Writes after the migration keep millisecond precision, so the lock keeps working. The column
stays mixed-width, which is safe here because nothing orders or compares it as a time.

## SQLite

SQLite keeps its old default. SQLite cannot `ALTER` a column default, and `ensure_schema`
builds SQLite databases from `schema.py`, which already carries the canonical one.

One SQLite behaviour differs from the PostgreSQL peer and is left as it is. SQLite ROLLS OVER
an impossible day: `2026-02-30` becomes `2026-03-02` rather than failing. PostgreSQL rejects
the same value and the peer migration skips it. A stored `2026-02-30` is corrupt either way,
and this is the local dialect, not production. A new
SQLite database is correct. An existing one gets the backfill from
`090_canonical_text_timestamps.sql` and keeps its old default until it is rebuilt.
