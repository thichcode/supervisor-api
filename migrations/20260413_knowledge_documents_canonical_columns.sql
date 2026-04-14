-- Migration: normalize knowledge_documents column names
--
-- الهدف:
--   Align the database with the canonical SQLAlchemy model names used by
--   src/db/models.py and the init.sql schema.
--
-- Existing deployments may still have legacy columns:
--   - doc_id
--   - doc_type
--
-- This migration renames them to:
--   - document_id
--   - document_type
--
-- Safe to run multiple times.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'knowledge_documents'
          AND column_name = 'doc_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'knowledge_documents'
          AND column_name = 'document_id'
    ) THEN
        EXECUTE 'ALTER TABLE knowledge_documents RENAME COLUMN doc_id TO document_id';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'knowledge_documents'
          AND column_name = 'doc_type'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'knowledge_documents'
          AND column_name = 'document_type'
    ) THEN
        EXECUTE 'ALTER TABLE knowledge_documents RENAME COLUMN doc_type TO document_type';
    END IF;
END $$;

-- Optional cleanup: create canonical indexes if they do not already exist.
CREATE INDEX IF NOT EXISTS idx_documents_document_id
    ON knowledge_documents(document_id);
CREATE INDEX IF NOT EXISTS idx_documents_type
    ON knowledge_documents(document_type);
CREATE INDEX IF NOT EXISTS idx_documents_category
    ON knowledge_documents(category);

COMMIT;
