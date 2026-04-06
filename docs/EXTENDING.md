# Extending SablePlatform

Step-by-step guides for adding a workflow, adapter, alert check, or migration.

---

## Adding a Workflow

### 1. Create the file

```
sable_platform/workflows/builtins/my_workflow.py
```

### 2. Define the workflow

```python
"""Workflow: my_workflow — short description."""
from __future__ import annotations

from sable_platform.workflows.models import StepDefinition, StepResult, WorkflowDefinition
from sable_platform.workflows import registry


def _step_one(ctx) -> StepResult:
    org_id = ctx.org_id
    # ctx.db is an open sqlite3.Connection
    # ctx.input_data is the merged dict of config + all prior step outputs
    # ctx.config is the original config dict (unchanged across steps)
    result = {"some_key": "some_value"}
    return StepResult("completed", result)


def _step_two(ctx) -> StepResult:
    # Prior step output is available via ctx.input_data
    some_key = ctx.input_data.get("some_key")
    return StepResult("completed", {"summary": some_key})


MY_WORKFLOW = WorkflowDefinition(
    name="my_workflow",
    version="1.0",
    steps=[
        StepDefinition(name="step_one", fn=_step_one, max_retries=1),
        StepDefinition(name="step_two", fn=_step_two, max_retries=0),
    ],
)

registry.register(MY_WORKFLOW)
```

### 3. Register it

Add an import to `_auto_register()` in `sable_platform/workflows/registry.py`:

```python
def _auto_register() -> None:
    from sable_platform.workflows.builtins import prospect_diagnostic_sync  # noqa: F401
    # ... existing imports ...
    from sable_platform.workflows.builtins import my_workflow  # noqa: F401
```

The `registry.register()` call at module level of your file runs as a side effect of the import. If you forget to add it here, `sable-platform workflow list` will never show your workflow and `workflow run my_workflow` will raise `WORKFLOW_NOT_FOUND`.

### 4. Verify

```bash
sable-platform workflow list
# my_workflow should appear

sable-platform workflow run my_workflow --org <org_id> --config '{"key": "val"}'
```

### StepDefinition reference

| Field | Default | Notes |
|-------|---------|-------|
| `name` | required | Must be unique within the workflow. Changing names invalidates existing run fingerprints — resumes of in-flight runs will fail unless `--ignore-version-check` is passed. |
| `fn` | required | `Callable[[StepContext], StepResult]` |
| `max_retries` | `1` | Set `0` for steps that must not retry (e.g., irrevocable actions). |
| `retry_delay_seconds` | `0` | Seconds between retries. |
| `skip_if` | `None` | `Callable[[StepContext], bool]`. If returns `True`, step is marked `skipped` and execution continues. Do not capture mutable ctx fields in a closure — evaluate inside the lambda. |
| `timeout_seconds` | `None` | Per-step execution timeout in seconds. If exceeded, step returns `StepResult(status="failed", error="step_timeout")`. `None` means no timeout. |

### Gotchas

- **Step output accumulation**: `StepResult.output` is merged into `ctx.input_data` for all subsequent steps. Keys can shadow earlier outputs — use namespaced keys like `step_one_result` to avoid collisions.
- **Idempotency on resume**: Steps are re-run from the first non-completed step. If a step has side effects (e.g., writes to an external system), guard with a check: `if ctx.input_data.get("already_submitted"): return StepResult("completed", {})`.
- **Synchronous blocking**: The engine runs steps synchronously. Long-running steps (e.g., subprocess adapters for Cult Grader) block the process. Use `timeout_seconds` on `StepDefinition` to bound execution time. For steps that poll external state, design them to fail fast and resume manually (see `prospect_diagnostic_sync` / `poll_diagnostic` for the pattern).

---

## Adding an Adapter

### 1. Create the file

```
sable_platform/adapters/my_adapter.py
```

### 2. Implement the adapter

