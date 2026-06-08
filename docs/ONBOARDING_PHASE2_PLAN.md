# ONBOARDING_PHASE2_PLAN.md — the §9 Phase-2 items

Status: **spec, pre-implementation** (2026-06-07). Implements the three deferred items from
`CLIENT_ONBOARDING_PLAN.md` §9. Will be adversarial-(security-)QA-looped before any code ships.

**The non-negotiable design invariant for all three: BEHAVIOR-NEUTRAL UNTIL DATA + FLAGS.**
Each change must be a strict no-op on the current production state (empty new tables, flags off),
so deploying it changes nothing until an operator opts in. This is what makes the eventual
`push and deploy` safe. Every chunk preserves fail-closed auth + fail-open features.

---

## Chunk P1 — DB-backed allowlist (kill the file+redeploy footgun)

**Goal:** add/remove SableWeb access from the DB (CLI), no redeploy. **Auth-critical** — the design
preserves every property in `SableWeb` THREAT_MODEL + the explore map.

### The hard constraint (why this isn't a simple swap)
`lookupUser()` and `operatorCanAccessOrg()` are **synchronous** and called on every org-gated route.
A DB read is async. We must NOT make them async (huge ripple + risk). Solution: a **two-layer
module cache** — env/file stay sync; DB is an async-refreshed layer that never blocks a sync caller.

### Data model (SablePlatform migration 075 — VERIFY HEAD; 074 is current)
`allowlist_entries` (OPS-ONLY, like the other auth/PII tables — never on `/client`):
```
email         TEXT PRIMARY KEY            -- lowercased
role          TEXT NOT NULL               -- admin | operator | client | client_ops  (CHECK)
operator_id   TEXT                        -- for admin/operator/client_ops
org           TEXT                        -- for client/client_ops
assigned_orgs TEXT                        -- JSON array string, optional (operator scoping)
enabled       INTEGER NOT NULL DEFAULT 1  -- soft-disable without delete (offboarding)
notes         TEXT
created_at / updated_at TEXT
CHECK (role IN ('admin','operator','client','client_ops'))
```
Full 7-place migration contract + `db/allowlist.py` CRUD + a `sable-platform allowlist
add/list/disable/rm` CLI (audit-stamped). This is the operator surface that replaces editing
`ALLOWLIST_JSON`.

### SableWeb loader change (`src/lib/allowlist.ts`) — the safe two-layer cache
- Keep `lookupUser(email)` **synchronous**. Internally merge two module caches:
  `merged = { ...dbCache, ...fileCache, ...envCache }` — **env/file WIN over DB** (an operator can
  always override a bad DB row via env; DB only *adds* users not in env/file; a DB row can never
  escalate above or lock out an env user — verified `allowlist.ts:206-209`). Hardcoded fallback stays
  dev-only; prod fail-closed (empty when all sources empty) is **unchanged**.
- `dbCache` is a **separate** module variable, refreshed **async**, that PERSISTS across sync env/file
  reloads (so a sync reload never transiently drops DB users), with the **same 5-min TTL** as the file
  cache and a **single in-flight refresh** (de-duped promise — no connection storm on a cold cache).
- **`reloadAllowlistDb()` (the one risky function) hard rules — QA T1-1/T2-3/T2-6:**
  1. `import "server-only"` at the top (like `ops-identity.ts:1`) so it can NEVER be bundled into an
     edge/client path.
  2. **Mode-gate:** early-return `{}` unless live mode (`env.DEPLOYMENT_MODE === "live_filesystem"` or
     `SABLE_DATABASE_URL` set). The Vercel **demo** runs `AUTH_ENABLED=true` but NO DB → `getDriver()`
     would `new Database(missing, {readonly})` and **throw on open** (`db.ts:50-52`). Skipping it in
     non-live mode prevents a login 500 on a currently-working deployment.
  3. **Wrap the ENTIRE body — including the `getDriver()` construction, not just the `.all()` query —**
     in try/catch → on ANY throw, leave `dbCache` untouched/empty + log. "DB read error" is too narrow;
     the construct can throw.
  4. **Per-row** Zod validate-and-SKIP (log the bad row); do NOT feed the whole DB result into
     `AllowlistFileSchema.safeParse` (that fails the *whole* set on one bad row — wrong model here). One
     malformed row drops only itself, never empties `dbCache`.
  5. **Lowercase the email key on read**, mirroring `parseAndNormalize` (`allowlist.ts:140`) exactly —
     and `db/allowlist.py` lowercases `email` on write — so a case mismatch can't silently lock out.
