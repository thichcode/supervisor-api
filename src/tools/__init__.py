"""
Supervisor Tools Package
Collection of tools for the supervisor API
"""

# RAG Pipeline
from src.tools.rag_pipeline import (
    RAGPipeline,
    RAGConfig,
    Document,
    SearchResult,
    BM25Indexer,
    EmbeddingModel,
    get_rag_pipeline,
    create_document,
)

# File Processor
from src.tools.file_processor import (
    FileProcessor,
    FileContent,
    TableData,
    get_file_processor,
)

# API Client
from src.tools.api_client import (
    APIClient,
    APIResponse,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitOpenError,
    RateLimiter,
    RetryConfig,
    RateLimitConfig,
    create_api_client,
)

# Scheduler
from src.tools.scheduler import (
    Scheduler,
    Job,
    JobResult,
    JobStatus,
    JobType,
    get_scheduler,
    schedule_report_generation,
    schedule_data_sync,
    CRON_EXAMPLES,
)

# Notification
from src.tools.notification import (
    NotificationSender,
    NotificationMessage,
    ChannelConfig,
    Channel,
    TemplateRenderer,
    get_notification_sender,
    create_telegram_sender,
    create_slack_sender,
    create_teams_sender,
    create_email_sender,
)

# Audit Logger
from src.tools.audit_logger import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    RiskLevel,
    get_audit_logger,
    audited,
)

# Validators
from src.tools.validators import (
    ValidationError,
    ValidationResult,
    FieldValidator,
    SchemaValidator,
    JSONSchemaValidator,
    DataSanitizer,
    OutputFormatter,
    CommonValidators,
    validate,
    validate_email,
    validate_json,
    validate_uuid,
    SCHEMAS,
)

# n8n Connector
from src.tools.n8n_connector import (
    N8NConnector,
    ActionType,
    RiskLevel as N8NRiskLevel,
    ActionRequest,
    SystemAction,
    SYSTEM_ACTIONS,
    get_n8n_connector,
)

from src.tools.n8n_tool import (
    N8NTool,
    get_n8n_tool,
)

# URL Fetcher
from src.tools.url_fetcher import (
    URLFetcher,
    URLInfo,
    INTERNAL_DOMAINS,
    TRUSTED_DOMAINS,
)

__all__ = [
    # RAG
    "RAGPipeline",
    "RAGConfig",
    "Document",
    "SearchResult",
    "BM25Indexer",
    "EmbeddingModel",
    "get_rag_pipeline",
    "create_document",
    
    # File
    "FileProcessor",
    "FileContent",
    "TableData",
    "get_file_processor",
    
    # API Client
    "APIClient",
    "APIResponse",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "CircuitOpenError",
    "RateLimiter",
    "RetryConfig",
    "RateLimitConfig",
    "create_api_client",
    
    # Scheduler
    "Scheduler",
    "Job",
    "JobResult",
    "JobStatus",
    "JobType",
    "get_scheduler",
    "schedule_report_generation",
    "schedule_data_sync",
    "CRON_EXAMPLES",
    
    # Notification
    "NotificationSender",
    "NotificationMessage",
    "ChannelConfig",
    "Channel",
    "TemplateRenderer",
    "get_notification_sender",
    "create_telegram_sender",
    "create_slack_sender",
    "create_teams_sender",
    "create_email_sender",
    
    # Audit
    "AuditLogger",
    "AuditEvent",
    "AuditEventType",
    "RiskLevel",
    "get_audit_logger",
    "audited",
    
    # Validators
    "ValidationError",
    "ValidationResult",
    "FieldValidator",
    "SchemaValidator",
    "JSONSchemaValidator",
    "DataSanitizer",
    "OutputFormatter",
    "CommonValidators",
    "validate",
    "validate_email",
    "validate_json",
    "validate_uuid",
    "SCHEMAS",
    
    # n8n
    "N8NConnector",
    "ActionType",
    "N8NRiskLevel",
    "ActionRequest",
    "SystemAction",
    "SYSTEM_ACTIONS",
    "get_n8n_connector",
    "N8NTool",
    "get_n8n_tool",
    
    # URL Fetcher
    "URLFetcher",
    "URLInfo",
    "INTERNAL_DOMAINS",
    "TRUSTED_DOMAINS",
]