```python
"""Adapter for MyRepo — subprocess invocation."""
from __future__ import annotations

import json
from pathlib import Path

from sable_platform.adapters.base import SubprocessAdapterMixin
from sable_platform.errors import SableError, STEP_EXECUTION_ERROR


class MyAdapter(SubprocessAdapterMixin):
    name = "my_adapter"

    def run(self, input_data: dict) -> dict:
        repo = self._resolve_repo_path("SABLE_MY_REPO_PATH")
        # Build the command to invoke
        cmd = ["python", "main.py", "run", "--org", input_data["org_id"]]
        self._run_subprocess(cmd, cwd=repo, timeout=1800)
        # Return a job reference that status() and get_result() can use
        return {"status": "submitted", "job_ref": str(repo / "output" / "latest")}

    def status(self, job_ref: str, conn=None) -> str:
        output_path = Path(job_ref) / "result.json"
        return "completed" if output_path.exists() else "pending"

    def get_result(self, job_ref: str, conn=None) -> dict:
        result_path = Path(job_ref) / "result.json"
        if not result_path.exists():
            raise SableError(STEP_EXECUTION_ERROR, f"Result not found at {result_path}")
        return json.loads(result_path.read_text())
```

### 3. Add the env var

Add to `ENVIRONMENT_SETUP.md` (Adapter Paths table) and `ARCHITECTURE.md` (module map). Add to `docs/CROSS_REPO_INTEGRATION.md` (adapter reference).

### 4. Use it in a workflow step

```python
def _run_my_adapter(ctx) -> StepResult:
    from sable_platform.adapters.my_adapter import MyAdapter
    result = MyAdapter().run(ctx.input_data)
    return StepResult("completed", result)
```

### SubprocessAdapterMixin reference

| Method | Raises | Notes |
|--------|--------|-------|
| `_resolve_repo_path(env_var)` | `SableError(INVALID_CONFIG)` | Reads env var, asserts directory exists. |
| `_run_subprocess(cmd, cwd, timeout=1800)` | `SableError(STEP_EXECUTION_ERROR)` | On non-zero exit or timeout. Kills process group on timeout to prevent orphaned grandchildren. |

### Gotchas

- **Do not import adapters at module level in workflows.** Import inside the step function. This keeps tests fast and avoids circular imports.
- **Do not log env vars.** The subprocess inherits the caller's environment — `ANTHROPIC_API_KEY`, `SABLE_HEALTH_TOKEN`, etc. are in scope. Log command structure, not environment contents.
- **Default timeout is 30 min.** Cult Grader runs typically take 5–20 min. Set `timeout` conservatively — a timed-out adapter call kills the workflow step.

---

## Adding an Alert Check

### 1. Add the check function to `alert_checks.py`

```python
def _check_my_condition(conn: sqlite3.Connection, org_id: str) -> list[str]:
    """Warning: short description of what triggers this alert."""
    try:
        rows = conn.execute(
            "SELECT entity_id FROM entities WHERE org_id=? AND ...",
            (org_id,),
        ).fetchall()
    except Exception as e:
        log.warning("Alert check my_condition failed: %s", e)
        return []

    created = []
    for r in rows:
        # 3-part key for entity-scoped alerts, 2-part for org-scoped
        dedup_key = f"my_condition:{org_id}:{r['entity_id']}"
        alert_id = create_alert(
            conn,
            alert_type="my_condition",
            severity="warning",        # critical | warning | info
            title=f"Short title for {r['entity_id'][:16]}",
            org_id=org_id,
            body="Longer explanation and recommended operator action.",
            dedup_key=dedup_key,
        )
        if alert_id:
            created.append(alert_id)
    return created
```

**Important:** Check functions must NOT call `_deliver()`. They only create alert rows and return IDs. The caller (CLI or builtin workflow step) calls `deliver_alerts_by_ids(conn, alert_ids)` after evaluation to dispatch notifications.