- `await reloadAllowlistDb()` is inserted in the **login callback** (`api/auth/callback/route.ts`)
  **between email-extraction and the `lookupUser(email)` at line 59** — so a DB user is present at the
  one moment that bakes the JWT. Its T1-1 wrapping means a DB failure there degrades to env/file, never
  a 500.
- **Fail direction (verified):** an empty/stale `dbCache` makes `lookupUser` return `null` for a DB-only
  user → `operatorCanAccessOrg` returns **false** (deny) — `allowlist.ts:225-236` requires a matching
  entry; unknown role → false. **No path grants access on an empty/stale dbCache.** Accepted caveat: a
  DB-only `client_ops`/`operator` may get an intermittent spurious **403** right at a TTL boundary
  before the async refresh lands (fail-CLOSED, retry succeeds) — documented, acceptable; the
  single-in-flight refresh ensures the cache can't *stay* empty.
- **Offboarding caveat (T2-2):** `enabled=0` stops *new logins* within the dbCache TTL; it does NOT
  revoke a live JWT before its 8h expiry (`operatorCanAccessOrg` short-circuits `admin` from the JWT,
  never consulting the allowlist — `allowlist.ts:217`). Same as today's env-allowlist offboarding;
  `allowlist disable` is not an instant session kill. Document it so operators aren't misled.
- The narrow edge allowlists (`kol-create`, `reply-feed-admins`) stay **code/env only** (edge runtime,
  no DB; `middleware.ts:17-18` never imports `lookupUser`/`getDriver`) — out of scope, explicitly.

### Behavior-neutral proof
Prod today has `ALLOWLIST_JSON` set and an empty `allowlist_entries` table → `dbCache={}` →
`merged = envCache` = **identical to today**. The demo (auth on, no DB) mode-gates the DB read → also
identical to today. The feature activates only when an operator runs `sable-platform allowlist add …`.

---

## Chunk P2 — Entitlement enforcement (dormant by default, double fail-open)

**Goal:** features can check `org_entitlements`, but it's a **no-op on prod** until backfilled AND a
flag is flipped. The danger is breaking TIG (no entitlement rows yet) — the design makes that
**structurally impossible** without an explicit two-step opt-in.

### The helper — `sable_platform/db/entitlements.py`
```
ACTIVE = ("trial", "active")               # the ONE definition of "active", used everywhere
enforcement_enabled() -> bool              # os.environ["ENTITLEMENT_ENFORCEMENT"] truthy; default OFF
has_entitlement(conn, org_id, service_key) -> bool   # True = ALLOW
```
Returns True (allow) UNLESS ALL of:
1. `enforcement_enabled()` is true (default OFF — **process env only**, NEVER `config_json`; a client
   can write `config_json` via the CLI, so the master switch must be operator-process-controlled like
   `SABLE_OPERATOR_ID`. Per-SKU knobs live in `org_entitlements.config_json`, the master switch does
   not), AND
2. the org has **≥1 row with `status IN ACTIVE`** (it's been onboarded — un-backfilled orgs have ZERO
   active rows → always allowed, even with the flag on), AND
