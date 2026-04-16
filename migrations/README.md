# Migrations

This repository currently ships raw SQL migrations instead of Alembic.

## Apply the learning hardening migration

```bash
psql "$DATABASE_URL" -f migrations/20260415_01_learning_hardening.up.sql
```

## Roll back

```bash
psql "$DATABASE_URL" -f migrations/20260415_01_learning_hardening.down.sql
```

## What this migration does

- Adds `dedupe_key`, `claimed_at`, and `claimed_by` to `response_learning_events`
- Adds a unique partial index on `response_learning_events.dedupe_key`
- Adds a claim-queue index for the replay worker
- Adds `embedding` to `response_patterns` for semantic matching

## Notes

- The migration is transactional and uses an advisory lock to avoid concurrent runs.
- Existing rows are left intact; the application computes `dedupe_key` for new events.
- Semantic matching falls back to deterministic hashed vectors when transformer embeddings are unavailable.
