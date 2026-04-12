# SablePlatform API Plan

Operator-first API plan for SablePlatform. The goal is not to expose every internal capability. The goal is to let trusted operators and advanced internal users work against the canonical system of record without scraping CLI output or touching the database directly.

---

## Product Stance

### What this API is for

- Operator automation.
- Claude Code / Codex / custom script integrations for trusted users.
- Safe reads and safe writes against canonical SablePlatform objects.
- Gradual path toward selected client-facing API methods later.

### What this API is not for

- Broad public self-serve access.
- Arbitrary workflow triggering by default.
- Direct downstream API spend by default.
- Replacing the workflow engine, audit log, or operator controls.

---

## Core Principles

1. **Owner-approved access only.**
   Every token or credential is created by and approved by the owner. No self-service token minting.

2. **Default deny for spend.**
   The default API surface must not trigger downstream AI/API spend.

3. **Safe reads and safe writes first.**
   Start with alerts, notes, artifacts, playbook reads, workflow status, actions, and outcomes. Add prospect pipeline access only after tenant ownership is explicit in the schema.

4. **Secondary approval for spendful operations.**
   If a spendful action is ever exposed, it must require a separate approval path and be heavily scoped.

5. **Conservative rate limiting.**
   Optimize for trust and abuse resistance, not max throughput.

6. **Audit everything.**
   Every API mutation should stamp actor identity, token identity, org scope, and request metadata into the audit trail.

7. **Do not promise org scoping where the schema does not support it yet.**
   Prospect pipeline access is desirable, but the current schema is still single-operator in important places. The API plan must not expose fake org-scoped prospect semantics before the data model is fixed.

8. **Authorize by resource ownership, not by path shape alone.**
   For any endpoint keyed by a global ID, resolve the owning org from the resource first, then enforce token scope, then perform the read or write.

9. **Keep HTTP handlers thin.**
   API routes should call shared domain helpers and persistence code. Do not fork business rules between the CLI and the API layer.

---

## Best First Use Cases

These are the operator use cases that look high-value and low-risk.

### Read-only / low-risk reads

- Read open alerts and recent alert history.
- Read latest artifacts for an org.
- Read playbook targets and playbook outcomes.
- Read workflow run status and recent workflow history.
- Read entity notes, key journeys, decay, centrality, and watchlist state.
- Read lead / prospect pipeline data only after tenant ownership is explicit in the schema.

### Safe writes

- Add entity notes.
- Record prospect feedback or lead dispositions only after prospect tenant ownership is explicit.
- Acknowledge or resolve alerts.
- Create and update actions.
- Create outcomes linked to actions.
- Write operator annotations on artifacts or playbook targets.

### Explicitly deferred from default API access

- Triggering Cult Grader diagnostics.
- Triggering Lead Identifier or other spendful adapters.
- Arbitrary workflow execution.
- Any endpoint that can create downstream billable activity without a second gate.
- Prospect endpoints that pretend to be org-scoped before a real `client_org_id` or equivalent ownership model exists.

### Tenant-boundary note

- The repo currently documents that `prospect_scores.org_id` is semantically a prospect `project_id`, not a Sable client org_id.
- Because of that, the initial API should treat prospect data as a blocked surface for org-scoped methods until the schema is hardened.
- If internal operators urgently need pipeline access before that migration, it should be an explicitly internal-only endpoint with no tenant-scoped promises and no client-facing contract.

---

## Permission Model

Use a small, explicit permission model.

### Suggested scopes

- `read_only`
  Can read org-scoped resources only.

- `write_safe`
  Can perform low-risk writes: notes, feedback, alert ack/resolve, actions, outcomes.

- `spend_request`
  Can request a spendful operation, but cannot execute it directly.

- `spend_execute`
  Reserved for owner-only or equivalent trusted approval path.

### Suggested token metadata

- token label
- operator identity
- allowed orgs
- allowed scopes
- created_at / created_by
- expires_at
- last_used_at
- enabled / revoked

### Recommended implementation detail

