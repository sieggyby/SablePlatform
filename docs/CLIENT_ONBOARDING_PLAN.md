# CLIENT_ONBOARDING_PLAN.md — CLI-driven client & operator onboarding

Status: **spec, pre-implementation** (2026-06-07). Supersedes the manual, implicit go-live
sequence and turns `CLIENT_LIFECYCLE.md` from a 6-stage skeleton into an executable flow.
This plan is the source of truth; it will be adversarial-QA-looped before any code ships.

---

## 0. Why (the audit finding)

Onboarding today is an **implicit ~10-step manual sequence across 4 repos** with no unified
runbook and no structured capture of client inputs:

- `onboard_client` workflow is a **no-op for provisioning** — it only verifies adapter env-paths
  and writes a `sync_runs` row (`workflows/builtins/onboard_client.py:116-130`).
- `org create` writes a row with **empty `config_json`** (`cli/org_cmds.py`). Only
  `orgs.twitter_handle` + `orgs.discord_server_id` are real columns; `client_telegram_chat_id`
  is a `config_json` key.
- **No home** for: team/posting accounts, client-contact TG/Discord, explainer docs, team bios,
  voice/tweet-control guidance, or **"what services the client gets"** (no entitlements concept).
- The context that actually makes a client work — `~/.sable/orgs/<org>/brief.md` +
  `guardrails.yaml` (+ optional `voice/*.md`) — is **hand-authored, no scaffold**.
- **Severe duplication / no SSOT:** the client's Twitter handle is re-entered in ~4 places
  (`orgs.twitter_handle`, `~/.sable/roster.yaml`, `relay_clients.x_handle_override`, SableWeb
  `composeAccounts.ts`); Discord guild + TG chat each in 2. Typos silently break things.
- **No operator onboarding** exists (implicit prereqs: allowlist entry, `SABLE_OPERATOR_ID`,
  hardcoded persona/account grants in `ops-identity.ts`).

## 0.1 Locked decisions (operator grill, 2026-06-07)

1. **Manifest as SSOT.** One structured record per client captures every input; an `apply` step
   projects it into the org row, `config_json`, the `~/.sable/orgs/<org>/` scaffold, and entitlements.
2. **Storage = DB tables in SablePlatform** (new migration). Human-authored *prose* (brief, bios,
   explainer, voice) stays as files under `~/.sable/orgs/<org>/`, **referenced** by the record.
3. **Real `org_entitlements` table now** — a SKU-level vocabulary (what a client buys; finer-grained
   than the four canonical Suite *modules* `{pulse, cm, engage, pairwise}` in PRODUCT_ARCHITECTURE.md
   §4.1), each SKU tagged with its parent module where one applies. NOT the same as the
   SableRevenueLedger's `plan_module` — coordination is an OPEN item (§1.4 / §9), not a settled join.
4. **v1 = reconcile-SP + scaffold; checklist the cross-repo rest.** `apply` writes everything inside
   SablePlatform's reach and **emits a precise, copy-pasteable checklist** for the bits that need a
   redeploy (SableWeb allowlist, SableTracking `.env` routing, `composeAccounts.ts`). It does NOT
   auto-edit other repos in v1.

5. **Ledger ownership RESOLVED (operator, 2026-06-07): onboarding owns it; ledger references.**
   This plan owns the operational SSOT — `client_intake` (the client header) + `org_entitlements`
   (entitlement STATE). The SableRevenueLedger (still pre-migration) will REFERENCE `org_entitlements`
   for its entitled-set and add ONLY billing tables (payments, margin, billed-subset). **Prospect-quote
   gap resolved by the draft-org rule (§1.0):** in onboarding's world a draft (`status='inactive'`) org
   + its `client_intake` row ALWAYS exist after `onboard init`, so `client_intake.org_id` stays a
   NOT-NULL PK FK — no nullable-org needed here. The ledger keeps its own pre-onboarding quote rows for
   not-yet-onboarded entities (true no-org) and references `client_intake` once `onboard init` has run.
   The ledger's D9 `client` becomes the *commercial* header that points at `client_intake`, not a
   duplicate of it. (Write this contract into `SableRevenueLedger/docs/DECISIONS.md` when that effort
   resumes.)

**The clever core:** entitlements drive the required-input checklist. `onboard status` flags an
input as *missing* only when a service the client is **actually buying** needs it.

---

## 1. Data model (migration 073 — VERIFY HEAD at impl; 072 is current head)

