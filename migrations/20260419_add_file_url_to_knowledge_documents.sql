-- Migration: add file_url to knowledge_documents for compatibility with KnowledgeDocument model
--
-- Some older databases were created before the file_url column existed.
-- The importer and APIs now expect this column to be present.

BEGIN;

ALTER TABLE IF EXISTS knowledge_documents
    ADD COLUMN IF NOT EXISTS file_url VARCHAR(500);

COMMIT;
