# ONBOARDING_RUNBOOK.md — new client & new operator

The executable runbook. The design + rationale live in `docs/CLIENT_ONBOARDING_PLAN.md`; the
lifecycle map in `docs/CLIENT_LIFECYCLE.md`. This is the step list. Everything client-side is
driven by `sable-platform onboard …` (requires `SABLE_OPERATOR_ID`).

## What changed vs. the old process
Onboarding used to be ~10 implicit manual steps across 4 repos with no single source of truth
(the client's Twitter handle re-entered in 4 places, etc.). Now a structured **intake manifest**
(SablePlatform mig 073, OPS-ONLY tables) is the SSOT; `onboard status` tells you exactly what's
still missing (entitlement-driven — only flags inputs a service the client is buying needs), and
`onboard apply` reconciles everything inside SablePlatform + prints a copy-paste checklist for the
cross-repo bits that need a redeploy.

---

## ⚠️ Onboarding an EXISTING live client (e.g. TIG, RobotMoney) — populate-only

For an org that is ALREADY active and serving (not a fresh prospect), use the **populate-only**
path and do NOT run `apply`/`init`/`activate`:
- ✅ SAFE (writes only the new mig-073 tables, never the live `orgs` row): `scripts/backfill_intake.py`,
  `onboard service add`, `onboard account add`, `onboard doc add`, `onboard set <intake-field>`,
  `onboard status`. ⚠️ NB: `onboard set` is dual-purpose — `set <intake-field>` (email/telegram/
  website/notes) is safe, but `set <config-field>` (sector/stage/cost-cap/checkin_enabled) writes
  the live `orgs.config_json` (validated merge, additive — but it IS a live-row write).
- ⛔ AVOID on a live org: `onboard apply` (writes the live `orgs` row — projects handles, can flip
  `checkin_enabled`, stamps `org_type:'client'`, sets `manifest_status='applied'`), `onboard init`
  (intended for a NEW org; would re-stamp `display_name`), `onboard activate` (no-op on an active org).
  Handles are now FILL-ONLY (apply can't overwrite a set handle — audit T1-A), but the other writes
  still mutate shared live config. If you must reconcile, run `onboard apply --dry-run` first and diff.
- **Entitlement caveat:** adding `org_entitlements` rows is inert today (`ENTITLEMENT_ENFORCEMENT`
  unset) but it REMOVES the "0-rows → always allow" airbag for that org — so before EVER flipping the
  flag, declare the COMPLETE in-use service set per org and run `entitlements preflight` to "no gaps."

## A. New client go-live

```bash
# 0. (optional) backfill an EXISTING live client so `status` is truthful immediately
python scripts/backfill_intake.py --dry-run        # then without --dry-run

# 1. Start the manifest (creates a DRAFT org + scaffolds ~/.sable/orgs/<org>/)
sable-platform onboard init <org> --name "Display Name" [--from-prospect]
#    --from-prospect carries a sable-audit prospect's twitter/discord into the registry.

# 2. Record what services they're buying (this DRIVES the required-input checklist)
sable-platform onboard service catalog                       # see the SKUs + what each needs
sable-platform onboard service add <org> reply_assist
sable-platform onboard service add <org> tracking
#    … cult_grader / kol / compose / checkin / audit / engage_bot / cm / pulse / client_portal …

# 3. Capture the client's inputs (chase down whatever `status` flags)
sable-platform onboard account add <org> --platform twitter  --handle @Client   --role official
sable-platform onboard account add <org> --platform twitter  --handle @Founder  --role founder --controlled
sable-platform onboard account add <org> --platform discord  --handle <guild_id> --role community
sable-platform onboard set     <org> primary_contact_email ceo@client.io
sable-platform onboard set     <org> client_telegram_chat_id -- -5050566880     # note the `--`
sable-platform onboard doc add <org> --kind explainer --label "Litepaper" --location https://...
#    Fill the scaffolded prose: ~/.sable/orgs/<org>/brief.md + guardrails.yaml (+ voice/<handle>.md
#    per controlled account). Assign a reply persona in ~/.sable/roster.yaml (org: <org>).

# 4. Check readiness — repeat until no blocking items
sable-platform onboard status <org>                          # ❌ = chase it down; exit!=0 while blocking

# 5. Go live: activate + project handles + derive checkin + scaffold + print the cross-repo checklist
sable-platform onboard apply <org> --dry-run                 # preview
sable-platform onboard apply <org>

# 6. Do the cross-repo checklist `apply` printed (each needs a redeploy/restart):
#    - SableWeb allowlist.json  (client / client_ops entry) → deploy CODE before ALLOWLIST_JSON
#    - SableWeb composeAccounts.ts (managed accounts, if `compose`)
#    - SableTracking .env GROUP_TO_CLIENT_JSON / DISCORD_GUILD_TO_CLIENT (if `tracking`) → restart
#    - sable-platform relay enable <org>  (if reply_assist)

# 7. First data sync + QA the portal
sable-platform workflow run weekly_client_loop --org <org>
#    Log in as the client email on sable.tools and confirm /client renders.
```

`apply` is idempotent — safe to re-run after you add a missing input.

---

## B. New operator

Operator grants live in file+redeploy locations (allowlist, persona/compose grants), so this is a
**checklist emitter** — it prints the exact edits; it does not write other repos.

```bash
sable-platform operator checklist <operator_id> --email op@sable.io --role operator \
  [--orgs tig,solstitch] [--persona @tigintern] [--compose-as @tigfoundation]
```
Then apply what it prints:
1. **SableWeb `allowlist.json`** — the `{role, operatorId[, assignedOrgs]}` entry. Deploy the CODE
   before the `ALLOWLIST_JSON` env (avoid the lockout trap), then redeploy.
2. **Shell:** `export SABLE_OPERATOR_ID=<operator_id>` (must match the allowlist `operatorId`).
3. **Adapter paths:** `SABLE_TRACKING_PATH` / `SABLE_SLOPPER_PATH` / `SABLE_CULT_GRADER_PATH` /
   `SABLE_LEAD_IDENTIFIER_PATH`.
4. **(if reply/compose)** `ops-identity.ts` persona grant + `composeAccounts.ts` compose-as grant
   (+ redeploy).

> A DB-backed allowlist (so operator/client access stops being a file+redeploy) is a separate,
> security-sensitive effort — see `CLIENT_ONBOARDING_PLAN.md` §9.

---

## Notes
- The intake tables (`client_intake` / `client_accounts` / `client_docs` / `org_entitlements`) are
  **OPS-ONLY** — they hold client PII + commercial state and must never cross the SableWeb `/client`
  wall (the `data-exposure` tests ban their columns).
- Entitlements are STATE only (what the client gets), never money — the SableRevenueLedger references
  `org_entitlements` and owns all billing.
