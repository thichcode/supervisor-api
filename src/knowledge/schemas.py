from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class KnowledgeType(str, Enum):
    POLICY = "policy"
    FAQ = "faq"
    GUIDE = "guide"
    DOCUMENT = "document"


class PolicyCreate(BaseModel):
    policy_id: str
    title: str
    content: str
    category: str
    tags: List[str] = []
    version: str = "1.0"


class PolicyResponse(BaseModel):
    policy_id: str
    title: str
    content: str
    category: str
    tags: List[str]
    version: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FAQCreate(BaseModel):
    question_id: str
    question: str
    answer: str
    category: str
    tags: List[str] = []
    keywords: List[str] = []


class FAQResponse(BaseModel):
    question_id: str
    question: str
    answer: str
    category: str
    tags: List[str]
    keywords: List[str]
    is_active: bool
    usage_count: int
    created_at: datetime
    updated_at: datetime


class GuideCreate(BaseModel):
    guide_id: str
    title: str
    content: str
    guide_type: str
    category: str
    tags: List[str] = []
    steps: List[dict] = []


class GuideResponse(BaseModel):
    guide_id: str
    title: str
    content: str
    guide_type: str
    category: str
    tags: List[str]
    steps: List[dict]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeSearchRequest(BaseModel):
    query: str
    search_type: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    limit: int = 5


class KnowledgeSearchResult(BaseModel):
    knowledge_type: KnowledgeType
    id: str
    title: str
    content: str
    category: str
    tags: List[str]
    similarity: float = 1.0
    metadata: dict = Field(default_factory=dict)


class KnowledgeSearchResponse(BaseModel):
    results: List[KnowledgeSearchResult]
    total: int
    search_type: str
    query: str


class KnowledgeStats(BaseModel):
    policies_count: int
    faqs_count: int
    guides_count: int
    documents_count: int
    categories: List[dict]