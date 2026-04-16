-- Revision: 20260415_01_learning_hardening
-- Purpose: rollback the learning hardening migration.
--
-- Apply:
--   psql "$DATABASE_URL" -f migrations/20260415_01_learning_hardening.down.sql

BEGIN;

SELECT pg_advisory_xact_lock(hashtext('supervisor-api:20260415_learning_hardening'));

DROP INDEX IF EXISTS idx_response_learning_events_claimed_by;
DROP INDEX IF EXISTS idx_response_learning_events_pending_claim;
DROP INDEX IF EXISTS idx_response_learning_events_dedupe_key;

ALTER TABLE response_learning_events
    DROP COLUMN IF EXISTS claimed_by;

ALTER TABLE response_learning_events
    DROP COLUMN IF EXISTS claimed_at;

ALTER TABLE response_learning_events
    DROP COLUMN IF EXISTS dedupe_key;

ALTER TABLE response_patterns
    DROP COLUMN IF EXISTS embedding;

COMMIT;