- Store token hashes, not raw tokens.
- Prefix tokens clearly, for example `sp_live_...`.
- Attribute every write to both:
  - operator identity
  - token identity / label
- For resource-ID endpoints, require an ownership lookup that maps the target object to an org before authorization succeeds.

### Request-scoped actor attribution

- Do not rely on process-wide `SABLE_OPERATOR_ID` alone for API-triggered writes.
- Each authenticated request should carry:
  - token identity
  - approved operator identity
  - request ID
  - allowed org scope snapshot
- Domain helpers that currently fall back to env-based operator identity should gain an explicit request actor path before API writes are exposed.
- Audit rows should preserve both:
  - the human/approved actor
  - the API credential used to perform the action

---

## Spend Authorization Model

If SablePlatform exposes any spendful action via API, do not expose a direct `POST /run-whatever` shape first.

Instead:

1. Operator creates a **spend request**.
2. Request is stored with:
   - org
   - requested action
   - estimated spend band
   - reason / note
   - requester identity
   - idempotency key
3. Owner reviews and approves or rejects.
4. Approval creates a short-lived, one-time authorization.
5. Only then can the action execute.

### Required safeguards

- approval TTL, for example 15 minutes
- one-time use approval token
- exact operation binding
- exact org binding
- optional max estimated spend binding
- audit row on request, approval, execution, rejection

### Good first spendful candidates, later

- trigger `lead_discovery`
- trigger `prospect_diagnostic_sync`
- trigger a targeted diagnostic refresh for a known org

### Not for early phases

- generic adapter execution
- unbounded batch operations
- anything that can fan out across many orgs from one request

---

## Rate Limiting

Be conservative on purpose.

### Suggested defaults

- `read_only`: 60 requests/minute per token
- `write_safe`: 20 requests/minute per token
- `spend_request`: 3 requests/hour per token
- `spend_execute`: owner-only, extremely low volume

### Additional controls

- per-IP limit in addition to per-token limit
- burst limit lower than steady-state assumptions
- hard ceiling on concurrent requests per token
- reject disabled or expired tokens before touching application logic

### Fail-safe behavior

- return 429 with a clear retry hint
- log rate-limit hits
- emit metrics for repeated rate-limit pressure

---

## API Surface MVP

Keep the surface business-object shaped, not table-shaped.

### Recommended thin slice: private alert triage API

This is the smallest API that is worth building even if adoption starts small.

#### Why this slice

- alerts are already a real daily operator surface
- alert reads and triage do not trigger downstream spend
- alert resources already fit the repo's org-scoped model better than prospect data
- this slice forces the right foundations:
  - token auth
  - org scope enforcement
  - resource-ownership checks on global IDs
  - request-scoped actor attribution
  - conservative rate limiting
  - safe-write audit coverage
- it is immediately useful for Claude/Codex operator workflows, custom dashboards, and alert inbox tooling

#### Thin-slice endpoints

- `GET /v1/orgs/{org_id}/alerts`
- `POST /v1/alerts/{alert_id}/acknowledge`
- `POST /v1/alerts/{alert_id}/resolve`

#### Thin-slice requirements

- owner-only token issuance and revocation, likely via CLI first
- `read_only` and `write_safe` token support only
- request-scoped actor propagation into audit writes
- resource ownership lookup on `alert_id` before authorization
- per-token and per-IP rate limiting
- structured error model
- OpenAPI spec for just these routes
- idempotent alert triage behavior

#### Explicitly not in the thin slice

- prospect reads or writes
- artifact APIs
- playbook APIs
- notes
- actions / outcomes
- workflow-run APIs
- any spend request or spend execution path

#### What this unlocks next

- broader org-scoped read APIs using the same auth/rate-limit/audit spine
- safe writes for notes, actions, and outcomes
- MCP wrapper for alert triage
- selective client-facing alert summary APIs later

Operator-facing summary for this slice: [docs/API_ALERT_TRIAGE_MVP.md](/Users/sieggy/Projects/SablePlatform/docs/API_ALERT_TRIAGE_MVP.md)

