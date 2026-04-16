-- Revision: 20260415_01_learning_hardening
-- Purpose: add idempotency and claim/lock support for response learning,
--          plus semantic pattern storage.
--
-- Apply:
--   psql "$DATABASE_URL" -f migrations/20260415_01_learning_hardening.up.sql

BEGIN;

SELECT pg_advisory_xact_lock(hashtext('supervisor-api:20260415_learning_hardening'));

ALTER TABLE response_learning_events
    ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(64);

ALTER TABLE response_learning_events
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

ALTER TABLE response_learning_events
    ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(100);

ALTER TABLE response_patterns
    ADD COLUMN IF NOT EXISTS embedding JSON;

CREATE UNIQUE INDEX IF NOT EXISTS idx_response_learning_events_dedupe_key
    ON response_learning_events (dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_response_learning_events_pending_claim
    ON response_learning_events (processed, claimed_at, created_at, id);

CREATE INDEX IF NOT EXISTS idx_response_learning_events_claimed_by
    ON response_learning_events (claimed_by);

COMMIT;
