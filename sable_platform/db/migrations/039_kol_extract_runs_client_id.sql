-- Migration 039: per-client scoping for kol_extract_runs.
--
-- Adds a client_id column to kol_extract_runs so SableWeb (and any other
-- consumer) can scope graph queries to one client at a time. Without this,
-- multi-client deployments (TIG, Multisynq, PSY, etc.) would bleed each
-- other's audience and cohort data into the same graph.
--
-- Backfill rule: every existing run gets client_id='solstitch' since the
-- only Phase-2/6/6b runs we have so far are SolStitch's. Future runs MUST
-- supply --client <id> at extraction time. Default value '_external' is
-- the catch-all sentinel used elsewhere in the platform.
--
-- See ~/Projects/SableKOL/docs/sableweb_kol_build_plan.md for the design
-- context (audit finding #4 — "Client scoping is the biggest data-model gap").

ALTER TABLE kol_extract_runs ADD COLUMN client_id TEXT NOT NULL DEFAULT '_external';

UPDATE kol_extract_runs SET client_id = 'solstitch' WHERE client_id = '_external';

CREATE INDEX IF NOT EXISTS idx_kol_extract_runs_client
    ON kol_extract_runs(client_id, extract_type, cursor_completed);

UPDATE schema_version SET version = 39 WHERE version < 39;