### Phase 1b: Read-heavy operator API

- `GET /v1/orgs/{org_id}/alerts`
- `GET /v1/orgs/{org_id}/artifacts`
- `GET /v1/orgs/{org_id}/playbook/targets`
- `GET /v1/orgs/{org_id}/playbook/outcomes`
- `GET /v1/orgs/{org_id}/entities`
- `GET /v1/workflows/runs/{run_id}`
- `GET /v1/orgs/{org_id}/workflow-runs`

### Phase 2: Safe writes

- `POST /v1/entities/{entity_id}/notes`
- `POST /v1/alerts/{alert_id}/acknowledge`
- `POST /v1/alerts/{alert_id}/resolve`
- `POST /v1/actions`
- `POST /v1/outcomes`

### Phase 2.5: Tenant-model hardening for prospect APIs

- add `client_org_id` or equivalent canonical ownership field for prospect records
- backfill existing prospect data safely
- document the contract in schema docs and API docs
- only then add:
  - `GET /v1/orgs/{org_id}/prospects`
  - `GET /v1/orgs/{org_id}/prospect-pipeline`
  - `POST /v1/prospects/{project_id}/feedback`

### Phase 3: Approval-gated spend requests

- `POST /v1/spend-requests`
- `GET /v1/spend-requests/{request_id}`
- `POST /v1/spend-requests/{request_id}/approve`
- `POST /v1/spend-requests/{request_id}/reject`
- `POST /v1/spend-requests/{request_id}/execute`

### Phase 4: Narrow client-facing read APIs

- read-only artifact access
- read-only playbook target access
- read-only alert summaries
- read-only workflow / freshness status

Do not expose raw internal tables or unrestricted search first.

---

## Recommended Deployment Shape

### Initial deployment

- Run the API on the existing VPS.
- Keep it private initially:
  - Tailscale, VPN, or reverse-proxy allowlist preferred
  - public internet exposure only after auth + rate limiting + logs are proven
- Reuse existing Postgres / runtime environment.

### Why this is attractive

- near-zero incremental infra cost
- minimal operational sprawl
- easiest path to internal adoption

### Added infra cost

- likely `$0` incremental if it stays on the existing VPS
- extra cost appears only if traffic, isolation, or observability needs force a bigger box or managed services

---

## Suggested Implementation Phases

### Phase 0: Plan and contracts

- define token model
- define scopes
- define API object schemas
- define audit attribution requirements
- define rate-limit policy
- define which endpoints are blocked until tenant ownership exists
- define resource-to-org ownership rules for every global-ID route
- define canonical persistence contracts for any new write shape before exposing the endpoint

### Phase 1a: Thin-slice private alert triage API

- authenticated HTTP service
- owner-issued tokens only
- `GET /v1/orgs/{org_id}/alerts`
- `POST /v1/alerts/{alert_id}/acknowledge`
- `POST /v1/alerts/{alert_id}/resolve`
- request-scoped actor propagation into alert/audit writes
- resource ownership lookup before serving `alert_id` routes
- rate limiting, structured errors, request logging, and OpenAPI for this slice only

### Phase 1b: Broader internal read-only API

- authenticated HTTP service
- org-scoped reads only
- OpenAPI spec
- structured error model
- metrics and request logging
- resource ownership lookup before serving any global-ID endpoint

### Phase 2: Safe-write operator API

- notes
- alert triage
- actions / outcomes
- idempotency keys for writes
- explicit request actor propagation into audit/workflow storage
- no new write endpoint without a named canonical table/contract

### Phase 2.5: Prospect tenant hardening

- add canonical client ownership for prospect data
- migrate and backfill SQLite + Postgres
- then expose prospect reads/writes

### Phase 3: Spend request and approval flow

- new spend request object
- owner approval UI/CLI/API path
- one-time execution approval
- cost and audit instrumentation

### Phase 4: Agent-friendly integration

