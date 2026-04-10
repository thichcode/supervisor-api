-- Initialize supervisor database schema

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(100) NOT NULL,
    user_id VARCHAR(100) NOT NULL,
    thread_id VARCHAR(100) NOT NULL,
    message_text TEXT NOT NULL,
    direction VARCHAR(10) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_request_id ON messages(request_id);
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_created ON messages(thread_id, created_at);

-- Conversation summaries table
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id SERIAL PRIMARY KEY,
    conversation_id VARCHAR(100) UNIQUE NOT NULL,
    summary_text TEXT NOT NULL,
    unresolved_points JSONB DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversation_summaries_id ON conversation_summaries(conversation_id);

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

CREATE TRIGGER update_case_memory_updated_at
    BEFORE UPDATE ON case_memory
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_memory_items_updated_at
    BEFORE UPDATE ON memory_items
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
    doc_id VARCHAR(100) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    doc_type VARCHAR(50) NOT NULL,
    category VARCHAR(100) NOT NULL,
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    embedding JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_doc_id ON knowledge_documents(doc_id);
CREATE INDEX IF NOT EXISTS idx_documents_type ON knowledge_documents(doc_type);
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
