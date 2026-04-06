-- Migration 027: enforce at most one active workflow run per (org, workflow)
--
-- Clean up any pre-existing duplicate active runs first so the unique partial
-- index can be created safely on real databases.
UPDATE workflow_runs
SET status = 'failed',
    completed_at = COALESCE(completed_at, datetime('now')),
    error = COALESCE(error, 'auto-failed by migration 027: duplicate active workflow run')
WHERE status IN ('pending', 'running')
  AND EXISTS (
      SELECT 1
      FROM workflow_runs newer
      WHERE newer.org_id = workflow_runs.org_id
        AND newer.workflow_name = workflow_runs.workflow_name
        AND newer.status IN ('pending', 'running')
        AND (
            COALESCE(newer.started_at, newer.created_at) > COALESCE(workflow_runs.started_at, workflow_runs.created_at)
            OR (
                COALESCE(newer.started_at, newer.created_at) = COALESCE(workflow_runs.started_at, workflow_runs.created_at)
                AND newer.run_id > workflow_runs.run_id
            )
        )
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_active_lock
ON workflow_runs(org_id, workflow_name)
WHERE status IN ('pending', 'running');

UPDATE schema_version SET version = 27 WHERE version < 27;
