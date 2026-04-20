BEGIN;

CREATE TABLE IF NOT EXISTS conversation_state (
    id BIGSERIAL PRIMARY KEY,
    thread_id VARCHAR(255) NOT NULL UNIQUE,
    active_topic_title TEXT,
    active_topic_summary TEXT,
    conversation_mode VARCHAR(50) DEFAULT 'continuation',
    continuity_score NUMERIC(4,3) DEFAULT 0.500,
    last_user_intent TEXT,
    last_assistant_intent TEXT,
    open_loops JSONB NOT NULL DEFAULT '[]'::jsonb,
    key_entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    recent_decisions JSONB NOT NULL DEFAULT '[]'::jsonb,
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_message_at TIMESTAMPTZ,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_state_thread_id ON conversation_state(thread_id);
CREATE INDEX IF NOT EXISTS idx_conversation_state_last_message_at ON conversation_state(last_message_at);
CREATE INDEX IF NOT EXISTS idx_conversation_state_mode ON conversation_state(conversation_mode);

COMMIT;