> Migration contract is the SablePlatform 7-place chain: SQL file + `connection._MIGRATIONS` +
> `migrate_pg.py` (TABLE_LOAD_ORDER + SEQUENCE_TABLES) + `schema.py` + an Alembic revision +
> test-literal head bumps (72→73) + `docs/CLI_REFERENCE.md` parity. **No `;` inside `--` comments**
> (the splitter trap — `feedback_sableplatform_migration_sql`).
>
> **Migration-contract specifics verified by QA (do not re-derive wrong):**
> - Alembic revisions live at **`sable_platform/alembic/versions/`** (head `…a072_relay_topic_picks.py`).
>   There is NO `db/alembic/` dir — don't reach for it.
> - **SEQUENCE_TABLES** (`migrate_pg.py:222`): the 3 tables with `INTEGER PRIMARY KEY AUTOINCREMENT`
>   (`client_accounts`, `client_docs`, `org_entitlements`) each need a SEQUENCE_TABLES `id` entry;
>   `client_intake` (TEXT PK) goes in **TABLE_LOAD_ORDER only**. All four go in TABLE_LOAD_ORDER
>   **after `orgs`** (FK-safe load order). The third map, `_TEXT_PK_COLUMNS` (`migrate_pg.py:331`,
>   TEXT-PK NULL-repair), is NOT needed: `client_intake`'s TEXT PK is a NOT-NULL FK (never NULL).
> - **CLI_REFERENCE.md says "72 migrations" and `test_doc_parity` is GREEN at baseline**
>   (`len(_MIGRATIONS)==72`). Chunk 1 bumps the literal **72→73** (round-1's "71/RED" note was wrong).

### 1.0 The draft-org rule (resolves the FK-ordering trap) — REQUIRED READ

FKs are enforced on **SQLite too** (`db/engine.py:59` runs `PRAGMA foreign_keys=ON` on every
connection used by `get_db()`), so a manifest row cannot reference a non-existent `orgs` row.
Therefore **`onboard init` upserts a DRAFT org row first, then the `client_intake` row.** This
keeps the FKs (real referential integrity) AND lets `init` reach the validated `org config set`
path later.

This needs a **new canonical writer** — the plan does NOT (and cannot) "reuse `upsert_prospect_org`":
that function force-stamps `status='inactive'` + `config_json={org_type:'prospect',
max_ai_usd_per_org_per_week:0.50}` (`db/orgs.py:50-69`), which would inject a $0.50/wk prospect cap
onto a paying client. Instead, add a sibling to `db/orgs.py` (same canonical *home*, honoring B-N1):
```
upsert_client_org(conn, *, org_id, display_name, status,         # 'inactive' (draft) | 'active'
                  twitter_handle=None, discord_server_id=None, config_extra=None)
```
- `init` calls it with `status='inactive'` (draft; no prospect cap, `config_json.org_type='client'`).
- `apply` calls it with `status='active'` + the canonical `twitter_handle`/`discord_server_id` from
  `client_accounts` (COALESCE — only overwrites when non-NULL).
- `--from-prospect` simply means the org already exists (created by sable-audit `upsert_prospect_org`);
  the upsert is idempotent and preserves the operator-set status until `apply` activates.

Four tables, all `org_id`-keyed (FK → `orgs.org_id`). Dialect-agnostic SQL (sqlite + pg).

### 1.1 `client_intake` — the manifest header (one row per org)
```
org_id              TEXT PRIMARY KEY  REFERENCES orgs(org_id)
manifest_status     TEXT NOT NULL DEFAULT 'draft'   -- draft | ready | applied
primary_contact_name      TEXT
primary_contact_email     TEXT      -- the client login candidate (drives the allowlist checklist)
primary_contact_telegram  TEXT      -- how to reach the CLIENT (distinct from internal chat)
website_url         TEXT
notes               TEXT
created_at          TEXT NOT NULL DEFAULT (datetime('now'))
updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
```

### 1.2 `client_accounts` — the unified handle registry (the future SSOT for handles)
```
id            INTEGER PRIMARY KEY AUTOINCREMENT
org_id        TEXT NOT NULL REFERENCES orgs(org_id)
platform      TEXT NOT NULL          -- twitter | discord | telegram
handle        TEXT NOT NULL          -- @handle, guild id, chat id, or channel id
role          TEXT NOT NULL          -- official | founder | team | intern | contact | community
controlled    INTEGER NOT NULL DEFAULT 0  -- 1 = Sable posts AS this account (managed/compose)
display_name  TEXT
bio           TEXT                   -- optional per-person bio (team bios attach to the handle)
notes         TEXT
created_at    TEXT NOT NULL DEFAULT (datetime('now'))
UNIQUE(org_id, platform, handle)
```
> v1 stores these and `apply` projects the canonical ones into `orgs.twitter_handle` /
> `orgs.discord_server_id` + EMITS the `roster.yaml` / `composeAccounts.ts` / `relay_clients`
> snippets. **Phase 2 direction:** make those consumers READ this table (kill duplication fully).

### 1.3 `client_docs` — pointers to explainer/bio/voice artifacts
```
id          INTEGER PRIMARY KEY AUTOINCREMENT
org_id      TEXT NOT NULL REFERENCES orgs(org_id)
kind        TEXT NOT NULL          -- explainer | bio | voice | brand | other
label       TEXT NOT NULL
location    TEXT NOT NULL          -- URL or local path (e.g. ~/.sable/orgs/<org>/brief.md)
notes       TEXT
created_at  TEXT NOT NULL DEFAULT (datetime('now'))
```

### 1.4 `org_entitlements` — what services the client gets (the SKU ledger)
```
id           INTEGER PRIMARY KEY AUTOINCREMENT
org_id       TEXT NOT NULL REFERENCES orgs(org_id)
service_key  TEXT NOT NULL          -- see §2 taxonomy
tier         TEXT                   -- free | standard | premium (service-defined; nullable)
status       TEXT NOT NULL DEFAULT 'active'  -- trial | active | paused | ended
started_at   TEXT
ended_at     TEXT
config_json  TEXT NOT NULL DEFAULT '{}'      -- per-service knobs (e.g. {"cap_usd_week": 5})
notes        TEXT
created_at   TEXT NOT NULL DEFAULT (datetime('now'))
updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
UNIQUE(org_id, service_key)
```
> **This table is entitlement STATE only — never $.** Pricing/billing/revenue stays in the
> SableRevenueLedger. **Coordination required (see §9, OPEN):** the ledger's `DECISIONS.md` plans its
> OWN `client` table (D9, overlaps `client_intake`) and `plan_module`/`subscription_module` (D14,
> overlaps `org_entitlements`), and is a concurrent claimant on adjacent migration slots (071+). The
> earlier "the join the ledger already wants" framing was wrong — the ledger never named
> `org_entitlements`. Recommended contract (must be written into BOTH docs before either authors
> migrations): **this plan owns the operational client header (`client_intake`) + entitlement state
> (`org_entitlements` = "entitled modules"); the ledger REFERENCES `org_entitlements` for its
> entitled-set and adds only billing tables (`subscription_module` = billed subset, payments, margin).**
> The ledger's D9 `client` should merge into `client_intake`, not duplicate it.

### 1.6 Ops-only wall (DATA EXPOSURE — non-negotiable)

All four tables hold **ops-only** data: client PII (`primary_contact_email`,
`primary_contact_telegram`, `bio`, `notes`) and commercial state (`org_entitlements.tier`/`status`/
`config_json` caps). **They MUST NEVER cross the SableWeb `/client` wall** (THREAT_MODEL.md Risk 1;
the `assembleClientData()` boundary + the `data-exposure.test.ts` banned-field deep-scan). This
matches SableRevenueLedger D16 (financial-table access-control). Action items baked into the chunks:
- These tables are read ONLY by `/ops` surfaces + the CLI; `assembleClientData()` never joins them.
- Chunk 1 adds the new PII/commercial column names (`primary_contact_email`,
  `primary_contact_telegram`, `tier`, etc.) to **BOTH** SableWeb data-exposure tests' banned sets:
  `tests/data-exposure.test.ts:18` (static-mock scan) AND `tests/data-exposure-live.test.ts:57`
  (the one that runs `assembleClientData()` against a seeded sable.db — this is the guard that
  actually catches a careless DB join; the two sets are duplicated, not shared).

### 1.5 Files scaffolded under `~/.sable/orgs/<org>/` (prose — unchanged contract)
`apply`/`init` creates skeletons **only if absent** (never clobbers operator edits):
- `brief.md` — the reply-assist ground-truth (read verbatim by Slopper `org_context.build_org_context`).
- `guardrails.yaml` — `do_not_mention` (list of **strings**), `forbidden_claims` (list of `{term, why}`),
  optional `style_allow` (list of strings), optional **`tickers: {appropriate: ["$TIG", …]}`** (the
  NESTED shape `org_context.load_org_tickers` requires — a flat `tickers: [...]` loads as no-tickers).
  Verified against `Sable_Slopper/sable/shared/org_context.py`.
- `voice/` — per-controlled-account voice docs (one `.md` per `controlled=1` account).
- `bios.md` — optional, if bios aren't captured per-account.
Each scaffolded file is auto-registered as a `client_docs` row pointing at its local path.

---

## 2. Service taxonomy + required-input matrix (the heart of `status`)

Declarative, code-side (`sable_platform/onboarding/requirements.py`), versioned with the schema.
These `service_key`s are **SKU-level** (billable units the client buys); the `module` column maps each
to its canonical Suite module (`pulse|cm|engage|pairwise`) from PRODUCT_ARCHITECTURE.md §4.1, or marks
it a standalone *product*/*surface* (`audit` is a product; `client_portal` a surface). This vocabulary
is deliberately finer than the 4 modules — reconcile with the ledger's `plan_module` per §1.4/§9.

| service_key   | module    | what it is                         | required inputs                                                       | cross-repo provisioning (checklist)                                   |
|---------------|-----------|------------------------------------|----------------------------------------------------------------------|------------------------------------------------------------------------|
| `client_portal` | surface | client logs in to sable.tools      | `primary_contact_email`                                              | SableWeb `allowlist.json` `{role:client, org}` + redeploy             |
| `reply_assist`| engage    | Tweet Assist reply mode            | `twitter_handle`, `brief.md`, `guardrails.yaml`, ≥1 reply persona    | `relay_clients` enable; `roster.yaml` persona; (client_ops allowlist) |
| `compose`     | engage    | Tweet Assist compose / managed acct| ≥1 `controlled=1` account, a `voice/` doc per controlled account     | `composeAccounts.ts` entry + redeploy; per-account grant              |
| `tracking`    | (product) | SableTracking content intake       | a `discord` guild **or** `telegram` intake group in `client_accounts`| SableTracking `.env` `GROUP_TO_CLIENT_JSON` / `DISCORD_GUILD_TO_CLIENT` + restart |
| `cult_grader` | (product) | community-health diagnostics       | `twitter_handle` + (discord guild or `tracking`)                     | Cult Grader prospect YAML (or internal-data path)                     |
| `checkin`     | (product) | weekly client check-in             | `config: client_telegram_chat_id` + `checkin_enabled`                | `org config set` (done by `apply`)                                    |
| `kol`         | (product) | SableKOL outreach                  | `twitter_handle`                                                      | SableKOL sidecar (already live)                                       |
| `audit`       | (product) | sable-audit community audit        | a `discord` guild in `client_accounts`                               | register guild / self-invite                                          |
| `cm`          | cm        | SableAutoCM (NULO) community-manager| bot token, tenant config                                            | BotFather token + `config/<tenant>.yaml` + process (OPERATIONS_RUNBOOK)|
| `pulse`       | pulse     | sable-pulse legibility bot         | bot token, tenant config                                             | BotFather token + `config/<tenant>.yaml` + process (OPERATIONS_RUNBOOK)|
| `engage_bot`  | engage    | sable-roles Discord (fitcheck/roast)| a `discord` guild in `client_accounts`                              | `GUILD_TO_ORG`/`FITCHECK_CHANNELS` `.env` + bot deploy                |
| `pairwise`    | pairwise  | pairwise reactor/tournament        | a `discord` guild in `client_accounts`                              | (per pairwise runbook — not yet live)                                 |

> `cm` and `pulse` are **distinct** modules (the earlier `autocm/pulse` slash-key was wrong). `engage`
> spans several SKUs (`reply_assist`/`compose`/`tracking`/`engage_bot`). A client buys SKUs; `status`
> reasons over SKUs; the `module` tag is what the revenue ledger rolls up.

**Always-required (any client):** `org_id`, `display_name`.
**`status` logic:** for each entitlement with status ∈ {trial, active}, check its required inputs;
emit ✅ present / ❌ MISSING (with "needed for: <service>") / ⚠️ present-but-unverified. Inputs no
active service needs are listed as ℹ️ optional, never as blocking.

---

## 3. CLI surface — `sable-platform onboard …`

New command group `cli/onboarding_cmds.py` (registered in `cli/main.py`). All mutations stamp
`SABLE_OPERATOR_ID` to the audit log (reuse `db/audit.log_audit`). Thin CLI over a pure
`sable_platform/onboarding/` service module (testable without click).
> Note: the `onboard` group is NOT in `cli/main.py`'s fail-closed exemption list (only
> `init`/`db-health`/`api-serve` are), so **`onboard init` requires `SABLE_OPERATOR_ID`** — unlike the
> top-level `init`. This is intended (every onboarding mutation is audit-stamped); just don't expect
> `onboard init` to run id-less the way `init` does.

```
onboard init <org_id> --name "Display Name" [--from-prospect]
    Create a draft manifest + scaffold ~/.sable/orgs/<org>/ skeletons. --from-prospect carries
    over twitter_handle + discord from an existing prospect org (created by sable-audit).

onboard set <org_id> <field> <value>
    Set a header field (primary_contact_email, primary_contact_telegram, website_url, notes).

onboard account add <org_id> --platform twitter|discord|telegram --handle X
                    --role official|founder|team|intern|contact|community
                    [--controlled] [--display-name ...] [--bio ...]
onboard account list/rm …

onboard doc add <org_id> --kind explainer|bio|voice|brand --label "…" --location <url|path>
onboard doc list/rm …

onboard service add <org_id> <service_key> [--tier …] [--status trial|active] [--config k=v …]
onboard service rm/list <org_id>            # the entitlement editor

onboard status <org_id> [--json]            # ★ THE CORE UX — see §4
onboard apply  <org_id> [--dry-run]         # ★ reconcile SP + emit checklist — see §5
onboard activate <org_id>                   # the go-live flip: orgs.status='active' via upsert_client_org.
                                            # DISTINCT from `org graduate` (that stamps prospect_scores,
                                            # not the org row, and keys on a project_id — db/prospects.py).
                                            # apply implies activate; this is the standalone verb.
```
> Note: do NOT call `graduate_prospect` from this flow — it only stamps `prospect_scores.graduated_at`
> for a row that may not exist for a non-prospected client, and never touches `orgs.status`. If a
> prospect_scores row exists, `activate` MAY best-effort stamp it, but org activation ≠ graduation.

## 4. `onboard status` — the "flagged needs you can track down" report

The command the operator lives in. Entitlement-aware. Example:
```
SolStitch (solstitch) — manifest: draft

SERVICES
  ✅ reply_assist (active)        ✅ tracking (active)        ⏸ checkin (paused)

REQUIRED INPUTS
  ✅ Display name              SolStitch
  ✅ Twitter handle           @SolStitch
  ❌ Reply brief              MISSING  → ~/.sable/orgs/solstitch/brief.md (needed for: reply_assist)
  ⚠️ Guardrails               present, 0 forbidden_claims — review (needed for: reply_assist)
  ❌ Discord/Telegram intake  MISSING  → add a `tracking` account (needed for: tracking)
  ✅ Reply persona            @sol_intern

ACCOUNTS (3)   twitter:@SolStitch (official) · twitter:@brian (founder, controlled) · …
DOCS (2)       explainer: Litepaper (url) · voice: brian_voice_v1 (local)

PROVISIONING (run `onboard apply` to do the SP-side; the rest is a checklist)
  ⬜ allowlist.json client entry (SableWeb redeploy)
  ⬜ SableTracking GROUP_TO_CLIENT_JSON routing (restart)

→ 2 blocking items. Chase: brief.md, a tracking group id.
```
`--json` returns the same structure for tooling. Exit code nonzero when blocking items remain
(so it can gate a future `weekly_client_loop` or CI check).

## 5. `onboard apply` — reconcile + checklist

Idempotent. With `--dry-run`, prints the diff without writing. Steps:
1. **Refuse if blocking inputs missing** (unless `--force`) — surfaces `status` first.
2. **Activate the org row** via the new `upsert_client_org(status='active', …)` (§1.0) — set
   `display_name`, and `twitter_handle` + `discord_server_id` **from the canonical `client_accounts`
   rows** (official twitter; first discord guild). This is NEW code in `db/orgs.py` (a sibling to
   `upsert_prospect_org`, NOT a reuse of it — that one would stamp the $0.50 prospect cap).
3. **Project config_json**: `sector`, `stage`, caps, `checkin_enabled` + `client_telegram_chat_id`,
   via the **extracted** validator (small refactor: lift the sector/stage-enum + numeric-range
   validation out of the `org_config_set` click command at `org_cmds.py:106-167` into a pure
   `validate_org_config(key, value)` so both the CLI and `apply` share it — no raw config writes).
4. **Materialize entitlements** into `org_entitlements` (already there; this is the apply confirmation).
5. **Scaffold** any missing `~/.sable/orgs/<org>/` files (never clobber) + register `client_docs`.
6. **Emit the cross-repo checklist** as copy-pasteable snippets, per active entitlement (§2 col 3):
   the exact `allowlist.json` line, the `GROUP_TO_CLIENT_JSON` entry, the `composeAccounts.ts`
   object, the `relay enable` command. v1 prints; it does not edit other repos.
7. Flip `manifest_status='applied'`; audit-log it.

## 6. Operator onboarding (sibling flow, lighter — `sable-platform operator …`)

Same pattern, smaller surface. An operator registry table is **deferred to Phase 2** unless the
QA loop argues otherwise; v1 is a **checklist emitter** because the real grants live in
file+redeploy locations (`allowlist.json`, `ops-identity.ts`/`composeAccounts.ts`):
```
operator checklist <operator_id> --email … --role admin|operator [--orgs tig,solstitch]
                                 [--persona @handle … ] [--compose-as @acct … ]
    Emits: the allowlist.json entry, the SABLE_OPERATOR_ID export, the adapter-path env block,
    and (if persona/compose grants) the ops-identity.ts / composeAccounts.ts edits — as a
    single copy-paste runbook. Does not write other repos.
```
> Rationale for not building an operator table in v1: it would be a 5th place operator identity
> lives (`allowlist` + `SABLE_OPERATOR_ID` + `ops-identity` + work_tracking). Better to FIRST
> decide (Phase 2) whether the allowlist itself moves to the DB — see §9 Open decisions.

## 7. Backfill the existing clients

A one-shot `scripts/backfill_intake.py` seeds `client_intake` + `client_accounts` +
`org_entitlements` for the live orgs (TIG, SolStitch; RobotMoney dormant), so `onboard status` is
truthful for them on day one (and surfaces their *actual* current gaps). **v1 sources = `orgs`
(twitter_handle, discord_server_id, config_json.discord_guild_id) + `relay_clients` (an enabled row
→ infer a `reply_assist` entitlement).** Reply **personas** need NO seeding — `onboard status` reads
them LIVE from `roster.yaml` (`_roster_personas`). Seeding **controlled/managed accounts** from
`composeAccounts.ts` is deferred to Phase 2 (the §10 non-goal: don't make other repos' files a backfill
source); until then a `compose`-entitled client's controlled accounts are added via `onboard account
add --controlled`. Manifest stays `'draft'` — a backfill is a review starting point, not a completeness
claim.

## 8. Phasing (each chunk: build → fresh-subagent adversarial QA loop → docs → tests)

- **Chunk 1 — schema.** Migration 073 (4 tables, FKs→orgs) + full 7-place contract (incl.
  SEQUENCE_TABLES for the 3 AUTOINCREMENT tables + **bump CLI_REFERENCE 72→73**) +
  `db/orgs.upsert_client_org()` (the new draft/active client writer, §1.0) +
  `db/onboarding.py` CRUD helpers + register the new PII/commercial column names in **both** SableWeb
  data-exposure tests' banned sets (§1.6) + tests. (Mirrors the 067/070 community-audit discipline.)
  Gate cleared: §9 ledger-coordination RESOLVED 2026-06-07 (§0.1(5)).
- **Chunk 2 — pure core.** `onboarding/requirements.py` (service→input matrix) +
  `onboarding/status.py` (the readiness computation, pure, fully unit-tested with fixtures) +
  `onboarding/scaffold.py` (file templates). No click, no I/O beyond the injected conn + a fs seam.
- **Chunk 3 — CLI.** `onboard init/set/account/doc/service/status` over the core.
- **Chunk 4 — apply + checklist emitter** (the cross-repo snippet generators).
- **Chunk 5 — backfill script** + run it for TIG/SolStitch; verify `status` truthfulness.
- **Chunk 6 — operator checklist** (lighter) + docs.
- **Docs last:** rewrite `CLIENT_LIFECYCLE.md` to point at the new flow; new
  `docs/ONBOARDING_RUNBOOK.md`; CLAUDE.md key-files + CLI_REFERENCE parity; update PRODUCT_ARCHITECTURE.

## 9. Open decisions (resolve in the QA loop or defer explicitly)

- **★ SableRevenueLedger coordination — RESOLVED 2026-06-07** (see §0.1(5)): onboarding owns
  `client_intake` + `org_entitlements`; ledger references + adds billing-only; draft-org rule covers
  the prospect representation; ledger keeps true no-org quote rows. Chunk 1 is unblocked. *(Original
  framing retained below for context.)* The ledger
  (`SableRevenueLedger/docs/DECISIONS.md`, planning-only/uncommitted) plans a `client` table (D9) and
  `plan_module`/`subscription_module` (D14) that overlap `client_intake` + `org_entitlements`, and is a
  concurrent claimant on migration slots 071+. **Recommended contract (write into BOTH docs before
  either authors migrations):** onboarding owns the operational client header + entitlement STATE; the
  ledger REFERENCES `org_entitlements` as its entitled-set and adds only billing tables; the ledger's
  D9 `client` merges into `client_intake`. **Caveat the QA loop surfaced:** D9's `client.org_id` is
  UNIQUE-but-**NULLABLE** *by design* (to hold not-yet-clients/prospects with no org during the quote
  flow), whereas `client_intake.org_id` is a NOT-NULL PK FK→orgs and **cannot represent a prospect with
  no org**. So a naïve merge would drop D9's prospect-quote capability. The merge only works if
  `client_intake` gains a prospect representation (nullable-org row, or a draft status that doesn't
  require an orgs row) OR the contract becomes "ledger keeps D9 prospect rows, references
  `client_intake` only for converted clients." **This is the one decision that should not be made
  unilaterally** — confirm with the operator before Chunk 1 migrations land, since it sets table names
  + the prospect model both initiatives build on.
- **Does the allowlist move to the DB?** The single biggest footgun is `allowlist.json` +
  redeploy. v1 only *emits* the entry. A DB-backed allowlist (read by SableWeb at runtime) is a
  separate, security-sensitive effort — flagged, not in scope here. Decide before operator-table.
- **`controlled` account → managed-account drift.** v1 emits the `composeAccounts.ts` entry; the
  real fix (SableWeb reads `client_accounts` where `controlled=1`) is Phase 2.
- **Entitlement ↔ feature gating.** v1 records entitlements; it does NOT yet *enforce* them at the
  feature layer (e.g., relay sweep checking `org_entitlements`). Recording first, gating later
  (coordinate with the cost-cap gate that already exists).

## 10. Non-goals (v1)

- Auto-editing other repos' files / triggering redeploys.
- Pricing/revenue (that's SableRevenueLedger; we expose the join key only).
- A web UI for onboarding (CLI-first per the operator's ask).
- Replacing `roster.yaml` / `composeAccounts.ts` as readers (Phase 2 SSOT consolidation).

## 11. Test strategy

- Schema: migration up/down parity (sqlite + the pg literal-head bump), CRUD round-trips.
- Pure core: `status` matrix tests — for each service, a fixture with/without each required input
  asserts the exact ✅/❌/⚠️ set and the blocking-exit-code. Scaffold templates: never-clobber test.
- CLI: in-memory sable.db + a tmp `SABLE_HOME` fs seam; `apply --dry-run` golden-output test;
  audit-log stamping test.
- Backfill: run against a seeded fixture mirroring TIG, assert no duplication and truthful gaps.

## 12. QA log

**Round 1 (2026-06-07, adversarial subagent vs. real code) — verdict BLOCK → fixed.** All findings
verified against source; resolved in this revision:
- **T1-1 (FK ordering, BLOCKING):** SQLite enforces FKs (`engine.py:59` `PRAGMA foreign_keys=ON`), so
  `onboard init` would hard-fail for net-new clients. → §1.0 draft-org rule (init upserts a draft org
  first; FKs kept).
- **T1-2 (false "no 3rd writer" claim, BLOCKING):** `upsert_prospect_org` stamps `inactive` + $0.50
  prospect cap; reusing it for a client is a bug. → §1.0/§5 add `upsert_client_org()` (honest new
  sibling writer).
- **T1-3 (data-exposure, BLOCKING):** client PII + entitlement tier/cost are ops-only. → §1.6 ops-only
  wall + proactive `data-exposure.test.ts` banned-field registration (Chunk 1).
- **T2-1 (taxonomy drift):** §2 keys weren't the canonical `{pulse,cm,engage,pairwise}` modules. →
  reframed as SKU vocabulary with a `module` column; split the wrong `autocm/pulse` key into `cm`+`pulse`;
  added `engage_bot`/`pairwise`.
- **T2-2 (ledger collision):** overstated "the join the ledger already wants." → §1.4 corrected +
  §9 ★ coordination decision (client_intake↔D9 client, org_entitlements↔D14 plan_module; needs sign-off).
- **T2-3 (graduate semantics):** `graduate_prospect` touches `prospect_scores`, not the org. →
  renamed to `onboard activate` (sets `orgs.status='active'`), decoupled from graduation.
- **T2-4 (CLI_REFERENCE baseline red):** doc says "71 migrations" vs `len==72`. → Chunk 1 fixes 71→73.
- **T2-5 (tickers shape):** scaffold must emit nested `tickers.appropriate`. → §1.5 corrected.
- **T3-1/T3-2 (alembic path + SEQUENCE_TABLES):** → §1 migration-contract specifics added.
- **Completeness (`org config set` is click-bound):** → §5 step 3 names the `validate_org_config`
  extraction.

**Round 2 (2026-06-07, fresh adversarial subagent) — verdict SHIP-WITH-FIXES → fixed; loop converged.**
Re-verified all round-1 fixes correct against code EXCEPT it caught that round-1's own T2-4 was stale:
- **T2-F1:** CLI_REFERENCE already says "72 migrations" and `test_doc_parity` is GREEN (round-1's
  "71/RED" was wrong). → corrected §1/§8 to "bump 72→73".
- **T2-F2:** §1.6 named only the static `data-exposure.test.ts`; the real guard is
  `data-exposure-live.test.ts` (runs `assembleClientData()`). → register banned columns in BOTH.
- **T2-F3:** "D9 `client` merges into `client_intake`" is incompatible — D9's `org_id` is nullable by
  design (prospect quotes) but `client_intake.org_id` is a NOT-NULL PK FK. → §9 now states the caveat
  + the specific thing to resolve with the operator.
- Tier-3: noted `_TEXT_PK_COLUMNS` is not needed; retagged `tracking` SKU `module=(product)`; noted
  `onboard init` requires `SABLE_OPERATOR_ID`.
Round 2 confirmed **no Tier-1**, and `org_entitlements`/`client_accounts` UNIQUE constraints, `apply`
idempotency, `--dry-run` read-only path, audit-stamping, and the `checkin`/`cult_grader`/`kol`/`compose`
required-inputs all SOUND against the real tools. **Loop converged** — remaining open items are genuine
cross-initiative decisions (the §9 ★ ledger contract) requiring operator sign-off, not code defects.

---

### Chunk 1 — IMPLEMENTED + QA-converged (2026-06-07)

Migration 073 (4 tables) + full 7-place contract (`connection._MIGRATIONS`, `migrate_pg`
TABLE_LOAD_ORDER+SEQUENCE_TABLES, `schema.py`, Alembic `c7d8e9f0a073`, head-asserts 72→73,
CLI_REFERENCE 72→73) + `db/orgs.upsert_client_org` + `db/onboarding.py` CRUD + `tests/db/test_onboarding.py`
+ banned-column registration in BOTH SableWeb data-exposure tests. **Green:** `tests/db/` 958 passed,
broad org/cost/cli/migration sweep 515 passed, onboarding 15 passed, SableWeb data-exposure 17 passed,
single Alembic head verified. **§9 ★ ledger decision RESOLVED** (operator: onboarding owns it; §0.1(5)).

**Adversarial QA round (fresh subagent) — SHIP-WITH-FIXES → fixed:**
- **T2-1 (prospect-cap leak):** the prospect→client flip left the auto `$0.50` cap in `config_json`,
  which `cost.get_org_cost_cap` reads as a LIVE cap (silently throttling a paying client — the exact
  thing the docstring promised not to do). → `upsert_client_org` now `pop`s the cap ONLY on a
  prospect→client flip (an existing client's operator-set cap is preserved); added two regression tests
  (cap-gone-on-flip + cap-preserved-on-client-reapply) — the original flip test passed for the wrong
  reason (never asserted the cap).
- **T3-1:** `add_account` re-add full-replaced metadata → COALESCE-preserve `display_name`/`bio`/`notes`
  (a role correction no longer nulls a bio); test added.
- **T3-2:** added `client_accounts`/`client_docs` to both data-exposure ban sets for symmetry.
- QA confirmed: 3-way column parity (SQL≡Alembic≡schema.py), FK load-order, schema-parity + migrate_pg
  coverage guards green, injection gate airtight (allowlist before SQL build), CompatRow access correct.

---

### Chunk 2 — IMPLEMENTED + QA-converged (2026-06-07)

Pure core under `sable_platform/onboarding/`: `requirements.py` (the §2 service→required-input matrix,
SKU keys tagged with their Suite module), `status.py` (`Evidence`→`compute_status`→`render`, entitlement-
driven — an input is flagged MISSING only when an ACTIVE service needs it), `scaffold.py` (brief.md /
guardrails.yaml [Slopper-loadable nested-tickers shape] / bios.md / voice/ templates, never-clobber).
**Pure** — no DB/network/fs except scaffold's injected base dir. `tests/onboarding/` **22 passed**; smoke-
render matches the §4 mock exactly (missing-first, chase line, provisioning checklist).

**Adversarial QA round (fresh subagent) — SHIP-WITH-FIXES → fixed:**
- **T2-1:** `compute_status` did a bare `e["service_key"]` → KeyError on a partial entitlement row. →
  filter to rows with a `service_key` (drop junk, no crash); test added.
- **T2-2:** `_truthy` claimed to "mirror" `client_checkin_loop._coerce_bool` but didn't (numeric `1`
  read as off). → honor bool/numeric/string-set; numeric-`1` test added (parity with the checkin loop).
- **T3 (folded in):** role-aware account selection (a `contact` no longer masquerades as the official
  handle/intake group; official preferred), label padding widened, voice-doc filename collision dedup.
- QA verified key-name alignment with the real mig-073 columns + `orgs.config_json` checkin keys, purity,
  and the guardrails template `yaml.safe_load`s into the exact shape `org_context.py` requires.

---

### Chunks 3-4 — IMPLEMENTED + QA-converged (2026-06-07)

`cli/onboarding_cmds.py` — the full `onboard` group: `init` (draft org + intake + scaffold),
`set` (intake-field vs config-key routing), `account add/list/rm`, `doc add/list/rm`,
`service add/rm/list/catalog`, `status` (the §4 report, nonzero exit when blocking), `activate`,
`apply` (`--dry-run`/`--force`; activates the org + projects canonical twitter/discord from the
registry + derives checkin + scaffolds + emits the cross-repo checklist). Registered in `main.py`
(requires `SABLE_OPERATOR_ID`; every mutation audit-stamped). The config validator was EXTRACTED to
`db/orgs.validate_org_config`/`set_org_config` and `org config set` refactored to share it (no drift).
`tests/cli/test_onboarding_cmds.py` + the broad CLI+db sweep **226 passed**; E2E smoke matches §4.

**Adversarial QA round (fresh subagent) — SHIP-WITH-FIXES → fixed:**
- **T2-1:** `--from-prospect` was a no-op (re-introducing the handle re-entry the project kills). →
  `init --from-prospect` now seeds `client_accounts` from the prospect's `twitter_handle` +
  `config_json.discord_guild_id`/`discord_server_id`; test asserts the carried-over handle satisfies a
  twitter-needing service with no re-entry.
- **T3-1:** validation duplication collapsed (`org config set` now calls the shared `set_org_config`;
  inline copy deleted).
- **T3-2:** `account/doc/service rm` now reject a nonexistent org (no false-success); test added.
- QA confirmed: `--dry-run` writes nothing (no row/audit/scaffold change), `--force` overrides blocking,
  handle projection correct, operator gate fires, malformed roster/guardrails YAML degrades, no injection.

---

### Chunks 5-6 — IMPLEMENTED + QA-converged (2026-06-07) — PLAN COMPLETE

`scripts/backfill_intake.py` (seed intake + accounts + inferred `reply_assist` entitlement for live
clients from `orgs`+`relay_clients`, idempotent, `--dry-run`, skips prospects/inactive, degrades with no
relay table) + `tests/db/test_backfill_intake.py`. `operator checklist` command (emit-only new-operator
runbook: allowlist entry [shape matches SableWeb `OperatorEntrySchema`], `SABLE_OPERATOR_ID`, adapter
paths, persona/compose grants — never writes repos) registered in `main.py`. Docs:
`docs/ONBOARDING_RUNBOOK.md` (executable client + operator runbook) + `CLIENT_LIFECYCLE.md` pointer +
CLAUDE.md (CLI line + key-files) + CLI_REFERENCE `onboard` section. Broad sweep **1197 passed, 3 skipped**.

**Adversarial QA round (fresh subagent) — SHIP-WITH-FIXES → fixed:**
- **T2-1:** plan §7 over-promised backfill sources (`roster.yaml`/`composeAccounts.ts`). → §7 corrected to
  the real v1 sources (`orgs`+`relay_clients`); noted personas are read LIVE by `status` (no seeding
  needed) and composeAccounts seeding is a Phase-2 non-goal. QA verified the operator-checklist allowlist
  shape matches SableWeb's real schema, the `relay_clients.enabled` proxy is sound, dry-run is inert, and
  every runbook command/flag exists.
- **T3-1/T3-3:** backfill now skips a malformed-`config_json` row instead of halting the batch; operator
  checklist emits the allowlist line via `json.dumps` so the email key is escaped.

---

## ✅ STATUS: PLAN FULLY IMPLEMENTED (2026-06-07)
All 6 chunks built + adversarial-QA-converged + tested. Migration 073 live in the chain (head 74).
**Phase 2 (the §9 open items) is now ALSO DONE + QA-converged** — DB-backed allowlist (P1, mig 075),
entitlement enforcement (P2, dormant by default), composeAccounts SSOT (P3). See
`docs/ONBOARDING_PHASE2_PLAN.md`. Ready to run the backfill on the production sable.db and start
onboarding a real client through the CLI. (Entitlement enforcement stays OFF until the prod backfill +
`entitlements preflight` clears; the DB allowlist + composeAccounts SSOT are behavior-neutral until used.)
