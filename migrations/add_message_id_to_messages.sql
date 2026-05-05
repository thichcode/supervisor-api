-- Migration: Add message_id column to messages table
-- Run this SQL on your PostgreSQL database to add the new column

ALTER TABLE messages 
ADD COLUMN IF NOT EXISTS message_id VARCHAR(100);

-- Create index on message_id for faster queries
CREATE INDEX IF NOT EXISTS idx_messages_message_id 
ON messages(message_id);

-- Optional: If you want to backfill message_id from existing data (if available)
-- This is optional, depends on your data
-- UPDATE messages SET message_id = 'migrated-' || id::text WHERE message_id IS NULL;
