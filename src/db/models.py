from sqlalchemy import Column, String, Text, Integer, Float, DateTime, Boolean, JSON, Index
from sqlalchemy.sql import func
from src.db.session import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(100), nullable=False, index=True)
    user_id = Column(String(100), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    ticket_id = Column(String(255), nullable=True, index=True)
    ticket_system = Column(String(50), nullable=True, index=True)
    message_text = Column(Text, nullable=False)
    direction = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_messages_thread_created", "thread_id", "created_at"),
        Index("idx_messages_thread_ticket", "thread_id", "ticket_id"),
    )


class ConversationThread(Base):
    __tablename__ = "conversation_threads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(255), nullable=False, unique=True, index=True)
    channel = Column(String(50), nullable=True)
    platform = Column(String(50), nullable=True)
    user_id = Column(String(255), nullable=True, index=True)
    team_id = Column(String(255), nullable=True, index=True)
    title = Column(Text, nullable=True)
    primary_ticket_id = Column(String(255), nullable=True, index=True)
    ticket_system = Column(String(50), nullable=True, index=True)
    last_message_at = Column(DateTime, default=func.now())
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(100), nullable=False, unique=True, index=True)
    summary_text = Column(Text, nullable=False)
    unresolved_points = Column(JSON, default=list)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ConversationState(Base):
    __tablename__ = "conversation_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(255), nullable=False, unique=True, index=True)
    active_topic_title = Column(Text, nullable=True)
    active_topic_summary = Column(Text, nullable=True)
    conversation_mode = Column(String(50), default="continuation")
    continuity_score = Column(Float, default=0.5)
    last_user_intent = Column(Text, nullable=True)
    last_assistant_intent = Column(Text, nullable=True)
    open_loops = Column(JSON, default=list)
    key_entities = Column(JSON, default=list)
    recent_decisions = Column(JSON, default=list)
    state_json = Column(JSON, default=dict)
    last_message_at = Column(DateTime, nullable=True)
    turn_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, unique=True, index=True)
    display_name = Column(String(255))
    role = Column(String(100))
    team = Column(String(100))
    preferences = Column(JSON, default=dict)
    vip_flag = Column(Boolean, default=False)
    communication_style = Column(String(50))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class CaseMemory(Base):
    __tablename__ = "case_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(100), nullable=False, unique=True, index=True)
    status = Column(String(50))
    owner = Column(String(100))
    summary = Column(Text)
    open_items = Column(JSON, default=list)
    priority = Column(String(20))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_scope = Column(String(50), nullable=False, index=True)
    scope_id = Column(String(100), nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=True)
    confidence_score = Column(Float, default=1.0)
    ttl_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_memory_scope_id", "memory_scope", "scope_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(100), nullable=False, unique=True, index=True)
    decision = Column(String(50), nullable=False)
    risk_level = Column(String(20), nullable=False)
    agents_used = Column(JSON, default=list)
    input_summary = Column(Text)
    output_summary = Column(Text)
    processing_time_ms = Column(Integer)
    created_at = Column(DateTime, default=func.now())


class InteractionLog(Base):
    __tablename__ = "interaction_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(255), nullable=False, unique=True, index=True)
    thread_id = Column(String(255), nullable=False, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    ticket_id = Column(String(255), nullable=True, index=True)
    ticket_system = Column(String(50), nullable=True, index=True)
    input_text = Column(Text, nullable=False)
    output_text = Column(Text, nullable=True)
    intent = Column(String(50), nullable=True, index=True)
    risk_level = Column(String(20), nullable=True, index=True)
    confidence_score = Column(Float, default=0.0)
    model_provider = Column(String(50), nullable=True)
    model_name = Column(String(100), nullable=True)
    kb_hit_count = Column(Integer, default=0)
    kb_sources = Column(JSON, default=list)
    traffic_class = Column(String(32), nullable=True, index=True)
    approval_required = Column(Boolean, default=False)
    approval_status = Column(String(50), nullable=True)
    processing_latency_ms = Column(Integer, nullable=True)
    outcome_status = Column(String(50), nullable=True, index=True)
    extra_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_interaction_logs_thread_ticket", "thread_id", "ticket_id"),
    )


class UserStyleProfile(Base):
    __tablename__ = "user_style_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, unique=True, index=True)
    preferred_tone = Column(String(50), nullable=True)
    preferred_verbosity = Column(String(50), nullable=True)
    preferred_format = Column(String(50), nullable=True)
    preferred_language = Column(String(20), nullable=True)
    response_persona_hint = Column(Text, nullable=True)
    confidence_score = Column(Float, default=0.0)
    sample_count = Column(Integer, default=0)
    last_inferred_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class UserStyleSignal(Base):
    __tablename__ = "user_style_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, index=True)
    request_id = Column(String(255), nullable=True, index=True)
    signal_type = Column(String(50), nullable=False, index=True)
    signal_value = Column(String(100), nullable=False)
    signal_strength = Column(Float, default=0.5)
    source = Column(String(50), nullable=False)
    evidence = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_style_signals_user_created", "user_id", "created_at"),
    )


class FeedbackLog(Base):
    __tablename__ = "feedback_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(255), nullable=False, index=True)
    thread_id = Column(String(255), nullable=True, index=True)
    user_id = Column(String(255), nullable=True, index=True)
    ticket_id = Column(String(255), nullable=True, index=True)
    ticket_system = Column(String(50), nullable=True, index=True)
    feedback_type = Column(String(50), nullable=False, index=True)
    feedback_score = Column(Float, nullable=True)
    feedback_label = Column(String(100), nullable=True)
    feedback_text = Column(Text, nullable=True)
    edited_output_text = Column(Text, nullable=True)
    reviewer_id = Column(String(255), nullable=True)
    extra_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())


