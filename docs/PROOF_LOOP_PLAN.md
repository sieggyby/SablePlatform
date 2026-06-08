# Sable Proof Loop Plan

## Purpose

The proof loop is the commercial spine of Sable: turn operator work into client-safe proof that Sable is moving the account.

This is not a generic analytics roadmap. The loop should answer, every week and every renewal cycle:

1. What did Sable notice?
2. What did Sable recommend?
3. What did Sable actually do?
4. What observable result followed?
5. How confident are we that the result is attributable, associated, or merely directional?
6. What should happen next?

If a feature does not strengthen one of those links, it is not proof-loop work.

## Evidence The Loop Already Exists

The suite already has most of the parts, but they are spread across repos:

- **SablePlatform** owns the canonical ledgers:
  - `actions` and `outcomes` for generic recommendation/result tracking.
  - `reply_suggestions` and `reply_outcomes` for assisted-reply proof.
  - `playbook_targets` and `playbook_outcomes` for diagnostic -> playbook -> measured-result loops.
  - `mod_slot_sessions` and `operator_work_events` for scale-of-work reporting.
  - `get_work_summary()` for the ops rollup, with replies counted from `reply_outcomes` as the single source of truth.
  - `get_campaign_outcomes()` for objective-aware reply-campaign proof, using matured fixed-age engagement readings only.
- **SableWeb** is the client/operator proof surface:
  - `/client` already has `ExecutiveSummary`, `RecentOutcomes`, `RecommendationOutcomes`, `WorkDelivered`, and QBR export plumbing.
  - `/ops` has `ScaleOfWorkReport`, reply-assist capture, campaign performance, and action visibility.
- **Sable_Slopper** owns the content/reply production and measurement instruments:
  - Reply-assist generates suggestions.
  - Posted-reply fixed-age snapshots backfill `reply_outcomes.engagement_json` at 24h.
  - Campaign outcome routes expose post rate, matured 24h engagement, and adoption rate.
  - Pulse outcomes can write content-performance rows into `sable.db outcomes`.
- **SableTracking** contributes the proof ledger for community content:
  - Content records with `outcome_type` sync into Platform `outcomes`.
  - Content items and contributors feed the client value narrative.
- **Cult Grader** creates the before/after context:
  - Diagnostic deltas, playbook targets/outcomes, decay, member movement, and health changes supply the "what changed" side of proof.

The loop is real. The weakness is not lack of components; it is that the components do not yet enforce one canonical proof taxonomy or one minimum viable weekly proof packet.

## Proof Taxonomy

Every proof item must be labeled as one of four types:

| Type | Meaning | Client-safe wording |
|---|---|---|
| **Attributable** | Directly tied to a Sable action or assisted post. | "Sable-assisted replies generated 14 posted replies; 9 have matured 24h readings." |
| **Associated** | A Sable action was followed by a relevant movement, but causality is not proven. | "After the playbook push, recurring participant share rose from X to Y." |
| **Directional** | Useful trend signal with confounders. | "Momentum improved week over week; market context may contribute." |
| **Self-reported** | Operator-declared work, not independently measured. | "Operators declared 18.5 coverage hours." |

Do not call directional or self-reported signals "impact." Do not use raw impressions as the main proof claim. Prefer fixed-age engagement, reply count, outcomes completed, client feedback, retained contributors, and matured campaign metrics.

## Minimum Weekly Proof Packet

Each active client should have one generated proof packet per week with these sections:

1. **Work Delivered**
   - Replies delivered from `reply_outcomes`.
   - Declared coverage hours from closed `mod_slot_sessions`.
   - Communities covered from declared watched chats.
   - Content items logged from SableTracking.
   - KOL outreach actions when KOL work is logged.

2. **Actions And Outcomes**
   - New recommendations.
   - Completed/skipped client feedback.
   - Completed Sable actions with `outcome_notes`.
   - Generic `outcomes` rows created by Platform, Tracking, Slopper pulse, or manual CLI.

3. **Campaign Proof**
   - Campaign objective.
   - Assignments, posted count, post rate.
   - Matured 24h engagement average and measured count.
   - Adoption rate: unedited variant use over posted outcomes.
   - Explicit caveat when no outcomes have matured yet.

4. **Community Movement**
   - Diagnostic deltas from the latest completed Cult Grader run.
   - Playbook target outcomes.
   - Member decay/reactivation changes.
   - Content contribution movement.

5. **Next Move**
   - One primary recommendation.
   - The evidence behind it.
   - The owner.
   - The expected proof signal for next week.

## Readiness Gate

Do not sell a client on "proof-driven growth" unless all of these are true for that client:

- At least one current diagnostic baseline exists and is synced to Platform.
- SableWeb `/client` renders `ExecutiveSummary` without mock-only dependencies.
- SableWeb `/client` renders `WorkDelivered` or a clear empty state.
- Reply-assist posted outcomes can be recorded from Web.
- The posted-reply fixed-age snapshot job is scheduled and writing 24h readings.
- `ScaleOfWorkReport` has nonzero data or a clear reason it does not.
- SableTracking content sync is current or explicitly marked stale.
- Client-visible proof excludes operator identities, costs, raw notes, and internal verdicts.
- Every proof claim has a taxonomy label: attributable, associated, directional, or self-reported.

## Current Gaps

