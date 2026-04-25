from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Multi-Agent Supervisor System"
    app_version: str = "1.0.0"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    app_workers: int = 4
    app_env: Literal["development", "staging", "production"] = "development"
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "supervisor_db"
    db_user: str = "postgres"
    db_password: str = ""
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_pool_size: int = 10

    # Independent secrets - each must be different in production
    webhook_input_secret: str = ""  # Legacy - for backward compatibility only
    jwt_secret: str = ""  # JWT signing - MUST be independent
    hmac_secret: str = ""  # HMAC validation - MUST be independent  
    api_keys: str = ""  # Comma-separated API keys - MUST be independent

    power_automate_webhook_url: str = ""
    webhook_timeout: int = 30

    # Telegram approval notifications / gateway
    telegram_bot_token: str = ""
    telegram_approval_chat_ids: str = ""  # Comma-separated chat IDs or @channels
    telegram_parse_mode: str = "Markdown"
    approval_notification_cooldown_seconds: int = 0  # 0 = unlimited; e.g. 60 => 1/min per channel

    agent_timeout: int = 10
    agent_retry: int = 1

    memory_conversation_ttl: int = 86400
    memory_summary_ttl: int = 604800
    memory_max_tokens: int = 4000
    mempalace_enabled: bool = False
    mempalace_path: str = ""
    mempalace_top_k: int = 3
    mempalace_timeout_seconds: float = 2.0
    mempalace_retry_attempts: int = 2
    mempalace_circuit_failure_threshold: int = 3
    mempalace_circuit_success_threshold: int = 2
    mempalace_circuit_timeout_seconds: float = 30.0
    file_memory_enabled: bool = False
    file_memory_path: str = ""
    
    # Extra hosts for Docker (e.g., host.docker.internal:host-gateway)
    extra_hosts: list[str] = ["host.docker.internal:host-gateway"]

    # LLM Provider Configuration
    llm_provider: str = ""  # "ollama", "openai", or "azure" (auto-detect if empty)
    openai_api_key: str = ""
    llm_model: str = "llama3"  # Default to Ollama for Vietnamese
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2000
    llm_healthcheck_enabled: bool = False

    # Ollama Configuration (for self-hosted Vietnamese models)
    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "llama3"
    ollama_timeout: int = 320
    
    # Image Processing Model (separate from main LLM for OCR/tasks)
    ollama_image_model: str = "llama3.1-vision"

    # Azure OpenAI Configuration (optional)
    azure_openai_endpoint: str = ""
    azure_openai_key: str = ""
    azure_openai_api_version: str = "2024-02-01"
    azure_deployment_name: str = ""

    rate_limit_requests: int = 100
    rate_limit_window: int = 60

    log_level: str = "INFO"
    
    # Recommended AI Models for different use cases
    # Override via LLM_MODEL env var
    recommended_models: dict = {
        "faq": "llama3",           # Quick factual answers
        "policy": "llama3",        # Policy interpretation
        "support_case": "llama3", # Technical support
        "analysis": "llama3",     # Data analysis
        "executive": "llama3",    # High-priority executive
        "default": "llama3",
    }
    
    # Keyword patterns for intent classification and risk evaluation
    executive_keywords: list[str] = [
        # English
        "ceo", "cto", "cfo", "coo", "director", "vp", "head of", "manager",
        "urgent", "asap", "critical", "important", "priority",
        # Vietnamese
        "sếp", "giám đốc", "trưởng phòng", "quản lý", "lãnh đạo",
        "ban lãnh đạo", "cấp cao", "gấp", "khẩn",
    ]
    commitment_keywords: list[str] = [
        # English
        "commit", "guarantee", "promise", "assure", "ensure",
        "will do", "can do", "definitely", "certainly",
        # Vietnamese
        "cam kết", "đảm bảo", "chắc chắn", "sẽ làm", "hứa",
        "bảo đảm", "quyết tâm", "cam đoan", "đồng ý ngay",
    ]
    financial_keywords: list[str] = [
        # English
        "financial", "finance", "budget", "revenue", "profit", "cost",
        "expense", "quarterly", "annual", "invoice", "payment",
        "salary", "bonus", "allowance", "compensation",
        "contract value", "deal", "quote", "pricing",
        # Vietnamese
        "doanh thu", "lợi nhuận", "tài chính", "ngân sách", "chi phí",
        "lương", "thưởng", "phụ cấp", "tiền", "thanh toán",
        "hóa đơn", "báo giá", "hợp đồng", "giá trị",
    ]
    legal_keywords: list[str] = [
        # English
        "legal", "contract", "compliance", "regulation", "law",
        "agreement", "nda", "confidential", "patent", "ip",
        "dispute", "violation", "breach", "termination",
        # Vietnamese
        "luật", "pháp lý", "hợp đồng", "thỏa thuận",
        "bí mật", "điều khoản", "phạt", "chấm dứt",
        "tranh chấp", "vi phạm", "quyền sở hữu", "bản quyền",
    ]

    # v2 Enhancements Configuration
    enable_bm25_search: bool = True
    enable_bayesian_confidence: bool = True
    enable_lru_cache: bool = True
    enable_agent_router: bool = True
    enable_url_fetcher: bool = True
    enable_tools: bool = True
    enable_reasoning_loop: bool = False
    reasoning_loop_max_iterations: int = 3
    reasoning_loop_tool_retry: int = 1
    enable_llm_tool_planning: bool = False  # Hermes-style: LLM picks & calls tools directly
    reasoning_loop_rollout_user_percent: int = 100
    reasoning_loop_rollout_team_percent: int = 100
    reasoning_loop_rollout_salt: str = "reasoning-loop-v1"

    # Extended Tools (Disabled by default - for future use)
    # Enable via env vars: ENABLE_RAG_PIPELINE=true, etc.
    enable_rag_pipeline: bool = False   # Hybrid search for knowledge base
    enable_file_processor: bool = False  # Process PDF/Excel/CSV attachments
    enable_scheduler: bool = False       # Cron jobs for automation
    enable_api_client: bool = False       # External API integrations
    enable_audit_logger: bool = False    # Compliance audit logging
    enable_validators: bool = False      # Input validation
    enable_fact_store: bool = True  # Structured fact memory via sqlite FactStore
    enable_subagent_delegation: bool = True  # Parallel subagent pool for multi-source tasks
    enable_user_style_learning: bool = True  # Learn user communication style per user_id
    user_style_learning_user_id: str = ""  # Backward-compatible single user_id
    user_style_learning_user_ids: str = ""  # Comma-separated list of user_ids

    # Notification - enabled if any notification config is set
    enable_notification: bool = False    # Master toggle (auto-enabled if email/sms/teams configured)

    # n8n Configuration
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str = ""
    n8n_webhook_secret: str = ""

    # Notification Configuration
    notification_email_enabled: bool = False
    notification_sms_enabled: bool = False
    notification_teams_enabled: bool = True
    notification_webhook_url: str = ""
    
    # Extended SMTP config (for email notifications)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_email: str = ""
    teams_webhook_url: str = ""

    # Scheduler Configuration
    scheduler_enabled: bool = False
    scheduler_cron_default: str = "0 9 * * *"  # 9 AM daily

    @property
    def style_learning_user_ids(self) -> set[str]:
        raw_values = [self.user_style_learning_user_id, self.user_style_learning_user_ids]
        user_ids: set[str] = set()
        for raw in raw_values:
            if not raw:
                continue
            for item in raw.split(","):
                cleaned = item.strip()
                if cleaned:
                    user_ids.add(cleaned)
        return user_ids

    def should_learn_user_style(self, user_id: str) -> bool:
        user_ids = self.style_learning_user_ids
        return bool(user_ids) and user_id in user_ids

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
