from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


def utc_now() -> datetime:
    return datetime.now().replace(tzinfo=None)


class IntentType(str, Enum):
    FAQ = "faq"
    POLICY = "policy"
    SUPPORT_CASE = "support_case"
    ANALYSIS = "analysis"
    EXECUTIVE_REQUEST = "executive_request"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryScopeType(str, Enum):
    CONVERSATION = "conversation"
    USER = "user"
    CASE = "case"
    EPISODIC = "episodic"


class UserInfo(BaseModel):
    id: str
    display_name: str
    role: Optional[str] = None
    team: Optional[str] = None
    vip_flag: bool = False
    preferences: dict = Field(default_factory=dict)


class ConversationInfo(BaseModel):
    thread_id: str
    message_id: str
    summary: Optional[str] = None
    unresolved_points: list[str] = Field(default_factory=list)


class CaseInfo(BaseModel):
    case_id: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None


class MessageInfo(BaseModel):
    text: str
    timestamp: datetime = Field(default_factory=utc_now)


class InputPayload(BaseModel):
    request_id: str
    source: str = "ms_teams"
    timestamp: str
    user: UserInfo
    conversation: ConversationInfo
    case: Optional[CaseInfo] = None
    message: MessageInfo


class OutputPayload(BaseModel):
    request_id: str
    status: str = "completed"
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: str
    metadata: dict = Field(default_factory=lambda: {
        "intent": "",
        "agents_used": [],
        "processing_time_ms": 0
    })


class IntentClassification(BaseModel):
    intent: IntentType
    confidence: float = Field(ge=0.0, le=1.0)


class RiskEvaluation(BaseModel):
    risk_level: RiskLevel
    flags: list[str] = Field(default_factory=list)


class MemoryItem(BaseModel):
    id: Optional[int] = None
    memory_scope: MemoryScopeType
    scope_id: str
    content: str
    embedding: Optional[list[float]] = None
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    ttl_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AuditLog(BaseModel):
    id: Optional[int] = None
    request_id: str
    decision: str
    risk_level: str
    agents_used: list[str] = Field(default_factory=list)
    input_summary: str
    output_summary: str
    processing_time_ms: int
    created_at: datetime = Field(default_factory=utc_now)


class MessageType(str, Enum):
    TEXT = "text"
    GUIDELINE = "guideline"
    SYSTEM_QUERY = "system_query"
    NOTIFICATION = "notification"


class ChatRequest(BaseModel):
    user_id: str
    display_name: str
    message: str
    thread_id: Optional[str] = None
    case_id: Optional[str] = None
    message_type: MessageType = MessageType.TEXT
    metadata: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    request_id: str
    status: str
    message: str
    message_type: MessageType
    confidence: float
    attachments: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SystemQueryRequest(BaseModel):
    query: str
    query_type: str = Field(default="user_info", description="user_info, case_info, general")
    user_id: Optional[str] = None
    case_id: Optional[str] = None


class SystemQueryResponse(BaseModel):
    results: dict
    confidence: float
    metadata: dict = Field(default_factory=dict)


class GuideDeliveryRequest(BaseModel):
    user_id: str
    display_name: str
    guide_id: str
    guide_title: str
    guide_content: str
    thread_id: Optional[str] = None


class GuideDeliveryResponse(BaseModel):
    status: str
    guide_id: str
    delivered: bool
    message: str
    metadata: dict = Field(default_factory=dict)


class CallbackRequest(BaseModel):
    original_request_id: str
    user_id: str
    message: str
    callback_url: str
    method: str = "POST"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    id: Optional[str] = None
    request_id: str
    user_id: str
    display_name: str
    original_message: str
    ai_response: str
    confidence: float
    threshold: float = 0.9
    status: ApprovalStatus = ApprovalStatus.PENDING
    action_type: str = Field(default="send_message", description="send_message, deliver_guide, system_query")
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_comment: Optional[str] = None


class ApprovalRequestResponse(BaseModel):
    approval_id: str
    request_id: str
    status: ApprovalStatus
    message: str
    confidence: float
    threshold: float
    created_at: datetime


class ApprovalListResponse(BaseModel):
    approvals: list[ApprovalRequestResponse]
    total: int
    pending_count: int


class ApprovalActionRequest(BaseModel):
    action: str = Field(..., description="approve or reject")
    comment: Optional[str] = None
    reviewed_by: str = Field(..., description="Who is approving/rejecting")
