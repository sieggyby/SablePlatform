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

No open platform items.

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
