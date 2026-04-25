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
    offset: int = 0


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
    template_id: str = ""
    template_label: str = ""
    template_score: float = 0.0
    template_terms: List[str] = Field(default_factory=list)
    clarification: dict = Field(default_factory=dict)


class KnowledgeStats(BaseModel):
    policies_count: int
    faqs_count: int
    guides_count: int
    documents_count: int
    categories: List[dict]


class DocumentCreate(BaseModel):
    document_id: str
    title: str
    content: str
    document_type: str
    category: str
    tags: List[str] = []
    file_url: Optional[str] = None


class DocumentResponse(BaseModel):
    document_id: str
    title: str
    content: str
    document_type: str
    category: str
    tags: List[str]
    file_url: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BulkImportRequest(BaseModel):
    policies: List[PolicyCreate] = []
    faqs: List[FAQCreate] = []
    guides: List[GuideCreate] = []
    documents: List[DocumentCreate] = []


class BulkImportResponse(BaseModel):
    status: str
    imported: dict
    errors: List[dict] = []


# ============ File Processing Schemas ============

class FileProcessRequest(BaseModel):
    """Request to process a file for knowledge base"""
    file_path: str = Field(description="Path to file on server")
    file_url: Optional[str] = Field(None, description="URL to download file")
    knowledge_type: str = Field("document", description="Type: policy, faq, guide, document")
    category: str = Field("general", description="Category for classification")
    tags: List[str] = Field(default_factory=list, description="Manual tags")
    auto_classify: bool = Field(True, description="Auto-detect knowledge type using LLM")
    extract_metadata: bool = Field(True, description="Extract metadata from file")


class FileProcessResponse(BaseModel):
    """Response from file processing"""
    status: str
    file_name: str
    file_size: int
    extracted_content: str
    knowledge_type: str
    category: str
    suggested_tags: List[str]
    extracted_fields: dict
    chunks_count: int
    embeddings_generated: bool
    processing_time_ms: int
    errors: List[str] = []


class BatchFileRequest(BaseModel):
    """Batch file processing request"""
    files: List[FileProcessRequest]
    import_to_knowledge_base: bool = Field(True, description="Auto-import after processing")


class BatchFileResponse(BaseModel):
    """Batch file processing response"""
    status: str
    total_files: int
    successful: int
    failed: int
    results: List[FileProcessResponse]