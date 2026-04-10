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
    ollama_timeout: int = 120

    # Azure OpenAI Configuration (optional)
    azure_openai_endpoint: str = ""
    azure_openai_key: str = ""
    azure_openai_api_version: str = "2024-02-01"
    azure_deployment_name: str = ""

    rate_limit_requests: int = 100
    rate_limit_window: int = 60

    log_level: str = "INFO"
    executive_keywords: list[str] = ["ceo", "cto", "cfo", "director", "vp", "urgent", "asap"]
    commitment_keywords: list[str] = ["cam kết", "đảm bảo", "chắc chắn", "sẽ làm", "hứa", "commit"]
    financial_keywords: list[str] = ["financial", "finance", "budget", "quarterly", "doanh thu", "tài chính"]
    legal_keywords: list[str] = ["legal", "contract", "compliance", "luật", "pháp lý", "hợp đồng"]

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