3. the org does NOT have THIS `service_key` with `status IN ACTIVE`.
On any exception (missing table/DB error) → **allow** (fail-open). **`active` is computed with the SAME
`status IN ('trial','active')` filter in BOTH the per-org guard (#2) and the per-SKU check (#3)** — so
a `paused`/`ended` row is uniformly "not active." Truth table (all 4 status cases):
| flag | org's rows | result |
|------|-----------|--------|
| off | anything | ALLOW |
| on  | 0 active rows (none, or only paused/ended) | ALLOW (un-/de-onboarded → safe) |
| on  | ≥1 active row, THIS sku active | ALLOW |
| on  | ≥1 active row, THIS sku paused/ended/absent | DENY (intended enforcement) |
| on  | DB error / no table | ALLOW (fail-open) |
This **double guard** (global flag + per-org has-active-rows) means flipping the flag cannot break a
live client that hasn't been explicitly entitled.

### Chokepoints (all flag-gated, fail-open) — SP-side only in v1
- **`relay.db.list_due_sweep_orgs()` (T1-2 — it has TWO Slopper callers: `sable/reply/sweep.py`
  production timer + `sable/commands/reply.py` CLI).** Add the entitlement filter as a **pure Python
  POST-filter at the END of `list_due_sweep_orgs`** (after the existing SQL selects the due orgs):
  `if enforcement_enabled(): due = [o for o in due if has_entitlement(conn, o, "reply_assist")]`. Flag
  off → `due` returned verbatim (the SQL `WHERE` is **never** touched → default result set provably
  unchanged). One edit covers BOTH callers transitively. Do NOT join `org_entitlements` into the
  selection SQL.
- `client_checkin_loop` notify step — `has_entitlement(…, "checkin")` alongside the existing
  `checkin_enabled` gate.
- `lead_discovery` — `has_entitlement(…, "cult_grader")` pre-loop guard.
- A `sable-platform entitlements preflight` CLI — reports which active orgs lack entitlement coverage
  for the services they're actually using, so the operator validates BEFORE ever flipping the flag.
- SableWeb reply/compose **route-level** enforcement is explicitly **deferred** (hot client path; the
  sweep filter already starves un-entitled orgs upstream). Noted, not built, in v1.

### Behavior-neutral proof
`ENTITLEMENT_ENFORCEMENT` unset (default) → every `has_entitlement` returns True → zero change.
Even set, every prod org with 0 entitlement rows is allowed. Site-down is impossible without (a)
backfilling rows AND (b) flipping the flag — two deliberate operator acts, with a preflight check.

---

## Chunk P3 — composeAccounts SSOT (managed-account list from `client_accounts`)

**Goal:** a new client's controlled accounts (added via `onboard account add --controlled`) appear in
Tweet-Assist compose WITHOUT editing `composeAccounts.ts`. **The per-operator GRANT stays in code** —
only the account LIST/metadata moves to the DB. The compose-route dual-check is source-agnostic, so
the security boundary is unchanged.

### Design
- The managed-account list becomes `DB(client_accounts where controlled=1, platform='twitter') ∪
  hardcoded MANAGED_ACCOUNTS` (hardcoded as the always-present fallback). `defaultRatio` is a SableWeb
  UI choice → stays in code (`composeDefaultRatioFor`).
- Same two-layer async-cache pattern as P1 (sync helpers `managedAccount`/`composeAccountAllowsOrg`/
  `composeAccountsFor` keep reading a merged cache; DB layer refreshed async). DB empty → today's list.
- **The grant is UNTOUCHED:** `CLIENT_OPS_PERSONA_HANDLES` / `OPERATOR_PERSONA_OWNER` /
  `UNRESTRICTED_COMPOSERS` stay in `ops-identity.ts`. `composeAccountsFor` (`ops-identity.ts:87`) still
  filters the (now merged) list by role+org+grant — the grant filter runs **AFTER** the merge, so a DB
  row can only ADD an account to an org's list; it can NEVER grant an operator access they don't already
  have. The route's `composeAccountAllowsOrg` + membership re-check + outer `operatorCanAccessOrg` are
  identical and source-agnostic.
- **Handle-uniqueness (T3-1):** the DB loader enforces a unique handle across the merged managed list
  (log+drop a colliding DB row), since `managedAccount`/`composeAccountAllowsOrg` resolve a handle to
  exactly one account. A collision can't escalate (the org-access re-check blocks a mis-attributed org),
  but uniqueness keeps it unambiguous.
- **Mandatory regression test (T3-2):** the Bharat→@tigintern boundary must hold with a DB-sourced list
  — assert a DB row `{controlled=1, org=tig, handle=@x}` does NOT appear in `client_ops` Bharat's
  `composeAccountsFor` (his grant set is `{@tigintern}` regardless of list source), and that the
  existing compose-route 403 tests still pass.

### Behavior-neutral proof
`client_accounts` has no `controlled=1, platform='twitter'` rows for any live org today → DB layer
empty → list = hardcoded MANAGED_ACCOUNTS = identical to today. Activates only when an operator marks
a controlled account.

---

## Cross-cutting

- **Deploy safety:** all three are no-ops on current prod state (empty tables, flags off). The
  migration is additive (new table only). So `push + deploy` cannot change runtime behavior until an
  operator opts in — that is the gate that makes deploying an auth-touching change acceptable.
- **OPS-ONLY:** `allowlist_entries` joins `client_intake`/`org_entitlements` as ops-only; never on
  `/client`. Add `allowlist_entries` + `assigned_orgs` to BOTH `data-exposure.test.ts` AND
  `data-exposure-live.test.ts` banned sets (`operator_id`/`operatorId`/`operator`/`org` are already
  banned — no new collision). It's an AUTH table in the shared `sable.db`, only ever read via
  `getDriver()` at `nodejs` runtime (the `server-only`-imported `reloadAllowlistDb`); the edge bundle
  never touches it.
- **Tests per chunk:** P1 — loader precedence/merge + DB-empty-equals-today + fail-closed + migration
  contract + CLI + the two-layer cache (DB user not dropped by a sync reload). P2 — the truth table
  (flag off / 0-rows / onboarded-missing / onboarded-has), fail-open-on-error, the sweep filter, the
  preflight CLI. P3 — DB∪hardcoded merge, grant-boundary-unchanged (the Bharat→@tigintern test still
  passes with a DB-sourced list), DB-empty-equals-today.

## Phasing (each: build → fresh-subagent adversarial QA loop → fix → re-audit until clean → docs)
P1 (migration 075 + db/allowlist.py + CLI + SableWeb loader) → P2 (entitlements helper + chokepoints +
preflight CLI) → P3 (composeAccounts DB source). Then docs (CLAUDE/CLI_REFERENCE/THREAT_MODEL/runbook +
PLAN §9 close-out), then push + deploy-if-no-conflicts.

## Implementation status
- **P1 — DONE + QA-converged** (see the P1 block in §QA log).
- **P2 — DONE + QA-converged (2026-06-07).** `db/entitlements.py` (`enforcement_enabled` [process-env
  only], `has_entitlement` [double-guard truth table, fail-open], `filter_entitled` [pure pass-through]).
  Chokepoints: `relay.db.list_due_sweep_orgs` Python post-filter (covers both Slopper callers, SQL
  untouched), `client_checkin_loop._notify_and_send` + `lead_discovery._trigger_cult_grader_for_tier1`
  early-return gates. `sable-platform entitlements preflight` CLI (scans ALL non-prospect orgs). DORMANT
  by default. **577 + 14 tests pass.** Impl QA round 1 (SHIP-WITH-FIXES → fixed: missing
  sweep/checkin/preflight tests added; preflight scope broadened off `status='active'`). Round 2 → SHIP,
  CONVERGED (no Tier-1/2; the relay_clients-vs-relay_sweep_config preflight proxy is conservative-by-design).
- **P3 — DONE + QA-converged (2026-06-07).** NEW server-only `SableWeb/src/lib/managed-accounts.ts`
  (DB-merge of `client_accounts` controlled=1 ∪ hardcoded `MANAGED_ACCOUNTS`, hardcoded wins on collision,
  two-layer async cache like P1). `ops-identity.composeAccountsFor` + the compose route use the merged
  list; `composeAccounts.ts` stays the CLIENT-safe hardcoded fallback (unchanged). **The per-operator
  GRANT is unchanged — a DB row only ADDs to the list, the grant filter runs after, so Bharat→@tigintern
  holds (regression-tested).** SableWeb **661 passed** + tsc clean. Security QA → SHIP, CONVERGED, no
  Tier-1/2 (grant boundary provably preserved; server-only isolation clean; behavior-neutral on empty DB).
  Tier-3 (cosmetic, not done): the tweetbank route + tweet-assist page don't `await reloadManagedAccountsDb`
  → a just-added controlled account's bank/switcher visibility lags ≤1 TTL (fail-safe — never a wrong grant).

## QA log

**Round 1 (2026-06-07, adversarial SECURITY subagent) — verdict SHIP-WITH-FIXES → fixed.** Auth gate
logic verified fail-CLOSED + sound (precedence can't escalate/lockout; empty/stale dbCache → deny, never
grant). Findings folded in:
- **T1-1 (BLOCKING — demo-deploy lockout):** the Vercel demo runs `AUTH_ENABLED=true` but no DB, so
  `getDriver()` throws on open → a login 500 on a working deployment. → `reloadAllowlistDb` now (a)
  mode-gates to live-only, (b) wraps the WHOLE body incl. `getDriver()` construction, (c) `server-only`,
  + a test for "auth-on/no-DB → login still works."
- **T1-2 (BLOCKING — sweep has TWO Slopper callers):** `list_due_sweep_orgs` is consumed by both
  `sweep.py` + `commands/reply.py`. → entitlement filter is a PURE Python post-filter at the end of that
  one function (SQL `WHERE` untouched → default result set unchanged), covering both callers.
- **T2-1..6:** insertion point pinned + DB-only-user intermittent-403 caveat (fail-closed, documented);
  `enabled=0` ≠ instant session-kill caveat; email lowercased on write+read; per-row Zod skip (not
  whole-blob); `active := status IN ('trial','active')` defined once + 4-case truth table; master flag is
  process-env-only (never config_json); `allowlist_entries`/`assigned_orgs` added to BOTH data-exposure
  bans; `server-only` import.
- **T3:** handle-uniqueness in the merged managed list + the mandatory Bharat→@tigintern regression test;
  dbCache TTL=5min + single in-flight refresh; re-verify migration head (075) at impl.

**Round 2 (2026-06-07, fresh security subagent) — verdict SHIP; loop CONVERGED.** All four round-1 fixes
verified correct + sufficient against real code (not reworded). The flagged "server-only taint could
break an edge import" risk was DISPROVEN — `middleware.ts` imports `kol-create-allowlist`/
`reply-feed-admins`, never `@/lib/allowlist`; every `allowlist.ts` importer is nodejs/server-component,
so `import "server-only"` is safe. No residual fail-open (empty/stale dbCache → null → deny). No
behavior change on empty tables + flags off. Migration head still 074 → 075 confirmed. Two Tier-3 impl
notes to fold in during build:
- The `loadAllowlist()` line-159 fast-path (`return _cachedAllowlist!`) must be replaced by a merge that
  folds `dbCache` in on EVERY sync lookup (cheap spread) — don't keep the early return verbatim or the
  persistent dbCache is silently dropped on a cache hit.
- (citation nit only — the merge lives in `loadAllowlist`, not the cited lookup lines.)
**Cleared to implement P1.**

**P1 IMPLEMENTED + QA-converged (2026-06-07).** Migration 075 `allowlist_entries` (7-place contract,
CHECK role + `email=lower(email)`) + `db/allowlist.py` CRUD (lowercase-on-write) + `sable-platform
allowlist add/list/disable/enable/rm` CLI (audit-stamped). SableWeb `allowlist.ts` two-layer cache
(`reloadAllowlistDb` mode-gated + whole-body-wrapped + dynamic `import("./db")` + per-row Zod skip +
single-in-flight; `loadAllowlist` merges DB UNDER env/file; `server-only`) + callback `await`. SP **983
passed**; SableWeb **655 passed** + tsc clean.
- **Impl QA round 1 (fresh security subagent) — SHIP-WITH-FIXES → fixed:**
  - **T2-A (fail-open the plan rounds MISSED):** `operatorCanAccessOrg`'s operator branch returned `true`
    on a null entry → a DB-only `--assigned-orgs` operator would broaden to ALL orgs on a cold dbCache.
    → fail CLOSED on null/role-mismatch (`if (!entry || entry.role!=="operator") return false`); a
    present-but-unscoped operator still gets all orgs; regression test added. Also offboards a removed
    operator's stale JWT (strictly safer). Two synthetic-operator tests updated to a real allowlisted
    operator.
  - **T2-B:** added the plan-mandated `import "server-only"`.
- **Impl QA round 2 (fresh) — SHIP; CONVERGED.** Both fixes correct + sufficient, no new defect; full
  suites green. (`tests/setup.ts` mocks `server-only`→{}; the callback warms dbCache before JWT mint, so
  legitimate DB operators work.)
