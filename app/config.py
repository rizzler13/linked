from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file so it works regardless of
# which directory uvicorn is launched from.
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM providers
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""   # optional — used as Call A fallback if set

    # LLM model names
    gemini_model: str = "gemini-3.5-flash"
    groq_model: str = "qwen/qwen3.8-27b"
    openrouter_model: str = "nvidia/nemotron-3.5-lightning:free"

    # LLM generation settings
    llm_temperature: float = 0.2
    llm_max_output_tokens: int = 8192

    # Fetcher settings
    fetch_connect_timeout: float = 10.0
    fetch_read_timeout: float = 25.0
    fetch_max_redirects: int = 6
    fetch_max_bytes: int = 2 * 1024 * 1024  # 2 MB

    # Multi-page crawling
    max_extra_pages: int = 2

    # Rate limiting
    rate_limit_per_minute: int = 5
    cache_ttl_seconds: int = 600       # 10-minute URL-keyed result cache
    cache_max_size: int = 100
    llm_semaphore_slots: int = 10      # protect Gemini free tier (15 RPM)

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    port: int = 4000


@lru_cache
def get_settings() -> Settings:
    return Settings()