- MCP server or thin wrapper over the API
- operator prompt recipes / examples
- strong guardrails around spendful paths

### Phase 5: Selective client-facing API

- read-only methods first
- carefully selected writeback later
- only after tenant boundaries and support playbooks are mature

---

## Gradual Client-Facing Adoption

Client-facing API is plausible, but should come after operator API proves itself.

### Recommended ladder

1. **Internal operators only**
   Private network, manually issued tokens, read-heavy.

2. **Trusted agencies / advanced operators**
   Read-heavy plus safe writes.

3. **Selected clients, read-only**
   Artifacts, summaries, freshness, actions, outcomes.

4. **Selected client writeback**
   Notes, feedback, acceptance/rejection, outcome confirmation.

5. **Anything spendful**
   Last, opt-in, and approval-gated.

---

## Engineering Tasks

### New code areas likely needed

- HTTP API module
- token auth / scope middleware
- rate limiting layer
- API-facing request / response schemas
- shared service layer for API + CLI business operations
- audit attribution helpers
- spend request model and persistence
- token management CLI for owner-only issuance / revocation

### Likely schema additions

- `api_tokens`
- `api_token_org_scopes` or equivalent scoped config
- `api_request_log` only if existing logging is insufficient
- `spend_requests`
- optional `spend_approvals`

### Existing write surfaces to reuse first

- entity notes should continue to use the canonical notes persistence already in the repo
- alerts, actions, outcomes, playbook reads, and workflow-run reads should stay backed by existing domain helpers
- do not introduce a second storage path for those concepts just because the transport becomes HTTP

### New write concepts that need explicit contracts before implementation

- prospect feedback / lead disposition
- operator annotations on artifacts
- operator annotations on playbook targets

For each new concept above, define before implementation:

- canonical table name
- Pydantic contract shape
- idempotency behavior
- audit semantics
- cross-suite implications

Schema changes must follow the repo's dual-migration rule:
- SQLite migration file + `_MIGRATIONS` entry
- Alembic revision for Postgres

---

## Testing Requirements

### Must-have tests

- auth required / invalid token / revoked token
- org scope enforcement
- wrong-org access on every global-ID endpoint returns `403` or `404`
- rate limiting
- safe-write idempotency
- audit rows include token/operator identity
- spend request cannot execute without approval
- approval TTL expiration
- approval cannot be replayed
- read endpoints return empty results cleanly
- prospect endpoints stay disabled or unavailable until tenant ownership exists
- once prospect tenant ownership is added, tenant-leak tests prove org filtering is correct

### Good early parity tests

- docs reference only supported API scopes and approval model
- OpenAPI paths match implemented routes
- token scope matrix tests
- docs never advertise org-scoped prospect endpoints before the ownership migration lands
- route handlers call shared services rather than re-implementing business logic ad hoc

### Thin-slice acceptance tests

- list alerts returns only alerts for orgs in token scope
- acknowledging an alert is idempotent and auditable
- resolving an alert is idempotent and auditable
- `alert_id` from another org cannot be acknowledged or resolved
- rate-limit behavior is exercised on alert list and alert triage routes
- owner can revoke a token and immediately block further alert access

---

## Success Criteria

This API is worth keeping if it does the following:

- operators can build custom Claude/Codex flows without touching the DB
- safe reads and writes cover most daily triage work
- no unapproved spend can be triggered through the default surface
- every mutation is attributable and auditable
- rate limits are conservative enough that the API is boring to operate

For the thin slice specifically:

- one trusted operator can fetch and triage alerts entirely through the API
- the audit trail preserves both operator identity and token identity
- no new spendful capability is exposed
- no new tenant-boundary ambiguity is introduced

---

## Open Questions

- Should approvals live only in SablePlatform, or also require an out-of-band confirmation step?
- Should owner approvals happen via CLI first, or via a minimal admin API route?
- Is Tailscale/private-network-only enough for phase 1, or should public exposure be supported from day one?
- When client-facing API arrives, should it be a separate token type and hostname?
