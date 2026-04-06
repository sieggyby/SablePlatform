# Alert System

End-to-end reference for how SablePlatform detects, deduplicates, delivers, and manages alerts.

See `docs/EXTENDING.md` § Adding an Alert Check for the step-by-step guide to adding a new alert type.

---

## Overview

```
evaluate_alerts()
  │
  ├── per-org loop ──► 10 _check_* functions
  │                     each returns [] or [alert_id, ...]
  │
  ├── cross-org ──────► _check_workflow_failures()
  │
  └── per-org loop ──► _check_discord_pulse_regression()
                         (separate because it's query-heavy)

Each check:
  └── create_alert()  ──► dedup gate (blocks if dedup_key in new/acknowledged)
        └── returns alert_id (no delivery here)

Caller (CLI or workflow step):
  └── deliver_alerts_by_ids(conn, alert_ids)
        └── for each alert_id:
              └── _deliver() ──► enabled check
                               ──► min_severity filter
                               ──► cooldown gate (4h default, per dedup_key)
                               ──► _send_telegram() (if configured)
                               ──► _send_discord()  (if configured)
                               ──► log.warning (always)
                               ──► webhook dispatch (best-effort)
                               ──► mark_delivered() or mark_delivery_failed()
```

`evaluate_alerts()` is a **pure DB path** — no external HTTP calls. Alert creation is committed before delivery begins. Each per-org block runs inside a `try/except` so one broken org does not abort evaluation for remaining orgs. The cross-org `_check_workflow_failures` block has its own isolation.

---

## The 12 Alert Checks

| Check function | Type | Severity | Trigger |
|---|---|---|---|
| `_check_tracking_stale` | org-scoped | critical | No successful `sable_tracking` sync in last 14 days |
| `_check_cultist_tag_expiring` | entity-scoped | warning | `cultist_candidate` tag expires within 7 days |
| `_check_sentiment_shift` | run-scoped | warning | `sentiment_negative` jumped from <10% to >20% |
| `_check_mvl_score_change` | run-scoped | info | `mvl_stack_score` changed by ≥1 |
| `_check_actions_unclaimed` | action-scoped | info | Action pending >7 days without an operator |
| `_check_discord_pulse_stale` | org-scoped | warning | No Discord pulse data in last 7 days |
| `_check_stuck_runs` | run-scoped | warning | Workflow run in `running` state for >2 hours |
| `_check_member_decay` | entity-scoped | warning/critical | Entity decay score ≥ warning threshold; escalates to critical if entity has a structurally important tag (`cultist_candidate`, `cultist`, `voice`, `mvl`, `top_contributor`) and score ≥ critical threshold |
| `_check_bridge_decay` | entity-scoped | critical | High-centrality bridge node with high decay score (centrality ≥ 0.3, decay ≥ 0.6) |
| `_check_watchlist_changes` | entity-scoped | warning/critical | Watched entity state changed; escalates to critical if `decay_score` increased by ≥ 0.1 |
| `_check_workflow_failures` | run-scoped | critical | `workflow_runs` with `status='failed'` in last 30 days with no open alert |
| `_check_discord_pulse_regression` | org/project-scoped | warning | WoW retention rate dropped >5% |

---

## Dedup Key Formats

`create_alert()` blocks creating a new alert when an alert with the same `dedup_key` already exists with `status IN ('new', 'acknowledged')`. Only `resolved` allows re-alerting.

| Alert type | Dedup key |
|---|---|
| `tracking_stale` | `tracking_stale:{org_id}` |
| `cultist_tag_expiring` | `tag_expiring:{entity_id}:cultist_candidate` |
| `sentiment_shift` | `sentiment_shift:{org_id}:{run_id_after}` |
| `mvl_score_change` | `mvl_change:{org_id}:{run_id_after}` |
| `action_unclaimed` | `unclaimed:{org_id}:{action_id}` |
| `discord_pulse_stale` | `discord_pulse_stale:{org_id}` |
| `stuck_run` | `stuck_run:{org_id}:{run_id}` |
| `member_decay` | `member_decay:{org_id}:{entity_id}` |
| `bridge_decay` | `bridge_decay:{org_id}:{entity_id}` |
| `watchlist_change` | `watchlist_change:{org_id}:{entity_id}` |
| `workflow_failed` | `workflow_failed:{org_id}:{run_id}` |
| `discord_pulse_regression` | `discord_pulse_regression:{org_id}:{project_slug}:{run_date}` |

**Always include `org_id`.** Omitting it causes cross-org suppression collisions.

---

## Per-Org Threshold Overrides

Each org can override default thresholds via `config_json`. Set them with `sable-platform org config set`. Checks read this field at evaluation time — no restart required.

```bash
sable-platform org config set tig tracking_stale_days 21
sable-platform org config set tig decay_warning_threshold 0.5
```

| Config key | Default | Applies to |
|---|---|---|
| `tracking_stale_days` | `14` | `_check_tracking_stale` |
| `discord_pulse_stale_days` | `7` | `_check_discord_pulse_stale` |
| `stuck_run_threshold_hours` | `2` | `_check_stuck_runs` |
| `decay_warning_threshold` | module default | `_check_member_decay` |
| `decay_critical_threshold` | module default | `_check_member_decay` |
| `bridge_centrality_threshold` | `0.3` | `_check_bridge_decay` |
| `bridge_decay_threshold` | `0.6` | `_check_bridge_decay` |
| `discord_pulse_regression_threshold` | `0.05` | `_check_discord_pulse_regression` |
| `max_ai_usd_per_org_per_week` | `5.0` | Cost budget cap |

