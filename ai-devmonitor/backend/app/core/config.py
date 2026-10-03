from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./devmonitor.db"
    redis_url: str | None = None

    # auth
    jwt_secret: str = "change-me-in-production-please-32b!"
    jwt_ttl_minutes: int = 480
    admin_user: str = "admin"
    admin_password: str = "admin"
    viewer_user: str = "viewer"
    viewer_password: str = "viewer"
    ingest_api_key: str = "dev-ingest-key"

    # AI
    ai_mode: str = "hybrid"  # local | cloud | hybrid
    ai_provider: str = "mock"  # mock | openai | ollama | anthropic
    ai_local_provider: str = "mock"  # provider used in local mode
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ai_timeout_s: float = 30.0

    # detection thresholds
    error_rate_warning: float = 5.0
    error_rate_critical: float = 15.0
    latency_p95_critical_ms: float = 1500.0
    cpu_critical: float = 90.0
    window_minutes: int = 5
    background_detection: bool = True
    detection_interval_s: int = 15


settings = Settings()
