BEGIN;

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS ticket_id VARCHAR(255),
  ADD COLUMN IF NOT EXISTS ticket_system VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_messages_ticket_id ON messages(ticket_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_ticket ON messages(thread_id, ticket_id);

CREATE TABLE IF NOT EXISTS conversation_threads (
  id BIGSERIAL PRIMARY KEY,
  thread_id VARCHAR(255) NOT NULL UNIQUE,
  channel VARCHAR(50),
  platform VARCHAR(50),
  user_id VARCHAR(255),
  team_id VARCHAR(255),
  title TEXT,
  primary_ticket_id VARCHAR(255),
  ticket_system VARCHAR(50),
  last_message_at TIMESTAMPTZ DEFAULT NOW(),
  status VARCHAR(50) DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_threads_primary_ticket ON conversation_threads(primary_ticket_id);

CREATE TABLE IF NOT EXISTS user_style_profiles (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(255) NOT NULL UNIQUE,
  preferred_tone VARCHAR(50),
  preferred_verbosity VARCHAR(50),
  preferred_format VARCHAR(50),
  preferred_language VARCHAR(20),
  response_persona_hint TEXT,
  confidence_score NUMERIC(5,4) DEFAULT 0.0,
  sample_count INT DEFAULT 0,
  last_inferred_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_style_signals (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(255) NOT NULL,
  request_id VARCHAR(255),
  signal_type VARCHAR(50) NOT NULL,
  signal_value VARCHAR(100) NOT NULL,
  signal_strength NUMERIC(5,4) DEFAULT 0.5,
  source VARCHAR(50) NOT NULL,
  evidence JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_style_signals_user_created
  ON user_style_signals(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS interaction_logs (
  id BIGSERIAL PRIMARY KEY,
  request_id VARCHAR(255) NOT NULL UNIQUE,
  thread_id VARCHAR(255) NOT NULL,
  user_id VARCHAR(255) NOT NULL,
  ticket_id VARCHAR(255),
  ticket_system VARCHAR(50),
  input_text TEXT NOT NULL,
  output_text TEXT,
  intent VARCHAR(50),
  risk_level VARCHAR(20),
  confidence_score NUMERIC(5,4),
  model_provider VARCHAR(50),
  model_name VARCHAR(100),
  kb_hit_count INT DEFAULT 0,
  kb_sources JSONB DEFAULT '[]'::jsonb,
  approval_required BOOLEAN DEFAULT FALSE,
  approval_status VARCHAR(50),
  processing_latency_ms INT,
  outcome_status VARCHAR(50),
  extra_metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_thread_ticket
  ON interaction_logs(thread_id, ticket_id);

CREATE TABLE IF NOT EXISTS feedback_logs (
  id BIGSERIAL PRIMARY KEY,
  request_id VARCHAR(255) NOT NULL,
  thread_id VARCHAR(255),
  user_id VARCHAR(255),
  ticket_id VARCHAR(255),
  ticket_system VARCHAR(50),
  feedback_type VARCHAR(50) NOT NULL,
  feedback_score NUMERIC(5,2),
  feedback_label VARCHAR(100),
  feedback_text TEXT,
  edited_output_text TEXT,
  reviewer_id VARCHAR(255),
  extra_metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_logs_request ON feedback_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_feedback_logs_ticket_id ON feedback_logs(ticket_id);

CREATE TABLE IF NOT EXISTS response_learning_events (
  id BIGSERIAL PRIMARY KEY,
  request_id VARCHAR(255) NOT NULL,
  user_id VARCHAR(255),
  thread_id VARCHAR(255),
  ticket_id VARCHAR(255),
  ticket_system VARCHAR(50),
  event_type VARCHAR(50) NOT NULL,
  event_payload JSONB NOT NULL,
  processed BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_learning_events_processed
  ON response_learning_events(processed, created_at);

CREATE TABLE IF NOT EXISTS knowledge_candidates (
  id BIGSERIAL PRIMARY KEY,
  source_request_id VARCHAR(255) NOT NULL,
  source_thread_id VARCHAR(255),
  ticket_id VARCHAR(255),
  ticket_system VARCHAR(50),
  extracted_title TEXT,
  extracted_content TEXT NOT NULL,
  category VARCHAR(100),
  tags JSONB DEFAULT '[]'::jsonb,
  confidence_score NUMERIC(5,4),
  status VARCHAR(50) DEFAULT 'pending',
  reviewer_id VARCHAR(255),
  review_note TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  reviewed_at TIMESTAMPTZ,
  promoted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS approval_requests (
  id BIGSERIAL PRIMARY KEY,
  request_id VARCHAR(255) NOT NULL,
  thread_id VARCHAR(255),
  user_id VARCHAR(255),
  ticket_id VARCHAR(255),
  ticket_system VARCHAR(50),
  proposed_response TEXT NOT NULL,
  reason TEXT,
  risk_level VARCHAR(20),
  confidence_score NUMERIC(5,4),
  status VARCHAR(50) DEFAULT 'pending',
  approver_id VARCHAR(255),
  action_note TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  acted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS thread_ticket_links (
  id BIGSERIAL PRIMARY KEY,
  thread_id VARCHAR(255) NOT NULL,
  ticket_id VARCHAR(255) NOT NULL,
  ticket_system VARCHAR(50) NOT NULL DEFAULT 'servicedesk_plus',
  relation_type VARCHAR(50) NOT NULL DEFAULT 'primary',
  confidence_score NUMERIC(5,4) DEFAULT 1.0,
  linked_by VARCHAR(50) NOT NULL DEFAULT 'system',
  extra_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(thread_id, ticket_id, ticket_system)
);

CREATE INDEX IF NOT EXISTS idx_thread_ticket_links_thread_id ON thread_ticket_links(thread_id);
CREATE INDEX IF NOT EXISTS idx_thread_ticket_links_ticket_id ON thread_ticket_links(ticket_id);

INSERT INTO thread_ticket_links (
  thread_id,
  ticket_id,
  ticket_system,
  relation_type,
  confidence_score,
  linked_by,
  extra_metadata
)
SELECT
  thread_id,
  primary_ticket_id,
  COALESCE(ticket_system, 'servicedesk_plus'),
  'primary',
  1.0,
  'migration',
  '{}'::jsonb
FROM conversation_threads
WHERE primary_ticket_id IS NOT NULL
ON CONFLICT (thread_id, ticket_id, ticket_system) DO NOTHING;

COMMIT;
