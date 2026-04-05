from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    app_workers: int = 4

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

    openai_api_key: str = ""
    llm_model: str = "gpt-4"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2000

    rate_limit_requests: int = 100
    rate_limit_window: int = 60

    log_level: str = "INFO"

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
