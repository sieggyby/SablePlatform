-- 070_community_audit_leads.sql
-- Lead-capture for the community-audit funnel (sable-audit / PLAN §1.2). A prospect
-- enters their email on the public /audit page. This is a NON-privileged marketing
-- list -- it is NOT the SableWeb allowlist and grants no access.
--
-- No FK: a lead may precede any guild (email captured before the bot is invited).
-- Comment hygiene: no semicolons inside double-dash comment lines (the runner splits
-- on the literal semicolon). Column conventions: counts/PKs INTEGER, all _at columns
-- TEXT with the strftime ISO-8601-Z default below, JSON/text blobs TEXT.

CREATE TABLE community_audit_leads (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  email       TEXT NOT NULL,
  guild_id    TEXT,
  source      TEXT NOT NULL DEFAULT 'audit_page',
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX community_audit_leads_by_email ON community_audit_leads(email);

UPDATE schema_version SET version = 70 WHERE version < 70;
