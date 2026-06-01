# Shared Media Layer — Cross-Repo Plan (rev 2)

**Status:** Phase 1 IMPLEMENTED + green (2026-05-30). **PR-P** (SablePlatform): migration 055 + `sable_platform/media/` lib; full suite passes (1566). **PR-S** (Slopper): R2 config, gated `sable/shared/media.py` helper, `media_url` surfaced through vault sync (both branches) → serve `/search`, idempotent `workspace/card/media_backfill.py`, `[r2]` extra, tests green (vault+shared 124). **Remaining Phase-1 step (gated, needs live bucket):** run `media_backfill.py` once R2 creds exist; auto-upload-on-render in the card/clip tools is an optional follow-up (re-running the idempotent backfill covers new renders). Phase 2 (proxy + Tracking + UI) not started. SablePlatform = owner; SableSlopper + SableTracking = consumers.
**Goal:** One media-storage + URL + registry implementation in **SablePlatform** that holds *all things created in SableSlopper* (clips/cards/brainrot/memes) **and** *all things surfaced in SableTracking* (contribution media).
**rev 2** incorporates the cross-repo adversarial audit. Changes marked `[AUDIT-FIX]`.

> **Biggest rev-2 change `[AUDIT-FIX]`:** split into two phases. **Phase 1** (this plan, ready to build) = the SablePlatform media foundation + Slopper upload/register/surface — clean, low-risk, independently shippable. **Phase 2** (separate plan + its own audit) = the media **proxy** + the **SableTracking v5 amendment** + the operator UI — these carry the real cross-repo risk (they restructure v5's audited PRs 2/3/4 and relocate an audited rate-limited proxy) and must not ride on Phase 1's approval.

---

## 1. Decisions (locked with operator)
- **Private R2 + media proxy**, not public buckets. Persisted ref `'<bucket>/<key>'`; resolved via `MEDIA_PROXY_BASE_URL`.
- **SablePlatform owns the shared layer.** Consumers import it; nobody reimplements R2 I/O.
- **Sync core + async adapter.** Shared S3 client is sync `boto3` (SablePlatform + Slopper are sync). SableTracking keeps its async `MediaClient` and delegates upload to the sync core via `run_in_executor`. `[AUDIT-NOTE: verified an improvement over v5 — drops the aioboto3-on-3.13 resolution risk (v5 F5); Tracking's platform_sync already calls sync get_db() from async code, so the pattern is precedented.]`
- **Phase the work** (see banner). Foundation first; proxy + v5 amendment separately re-audited.

---

# PHASE 1 — Foundation (build now)

## 2. Architecture (Phase 1)
```
SablePlatform/sable_platform/media/        <-- NEW pure-.py subpackage (auto-discovered; no package-data change)
  sanitize.py   _safe_key, _safe_filename, _MIME_TO_EXT, FilenameRejected   (pure/sync; port v5 §1.2 spec verbatim)
  r2_store.py   R2Store (sync boto3): is_configured(); put(bytes,key,mime,bucket)->'<bucket>/<key>'; presign_get(ref,ttl)
  urls.py       build_media_url(ref, base)   (base INJECTED, not imported)
  registry.py   register_asset(...) idempotent; get_asset / list_assets
SablePlatform/sable_platform/db/migrations/055_media_assets.sql   <-- NEW table
```
(Phase 1 does NOT add `proxy.py` — that's Phase 2.)

## 3. `media_assets` table (migration 055)
```sql
-- 055_media_assets.sql  (SQLite dialect. NO ';' inside '--' comments — the runner splits on ';')
CREATE TABLE IF NOT EXISTS media_assets (
    asset_id        TEXT PRIMARY KEY,                     -- uuid4 hex
    org_id          TEXT NOT NULL REFERENCES orgs(org_id),
    source_project  TEXT NOT NULL,                        -- slopper | tracking
    kind            TEXT NOT NULL,                        -- clip | card | brainrot | meme | contribution_media
    r2_ref          TEXT NOT NULL,                        -- canonical '<bucket>/<key>'
    mime            TEXT,
    bytes           INTEGER,
    sha256          TEXT,
    entity_id       TEXT REFERENCES entities(entity_id),
    content_item_id TEXT REFERENCES content_items(item_id),
    source_ref      TEXT,
    caption         TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_media_assets_org_ref ON media_assets(org_id, r2_ref);
CREATE INDEX IF NOT EXISTS ix_media_assets_org_kind ON media_assets(org_id, kind);
CREATE INDEX IF NOT EXISTS ix_media_assets_sha ON media_assets(org_id, sha256);
UPDATE schema_version SET version = 55 WHERE version < 55;
```
`[AUDIT-FIX]` timestamp default is the post-053 ISO-Z form (`strftime('%Y-%m-%dT%H:%M:%SZ','now')`), matching 054 — NOT `datetime('now')`.

**Idempotency contract `[AUDIT-FIX]`:** the `(org_id, r2_ref)` UNIQUE index is the real idempotency guarantee — re-registration upserts via `ON CONFLICT(org_id,r2_ref) DO UPDATE` (same dual-dialect idiom as `discord_state_pins.py:110`). The `sha256` index is **best-effort re-upload avoidance only** (a SELECT-then-skip; safe for the single-process consumers, NOT concurrency-safe — do not call it idempotency).

**Full 5-file integration (verified):**
1. `migrations/055_media_assets.sql` (above; semicolon-clean comments; self-bumps schema_version).
2. `db/connection.py::_MIGRATIONS` → append `("055_media_assets.sql", 55)` (current head entry is `054_discord_state_pins.sql`).
3. `db/schema.py` → add the `media_assets` `Table` and **replicate ALL THREE indexes + exact column types/nullability** `[AUDIT-FIX]` — `tests/db/test_schema.py` mechanically asserts table/column/type/nullability/**index** parity (`test_legacy_indexes_present_in_sa`, `test_same_columns_per_table`, …). Use `server_default=func.now()` (dialect-neutral) for `created_at`, like every other table. **Declare the unique key as a named `Index("ux_media_assets_org_ref", ..., unique=True)`, NOT a `UniqueConstraint`** `[AUDIT-FIX]` — a UniqueConstraint renders as a filtered-out `sqlite_autoindex_*` and fails the legacy-index parity check; precedent: `schema.py:202 Index("idx_diagnostic_cult_run_id", unique=True)`.
4. `alembic revision --autogenerate -m "055 media assets"` — files land in **`sable_platform/alembic/versions/`** (NOT a repo-root `alembic/`; `test_no_duplicate_alembic_tree` forbids that) `[AUDIT-FIX]`; chains off head `f2a3b4c5d054`; review that autogen emits `func.now()` not a SQLite literal.
5. `db/migrate_pg.py` → add `"media_assets"` to `TABLE_LOAD_ORDER` after orgs/entities/content_items (FK-safe). `[AUDIT-FIX] _TEXT_PK_COLUMNS entry NOT needed` — `asset_id` is always a uuid4 from `register_asset`, never NULL; that map only backfills NULL PKs.
6. Bump `docs/CLI_REFERENCE.md` count 54→55 (gated by `tests/test_doc_parity.py`). `[AUDIT-FIX]` the *structural* gate is `tests/db/test_schema.py` (step 3); the doc count is a separate, weaker gate — satisfy both.

## 4. Shared lib (Phase 1)
- **sanitize.py** — port v5-audited helpers verbatim (pure/sync): `_safe_key`, `_safe_filename(filename, mime)` (double-extension defense, browser-renderable stripping, media-only `_MIME_TO_EXT`), `FilenameRejected`.
- **r2_store.py** — sync `boto3` per-call client: `is_configured()`, `put(file_bytes, key, mime, bucket) -> '<bucket>/<key>'` (put_object + ContentType + `ContentDisposition=inline`), `presign_get(ref, ttl=3600)`. Config injected via constructor; no `settings` import. **No size cap imposed** (Tracking's 20 MB cap is a Tracking-intake concern, not the platform's) `[AUDIT-FIX]`.
- **urls.py** — `build_media_url(ref, base)`: empty→""; absolute(http*)→pass-through; else `f"{base.rstrip('/')}/{ref}"`.
- **registry.py** — `register_asset(conn, org_id, source_project, kind, r2_ref, *, mime, bytes, sha256, caption, entity_id=None, content_item_id=None, source_ref=None, metadata=None)` (upsert on `(org_id,r2_ref)`); `get_asset`, `list_assets(org_id, kind=...)`. Named `:params`; `compat.py` helpers for any cross-dialect SQL.

## 5. Slopper integration (PR-S)
- Dep: `boto3` as optional extra `[r2]` in `pyproject.toml`; import-guard so base install works without it.
- Config: add `r2_account_id, r2_access_key_id, r2_secret_access_key, r2_bucket, media_proxy_base_url` to `config.py::SECRET_ENV_MAP` (env-first, `R2_*`) + non-secret defaults in `_DEFAULTS`.
- Upload seam (finals only):
  - `sable/clip/assembler.py` — before the sidecar write (~L238), if R2 configured: upload `output` (+thumbnail), set `meta["media_url"]`/`meta["thumbnail_url"]`.
  - `workspace/card/meta_util.py::write_sidecar` — upload **after the finals-dir guard** (`if mp4.parent != FINALS_DIR: return None`) `[AUDIT-FIX]`, then set `data["media_url"]`. Single funnel covers card + brainrot.
- Registry: `register_content_artifact` + the meme call sites also call `register_asset(...)` (non-fatal, like the existing artifact write).
- Vault surfacing `[AUDIT-FIX — two lines, both branches]`: `_build_note_frontmatter` is an allowlist, so add `fm["media_url"]`/`fm["thumbnail_url"]` there (create branch) **AND** add them to the re-sync **update** branch (`sync.py:210-211`, which today only copies `assembled_at`+`output`). Without the update-branch line, the **backfill of the 79 already-indexed clips silently drops `media_url`** (the bug the audit caught). Once in frontmatter, `serve /search`'s frontmatter spread exposes it with zero route change.
- **Backfill**: `sable clip media-backfill` walks `_sync_index.json` finals (the 79) → upload → `register_asset` → stamp `media_url` into sidecars → re-sync (relies on the update-branch fix above). Idempotent via `(org_id, r2_ref)` + sha256 reuse.

## 6. Phase-1 cost / risks
- **Cost:** R2 ~$0.015/GB-mo, zero egress; 79 clips ≈ 800 MB ≈ ~$0.01/mo. No metered AI in the storage path; boto3 = bandwidth only. Within Slopper $3/run + $200/mo.
- **Dual-migration:** prod is Postgres (Alembic-owned; the .sql runner never touches PG) → the Alembic revision (step 4) is mandatory or the table never exists in prod; verify `alembic upgrade head` on a scratch PG.
- **Semicolon-in-comment runner trap** → 055 comments kept semicolon-free.
- **Private bucket ⇒ URLs only resolve once the Phase-2 proxy is live** → Phase 1 stores `media_url` but it's not browsable yet; `output` local path remains for CLI. Acceptable and explicit.
- **FK-nullable** for Slopper clips with no entity/content_item → DDL nullable; correct.

## 7. Phase-1 tests
- **SablePlatform:** migration applies (SQLite + scratch PG/Alembic); `tests/db/test_schema.py` parity incl. all 3 indexes; `register_asset` upsert idempotency on `(org_id,r2_ref)`; sanitize helpers (port v5 moto/unit tests); `build_media_url`; doc-parity count.
- **Slopper:** upload seam stamps `media_url` (mock R2Store); `_build_note_frontmatter` carries it on **both** create and update; backfill idempotent + surfaces on re-synced (already-indexed) notes; `[r2]` import-guard when boto3 absent.

---

# PHASE 2 — Proxy + Tracking amendment + operator UI (separate plan, separate audit)

> Deferred out of Phase-1 approval. Sketched here so Phase 1 builds toward it; each item gets its own adversarial pass before build.

- **Media proxy `[AUDIT-FIX — keep v5's audited proxy].`** Do **not** rebuild the proxy in `sable serve` (that would discard v5's 4-audit two-tier `MediaRateLimiter` + XFF handling and create an unauthenticated private-object route on a server that authenticates everything). Leaning: **reuse SableTracking's existing aiohttp proxy** (the media/proxy logic lives in `app/storage/` — `r2_urls.py`/`media_backend.py`; the v5 §1.3 `handle_media` route is planned, not yet in `app/health.py` — Phase-2 plan must confirm the real location) as the single `MEDIA_PROXY_BASE_URL`, add the Slopper bucket to its `allowed_buckets`/`R2_BUCKET_PER_CLIENT`. Open question to resolve in the Phase-2 plan: auth model for `<video src>` (unauthenticated browser tag vs token) — this materially changes security posture and must be decided explicitly, not implied.
- **SableTracking v5 amendment `[AUDIT-FIX — restructures PRs 2/3/4, not just PR 3].`** PR 2's `_safe_key`/`_safe_filename` → import from `sable_platform.media.sanitize`. PR 3's aioboto3 `R2Client` → thin async adapter over `R2Store` (keep its `_safe_filename`+key assembly + `ensure_folder`). PR 4's proxy → the shared decision above. Plus `register_asset(source_project="tracking", kind="contribution_media", content_item_id=…, entity_id=contributor, …)`. PR-P must merge before these.
- **§1.4.bis coupling — name it, don't sidestep `[AUDIT-FIX].`** The grep stays literally clean, but `media_assets.r2_ref` is now a **real cross-repo coupling to the `'<bucket>/<key>'` ref format**, owned by SablePlatform (`sable_platform/media/urls.py`). Record it as an accepted, owned coupling in the v5 amendment + SablePlatform docs — the gate's intent (catch ref-format coupling) is satisfied by explicit ownership, not by the string rename.
- **Operator UI + `vault suggest` endpoint** — the earlier website plan, now backed by `media_assets` / `media_url`.

---

## 8. Sequencing
1. **PR-P** (SablePlatform foundation): lib + migration 055 (5 files) + doc bump + tests. Independently shippable.
2. **PR-S** (Slopper): config + upload seam + register_asset + vault `media_url` (both branches) + backfill. Needs PR-P.
3. **Phase 2** (separate plan + audit): proxy decision, v5 amendment, operator UI, R2 bucket+creds+`MEDIA_PROXY_BASE_URL`.

## 9. Docs to update (Phase 1)
- SablePlatform: `docs/EXTENDING.md` (media lib), `CLAUDE.md` "What's built", `CLI_REFERENCE.md` count, this plan.
- Slopper: `CLIP_LESSONS.md` (media_url flow), `CLAUDE.md`, memory `project_sable_tig_quote_card.md`.
- (Tracking docs updated in Phase 2.)
