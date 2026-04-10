from sqlalchemy import Column, String, Text, Integer, Float, DateTime, Boolean, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime
from src.db.session import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(100), nullable=False, index=True)
    user_id = Column(String(100), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    message_text = Column(Text, nullable=False)
    direction = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_messages_thread_created", "thread_id", "created_at"),
    )


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(100), nullable=False, unique=True, index=True)
    summary_text = Column(Text, nullable=False)
    unresolved_points = Column(JSON, default=list)
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
    doc_id = Column(String(100), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    doc_type = Column(String(50), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    tags = Column(JSON, default=list)
    metadata = Column(JSON, default=dict)
    embedding = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
