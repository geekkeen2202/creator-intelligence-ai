from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    debug: bool = False
    cors_origins: str = ""  # comma-separated, e.g. "https://app.example.com,https://staging.example.com"

    database_url: str

    supabase_jwt_secret: str
    supabase_url: str = ""

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4-6"
    # Cheaper/faster route for short-form tasks (Shorts hooks/captions/hashtags —
    # TechnicalDesign.md §5.2). Empty = same as openrouter_model.
    openrouter_fast_model: str = ""

    openai_api_key: str = ""  # Whisper fallback transcription only (TranscriptionPort)
    # $/minute — OpenAI's published whisper-1 rate as of this writing; verify
    # against https://openai.com/api/pricing before relying on this for
    # real billing (ARCHITECTURE.md §8 rule 11 cost metering).
    whisper_price_per_minute: float = 0.006

    youtube_api_key: str = ""
    x_bearer_token: str = ""
    facebook_access_token: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "creator-intelligence/0.1"

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    resend_api_key: str = ""
    email_from: str = "noreply@example.com"

    sentry_dsn: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
