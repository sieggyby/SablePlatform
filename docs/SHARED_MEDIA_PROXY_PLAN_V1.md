# Shared Media Layer — Phase 2 Plan (proxy + Tracking cutover + operator clip UI)

**Status:** APPROVED (2-round audit) + IMPLEMENTED 2026-05-30. **PR-X1 DONE** — Worker deployed + live-verified at `https://sable-media-proxy.siegby.workers.dev` (signed GET→200, Range→206, unsigned→403, tampered→403, unknown-bucket→404); signer in `sable_platform/media/signing.py` (13 tests). **PR-X2 DONE** — `POST /api/v1/write/clip-suggest` (signed media URLs, `reply_generate` gate + quota), `media_url` on `ReplySuggestion`, 8 clips uploaded to `sable-tig` + `media_url` in vault notes; tests green. **PR-X3 DONE** — SableWeb `/ops/clip-assist` (route + page + `ClipAssist.tsx` with `<video>` playback + `ClipSuggestResponseSchema` + `clipSuggest` client); typechecks clean. **Remaining to go live on the hosted site:** redeploy `sable serve` on the VPS with `MEDIA_SIGN_SECRET` + `MEDIA_PROXY_BASE_URL` in env, redeploy SableWeb, (optional) custom domain `media.sable.tools`. **PR-X4 (SableTracking cutover) NOT started** (separate; not needed for the Slopper clip path).

(historical) PR-X1 in progress: the shared HMAC signer (`sable_platform/media/signing.py` `sign_media_url`/`verify_media_signature`) is done + tested (13 media tests green); the Worker is scaffolded at `~/Projects/sable-media-proxy/` (`src/index.ts`, `wrangler.jsonc`, README) — signed-path verify, bucket allowlist, key-traversal guard, manual Range/206 + 304, per-IP edge limit + DO global ceiling. **Gated on operator:** a Workers-scoped Cloudflare API token (NOT the R2 S3 token), DNS `media.sable.tools`, and `wrangler secret put MEDIA_SIGN_SECRET`. Then `npm install && wrangler deploy`. Builds on Phase 1 (migration 055 `media_assets` + `sable_platform/media/` lib, shipped + green). SablePlatform owns the media contract; SableTracking + SableSlopper + SableWeb consume it.
**Goal:** Make stored `media_assets` (`r2_ref = '<bucket>/<key>'`, private R2) actually **resolvable and playable** — so (a) the operator can preview/grab reply clips on the website, and (b) SableTracking can flip `MEDIA_BACKEND=r2`.

---

## 0. Decisions for the operator (these fork the build — confirm before implementing)
1. **Proxy tech:** **Cloudflare Worker** (recommended — see §1) vs reuse SableTracking's 4×-audited aiohttp proxy.
2. **Media auth model:** **signed-path (HMAC, short TTL)** (recommended) vs public-unguessable-key vs Cloudflare Access (operator-only).
3. **Clip UI surface:** a **dedicated clip-suggest panel** (recommended — the text reply-assist is a separate feature) vs enriching `/ops/reply-assist` to also attach a matching clip.

Recommendations are baked into the plan below; the audit pressure-tests them; you make the final call.

---

## 1. The media proxy — Cloudflare Worker (recommended)

A single Worker serves `https://media.sable.tools/<bucket>/<key>` for **both** repos.

> `[AUDIT-FIX M1]` Framing correction: SableTracking's aiohttp proxy (`handle_media`/`MediaRateLimiter`) is **only in the v5 plan doc — not built**. So this is a choice between two *unbuilt* designs, not "discarding audited code." v5's design was *anonymous* + presign-then-302 (1h S3 signature); the Worker is HMAC-signed + direct-stream (a net security gain). This **supersedes** the Phase-1 plan's lean ("reuse v5's proxy"). The Worker is new code and gets its own adversarial audit before deploy (§6).

**Why Worker over aiohttp** (from the design pass):
- **R2 binding streams bytes directly** → no presign, no 302, no S3 creds in the proxy, no 1-hour-signature exposure window.
- **`cf-connecting-ip`** is edge-set and unforgeable → the entire XFF-spoofing class (v5 F1/F4, the hardest-audited aiohttp code) **disappears**.
- Native edge cache + Range/conditional-GET (matters for video) + zero R2 egress.
- One proxy, no Python coupling; swappable behind `MEDIA_PROXY_BASE_URL` with zero DB change (serves the same `r2_ref` contract).

