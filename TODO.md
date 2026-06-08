# SablePlatform — Roadmap

For completed work, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Platform Status

**v0.5 is production-ready.** 1297+ tests, 0 known cross-repo blockers. PostgreSQL live on Hetzner VPS (2026-04-09). SQLAlchemy Core migration complete. Alembic for Postgres, `pg_dump` backup, Docker/compose with direct `alerts evaluate` loop. Migration 044 (`api_tokens`) ships the alert-triage HTTP API foundation. `sable-platform sync-from-local` closes the laptop→prod data gap for Cult Grader weekly cadence.

---

## Open Items

### Proof loop — suite-level commercial spine

The canonical plan is [docs/PROOF_LOOP_PLAN.md](docs/PROOF_LOOP_PLAN.md). Treat this as the organizing principle for work-tracking, reply outcomes, campaign outcomes, playbook outcomes, QBR/export work, and revenue feedback. Near-term priority: close the posted-reply capture + fixed-age snapshot loop, then ship a weekly proof-packet assembly path before adding speculative analytics surfaces.

### API — next slices after the thin alert-triage MVP

The alert-triage MVP shipped 2026-05-12 (see AUDIT_HISTORY § SP-API-MVP). The token + rate-limit + ownership-check spine is reusable. Next-priority additions in order, per [TODO_API.md](TODO_API.md):

1. **Phase 1b broader read API** — `GET /v1/orgs/{org_id}/artifacts`, `/playbook/*`, `/entities`, `/workflow-runs/...`. Reuses the same auth/scope/rate-limit middleware. No new tables.
2. **Phase 2 safe writes** — `POST /v1/entities/{entity_id}/notes`, `POST /v1/actions`, `POST /v1/outcomes` with idempotency keys. Each new write surface needs an explicit canonical contract before exposure (see TODO_API.md § Engineering Tasks).
3. **Phase 2.5 prospect tenant hardening** — add `client_org_id` (or equivalent) to prospect rows. Backfill SQLite + Postgres. Then expose `GET /v1/orgs/{org_id}/prospects` etc. Blocked until this migration lands.
4. **Phase 3 spend-request flow** — separate `spend_requests` / `spend_approvals` model; owner approval gate; one-time execution authorization. Deliberately deferred until at least one read-only client surface exists.

Operator-facing summary for the MVP that already shipped: [docs/API_ALERT_TRIAGE_MVP.md](docs/API_ALERT_TRIAGE_MVP.md).

### SolStitch fit-check bot (`sable-roles`) — ops + platform-side follow-ups

**Status:** V1 shipped 2026-05-13 — live in SolStitch (guild `1501026101730869290`, `#fitcheck` channel `1501073373252292709`). Bot repo at `~/Projects/sable-roles/`. Build plan + TODO at `~/Projects/SolStitch/internal/fitcheck_v1_build_plan.md` (all 9 chunks complete) + `~/Projects/SolStitch/internal/fitcheck_build_TODO.md`. Ship runbook at `~/Projects/SolStitch/internal/ship_dms.md`.

**Platform-side ownership** (migrations 043 + helpers live in this repo):
- Migration `043_discord_streak_events.sql` + matching Alembic revision `b2da0d6b1be1`.
- `sable_platform/db/discord_streaks.py` — `upsert_streak_event`, `update_reaction_score` (optimistic-locked w/ ms-resolution `updated_at`), `get_event`, `compute_streak_state` (app-side iteration over distinct UTC days).
- Tests at `tests/db/test_discord_streaks.py` (19 cases). `audit_log` rows for fit-check actions use `source="sable-roles"` and `actor="discord:bot:<bot_user_id>"`.

