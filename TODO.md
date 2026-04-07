# SablePlatform — Roadmap

For completed work, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Platform Status

**v0.5 is complete.** All open items resolved.

---

## Open Items

~~ORG-CONFIG~~ — `org config set/get/list` shipped 2026-04-05. Valid sectors: DeFi/Gaming/Infrastructure/L1\/L2/Social/DAO/NFT/AI/Other. Valid stages: pre_launch/launch/growth/mature/declining. Numeric threshold keys coerced to float. 6 new tests.

~~ORG-JOURNEY~~ — `get_key_journeys(conn, org_id, limit=5)` added to `db/journey.py`; `sable-platform journey top --org ORG [--limit N] [--json]` shipped 2026-04-05. 4 new tests.

### SP-LEAD: Wire `lead_discovery` workflow for automated prospecting pipeline

**Goal:** Make the full Lead Identifier → score → Cult Grader diagnostic pipeline a single command: `sable-platform workflow run lead_discovery --org <org>`.

**Why:** Currently Lead Identifier and Cult Grader run manually. Wiring them into a SablePlatform workflow enables weekly automated prospecting via cron. This is the 20% of plumbing that makes the existing 80% of tooling into a revenue-generating machine.

**Mechanism:**
1. Create `sable_platform/adapters/lead_identifier.py` — subprocess adapter following the pattern in `sable_platform/adapters/cult_grader.py`. Calls `python main.py run --sync` in the Lead Identifier repo (path from `$SABLE_LEAD_IDENTIFIER_PATH`). Parses JSON output.
2. Create `sable_platform/workflows/builtins/lead_discovery.py` — new builtin workflow with steps:
   - `run_lead_identifier`: calls LeadIdentifierAdapter, syncs prospect_scores to sable.db via `sable_platform/db/prospects.py`
   - `trigger_cult_grader_for_tier1`: iterates new Tier 1 prospects (composite >= 0.50), triggers Cult Grader diagnostic for each via `CultGraderAdapter`
   - `sync_results`: marks workflow complete, logs cost
3. Register in `sable_platform/workflows/registry.py` via `_auto_register()` import
4. Add `lead_discovery` cron preset in `sable_platform/cron.py` (weekly-monday schedule)

**Key files:** `adapters/cult_grader.py` (reference pattern), `workflows/builtins/prospect_diagnostic_sync.py` (reference workflow), `db/prospects.py` (sync_prospect_scores), `workflows/registry.py` (_auto_register)

**Potential issues:**
- LeadIdentifierAdapter must handle the case where `SABLE_LEAD_IDENTIFIER_PATH` is unset (raise `SableError(ADAPTER_NOT_CONFIGURED)`)
- Cult Grader trigger step must be bounded: max 10 diagnostics per run to prevent cost blowout. Use `check_budget()` before each.
- The workflow should NOT fail if Cult Grader diagnostics fail for individual prospects — log errors, continue with remaining.

**Tests:** Follow patterns in `tests/workflows/` and `tests/adapters/`. Test: adapter invocation, workflow registration, step sequencing, Tier 1 filtering, bounded trigger count, budget check, partial failure handling.

**Validation:** `python3 -m pytest tests/ -q` — all 996+ tests must pass plus new ones.

---

### SP-INSPECT: Add `prospect_pipeline` inspect command

**Goal:** Give operators a single view of the full prospect funnel: Lead Identifier score → Cult Grader diagnostic status → outreach status → days since last diagnostic.

**Why:** Currently operators must manually cross-reference Lead Identifier output, sable.db diagnostic_runs, and prospect_scores. This command unifies the view.

**Mechanism:**
1. Add `prospect_pipeline` subcommand to `sable_platform/cli/inspect_cmds.py` (currently 576 lines, 12 subcommands — this becomes the 13th)
2. Query: JOIN `prospect_scores` with latest `diagnostic_runs` per org_id. Include composite_score, tier, fit_score (from diagnostic), days_since_last_diagnostic, recommended_action.
3. Flags: `--tier 1|2|3` filter, `--stale-days N` (show only prospects where last diagnostic > N days ago), `--json`
4. Output: table format (matching existing inspect commands) or JSON