1. **No canonical proof packet generator.**
   SableWeb has QBR export and proof components, but there is no weekly proof-packet builder that assembles the same fields every time.

2. **Reply proof depends on operator behavior.**
   Manual "mark posted" capture is the right primary path, but it needs a backstop via operator-tweet reconciliation.

3. **Fixed-age snapshots need operational monitoring.**
   The measurement loop depends on `sable quality snapshot-replies`. Missing or delayed snapshots should create an alert.

4. **Content and KOL work are not fully in the work ledger.**
   Mod slots and reply outcomes are covered. Slopper content work, SableTracking content items, and SableKOL outreach work need normalized work-event or outcome entries.

5. **Cult Grader trust gaps can poison proof.**
   Operator-handle leakage, stale member recommendations, and non-English cohort under-detection must be fixed before proof packets are used aggressively in sales or renewal narratives.

6. **BD feedback is not yet part of the suite proof loop.**
   Lead Identifier has calibration infrastructure, but real outcomes are still the gating input. Sales proof should include which diagnostics converted to meetings, proposals, and closes.

## Improved Build Order

### P0: Close The Reply Proof Loop

- Deploy SableWeb's posted-reply capture route and UI.
- Schedule `sable quality snapshot-replies` on production.
- Add a Platform alert check for posted replies older than 30h with empty `engagement_json`.
- Add a weekly ops report: generated suggestions, posted outcomes, matured readings, average fixed-age engagement, adoption rate.

Success condition: a posted assisted reply automatically becomes a client-safe measured proof item within 24-36 hours.

### P1: Create The Weekly Proof Packet

- Add a single proof-packet assembly path in SableWeb or SablePlatform.
- Inputs:
  - `get_work_summary()`
  - `list_outcomes()`
  - `get_campaign_outcomes()`
  - latest playbook outcomes
  - latest diagnostic delta
  - SableTracking content sync freshness
- Output:
  - Web client section
  - QBR/export text
  - operator call-prep brief

Success condition: weekly client update prep drops to reviewing/editing a generated packet, not hand-assembling evidence.

### P2: Normalize Work Sources

- Add normalized work events or outcomes for:
  - Slopper content shipped.
  - SableTracking content logged and outcome-tagged.
  - SableKOL outreach plans generated and candidate intel pulled.
  - Discord bot interventions when client-safe.
- Keep replies sourced only from `reply_outcomes`; do not mirror them into work events.

Success condition: the Scale-of-Work report can explain most operator labor without double-counting.

### P3: Add Proof Quality Controls

- Add deterministic proof-lint checks before a proof packet is marked client-safe:
  - No raw cost, operator identity, internal verdict, raw notes, or hidden allowlist fields.
  - No "caused" wording unless the proof type is attributable.
  - No raw impressions as the headline metric.
  - No stale Cult Grader member recommendation without recency annotation.
  - No named community member with `account_role != community`.
- Add a "proof confidence" footer to every packet.

Success condition: proof packets are safer than manual narrative writing, not just faster.

### P4: Connect Proof To Revenue

- Write BD lifecycle outcomes back into Platform:
  - meeting booked
  - proposal sent
  - closed
  - lost
  - renewal
  - expansion
- Pull those into Lead Identifier feedback calibration.
- Add a simple revenue proof dashboard:
  - prospects diagnosed
  - audits sent
  - meetings booked
  - closes
  - renewal risk
  - monthly recurring revenue under management

Success condition: Sable can prove not only client delivery value, but also which signals create sales.

## Repo-Specific Actions

### SablePlatform

- Add an alert check for stale/missing reply outcome snapshots.
- Add a proof-packet helper or contract if Web should not own all assembly.
- Extend sync-from-local once laptop-written `actions` and `outcomes` matter operationally.
- Add tests that `get_campaign_outcomes()` never counts `{}` engagement as zero.

### SableWeb

- Make the Proof of Value spine the default client narrative.
- Add a weekly proof-packet export next to QBR export.
- Promote Work Delivered out of "nice extra" status into the top summary when nonzero.
- Add UI copy that distinguishes attributable, associated, directional, and self-reported proof.

### Sable_Slopper

- Treat `sable quality snapshot-replies` as production infrastructure, not an analysis command.
- Add operator-tweet reconciliation as the backstop for missed "mark posted" clicks.
- Keep measured amplification scoped to fixed-age hard engagement and follow-through, never impression guarantees.

### SableTracking

- Ensure content records with outcomes sync reliably into Platform `outcomes`.
- Add a current/stale indicator for each client's latest content sync.
- Prefer outcome-tagged content counts over raw content volume in client proof.

### Sable_Cult_Grader

- Fix operator-handle exclusion before proof packets name community members.
- Add stale-member and language-cohort proof linting before rendering client narratives.
- Treat diagnostic movement as associated/directional unless tied to a specific Sable action.

### SableKOL

- Log KOL plan generation and candidate-intel pulls as work events or outcomes.
- Add a client-safe "outreach plan delivered" proof item, but keep draft/intel details ops-only.

## What Not To Build Yet

- Public self-serve analytics.
- Benchmark subscription products.
- A generalized proof API for clients.
- Automated posting.
- Claims of guaranteed reach, impressions, or growth.

The proof loop should first make the managed service easier to sell, deliver, and renew. SaaS-style access can come later if the proof packets repeatedly close and retain clients.
