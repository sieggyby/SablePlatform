# Alert Triage API MVP

This document primes operators on the planned first API slice for SablePlatform.

It is intentionally small. The goal is to support trusted operator workflows around alerts without exposing spendful or tenant-ambiguous capabilities.

## What This MVP Lets You Do

With an owner-issued token, an operator will be able to:

- list alerts for orgs that token is allowed to access
- acknowledge an alert when it has been seen or triaged
- resolve an alert when the issue is handled

This is enough to support:

- Claude/Codex workflows that review open alerts and suggest next steps
- custom alert inboxes or lightweight dashboards
- internal Slack-style triage tooling
- operator handoff flows where one system reads alerts and another marks them handled

## Planned Endpoints

- `GET /v1/orgs/{org_id}/alerts`
- `POST /v1/alerts/{alert_id}/acknowledge`
- `POST /v1/alerts/{alert_id}/resolve`

The read path is org-scoped. The write paths are resource-scoped, but the server must verify that the target alert belongs to an org the token is allowed to touch before any action succeeds.

## What This MVP Does Not Do

This slice will not:

- trigger workflows
- trigger Cult Grader, Lead Identifier, or any other spendful adapter
- expose prospect pipeline APIs
- expose artifact, playbook, notes, actions, or outcomes APIs yet
- provide public self-serve access

That keeps the first API boring, low-risk, and useful.

## Access Model

- tokens are issued by the owner only
- initial deployment is private-network-first
- tokens are scoped to allowed orgs
- tokens support only `read_only` and `write_safe` for this MVP
- conservative rate limits apply per token and per IP

Operators should expect this API to be treated like a privileged internal tool, not a public integration surface.

## Safety Model

Every mutation should be:

- attributable to the approved operator identity
- attributable to the specific API token used
- org-scope checked before execution
- rate-limited
- idempotent where practical
- written into the audit trail

The point is not just convenience. The point is safe convenience.

## Good Operator Uses

Good first uses for this MVP:

- a morning alert review assistant that fetches open alerts for one org
- a Claude/Codex helper that summarizes alerts and recommends whether to acknowledge or resolve
- a simple internal dashboard that shows alert state across a small set of allowed orgs
- a handoff tool that marks alerts resolved after an operator finishes follow-up work

## Bad Uses

This MVP is a poor fit for:

- anything that needs prospect data
- anything that needs write access beyond alert triage
- client-facing integrations
- automation that should be able to spend money or fan out across orgs

## Why This Slice Comes First

It gives operators real value while forcing the right foundation work:

- token auth
- org scope enforcement
- resource ownership checks
- request-scoped audit attribution
- rate limiting
- thin HTTP handlers over shared domain logic

If this slice works well, the same foundation can later support notes, actions, outcomes, artifact reads, and eventually more selective client-facing APIs.

## Current Status

**Shipped 2026-05-12** as a private-network alert-triage API. The token/auth/
rate-limit/ownership-check spine is in place and reusable for later phases
(Phase 1b broader reads, Phase 2 safe writes). See `AUDIT_HISTORY.md`
§ SP-API-MVP. For follow-up work, see [TODO_API.md](/Users/sieggy/Projects/SablePlatform/TODO_API.md).

### How to use it (operator quickstart)

```bash
# Issue a token (owner runs this; SABLE_OPERATOR_ID identifies the owner)
export SABLE_OPERATOR_ID=you
sable-platform api-token issue \
    --label tig-triage-bot \
    --operator triage_bot \
    --orgs tig \
    --scopes read_only,write_safe \
    --expires-in-days 90
# -> prints token_id and one-time secret. Save the secret immediately.

# Start the server (loopback by default)
sable-platform api-serve --port 8766

# Use it
curl -H "Authorization: Bearer $SECRET" http://127.0.0.1:8766/v1/orgs/tig/alerts
curl -X POST -H "Authorization: Bearer $SECRET" \
    http://127.0.0.1:8766/v1/alerts/$ALERT_ID/acknowledge
```

To expose publicly, front the server with an authenticated reverse proxy or
Tailscale. The server refuses to bind a non-loopback interface without
`--public` to make this an explicit decision.
