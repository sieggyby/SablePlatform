-- Migration 023: align centrality schema with Cult Grader output
-- Cult Grader emits in_centrality/out_centrality (degree-based), not
-- betweenness/eigenvector. Replace columns to match upstream data.

ALTER TABLE entity_centrality_scores ADD COLUMN in_centrality REAL NOT NULL DEFAULT 0.0;
ALTER TABLE entity_centrality_scores ADD COLUMN out_centrality REAL NOT NULL DEFAULT 0.0;

UPDATE schema_version SET version = 23 WHERE version < 23;