**Dedup key rules:**
- Entity-scoped: `"{alert_type}:{org_id}:{entity_id}"` (3 parts)
- Run-scoped: `"{alert_type}:{org_id}:{run_id}"` (3 parts)
- Org-scoped (no per-entity key): `"{alert_type}:{org_id}"` (2 parts)
- **Always include `org_id`.** Omitting it causes cross-org suppression collisions where one org's resolved alert silences another org's new alert of the same type.

**Dedup behavior:** `create_alert()` returns `None` (no new alert, delivery skipped) when an alert with the same `dedup_key` already exists with `status IN ('new', 'acknowledged')`. Only `resolved` status allows re-alerting.

### 2. Register it in `evaluate_alerts()`

Add an import at the top of `alert_evaluator.py` and a call inside the per-org loop:

```python
from sable_platform.workflows.alert_checks import (
    # ... existing imports ...
    _check_my_condition,
)

# Inside the per-org loop:
created.extend(_check_my_condition(conn, oid))
```

If the check is cross-org (like `_check_workflow_failures`), add it in the separate `try` block outside the per-org loop.

### 3. Add per-org config override (optional)

If the check uses a threshold configurable per org, read it from `org.config_json`:

```python
MY_THRESHOLD = 0.5  # module-level default

def _check_my_condition(conn, org_id):
    threshold = MY_THRESHOLD
    try:
        org_row = conn.execute("SELECT config_json FROM orgs WHERE org_id=?", (org_id,)).fetchone()
        if org_row and org_row["config_json"]:
            cfg = json.loads(org_row["config_json"])
            threshold = cfg.get("my_condition_threshold", threshold)
    except Exception as e:
        log.warning("Failed to parse my_condition config for org %s, using defaults: %s", org_id, e)
    # ... rest of check
```

Document the config key in `docs/ALERT_SYSTEM.md` § Per-Org Threshold Overrides.

### 4. Write tests

Tests must cover both cases:

```python
def test_my_condition_fires(db_conn):
    """Alert is created when condition is met."""
    # Set up org, insert triggering data
    # Call _check_my_condition(db_conn, "test_org")
    # Assert returned list is non-empty and alerts table has the row

def test_my_condition_cooldown(db_conn):
    """Alert is suppressed during cooldown window."""
    # Create an existing alert with the same dedup_key (status='new')
    # Call _check_my_condition again
    # Assert returned list is empty (create_alert returned None)
```

---

## Adding a Migration

### 1. Create the SQL file

```
sable_platform/db/migrations/031_my_change.sql
```

Migrations must be:
- **Append-only.** Never modify existing migrations.
- **Idempotent.** Use `IF NOT EXISTS`, `ON CONFLICT IGNORE`, etc.
- **Non-destructive.** No `DROP TABLE` or `DROP COLUMN` without explicit operator approval.
- **Self-versioning.** Each migration SQL must include its own `UPDATE schema_version` statement (required because DDL auto-commits in Python sqlite3, breaking the `with conn:` context manager pattern).

Example:

```sql
-- 031_my_change.sql
CREATE TABLE IF NOT EXISTS my_new_table (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    value REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_my_new_table_org_id ON my_new_table (org_id);

UPDATE schema_version SET version = 31 WHERE version < 31;
```

### 2. Register it in `connection.py`

Add an entry to `_MIGRATIONS` in `sable_platform/db/connection.py`:

```python
_MIGRATIONS = [
    # ... existing 30 entries ...
    ("031_my_change.sql", 31),
]
```

**If the entry is missing here, the migration will never run.** The SQL file alone is not enough.

### 3. Update version assertions

Find and update the version assertion in `tests/test_init.py`:

```python
# Before
assert version == 30
# After
assert version == 31
```

Also update the schema head comment in `ARCHITECTURE.md` (§ DB schema ownership).

### 4. Verify

```bash
# Apply migration to your local DB
sable-platform init

# Check version
sqlite3 ~/.sable/sable.db "SELECT value FROM schema_version LIMIT 1;"
# Should print 31

# Run tests
python3 -m pytest tests/ -q
```
