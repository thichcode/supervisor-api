-- Initialize supervisor database schema

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(100) NOT NULL,
    user_id VARCHAR(100) NOT NULL,
    thread_id VARCHAR(100) NOT NULL,
    ticket_id VARCHAR(255),
    ticket_system VARCHAR(50),
    message_text TEXT NOT NULL,
    direction VARCHAR(10) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_request_id ON messages(request_id);
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_created ON messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_ticket_id ON messages(ticket_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_ticket ON messages(thread_id, ticket_id);

-- Conversation threads table
CREATE TABLE IF NOT EXISTS conversation_threads (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(255) UNIQUE NOT NULL,
    channel VARCHAR(50),
    platform VARCHAR(50),
    user_id VARCHAR(255),
    team_id VARCHAR(255),
    title TEXT,
    primary_ticket_id VARCHAR(255),
    ticket_system VARCHAR(50),
    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversation_threads_thread_id ON conversation_threads(thread_id);
CREATE INDEX IF NOT EXISTS idx_conversation_threads_user_id ON conversation_threads(user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_threads_primary_ticket ON conversation_threads(primary_ticket_id);

-- Conversation summaries table
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id SERIAL PRIMARY KEY,
    conversation_id VARCHAR(100) UNIQUE NOT NULL,
    summary_text TEXT NOT NULL,
    unresolved_points JSONB DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversation_summaries_id ON conversation_summaries(conversation_id);

-- Conversation state table
CREATE TABLE IF NOT EXISTS conversation_state (
    id BIGSERIAL PRIMARY KEY,
    thread_id VARCHAR(255) UNIQUE NOT NULL,
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
    last_message_at TIMESTAMP,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversation_state_thread_id ON conversation_state(thread_id);
CREATE INDEX IF NOT EXISTS idx_conversation_state_last_message_at ON conversation_state(last_message_at);
CREATE INDEX IF NOT EXISTS idx_conversation_state_mode ON conversation_state(conversation_mode);

-- User profiles table
CREATE TABLE IF NOT EXISTS user_profiles (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    role VARCHAR(100),
    team VARCHAR(100),
    preferences JSONB DEFAULT '{}',
    vip_flag BOOLEAN DEFAULT FALSE,
    communication_style VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id);

CREATE TABLE IF NOT EXISTS user_style_profiles (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) UNIQUE NOT NULL,
    preferred_tone VARCHAR(50),
    preferred_verbosity VARCHAR(50),
    preferred_format VARCHAR(50),
    preferred_language VARCHAR(20),
    response_persona_hint TEXT,
    confidence_score FLOAT DEFAULT 0.0,
    sample_count INTEGER DEFAULT 0,
    last_inferred_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_style_profiles_user_id ON user_style_profiles(user_id);

CREATE TABLE IF NOT EXISTS user_style_signals (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    request_id VARCHAR(255),
    signal_type VARCHAR(50) NOT NULL,
    signal_value VARCHAR(100) NOT NULL,
    signal_strength FLOAT DEFAULT 0.5,
    source VARCHAR(50) NOT NULL,
    evidence JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_style_signals_user_id ON user_style_signals(user_id);
CREATE INDEX IF NOT EXISTS idx_style_signals_user_created ON user_style_signals(user_id, created_at);

-- Case memory table
CREATE TABLE IF NOT EXISTS case_memory (
    id SERIAL PRIMARY KEY,
    case_id VARCHAR(100) UNIQUE NOT NULL,
    status VARCHAR(50),
    owner VARCHAR(100),
    summary TEXT,
    open_items JSONB DEFAULT '[]',
    priority VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_case_memory_case_id ON case_memory(case_id);

-- Memory items table (episodic, semantic)
CREATE TABLE IF NOT EXISTS memory_items (
    id SERIAL PRIMARY KEY,
    memory_scope VARCHAR(50) NOT NULL,
    scope_id VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    embedding JSONB,
    confidence_score FLOAT DEFAULT 1.0,
    ttl_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_items_scope_id ON memory_items(memory_scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_scope ON memory_items(memory_scope);
CREATE INDEX IF NOT EXISTS idx_memory_items_ttl ON memory_items(ttl_at) WHERE ttl_at IS NOT NULL;

-- Audit logs table
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(100) UNIQUE NOT NULL,
    decision VARCHAR(50) NOT NULL,
    risk_level VARCHAR(20) NOT NULL,
    agents_used JSONB DEFAULT '[]',
    input_summary TEXT,
    output_summary TEXT,
    processing_time_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id ON audit_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_decision ON audit_logs(decision);

CREATE TABLE IF NOT EXISTS interaction_logs (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(255) UNIQUE NOT NULL,
    thread_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    ticket_id VARCHAR(255),
    ticket_system VARCHAR(50),
    input_text TEXT NOT NULL,
    output_text TEXT,
    intent VARCHAR(50),
    risk_level VARCHAR(20),
    confidence_score FLOAT DEFAULT 0.0,
    model_provider VARCHAR(50),
    model_name VARCHAR(100),
    kb_hit_count INTEGER DEFAULT 0,
    kb_sources JSONB DEFAULT '[]',
    approval_required BOOLEAN DEFAULT FALSE,
    approval_status VARCHAR(50),
    processing_latency_ms INTEGER,
    outcome_status VARCHAR(50),
    extra_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_request_id ON interaction_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_interaction_logs_thread_created ON interaction_logs(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_interaction_logs_user_created ON interaction_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_interaction_logs_ticket_id ON interaction_logs(ticket_id);

CREATE TABLE IF NOT EXISTS feedback_logs (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(255) NOT NULL,
    thread_id VARCHAR(255),
    user_id VARCHAR(255),
    ticket_id VARCHAR(255),
    ticket_system VARCHAR(50),
    feedback_type VARCHAR(50) NOT NULL,
    feedback_score FLOAT,
    feedback_label VARCHAR(100),
    feedback_text TEXT,
    edited_output_text TEXT,
    reviewer_id VARCHAR(255),
    extra_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feedback_logs_request ON feedback_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_feedback_logs_ticket_id ON feedback_logs(ticket_id);

CREATE TABLE IF NOT EXISTS response_learning_events (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255),
    thread_id VARCHAR(255),
    ticket_id VARCHAR(255),
    ticket_system VARCHAR(50),
    event_type VARCHAR(50) NOT NULL,
    event_payload JSONB NOT NULL DEFAULT '{}',
    processed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_response_learning_events_processed ON response_learning_events(processed, created_at);
CREATE INDEX IF NOT EXISTS idx_response_learning_events_ticket_id ON response_learning_events(ticket_id);

CREATE TABLE IF NOT EXISTS knowledge_candidates (
    id SERIAL PRIMARY KEY,
    source_request_id VARCHAR(255) NOT NULL,
    source_thread_id VARCHAR(255),
    ticket_id VARCHAR(255),
    ticket_system VARCHAR(50),
    extracted_title TEXT,
    extracted_content TEXT NOT NULL,
    category VARCHAR(100),
    tags JSONB DEFAULT '[]',
    confidence_score FLOAT DEFAULT 0.0,
    status VARCHAR(50) DEFAULT 'pending',
    reviewer_id VARCHAR(255),
    review_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP,
    promoted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_knowledge_candidates_request ON knowledge_candidates(source_request_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_candidates_ticket_id ON knowledge_candidates(ticket_id);

CREATE TABLE IF NOT EXISTS approval_requests (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(255) NOT NULL,
    thread_id VARCHAR(255),
    user_id VARCHAR(255),
    ticket_id VARCHAR(255),
    ticket_system VARCHAR(50),
    proposed_response TEXT NOT NULL,
    reason TEXT,
    risk_level VARCHAR(20),
    confidence_score FLOAT DEFAULT 0.0,
    status VARCHAR(50) DEFAULT 'pending',
    approver_id VARCHAR(255),
    action_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_request_id ON approval_requests(request_id);
CREATE INDEX IF NOT EXISTS idx_approval_requests_ticket_id ON approval_requests(ticket_id);

CREATE TABLE IF NOT EXISTS thread_ticket_links (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(255) NOT NULL,
    ticket_id VARCHAR(255) NOT NULL,
    ticket_system VARCHAR(50) NOT NULL DEFAULT 'servicedesk_plus',
    relation_type VARCHAR(50) NOT NULL DEFAULT 'primary',
    confidence_score FLOAT DEFAULT 1.0,
    linked_by VARCHAR(50) NOT NULL DEFAULT 'system',
    extra_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(thread_id, ticket_id, ticket_system)
);

CREATE INDEX IF NOT EXISTS idx_thread_ticket_links_thread_id ON thread_ticket_links(thread_id);
CREATE INDEX IF NOT EXISTS idx_thread_ticket_links_ticket_id ON thread_ticket_links(ticket_id);

-- Function to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for auto-update
CREATE TRIGGER update_conversation_summaries_updated_at
    BEFORE UPDATE ON conversation_summaries
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_conversation_threads_updated_at
    BEFORE UPDATE ON conversation_threads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_user_style_profiles_updated_at
    BEFORE UPDATE ON user_style_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_case_memory_updated_at
    BEFORE UPDATE ON case_memory
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_memory_items_updated_at
    BEFORE UPDATE ON memory_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_thread_ticket_links_updated_at
    BEFORE UPDATE ON thread_ticket_links
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- Knowledge Base Tables
-- ============================================

-- Knowledge Policies (SOPs, guidelines)
CREATE TABLE IF NOT EXISTS knowledge_policies (
    id SERIAL PRIMARY KEY,
    policy_id VARCHAR(100) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,
    tags JSONB DEFAULT '[]',
    version VARCHAR(20) DEFAULT '1.0',
    is_active BOOLEAN DEFAULT TRUE,
    embedding JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_policies_policy_id ON knowledge_policies(policy_id);
CREATE INDEX IF NOT EXISTS idx_policies_category ON knowledge_policies(category);
CREATE INDEX IF NOT EXISTS idx_policies_category_active ON knowledge_policies(category, is_active);

-- Knowledge FAQs
CREATE TABLE IF NOT EXISTS knowledge_faqs (
    id SERIAL PRIMARY KEY,
    question_id VARCHAR(100) UNIQUE NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,
    tags JSONB DEFAULT '[]',
    keywords JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT TRUE,
    usage_count INTEGER DEFAULT 0,
    embedding JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_faqs_question_id ON knowledge_faqs(question_id);
CREATE INDEX IF NOT EXISTS idx_faqs_category ON knowledge_faqs(category);
CREATE INDEX IF NOT EXISTS idx_faqs_category_active ON knowledge_faqs(category, is_active);

-- Knowledge Guides
CREATE TABLE IF NOT EXISTS knowledge_guides (
    id SERIAL PRIMARY KEY,
    guide_id VARCHAR(100) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    guide_type VARCHAR(50) NOT NULL,
    category VARCHAR(100) NOT NULL,
    tags JSONB DEFAULT '[]',
    steps JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT TRUE,
    embedding JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_guides_guide_id ON knowledge_guides(guide_id);
CREATE INDEX IF NOT EXISTS idx_guides_type ON knowledge_guides(guide_type);
CREATE INDEX IF NOT EXISTS idx_guides_type_category ON knowledge_guides(guide_type, category);

-- Knowledge Documents
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id SERIAL PRIMARY KEY,
    document_id VARCHAR(100) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    category VARCHAR(100) NOT NULL,
    tags JSONB DEFAULT '[]',
    extra_metadata JSONB DEFAULT '{}',
    embedding JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_document_id ON knowledge_documents(document_id);
CREATE INDEX IF NOT EXISTS idx_documents_type ON knowledge_documents(document_type);
CREATE INDEX IF NOT EXISTS idx_documents_category ON knowledge_documents(category);

-- Triggers for knowledge tables
CREATE TRIGGER update_knowledge_policies_updated_at
    BEFORE UPDATE ON knowledge_policies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_knowledge_faqs_updated_at
    BEFORE UPDATE ON knowledge_faqs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_knowledge_guides_updated_at
    BEFORE UPDATE ON knowledge_guides
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_knowledge_documents_updated_at
    BEFORE UPDATE ON knowledge_documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
