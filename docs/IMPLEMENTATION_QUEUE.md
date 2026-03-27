# SablePlatform Implementation Queue

> Governing documents: `TODO.md`, `docs/TODO_product_review.md`, `CLAUDE.md`
> Session date: 2026-03-26
> Conductor: Sable ruthless implementation conductor

---

## Executive Summary

All P1, P2, and Feature items from `TODO.md` are implemented. All five Simplify items are
implemented. Three Next Round Features from `docs/TODO_product_review.md` (Alert Cooldown,
Discord Pulse Stale Guard, Workflow Config Versioning) are implemented. Test suite: 133 passing.

Remaining work is documentation housekeeping only:
- CLAUDE.md is stale (wrong counts, missing cooldown/version-check docs)
- TODO.md has ~9 unchecked items that are actually done
- `docs/TODO_product_review.md` Next Round Features still show as pending
- Queue / Log / Report docs did not exist

---

## Scope

### In Scope
- CLAUDE.md: document cooldown semantics, `--ignore-version-check`, correct current state
- TODO.md: mark all completed features and simplify items done
- `docs/TODO_product_review.md`: mark Next Round Features done
- Create IMPLEMENTATION_LOG.md and IMPLEMENTATION_REPORT.md

### Out of Scope
- No new features
- No behavior changes
- `onboard_client` spec-vs-implementation divergence: **documented in report, not changed**
  (spec says halt on tool failure; implementation captures and completes — divergence is
  deliberate and tests validate it; changing requires user direction)

### Deferred
- Deferred-1: Full Discord Pulse Threshold Alerts (waiting on business threshold decisions)
- Deferred-2: `_deliver()` decomposition into `_should_deliver` + `_dispatch`

---

## Queue Rules

- Each slice is a single-focus change
- No slice touches behavior
- All slices must leave 133 tests passing

---

## Implementation Slices

### S-01 — CLAUDE.md: document cooldown + version-check + fix current state
**Status:** `done`
**Purpose:** CLAUDE.md is missing documentation for two operator-facing features and has
wrong counts (says "40/40 tests passing" and "2 builtin workflows").
**Files:** `CLAUDE.md`
**Acceptance:** cooldown semantics documented, `--ignore-version-check` documented,
test count and workflow count accurate.

---

### S-02 — TODO.md: mark all completed items done
**Status:** `done`
**Purpose:** Four Feature items and five Simplify items are implemented but not marked ✅.
A stale TODO is actively misleading.
**Files:** `TODO.md`
**Acceptance:** All implemented items show ✅ with resolution notes.

---

### S-03 — docs/TODO_product_review.md: mark Next Round Features done
**Status:** `done`
**Purpose:** The three Next Round Features (Alert Cooldown, Discord Pulse Stale Guard,
Workflow Config Versioning) are implemented but the product review doc still lists them
as pending.
**Files:** `docs/TODO_product_review.md`
**Acceptance:** All three Next Round Features show ✅ with confirmation of tests added.

---

### S-04 — Create IMPLEMENTATION_LOG.md and IMPLEMENTATION_REPORT.md
**Status:** `done`
**Purpose:** Required conductor artifacts. Capture what was implemented, what tests were
added, known risks, and next-wave recommendations.
**Files:** `docs/IMPLEMENTATION_LOG.md`, `docs/IMPLEMENTATION_REPORT.md`
**Acceptance:** Both files exist with accurate, complete content.
