-- Lead Identifier contract hardening: four new fields on prospect_scores
ALTER TABLE prospect_scores ADD COLUMN recommended_action TEXT;
ALTER TABLE prospect_scores ADD COLUMN score_band_low     REAL;
ALTER TABLE prospect_scores ADD COLUMN score_band_high    REAL;
ALTER TABLE prospect_scores ADD COLUMN timing_urgency     TEXT;

UPDATE schema_version SET version = 29 WHERE version < 29;
