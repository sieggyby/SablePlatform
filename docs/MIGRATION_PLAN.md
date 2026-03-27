# Migration Plan

Incremental migration from the current 4-repo stack to the new platform backbone. No big-bang rewrite. Each repo migrates independently.

## Step 0 — Install SablePlatform (all repos)

```bash
pip install -e /Users/sieggy/Projects/SablePlatform
```

Add to each repo's `requirements.txt`:
```
sable-platform @ file:///Users/sieggy/Projects/SablePlatform
```

This is safe to do before any shim/import changes. The package installs but nothing changes yet.

---

## Sable_Slopper

**Goal:** `sable.platform.*` becomes thin re-exports from `sable_platform.*`.

**Files to modify:**

Replace contents of each `sable/platform/*.py` with:

```python
# sable/platform/db.py
from sable_platform.db.connection import get_db, ensure_schema  # noqa: F401

# sable/platform/errors.py
from sable_platform.errors import (  # noqa: F401
    SableError, ORG_EXISTS, ORG_NOT_FOUND, ENTITY_NOT_FOUND, ENTITY_ARCHIVED,
    HANDLE_NOT_IN_ROSTER, NO_ORG_FOR_HANDLE, CROSS_ORG_MERGE_BLOCKED,
    SLUG_ORG_CONFLICT, STALE_DIAGNOSTIC, NO_DISCORD_DIAGNOSTIC, INVALID_CONFIG,
    ORG_MAPPING_ERROR, BUDGET_EXCEEDED, BRIEF_CAP_EXCEEDED, MAX_RETRIES_EXCEEDED,
    AMBIGUOUS_INPUT, AWAITING_OPERATOR_INPUT, INVALID_ORG_ID, INVALID_PATH,
    WORKFLOW_NOT_FOUND, STEP_EXECUTION_ERROR,
)

# sable/platform/entities.py
from sable_platform.db.entities import (  # noqa: F401
    create_entity, find_entity_by_handle, get_entity,
    update_display_name, add_handle, archive_entity
)

# sable/platform/tags.py
from sable_platform.db.tags import (  # noqa: F401
    add_tag, get_active_tags, get_entities_by_tag, _REPLACE_CURRENT_TAGS
)

# sable/platform/merge.py
from sable_platform.db.merge import (  # noqa: F401
    create_merge_candidate, get_pending_merges, execute_merge, reconsider_expired_merges
)

# sable/platform/jobs.py
from sable_platform.db.jobs import (  # noqa: F401
    create_job, add_step, start_step, complete_step, fail_step,
    get_job, get_resumable_steps, resume_job
)

# sable/platform/cost.py
from sable_platform.db.cost import (  # noqa: F401
    log_cost, get_weekly_spend, get_org_cost_cap, check_budget
)

# sable/platform/stale.py
from sable_platform.db.stale import mark_artifacts_stale  # noqa: F401
```

**`sable/platform/cli.py`** — leave unchanged. It imports from `sable.platform.*` which now re-exports.

**Migrations directory:**
Keep `sable/db/migrations/` but add a `README.md`:
```
Migrations are now owned by SablePlatform. See /Users/sieggy/Projects/SablePlatform/sable_platform/db/migrations/
```

**Verification:**
```bash
python -c "from sable.platform.db import get_db; print('ok')"
python -m pytest tests/ -x
sable org list
sable db status
```

---

## Sable_Cult_Grader

**Goal:** Remove `SABLE_PROJECT_PATH` dependency. Import from `sable_platform` directly.

**Files to modify:** `platform_sync.py`

Changes:
1. Remove the `_SABLE_PATH` / `SABLE_PROJECT_PATH` sys.path manipulation block
2. Remove `_MIGRATIONS_PATH` constant
3. Remove `_apply_pending_migrations()` function (migration now handled by `get_db()`)
4. Replace imports:
   ```python
   # Before
   from sable.platform.db import get_db
   from sable.platform.entities import find_entity_by_handle, create_entity, add_handle
   from sable.platform.tags import add_tag, get_active_tags
   from sable.platform.stale import mark_artifacts_stale
   from sable.platform.errors import SableError, ORG_MAPPING_ERROR

   # After
   from sable_platform.db.connection import get_db
   from sable_platform.db.entities import find_entity_by_handle, create_entity, add_handle
   from sable_platform.db.tags import add_tag, get_active_tags
   from sable_platform.db.stale import mark_artifacts_stale
   from sable_platform.errors import SableError, ORG_MAPPING_ERROR
   ```
5. Remove `SABLE_PROJECT_PATH` from `.env.example` and `CLAUDE.md` env var docs

**Verification:**
```bash
python -c "from sable_platform.db.connection import get_db; print('ok')"  # No SABLE_PROJECT_PATH needed
python -m pytest tests/ -x
```

---

## SableTracking

**Goal:** Remove `SABLE_PROJECT_PATH` dependency. Import from `sable_platform` directly.

**Files to modify:** `app/platform_sync.py`

Changes:
1. Remove the `_SABLE_PATH` sys.path block (lines 13–23)
2. Remove `_MIGRATIONS_PATH` constant
3. Remove `_apply_pending_migrations()` function
4. Replace imports (same pattern as CultGrader above)
5. Remove `SABLE_PROJECT_PATH` from `.env.example`

**Verification:**
```bash
python -c "from app.platform_sync import sync_to_platform; print('ok')"  # No SABLE_PROJECT_PATH
python -m pytest tests/ -x  # All 36 existing tests should still pass
```

---

## Sable_Community_Lead_Identifier

**Goal:** Make output discoverable by `LeadIdentifierAdapter`.

**Files to modify:** `output.py`

Add one line after writing the dated output file:
```python
# Write stable symlink/copy for adapter consumption
import shutil
latest_path = output_dir / "sable_leads_latest.json"
shutil.copy2(json_path, latest_path)
```

No other changes. The core pipeline remains fully standalone.

---

## Migration sequence

```
Week 1:  Install SablePlatform in all repo venvs. Run tests. No code changes yet.
Week 2:  Apply Sable_Slopper shims. Run Slopper tests.
Week 3:  Migrate Sable_Cult_Grader imports. Remove SABLE_PROJECT_PATH from CultGrader.
Week 4:  Migrate SableTracking imports. Remove SABLE_PROJECT_PATH from SableTracking.
Week 5:  Add latest.json output to Lead Identifier. Test LeadIdentifierAdapter.
Week 6+: Use sable-platform workflow run / resume for daily operations.
```

---

## Schema migrations (006–012)

Migration 006 adds `workflow_runs`, `workflow_steps`, `workflow_events`. Migrations 007–010 add
`alerts`, `alert_configs`, `discord_pulse_runs`, and related tables. Migration 011 adds
`alert_configs.cooldown_hours` and `alerts.last_delivered_at`. Migration 012 adds
`workflow_runs.step_fingerprint`.

All migrations run automatically on the first `get_db()` call after SablePlatform is installed.
Each migration is idempotent (`INSERT OR REPLACE INTO schema_version`). Safe to apply against
any existing database at version 5.

If `sable.db` is currently at version < 5, the full migration chain (002–012) will be applied
in order. Current schema head: **version 12**.