**Key files:** `cli/inspect_cmds.py` (add subcommand), `db/prospects.py` (query helpers), `db/connection.py` (get_db)

**Potential issues:**
- `prospect_scores.org_id` is semantically a project_id, not a Sable client org_id (see CLAUDE.md § Prospect Scores Schema Note). The JOIN to `diagnostic_runs` must match on `org_id` from both tables.
- Some prospects will have no diagnostic run yet — show `—` for fit_score and diagnostic date.

**Tests:** Add to `tests/cli/test_inspect_cmds.py`. Test: empty DB, prospects with/without diagnostics, tier filter, stale-days filter, JSON output.

**Validation:** `python3 -m pytest tests/ -q`

---

### SP-LIFECYCLE: Document client lifecycle

**Goal:** Create `docs/CLIENT_LIFECYCLE.md` mapping each stage of the prospect-to-client journey to specific CLI commands and SableWeb views.

**Why:** The pipeline exists but there's no single document showing how a prospect moves from discovery to active client. This is critical for onboarding new operators and for the BD person Sable will eventually hire.

**Stages to document:**
1. **Discovered** — Lead Identifier found → `sable-platform inspect prospect_pipeline`
2. **Diagnosed** — Cult Grader ran → `sable-platform workflow run prospect_diagnostic_sync --org <org>`
3. **Outreach** — Operator contacted → manual (diagnostic PDF as hook)
4. **Onboarding** — Client signed → `sable-platform workflow run onboard_client --org <org>`
5. **Active** — Workflows running → `sable-platform workflow run weekly_client_loop --org <org>`
6. **Monitoring** — Ongoing → `sable-platform alerts evaluate --org <org>`, `sable-platform dashboard`

Include which SableWeb views correspond to each stage (`/ops` prospect pipeline, `/client` portal).

---

### SP-TAGS: Add `cultist_candidate` and `bridge_node` to `_REPLACE_CURRENT_TAGS`

**Goal:** Make `add_tag()` auto-deactivate prior same-tag entries for `cultist_candidate` and `bridge_node`, matching the existing behavior for `team_member`, `high_lift_account`, etc.

**Why:** When Cult Grader's `platform_sync.py` seeds cultist candidates, each run creates tags with a unique `source_key` (`cult_doctor:{run_id[:8]}`). Because `cultist_candidate` uses additive mode (not in `_REPLACE_CURRENT_TAGS`), `--force` re-runs accumulate duplicate tags per entity. This caused 10x tag duplication in production (e.g., `dreadbong0` had 10 active `cultist_candidate` tags). Full root cause analysis in Cult Grader's `docs/DATA_PRUNING_LESSONS.md`.

**Mechanism:** In `sable_platform/db/tags.py`, add `"cultist_candidate"` and `"bridge_node"` to `_REPLACE_CURRENT_TAGS`.

**Tests:** Extend existing tag tests to verify replace behavior for these tag types.

**Validation:** `python3 -m pytest tests/ -q`

---

## Cross-Repo Integration — All Complete

All downstream repo integrations shipped as of 2026-04-05.

| Item | Repo | Status |
|------|------|--------|
| TRACK-5 (P7-1): TrackingMetadata contract in platform_sync.py | SableTracking | Done |
| TRACK-5 (P7-2): Write to `outcomes` table during sync | SableTracking | Done |
| TRACK-5 (P7-3): Write sync errors to `actions` table | SableTracking | Done |
| F-REJECT-3: `pull-feedback` CLI command | Lead Identifier | Done — §9A 2026-04-04 |
| Relationship web graph viz (`RelationshipGraph.tsx`) | SableWeb | Done — d3-force 2026-04-04 |
| Completed actions → outcomes (`DATA-7`) | SableWeb | Done — 2026-04-05 |