**Range validation:** Numeric config keys are validated against min/max bounds on `org config set`. Out-of-range values are rejected. See `org_cmds.py` `_NUMERIC_RANGES` for bounds.

---

## Delivery Pipeline

Alert delivery is **decoupled** from evaluation. Check functions only create alert rows — they do not call `_deliver()`. After `evaluate_alerts()` returns, the caller invokes `deliver_alerts_by_ids(conn, alert_ids)` to dispatch notifications. This ensures alert rows are committed before any HTTP calls, and delivery failures never affect alert creation.

```
_deliver(conn, org_id, severity, message, dedup_key=...)
  │
  ├── Load alert_configs row for org_id
  │     If missing: treat as enabled with default severity filter
  │
  ├── Check enabled flag
  │     If False: return (silent)
  │
  ├── Min severity filter
  │     Ranks: critical=3, warning=2, info=1
  │     If message severity < min_severity: return (silent)
  │
  ├── Cooldown gate (if dedup_key provided)
  │     Read last_delivered_at for dedup_key from alerts table
  │     If within cooldown_hours (default 4h): return (silent)
  │
  ├── _send_telegram(token, chat_id, message)
  │     Uses SABLE_TELEGRAM_BOT_TOKEN env var
  │     Skipped if token unset or telegram_chat_id not configured for org
  │     Logs HTTP status code only on failure (never logs token or URL)
  │
  ├── _send_discord(webhook_url, message)
  │     Skipped if discord_webhook_url not configured for org
  │
  ├── log.warning("ALERT {severity} [{org_id}]: {message}")
  │     Always runs, regardless of channel configuration
  │
  ├── Webhook dispatch (best-effort)
  │     dispatch_event(conn, "alert.created", org_id, {...})
  │     Failure is logged, does not affect alert or delivery status
  │
  └── mark_delivered(conn, dedup_key)
        or mark_delivery_failed(conn, dedup_key, error)
        Updates alerts.last_delivered_at / alerts.last_delivery_error
```

**Cooldown does not reset on acknowledge or resolve.** It is purely time-based per `dedup_key`. Set `cooldown_hours=0` on the `alert_configs` row to disable.

---

## Alert Lifecycle

```
[new] ──── operator acknowledges ────► [acknowledged]
  │                                         │
  │         operator resolves ──────────────┘
  │                 │
  └─────────────────▼
              [resolved]
                   │
                   └── dedup_key now re-fires on next evaluate_alerts()
```

**Status transitions:**
- `new` → `acknowledged`: `sable-platform alerts acknowledge <alert_id>`
- `new` / `acknowledged` → `resolved`: manual DB update or a future `resolve` command
- Resolved alerts are not surfaced by `sable-platform alerts list` by default

---

## Mute / Unmute

Muting an org sets `alert_configs.enabled = 0`. All delivery (Telegram, Discord, webhooks) is suppressed. Alerts are still created in the DB — mute only affects `deliver_alerts_by_ids()` / `_deliver()`.

```bash
sable-platform alerts mute tig
sable-platform alerts unmute tig
```

---

## Setting Up Delivery Channels

### Telegram

1. Create a bot via @BotFather → get `SABLE_TELEGRAM_BOT_TOKEN`.
2. Add the bot to your alert channel and obtain the `chat_id`.
3. Set the env var: `export SABLE_TELEGRAM_BOT_TOKEN="<token>"`
4. Configure the org: `sable-platform alerts config set --org tig --telegram-chat-id <chat_id>`
5. Test: `sable-platform alerts evaluate --org tig`

**Security:** `SABLE_TELEGRAM_BOT_TOKEN` must not be logged, committed, or surfaced in error output. `_send_telegram()` logs only the HTTP status code on failure — never the URL (which contains the token).

### Discord

1. In your Discord server: Server Settings → Integrations → Webhooks → New Webhook → Copy URL.
2. Configure the org: `sable-platform alerts config set --org tig --discord-webhook <url>`
3. Test: `sable-platform alerts evaluate --org tig`

---

## Running Evaluations

**Manual (ad-hoc):**
```bash
sable-platform alerts evaluate --org tig
sable-platform alerts evaluate            # all active orgs
```

**Via workflow (schedulable):**
```bash
sable-platform workflow run alert_check --org tig
# Or for all orgs:
sable-platform workflow run alert_check --org _platform --config '{"org_id": "_all"}'
```

**Via cron (recommended for production):**
```bash
sable-platform cron add --schedule "0 * * * *" -- sable-platform alerts evaluate
```

---

## Inspecting Alerts

```bash
# List all new/critical alerts
sable-platform alerts list --status new --severity critical

# List all alerts for an org
sable-platform alerts list --org tig

# Show delivery errors (alerts that fired but HTTP delivery failed)
sable-platform alerts list --org tig --json | jq '.[] | select(.last_delivery_error != null)'

# Acknowledge an alert
sable-platform alerts acknowledge <alert_id>

# Show current alert config for an org
sable-platform alerts config show --org tig
```

---

## Implementation Files

| File | Purpose |
|------|---------|
| `sable_platform/workflows/alert_evaluator.py` | `evaluate_alerts()` — thin orchestrator, per-org isolation |
| `sable_platform/workflows/alert_checks.py` | All 12 `_check_*` condition functions (create alerts only, no delivery) |
| `sable_platform/workflows/alert_delivery.py` | `deliver_alerts_by_ids()`, `_deliver()`, `_send_telegram()`, `_send_discord()` |
| `sable_platform/db/alerts.py` | `create_alert()`, `list_alerts()`, `mark_delivered()`, `mark_delivery_failed()`, `acknowledge_alert()` |
| `sable_platform/webhooks/dispatch.py` | HMAC-SHA256 webhook dispatch called from `_deliver()` |
