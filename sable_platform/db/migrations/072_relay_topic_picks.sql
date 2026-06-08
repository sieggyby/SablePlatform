-- 072_relay_topic_picks.sql
-- Tweet Assist Compose -- topic-suggestion FEEDBACK LOOP. When an operator clicks a
-- suggested-topic chip (to compose from it), SableWeb logs the pick here. The weekly
-- topic-synthesis refresh reads recent picks as a STEERING signal (favor adjacent
-- themes operators actually act on), closing the loop the cache (071) opened. 100
-- percent ADDITIVE: CREATE TABLE IF NOT EXISTS + CREATE INDEX only. NO table rebuild,
-- NO column drop.
--
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner in
-- connection.py splits on the literal semicolon). Conventions: counts/PKs INTEGER,
-- all _at columns TEXT with a strftime default, JSON blobs TEXT.
--
-- A topic pick is a USAGE SIGNAL (which themes operators engage), NOT measured fact.
-- There is NO cost column here, ever (cost lives only in cost_events). Append-only
-- event log -- NO UNIQUE constraint (an operator may pick the same topic twice, and a
-- repeat pick is itself signal). FK -> relay_clients (topics only render for relay
-- clients, so a pick can only originate for one).

CREATE TABLE IF NOT EXISTS relay_topic_picks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id           TEXT NOT NULL REFERENCES relay_clients(org_id),
    topic            TEXT NOT NULL,
    register_band    TEXT,
    operator_handle  TEXT,
    picked_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_relay_topic_picks_org
  ON relay_topic_picks(org_id, picked_at);

UPDATE schema_version SET version = 72 WHERE version < 72;