class ResponseLearningEvent(Base):
    __tablename__ = "response_learning_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(255), nullable=False, index=True)
    user_id = Column(String(255), nullable=True, index=True)
    thread_id = Column(String(255), nullable=True, index=True)
    ticket_id = Column(String(255), nullable=True, index=True)
    ticket_system = Column(String(50), nullable=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    dedupe_key = Column(String(64), nullable=True, unique=True, index=True)
    event_payload = Column(JSON, nullable=False, default=dict)
    processed = Column(Boolean, default=False, index=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    claimed_by = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, default=func.now())
    processed_at = Column(DateTime, nullable=True)


class KnowledgeCandidate(Base):
    __tablename__ = "knowledge_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_request_id = Column(String(255), nullable=False, index=True)
    source_thread_id = Column(String(255), nullable=True, index=True)
    ticket_id = Column(String(255), nullable=True, index=True)
    ticket_system = Column(String(50), nullable=True, index=True)
    extracted_title = Column(Text, nullable=True)
    extracted_content = Column(Text, nullable=False)
    category = Column(String(100), nullable=True, index=True)
    tags = Column(JSON, default=list)
    confidence_score = Column(Float, default=0.0)
    status = Column(String(50), default="pending", index=True)
    reviewer_id = Column(String(255), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    reviewed_at = Column(DateTime, nullable=True)
    promoted_at = Column(DateTime, nullable=True)


class ThreadTicketLink(Base):
    __tablename__ = "thread_ticket_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(255), nullable=False, index=True)
    ticket_id = Column(String(255), nullable=False, index=True)
    ticket_system = Column(String(50), nullable=False, default="servicedesk_plus", index=True)
    relation_type = Column(String(50), nullable=False, default="primary")
    confidence_score = Column(Float, default=1.0)
    linked_by = Column(String(50), nullable=False, default="system")
    extra_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_thread_ticket_unique", "thread_id", "ticket_id", "ticket_system", unique=True),
    )


class ApprovalRequestRecord(Base):
    __tablename__ = "approval_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(255), nullable=False, index=True)
    thread_id = Column(String(255), nullable=True, index=True)
    user_id = Column(String(255), nullable=True, index=True)
    ticket_id = Column(String(255), nullable=True, index=True)
    ticket_system = Column(String(50), nullable=True, index=True)
    proposed_response = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    risk_level = Column(String(20), nullable=True)
    confidence_score = Column(Float, default=0.0)
    status = Column(String(50), default="pending", index=True)
    approver_id = Column(String(255), nullable=True)
    action_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    acted_at = Column(DateTime, nullable=True)


class KnowledgePolicy(Base):
    __tablename__ = "knowledge_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_id = Column(String(100), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(100), nullable=False, index=True)
    tags = Column(JSON, default=list)
    version = Column(String(20), default="1.0")
    is_active = Column(Boolean, default=True)
    embedding = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_policies_category_active", "category", "is_active"),
    )


class KnowledgeFAQ(Base):
    __tablename__ = "knowledge_faqs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(String(100), nullable=False, unique=True, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    category = Column(String(100), nullable=False, index=True)
    tags = Column(JSON, default=list)
    keywords = Column(JSON, default=list)
    is_active = Column(Boolean, default=True)
    usage_count = Column(Integer, default=0)
    embedding = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_faqs_category_active", "category", "is_active"),
    )


class KnowledgeGuide(Base):
    __tablename__ = "knowledge_guides"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guide_id = Column(String(100), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    guide_type = Column(String(50), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    tags = Column(JSON, default=list)
    steps = Column(JSON, default=list)
    is_active = Column(Boolean, default=True)
    embedding = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_guides_type_category", "guide_type", "category"),
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(100), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    document_type = Column(String(50), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    tags = Column(JSON, default=list)
    file_url = Column(String(500), nullable=True)
    extra_metadata = Column(JSON, default=dict)
    embedding = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(String(100), nullable=False, unique=True, index=True)
    alert_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    extra_metadata = Column(JSON, default=dict)
    status = Column(String(20), default="active", index=True)
    acknowledged_by = Column(String(100), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())


class Config(Base):
    __tablename__ = "configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(50), default="general", index=True)
    is_sensitive = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ResponsePattern(Base):
    """
    Stores approved Q&A patterns for future matching.
    When a response is approved, we store the question-answer pair
    so similar future questions can use this response directly.
    """
    __tablename__ = "response_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_hash = Column(String(64), nullable=False, unique=True, index=True)
    question_text = Column(Text, nullable=False)
    answer_text = Column(Text, nullable=False)
    user_id = Column(String(100), nullable=True, index=True)
    thread_id = Column(String(255), nullable=True, index=True)
    team_id = Column(String(100), nullable=True, index=True)
    intent = Column(String(50), nullable=True, index=True)
    confidence_score = Column(Float, default=1.0)
    usage_count = Column(Integer, default=0)
    last_used_at = Column(DateTime, default=func.now())
    approved_by = Column(String(100), nullable=True)
    source_request_id = Column(String(100), nullable=True)
    embedding = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_patterns_team_active", "team_id", "is_active"),
        Index("idx_patterns_intent_active", "intent", "is_active"),
        Index("idx_patterns_usage", "usage_count", "last_used_at"),
    )
