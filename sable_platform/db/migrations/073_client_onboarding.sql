-- 073_client_onboarding.sql
-- Client and operator onboarding -- the intake-manifest SSOT plus entitlements
-- (docs/CLIENT_ONBOARDING_PLAN.md). client_intake is the per-org client header.
-- client_accounts is the unified handle registry (the future SSOT for the client
-- twitter/discord/telegram handles, today scattered across orgs/roster.yaml/relay).
-- client_docs points at explainer/bio/voice artifacts. org_entitlements records the
-- SKUs a client gets -- entitlement STATE only, NEVER money (the SableRevenueLedger
-- references org_entitlements for its entitled-set and owns all billing/pricing).
--
-- OPS-ONLY: every table here holds client PII or commercial state. NONE may cross the
-- SableWeb /client wall (assembleClientData never joins these). See PLAN section 1.6.
--
-- FK note: all four reference orgs(org_id). onboard init upserts a DRAFT org row first
-- (PLAN section 1.0), so the FK is always satisfiable -- no nullable-org needed here.
-- Comment hygiene: NO semicolons inside double-dash comment lines (the runner splits on
-- the literal semicolon). All _at columns TEXT with the strftime ISO-8601-Z default.

CREATE TABLE client_intake (
  org_id                    TEXT PRIMARY KEY REFERENCES orgs(org_id),
  manifest_status           TEXT NOT NULL DEFAULT 'draft',
  primary_contact_name      TEXT,
  primary_contact_email     TEXT,
  primary_contact_telegram  TEXT,
  website_url               TEXT,
  notes                     TEXT,
  created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CHECK (manifest_status IN ('draft', 'ready', 'applied'))
);

CREATE TABLE client_accounts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id        TEXT NOT NULL REFERENCES orgs(org_id),
  platform      TEXT NOT NULL,
  handle        TEXT NOT NULL,
  role          TEXT NOT NULL,
  controlled    INTEGER NOT NULL DEFAULT 0,
  display_name  TEXT,
  bio           TEXT,
  notes         TEXT,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  UNIQUE (org_id, platform, handle)
);
CREATE INDEX client_accounts_by_org ON client_accounts(org_id);

CREATE TABLE client_docs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id      TEXT NOT NULL REFERENCES orgs(org_id),
  kind        TEXT NOT NULL,
  label       TEXT NOT NULL,
  location    TEXT NOT NULL,
  notes       TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX client_docs_by_org ON client_docs(org_id);

CREATE TABLE org_entitlements (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id       TEXT NOT NULL REFERENCES orgs(org_id),
  service_key  TEXT NOT NULL,
  tier         TEXT,
  status       TEXT NOT NULL DEFAULT 'active',
  started_at   TEXT,
  ended_at     TEXT,
  config_json  TEXT NOT NULL DEFAULT '{}',
  notes        TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CHECK (status IN ('trial', 'active', 'paused', 'ended')),
  UNIQUE (org_id, service_key)
);
CREATE INDEX org_entitlements_by_org ON org_entitlements(org_id);

UPDATE schema_version SET version = 73 WHERE version < 73;
