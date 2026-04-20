-- Migration: add extra_metadata to knowledge_documents for compatibility with KnowledgeDocument model
--
-- Some older databases were created before the extra_metadata column existed.
-- The importer and APIs now expect this column to be present.

BEGIN;

ALTER TABLE IF EXISTS knowledge_documents
    ADD COLUMN IF NOT EXISTS extra_metadata JSONB DEFAULT '{}'::jsonb;

COMMIT;
