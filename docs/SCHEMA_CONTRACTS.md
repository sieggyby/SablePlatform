# Schema Contracts

Canonical reference for cross-suite data contracts in sable.db. Any change to
these contracts requires updating this document and notifying downstream consumers.

---

## Entity Status Values

| Status | Meaning | Display (SableWeb) |
|--------|---------|---------------------|
| `candidate` | Prospect entity from Lead Identifier | Shown |
| `confirmed` | Live entity, fully tracked | Shown |
| `archived` | Retired, no longer tracked | Hidden |

**SableWeb filter:** `WHERE status != 'archived'`

Adding a new status value requires:
1. Update this table
2. File a SableWeb TODO for filter review

---

## Prospect Tiers

| Tier | Composite Score | Action |
|------|----------------|--------|
| `Tier 1` | >= 0.70 | pursue |
| `Tier 2` | >= 0.55 | monitor |
| `Tier 3` | < 0.55 | pass (filtered from triage) |

Thresholds defined in `sable_platform/contracts/leads.py` as `PURSUE_THRESHOLD` / `MONITOR_THRESHOLD`.

### Prospect Lifecycle

| Column | Set by | Meaning |
|--------|--------|---------|
| `graduated_at` | `sable-platform org graduate <id>` | Prospect converted to active client |
| `rejected_at` | `sable-platform org reject <id>` | Prospect rejected (bad fit, etc.) |

- Default queries (`list_prospect_scores`) exclude graduated and rejected rows.
- Pass `include_graduated=True` or `include_rejected=True` to include them.
- A prospect can technically have both `graduated_at` and `rejected_at` set (e.g., graduated then later dropped). Each operation only checks its own column's NULL state.
- Rejection reason is stored in `audit_log` (action=`prospect_rejected`), not on the row.

### prospect_scores.org_id — Prospect Project Identifier

`prospect_scores.org_id` stores the **prospect's project_id** (the external crypto community
evaluated by Lead Identifier), **not** the Sable client org_id. The column name follows SQLite
FK conventions but is semantically a prospect identifier.

- `graduate_prospect(conn, project_id)` and `reject_prospect(conn, project_id)` treat it as a project_id.
- `list_prospect_scores()` has no Sable client filter — it returns all prospects globally (single-operator assumption).
- Do **not** use `prospect_scores.org_id` to filter by Sable client. Use `orgs.org_id` for that.
- If multi-tenant support is added, a migration will be needed to add a `client_org_id` column.

---

## Prospect Dimensions (5 canonical)

| Dimension | Source Field | Transform |
|-----------|-------------|-----------|
| `community_health` | `community_gap` | `1.0 - gap` |
| `language_signal` | `conversation_gap` | `1.0 - gap` |
| `growth_trajectory` | `tge_proximity` | passthrough |
| `engagement_quality` | `engagement_gap` | `1.0 - gap` |
| `sable_fit` | `composite` | passthrough (placeholder) |

---

## Cost Model Prefixes

| Prefix | Source | Notes |
|--------|--------|-------|
| `claude-*` | Anthropic API | Used by Cult Grader, Slopper |
| `socialdata` | SocialData API | Used by SableTracking |
| `replicate` | Replicate | Used by Slopper (image/video) |
| `elevenlabs` | ElevenLabs | Used by Slopper (TTS) |

`cost.py:log_cost()` accepts any model string — no validation.

---

## Artifact Types

| artifact_type | Source | Notes |
|---------------|--------|-------|
| `pulse_report` | Slopper | Pulse snapshot report |
| `meta_report` | Slopper | Meta analysis report |
| `discord_playbook` | Cult Grader | Discord playbook |
| `twitter_strategy_brief` | Cult Grader | Twitter strategy brief |
| `lead_identifier_output` | Lead Identifier | Lead scoring output |
| `content_meme` | Slopper (planned) | Meme content artifact |
| `content_clip` | Slopper (planned) | Video clip artifact |

`content_tweet` is deferred — Slopper writes text to stdout, not a file.

---

## Outcome Types

| outcome_type | Source | metric_name Convention |
|-------------|--------|------------------------|
| (diagnostic types) | Cult Grader | varies |
| `content_performance` | Slopper (planned) | `engagement_rate_{content_type}` |

Slopper outcomes use `recorded_by='pulse_outcomes'`.

---

## Alert Severity & Status

| Severity | Usage |
|----------|-------|
| `info` | Low-priority notifications (e.g., unclaimed actions) |
| `warning` | Requires attention (e.g., stale tracking, decay) |
| `critical` | Urgent action needed (e.g., bridge decay, high-risk member) |

| Status | Meaning |
|--------|---------|
| `new` | Freshly created, blocks dedup |
| `acknowledged` | Operator has seen it, still blocks dedup |
| `resolved` | Closed, allows re-alerting on same dedup_key |

---

## Decay Risk Tiers

| Tier | Score Range | Alert |
|------|-------------|-------|
| `low` | < 0.4 | None |
| `medium` | 0.4–0.6 | None |
| `high` | 0.6–0.8 | Warning |
| `critical` | >= 0.8 | Critical (if structurally important tag) |

---

## Interaction Types

| Type | Meaning |
|------|---------|
| `reply` | Direct reply to another handle |
| `mention` | @ mention in a post |
| `co_mention` | Both handles mentioned in the same post |

---

## SableTracking Metadata Schema

See `sable_platform/contracts/tracking.py` for the canonical `TrackingMetadata` Pydantic model.
17 fields, versioned via `schema_version`. Adding a field requires bumping the version.
