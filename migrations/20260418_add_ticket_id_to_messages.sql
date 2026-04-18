-- Migration: Add ticket_id to messages table
-- Date: 2026-04-18
-- Description: Add missing ticket_id column to messages table

ALTER TABLE messages 
ADD COLUMN IF NOT EXISTS ticket_id VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_messages_ticket_id ON messages(ticket_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_ticket ON messages(thread_id, ticket_id);