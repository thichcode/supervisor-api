-- Add ResponsePattern table for Pattern Learning
-- Stores approved Q&A patterns for future matching

CREATE TABLE IF NOT EXISTS response_patterns (
    id SERIAL PRIMARY KEY,
    question_hash VARCHAR(64) NOT NULL UNIQUE,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    user_id VARCHAR(100),
    thread_id VARCHAR(255),
    team_id VARCHAR(100),
    intent VARCHAR(50),
    confidence_score FLOAT DEFAULT 1.0,
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_by VARCHAR(100),
    source_request_id VARCHAR(100),
    embedding JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_patterns_user_id ON response_patterns(user_id);
CREATE INDEX IF NOT EXISTS idx_patterns_team_id ON response_patterns(team_id);
CREATE INDEX IF NOT EXISTS idx_patterns_intent ON response_patterns(intent);
CREATE INDEX IF NOT EXISTS idx_patterns_team_active ON response_patterns(team_id, is_active);
CREATE INDEX IF NOT EXISTS idx_patterns_intent_active ON response_patterns(intent, is_active);
CREATE INDEX IF NOT EXISTS idx_patterns_usage ON response_patterns(usage_count, last_used_at);
