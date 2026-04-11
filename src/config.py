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

    webhook_input_secret: str = ""
    power_automate_webhook_url: str = ""
    webhook_timeout: int = 30

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

    # n8n Configuration
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str = ""
    n8n_webhook_secret: str = ""

    # Notification Configuration
    notification_email_enabled: bool = False
    notification_sms_enabled: bool = False
    notification_teams_enabled: bool = True
    notification_webhook_url: str = ""

    # Scheduler Configuration
    scheduler_enabled: bool = False
    scheduler_cron_default: str = "0 9 * * *"  # 9 AM daily

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