**Open follow-ups (none blocking; tracked here so they don't drift):**

1. **VPS deploy of `sable-roles`** — V1 currently runs on Sieggy's local machine via `python -m sable_roles.main`. Plan §6 promises deploy within 24-48h of go-live. Reuse the Hetzner VPS that already hosts SP's Docker stack: add a `sable-roles` service to compose, mount the same `~/.sable/sable.db` volume (Postgres URL via `SABLE_DATABASE_URL` once on prod), share env conventions. Owner: Sieggy.
2. **`setup_hook` try/except around `tree.sync`** (`~/Projects/sable-roles/sable_roles/main.py:47`) — fit-check TODO C6 follow-up (b) and ship_dms.md §0.3 both flag that `tree.sync(guild=...)` raises `Forbidden 50001` if `GUILD_TO_ORG` lists a guild the bot isn't in yet, crashing startup. SableTracking's bot wraps the equivalent loop. Harden before any second-guild onboarding (multisynq, future clients).
3. **Operator allowlist for `#fitcheck` enforcement** — V1 deletes any text-only post including Brian's (founder). If Brian or any `@Atelier` member loses patience with the bot deleting their welcome/announcement-style messages, add an opt-in user_id allowlist (config-driven) that skips the delete+DM branch. New SQL column NOT needed — pure config.
4. **`@influenza` rotation automation** — same bot host. SolStitch role `1501076194005880913` per `~/Projects/SolStitch/discord_ids.txt`. Pending design: monthly top-N yappers via `SableTracking` listener data → API call → role grant/revoke. Cross-suite: needs an SP-side workflow + adapter to `sable-roles` (or direct DB read by `sable-roles` of `metric_snapshots`/`contributors`). Memory: `project_solstitch_influenza`.
5. **Multi-tenant routing review** — `sable-roles` reads `SABLE_ROLES_FITCHECK_CHANNELS_JSON` + `SABLE_ROLES_GUILD_TO_ORG_JSON` from `.env`. Same shape as SableTracking's `DISCORD_GUILD_TO_CLIENT`. When the second client lands, consider whether routing should move into a platform-side config table (joinable with `orgs.config_json`) instead of env vars per process.
6. **`/streak` history backfill (V2)** — V1 starts streaks at gateway-connect; no history import. Plan §0 + §8 deferred this. If/when needed, add an admin CLI: `sable-platform discord-streaks backfill --org X --channel-id Y --since DATE` that walks `channel.history()` and upserts. Owner: defer until a real ask.
7. **Cross-suite: surfacing fit-check signal to SableWeb / dashboard** — `discord_streak_events` is a real engagement-density signal but no SP alert check / dashboard view reads it. Candidate Tier-1 signal: "client X has zero fits in 7d" → alert. Defer until 2+ clients are running the bot.

Memory: `project_solstitch_fitcheck` already tracks the build context. Add bot-status + VPS-deploy date to that memory when item 1 ships.

### Sync — next iterations after laptop→prod MVP

`sable-platform sync-from-local --org X --target-url postgresql://...` shipped 2026-05-12. Open follow-ups:

1. **`actions` / `outcomes` coverage.** Cult Grader doesn't write these today, but operator manual actions on the laptop also don't reach prod. Add to the sync surface when there's a real laptop-action workflow.
2. **`diagnostic_deltas` replay.** Currently excluded because `run_id_before/after` are local INTEGER FKs. Either remap via `cult_run_id` lookup on the target, or accept that deltas are recomputed on prod from synced diagnostic_runs.
3. **Reverse direction.** "Pull prod state down to laptop for replay/debug." Not urgent — `migrate to-postgres` covers the one-shot first-cut, and there's no current need for the reverse.
4. **Multi-process safety on target.** Today each sync runs a sequence of per-table transactions. If two operators run sync concurrently against the same prod target, cursors race. Add a target-side advisory lock (Postgres `pg_advisory_lock` or a `platform_meta` token) before adopting any automated cadence.

---
## [Cult Grader cross-repo] 2026-06-01 — bridge-node backfill after lateral fix
Source: Sable_Cult_Grader/docs/MEGA_IMPLEMENTATION_PLAN.md §5/§6. Cult Grader is fixing a latent bug where `compute_twitter_metrics` passed the project DISPLAY NAME instead of the handle to the reply-graph edge builders, so the project's own account was never excluded as a graph node. Since reply_threads come from the project's own posts, the project became a dominant HUB node that captured bridge-node (articulation-point) detection. (There are NO self-edges — `a!=b` is enforced; density direction is ambiguous.) `platform_sync._seed_bridge_nodes` (~line 719) seeded `sable.db` bridge-node entities from that corrupted graph.
- ACTION: after Cult Grader WS-1d ships, re-sync / backfill bridge-node entities for affected orgs (bridge handle sets will CHANGE (arbitrarily — not necessarily shrink)). Consider a one-time `backfill_run_summary.py`-style pass.
- NOTE: `_sync_interaction_edges` (~line 1029) already uses reply_threads-only (honest) — unaffected.

---
## [Cult Grader cross-repo] 2026-06-02 (night) — persist tool_mode / coverage / v2 axes + validation-label surface
Source: Sable_Cult_Grader CHANGELOG (four gated features shipped). `run_summary_json` now carries `meta.tool_mode`, `meta.coverage`, `scores.thesis_kol_*`, and a `health_v2` block (two axes + a GATED blend).
- ACTION: persist `tool_mode`, `coverage`, the two v2 axes (scale/quality) + the gated-blend status into `sable.db` (additive column/blob; `run_summary_json.schema_version` unchanged). Do NOT persist the blend as a score while `status == "gated_directional"`.
- ACTION (optional, for v2 un-gating): a small operator UI to record ground-truth health judgments that write to Cult Grader's `diagnostics/_health_validation.jsonl` (or a table its `analysis/health_validation.evaluate_v2_validation` can read). Needs ≥12 labels before the v2 blend can un-gate. NEVER feeds back into v2 weights (lesson #13 — validation, not tuning).
- CONSTRAINT: KOL/v2 fields are descriptive — they do NOT feed `sable_fit_score` and must not be treated as the gating score.
