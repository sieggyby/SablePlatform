# Disaster Recovery Runbook — SablePlatform

Procedures for handling DB corruption, backup restore, migration rollback, and cron recovery.

Backend-specific commands differ in a few places:
- SQLite operators work directly with the `sable.db` file.
- PostgreSQL operators use `sable-platform backup`, `psql`, and `sable-platform db-health` against `SABLE_DATABASE_URL`.

---

## 1. Backup and Restore

### 1.1 Create a manual backup

```bash
sable-platform backup --dest ~/.sable/backups --label manual
```

For SQLite, this uses the online backup API and is safe while the platform is running. The backup file is written as `~/.sable/backups/sable_YYYYMMDDTHHMMSSz_manual.db`.

When `SABLE_DATABASE_URL` points at PostgreSQL, the same command shells out to `pg_dump` and writes `~/.sable/backups/sable_YYYYMMDDTHHMMSSz_manual.sql`.

The automated backup cron preset runs daily at 03:00 UTC:

```bash
sable-platform cron add --preset backup --org <org_id>
```

### 1.2 Restore from backup

#### SQLite restore

**Stop any running processes first** (the CLI is synchronous so this is just ensuring no active `sable-platform` command is running):

```bash
# Identify the backup to restore
ls -lt ~/.sable/backups/

# Restore (replace the live DB)
cp ~/.sable/backups/sable_<timestamp>.db ~/.sable/sable.db

# Verify the restored DB
sable-platform db-health
```

If `SABLE_DB_PATH` is set to a non-default location, use that path instead of `~/.sable/sable.db`.

#### PostgreSQL restore

Stop writers first, create or point `SABLE_DATABASE_URL` at an empty replacement database, then restore with `psql`:

```bash
# Identify the backup to restore
ls -lt ~/.sable/backups/

# Restore into the empty replacement Postgres DB
psql "$SABLE_DATABASE_URL" -f ~/.sable/backups/sable_<timestamp>.sql

# Verify the restored DB
sable-platform db-health
```

### 1.3 Verify backup integrity

```bash
# SQLite
sqlite3 ~/.sable/backups/sable_<timestamp>.db "PRAGMA integrity_check;"
# Expected output: ok

# PostgreSQL
# Restore into a disposable empty database and then run:
sable-platform db-health
```

---

## 2. DB Corruption

### 2.1 Detect corruption

#### SQLite

```bash
sqlite3 ~/.sable/sable.db "PRAGMA integrity_check;"
```

- `ok` — no corruption
- Any other output — corruption detected; proceed to restore

#### PostgreSQL

Use the backend-neutral healthcheck first:

```bash
sable-platform db-health
```

If that fails, inspect the server with `psql` and your Postgres logs. Corruption handling is a PostgreSQL operational concern rather than a local-file repair flow.

### 2.2 Attempt WAL recovery

If the main file is intact but the WAL file is corrupt, SQLite can often recover automatically:

```bash
sqlite3 ~/.sable/sable.db "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 ~/.sable/sable.db "PRAGMA integrity_check;"
```

### 2.3 Restore from backup

If the DB is unrecoverable, restore from the most recent backup (see §1.2). The most recent automated backup is at most 24 hours old if the daily cron preset is configured.

**Data loss window:** changes since the last backup are lost. After restoring:

1. Check `workflow_runs` for any runs that were in progress at the time of corruption:
   ```bash
   sable-platform workflow list --status running
   sable-platform workflow list --status pending
   ```
2. Resume or cancel them as appropriate:
   ```bash
   sable-platform workflow resume <run_id>
   # or
   sable-platform workflow cancel <run_id>
   ```

---

## 3. Migration Rollback

Migrations are intentionally forward-only. There is no automated rollback. Options:

### 3.1 Restore pre-migration backup

The safest rollback is restoring a backup taken before the migration was applied:

```bash
# Identify the last backup before the migration
ls -lt ~/.sable/backups/

# Restore
cp ~/.sable/backups/sable_<pre-migration-timestamp>.db ~/.sable/sable.db
```

Then redeploy the previous version of `sable-platform` before retrying the migration.

### 3.2 Check current schema version

```bash
# SQLite
sqlite3 ~/.sable/sable.db "SELECT version FROM schema_version;"

# PostgreSQL
psql "$SABLE_DATABASE_URL" -c "SELECT version FROM schema_version;"
```

### 3.3 Migration 027: duplicate active run auto-fail

Migration 027 auto-fails duplicate `pending`/`running` workflow runs when upgrading. After upgrade, check the log output for a warning like:

```
Migration 027: auto-failed N duplicate active workflow run(s)
```

To see which runs were affected:

```bash
sqlite3 ~/.sable/sable.db \
  "SELECT run_id, org_id, workflow_name, error FROM workflow_runs WHERE error LIKE 'auto-failed by migration 027%';"
```

Resume any legitimate runs that were caught in this cleanup:

```bash
sable-platform workflow resume <run_id> --ignore-version-check
```

---

## 4. Cron Recovery

### 4.1 Verify cron entries are installed

```bash
sable-platform cron list
```

### 4.2 Re-add missing presets

If the crontab was cleared or the server was rebuilt:

```bash
# Daily 03:00 UTC backup
sable-platform cron add --preset backup --org <org_id>

# Every 4h alert check
sable-platform cron add --preset alert_check --org <org_id>

# Weekly GC (Sunday 04:00 UTC)
sable-platform cron add --preset gc --org <org_id>
```

### 4.3 Verify alert evaluation is running

Check when alerts were last evaluated by looking for recent alert activity:

```bash
sable-platform alerts list --limit 5
```

If no alerts have been evaluated recently and alert_check cron is configured, check the cron log:

```bash
grep sable-platform /var/log/syslog | tail -20
# or on macOS:
log show --predicate 'process == "cron"' --last 1d | grep sable
```

### 4.4 Run alert evaluation manually

```bash
sable-platform alerts evaluate
```

---

## 5. Stuck or Orphaned Workflow Runs

### 5.1 Identify stuck runs

```bash
sable-platform workflow list --status running
```

Any run in `running` state for more than 2 hours (or the org-configured `stuck_run_threshold_hours`) will fire a `stuck_run` alert.

### 5.2 Force-fail a stuck run

```bash
sable-platform workflow unlock <run_id>
```

This marks the run as `failed` without resuming it, releasing the execution lock so a new run can start.

### 5.3 Resume after crash

If a run was interrupted mid-step (e.g., process killed), the engine auto-fails any step that was left in `running` state on next resume:

```bash
sable-platform workflow resume <run_id>
```

---

## 6. Data Retention GC

If the DB has grown large, run GC manually:

```bash
# Default: delete data older than 90 days
sable-platform gc

# Custom retention window
sable-platform gc --retention-days 30
```

GC is FK-safe and audit-log-immune (audit entries are never deleted). The weekly cron preset runs it automatically.

---

## 7. Health Check Quick Reference

```bash
# Programmatic health check (exits non-zero on failure)
sable-platform db-health

# SQLite DB integrity
sqlite3 "$SABLE_DB_PATH" "PRAGMA integrity_check;"

# Schema version
sqlite3 "$SABLE_DB_PATH" "SELECT version FROM schema_version;"
psql "$SABLE_DATABASE_URL" -c "SELECT version FROM schema_version;"

# Active workflow runs
sable-platform workflow list --status running

# Recent alerts
sable-platform alerts list --limit 10

# Cron status
sable-platform cron list
```