**Cost of the Worker** (honest tradeoffs the audit must weigh):
- R2 buckets bind **statically in `wrangler.jsonc`** → per-client onboarding = a config edit + redeploy (vs Tracking's runtime `R2_BUCKET_PER_CLIENT_JSON`). Mitigation: keep the per-client-bucket model (one binding per `sable-<client>`), accept redeploy-on-onboard, and `log()`/document it. (Alt: single shared bucket + org-prefixed keys — rejected, breaks Tracking's audited per-client isolation.)
- **Sanitize logic forks into TS** — the Worker must re-express `_safe_key` (reject `..`, leading `/`, NUL) at *serve* time. Keep it minimal + test it; note the dual-maintenance with `sable_platform.media.sanitize`.
- New deploy target (Cloudflare) outside the Hetzner/Railway footprint. Deploy headless via `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` `[AUDIT-FIX L3]` (also the fix for your `wrangler login` `request_forbidden` — skip OAuth: create a token with **Workers Scripts:Edit + Workers R2 Storage:Edit** and `export CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=339fd…696a`).

**Worker design:**
- Repo: a new `sable-media-proxy/` (worker + `wrangler.jsonc`); R2 bindings: `{binding:"sable-tig", bucket_name:"sable-tig"}`, one per client bucket.
- Route: `media.sable.tools/<bucket>/<key>` → look up the binding for `<bucket>` (the lookup table **is** the allowlist; unknown bucket → 404), `env[bucket].get(key, opts)`, return `new Response(object.body, …)` with `object.writeHttpMetadata(headers)` + `object.httpEtag` + `Cache-Control: private, max-age=300`.
  - **`[AUDIT-FIX H2]` Range + conditional GET are manual** (R2 binding does NOT auto-handle them): parse the `Range` request header → pass `{ range, onlyIf: request.headers }` to `.get()`; on a range hit emit **HTTP 206 + `Content-Range` + `Accept-Ranges: bytes`**; on a precondition match R2 returns an object **with no `.body`** → emit **304** (check `!object.body`, not null). This is load-bearing for `<video controls>` seeking — browsers reissue Range requests to scrub.
