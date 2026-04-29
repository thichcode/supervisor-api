from pydantic import BaseModel, ConfigDict, Field
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
    SYSTEM_QUERY = "system_query"
    GUIDE_REQUEST = "guide_request"


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
    chat_type: Optional[str] = None
    chat_scope: Optional[str] = None
    group_chat: Optional[bool] = None
    platform: Optional[str] = None


class ConversationStateInfo(BaseModel):
    thread_id: str
    active_topic_title: Optional[str] = None
    active_topic_summary: Optional[str] = None
    conversation_mode: str = "continuation"
    continuity_score: float = 0.5
    last_user_intent: Optional[str] = None
    last_assistant_intent: Optional[str] = None
    open_loops: list[dict] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    recent_decisions: list[dict] = Field(default_factory=list)
    state_json: dict = Field(default_factory=dict)
    last_message_at: Optional[datetime] = None
    turn_count: int = 0
    platform: Optional[str] = None
    chat_type: Optional[str] = None
    chat_scope: Optional[str] = None
    group_chat: Optional[bool] = None


class CaseInfo(BaseModel):
    case_id: Optional[str] = None
    ticket_id: Optional[str] = None
    ticket_system: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None


class AttachmentInfo(BaseModel):
    type: str = Field(default="file", description="Attachment type such as image, file, audio, video")
    name: Optional[str] = None
    content_type: Optional[str] = None
    url: Optional[str] = None
    content_url: Optional[str] = None
    file_url: Optional[str] = None
    base64_data: Optional[str] = None
    ocr_text: Optional[str] = None
    text: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class MessageInfo(BaseModel):
    text: str
    timestamp: datetime = Field(default_factory=utc_now)
    attachments: list[AttachmentInfo] = Field(default_factory=list)


class InputPayload(BaseModel):
    request_id: str
    source: str = "ms_teams"
    timestamp: str
    user: UserInfo
    conversation: ConversationInfo
    conversation_state: Optional[ConversationStateInfo] = None
    case: Optional[CaseInfo] = None
    message: MessageInfo


class OutputPayload(BaseModel):
    request_id: str = ""
    status: str = "completed"
    answer: str = ""
    confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    risk_level: str = "low"
    metadata: dict = Field(default_factory=lambda: {
        "intent": "",
        "agents_used": [],
        "processing_time_ms": 0
    })
    message: Optional[MessageInfo] = None


class IntentClassification(BaseModel):
    intent: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = "fallback"


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
    ticket_id: Optional[str] = None
    ticket_system: Optional[str] = None
    message_type: MessageType = MessageType.TEXT
    metadata: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str
    status: str
    customer_reply: str = Field(description="Reply to send to the customer")
    message: str = Field(default="", description="Backward-compatible alias for customer_reply")
    internal_note: Optional[str] = Field(default="", description="Internal note for support team (not sent to customer)")
    message_type: MessageType
    confidence: float
    attachments: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    # ← FIX: track delivery so Telegram approval vs direct reply is distinguishable
    delivery_status: str = Field(
        default="direct",
        description="direct | pending_approval | skipped",
    )
    approval_request_id: Optional[str] = Field(
        default=None,
        description="Set when delivery_status=pending_approval",
    )

    def model_post_init(self, __context):
        if not self.message and self.customer_reply:
            object.__setattr__(self, "message", self.customer_reply)
        elif not self.customer_reply and self.message:
            object.__setattr__(self, "customer_reply", self.message)


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
    # Vote fields for KB quality tracking
    voted_by: Optional[str] = None
    voted_at: Optional[datetime] = None
    vote: Optional[str] = None
    user_feedback: Optional[str] = None


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


class ApprovalVoteRequest(BaseModel):
    """Request body for voting on an approved response"""
    vote: str = Field(..., description="Vote: agree, change, or skip")
    user_id: str = Field(..., description="User who is voting")
    feedback: Optional[str] = Field(None, description="Optional feedback comment")


class FeedbackType(str, Enum):
    EXPLICIT_RATING = "explicit_rating"
    APPROVAL = "approval"
    REJECTION = "rejection"
    HUMAN_EDIT = "human_edit"
    USER_REPLY = "user_reply"


class FeedbackCreateRequest(BaseModel):
    request_id: str
    thread_id: Optional[str] = None
    user_id: Optional[str] = None
    ticket_id: Optional[str] = None
    ticket_system: Optional[str] = "servicedesk_plus"
    feedback_type: FeedbackType
    feedback_score: Optional[float] = None
    feedback_label: Optional[str] = None
    feedback_text: Optional[str] = None
    edited_output_text: Optional[str] = None
    reviewer_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class FeedbackResponse(BaseModel):
    id: int
    request_id: str
    feedback_type: FeedbackType
    feedback_label: Optional[str] = None
    stored: bool = True
    learning_event_created: bool = True


class UserStyleProfileResponse(BaseModel):
    user_id: str
    preferred_tone: Optional[str] = None
    preferred_verbosity: Optional[str] = None
    preferred_format: Optional[str] = None
    preferred_language: Optional[str] = None
    response_persona_hint: Optional[str] = None
    confidence_score: float = 0.0
    sample_count: int = 0
    updated_at: Optional[datetime] = None
