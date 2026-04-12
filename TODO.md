# SablePlatform — Roadmap

For completed work, see [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

See CLAUDE.md for project architecture, key files, and working conventions.

---

## Platform Status

**v0.5 is production-ready.** 1093 tests, 0 known cross-repo blockers. PostgreSQL live on Hetzner VPS (2026-04-09). SQLAlchemy Core migration complete (24 db modules, dialect-agnostic SQL, `RETURNING` for insert-ID portability). Alembic for Postgres, `pg_dump` backup, Docker/compose with direct `alerts evaluate` loop. Codex audit clean (2026-04-11). SS-COMPAT resolved in Slopper (2026-04-11). Next: API layer (see below).

---

## Open Items

### API plan

See [TODO_API.md](TODO_API.md) for the operator-first API plan: owner-approved access only, conservative rate limiting, safe reads/writes by default, and secondary approval for any spendful action. The recommended first implementation slice is a private alert-triage API: `GET /v1/orgs/{org_id}/alerts`, `POST /v1/alerts/{alert_id}/acknowledge`, and `POST /v1/alerts/{alert_id}/resolve`, with owner-issued tokens, request-scoped audit attribution, and strict org-scope enforcement. Prospect APIs stay deferred until prospect tenant ownership is explicit in the schema. Includes a gradual path toward selective client-facing API methods later. Operator-facing summary: [docs/API_ALERT_TRIAGE_MVP.md](docs/API_ALERT_TRIAGE_MVP.md).