- Sanitize `key` (reject `..`, leading `/`, NUL, `\`) before `.get()`. `[AUDIT-FIX L2]` traversal subset ONLY — do NOT re-run MIME/extension canonicalization (that runs at upload time in `sable_platform.media.sanitize`; re-running it would corrupt already-stored keys).
- **Auth = signed path (HMAC):** request carries `?exp=<unix>&sig=<hmac>`; Worker verifies `sig = HMAC_SHA256(secret, "<bucket>/<key>:<exp>")` over the **path+exp only** (NOT the Range header, so the browser reuses one signed URL across range requests) and checks `exp` not past. `[AUDIT-FIX M2]` verify with `crypto.subtle.verify()` / `crypto.timingSafeEqual()` — never a `===` string compare (timing attack). Secret shared between the apps (mint) and the Worker (`wrangler secret put`). Works in a bare `<video src>` (no headers), no presign window, prevents key enumeration. (Operator-only alt: Cloudflare Access on `media.sable.tools` — but breaks anonymous embeds; only if media is never public.)
- **Rate limit `[AUDIT-FIX H1]`:** the Workers Rate Limiting binding is **per-Cloudflare-location, not global** — it replaces v5's *per-client* tier (keyed on `cf-connecting-ip`, spoof-proof) but does NOT provide a global ceiling, and L7 DDoS protection is volumetric, not an app request ceiling. v5 had a real global limiter (500/s, burst 2000). So **a Durable-Object global token-bucket counter is REQUIRED in PR-X1** (not deferred) to restore that ceiling — or the operator must explicitly sign off on dropping it.

**URL minting (the apps):** add `sable_platform/media/urls.py::sign_media_url(ref, secret, ttl)` → `f"{base}/{ref}?exp=…&sig=…"`. The stored `r2_ref` stays bare; the signed URL is minted at **read time** (so changing the proxy/secret never requires a DB rewrite). `build_media_url(ref, base)` stays for the unsigned/legacy path.

---

## 2. SableTracking cutover (amends R2_MIGRATION_PLAN_V5)
- v5 **PR 2/3** unchanged-in-spirit but consume `sable_platform.media` (sanitize + R2Store via the async adapter) per Phase-1 plan §6.
- v5 **PR 4 (the aiohttp proxy) is REPLACED** by "point `MEDIA_PROXY_BASE_URL` at the shared Worker." Its `handle_media` + `MediaRateLimiter` are not built. Update v5 §1.3 + the THREAT_MODEL to the Worker model (and note the XFF-spoof findings are now N/A).
- After upload, `register_asset(source_project="tracking", kind="contribution_media", content_item_id, entity_id, …)`.
- Then flip `MEDIA_BACKEND=r2` (validate_config now satisfied: account/keys/`MEDIA_PROXY_BASE_URL` set, `R2_BUCKET_PER_CLIENT_JSON` has every client). Run v5 PR6 Drive→R2 migration as planned.
- The `media_links` reader side resolves via `sign_media_url` instead of `build_proxy_url` (or `build_proxy_url` learns to sign).

## 3. Operator clip-suggest UI (the website piece)
The text `/ops/reply-assist` stays as-is. Add a **clip-suggest** capability:
- **Slopper endpoint `[AUDIT-FIX B1 + M3]`** `POST /api/v1/vault/reply-suggest` (body `{tweet_url|tweet_text, org, account}`). **POST, not GET** — it spends Claude rank + SocialData, so it must not be prefetchable/cacheable, and the tweet text goes in the body (not query/access-logs). **Gate behind a write-tier action** — NOT `vault_read` (read-only operators hold that → cost leak); reuse `Action.reply_generate` or add a `clip_suggest` action to the admin/creator-only set. Mirror the `/write/reply` spend pattern: `reserve_generation` before spend + refund on failure + `log_cost` (and `call_claude_json` already budget-checks when `org` is passed). Returns ranked clips each `{content_id, score, reason, caption, media_url (signed), thumbnail_url, draft}`. Reuses `suggest_replies` — first add `media_url`/`thumbnail_url` to `ReplySuggestion` (`suggest.py:16-26`, populate from `note.get("media_url")`; null-guard so absent thumbnails sign to `""`) and sign at the endpoint.
- **SableWeb** `/ops/clip-assist` (separate surface from the text reply-assist): tweet input → POSTs → cards with **`<video src={signed media_url} controls preload=metadata>`** + draft + copy + download. `[AUDIT-FIX M4]` add a **new `ClipSuggestResponseSchema`** + a new `slopper-client` fn — do NOT widen `ReplyAssistResponseSchema` (that's the text-reply contract; Slopper response schemas are strict/non-passthrough so new fields are dropped unless declared).
- This is the surface that finally makes the 79 clips usable for replies on the web.

## 4. Bucket/onboarding reconciliation
Per-client buckets (`sable-<client>`), one Worker R2 binding each. Onboarding client X = create `sable-x`, add binding + redeploy Worker, add `R2_BUCKET_PER_CLIENT_JSON` entry (Tracking) / `R2_BUCKET` (Slopper, single-client today; multi-client Slopper would need an org→bucket map — small follow-up). Document the redeploy step.

## 5. Cost (per AGENTS.md)
- Worker: free tier likely covers it; R2 zero egress; edge cache reduces origin reads. ≪ targets.
- The clip-suggest endpoint spends Claude rank + SocialData per call (same as the CLI `suggest`) → rate-limited + `log_cost` + the existing per-operator patterns. Quota reuse (056 `operator_reply_quota`) optional.

## 6. Risks
- **Signed-URL secret management** (Worker `wrangler secret` ↔ app config) — rotate-able; document. If signing is skipped (public-unguessable), accept enumeration risk on a private-but-guessable keyspace.
- **Sanitize drift** (TS Worker vs Python lib) — minimal shared rule + tests both sides.
- **New deploy surface** (Cloudflare Worker) — CI/`CLOUDFLARE_API_TOKEN`, ownership.
- **Spend on a "read" endpoint** — clip-suggest calls Claude/SocialData; must not be unauthenticated/unthrottled (RBAC + rate limit + cost log).
- **Global rate-limit ceiling `[AUDIT-FIX H1]`** — the Workers Rate Limiting binding is per-location; the DO global counter (PR-X1) restores v5's global ceiling. Do not rely on DDoS protection for it.
- **New, unaudited proxy code** — v5's aiohttp proxy was never built, so nothing audited is lost; but the Worker is new code (signing, Range/206, sanitize, DO limiter) and must get its own adversarial pass before production deploy.
- **Spend on the clip-suggest path** — gated behind a write-tier action + quota + cost log (B1); never `vault_read`, never GET.

## 7. Phasing
1. **PR-X1** Worker (`sable-media-proxy`: signed-path verify, manual Range/206 + 304, key-traversal sanitize, **DO global rate-limit counter** + per-IP Rate Limiting binding) + `sign_media_url` in the lib + deploy (`CLOUDFLARE_API_TOKEN`/`ACCOUNT_ID`) + DNS `media.sable.tools`. Makes existing `media_assets` resolvable.
2. **PR-X2** Slopper backfill run (now `media_url` resolves) + clip-suggest read endpoint (`media_url` on `ReplySuggestion`).
3. **PR-X3** SableWeb clip-assist surface (video playback).
4. **PR-X4** SableTracking cutover (consume lib, register_asset, `MEDIA_BACKEND=r2`, Drive→R2 migration).

## 8. Tests
- Worker: signed-path accept/reject (good sig, expired, tampered key, `..` traversal), unknown bucket → 404, Range/304, rate-limit 429. (Vitest + Miniflare/`wrangler dev`.)
- Lib: `sign_media_url` round-trips with the Worker verifier (shared test vector).
- Slopper: `ReplySuggestion.media_url` populated + signed in the endpoint response (mock).
- SableWeb: schema accepts media fields; player renders when present.

## 9. Docs
- This plan; amend `R2_MIGRATION_PLAN_V5` §1.3/THREAT_MODEL (proxy → Worker); SablePlatform `media/` docs (signer); Slopper `CLIP_LESSONS` (clip-suggest endpoint); SableWeb ops docs.
