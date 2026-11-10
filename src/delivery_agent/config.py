"""Application settings loaded from environment variables (and an optional `.env` file)."""

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from delivery_agent.domain.special_dates import DEFAULT_SPECIAL_DATES_PATH
from delivery_agent.guardrails.terms import DEFAULT_TERMS_PATH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str | None = None
    model_id: str = "gpt-6-luna"
    # Model of the intent router and the reply rewriter (defaults to MODEL_ID).
    router_model_id: str = "gpt-6-luna"
    # Bounded tool loop of the order agent: at most this many tool calls per message.
    agent_tool_call_limit: int = Field(default=10, ge=1)

    order_api_base_url: str = "http://localhost:8000/api"
    order_api_timeout_seconds: float = Field(default=5.0, gt=0)

    # The menu (database, embeddings, vector index) lives in the Menu service; the agent only calls its API.
    menu_service_base_url: str = "http://127.0.0.1:8081"
    menu_service_timeout_seconds: float = Field(default=5.0, gt=0)
    # `uv run delivery-agent` starts the Menu service as a child process unless it is already running.
    menu_service_autostart: bool = True
    menu_service_startup_timeout_seconds: float = Field(default=60.0, gt=0)

    session_timeout_minutes: int = Field(default=15, ge=1)
    num_history_runs: int = Field(default=10, ge=1)
    # Of those runs, keep only the last N tool calls (and results). Both limits apply only when Agno replays the
    # context; with OpenAI response chaining (kept for cost) they don't reach the model (specs/007-short-replies).
    agent_max_tool_calls_from_history: int = Field(default=3, ge=0)
    timezone: str = "America/Sao_Paulo"

    agent_db_path: Path = Path("data/agent.db")

    # Guardrails (specs/003-guardrails).
    guardrail_terms_path: Path = DEFAULT_TERMS_PATH
    human_handoff_threshold: int = Field(default=3, ge=1)
    human_attendant_db_path: Path = Path("data/human_attendant.db")
    # One row per customer message (specs/004-greeting); also tells the first message of the day.
    intent_db_path: Path = Path("data/intent.db")

    # Holidays and commemorative dates mentioned in the first agent reply (specs/005-special-dates).
    special_dates_path: Path = DEFAULT_SPECIAL_DATES_PATH
    special_dates_window_days: int = Field(default=7, ge=0)

    # Langfuse tracing (specs/006-langfuse-tracing): on when enabled AND both keys are set. Same variable names as the
    # Langfuse SDK; they are passed to the client explicitly because `.env` is not exported to `os.environ`.
    langfuse_tracing_enabled: bool = True
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://us.cloud.langfuse.com"
    langfuse_tracing_environment: str = Field(default="development", pattern=r"^[a-z0-9_-]{1,40}$")
    langfuse_sample_rate: float = Field(default=1.0, ge=0, le=1)

    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    # Colored log lines: auto = only on a terminal (and not when NO_COLOR is set), always, never.
    log_color: Literal["auto", "always", "never"] = "auto"

    def today(self) -> date:
        """Current local date, used as the order `delivery_date`."""
        return datetime.now(ZoneInfo(self.timezone)).date()

    def langfuse_configured(self) -> bool:
        """Tell whether tracing should run: enabled and both Langfuse keys set (blank values count as unset)."""
        secret = self.langfuse_secret_key.get_secret_value() if self.langfuse_secret_key else ""
        return self.langfuse_tracing_enabled and bool(self.langfuse_public_key) and bool(secret)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once."""
    return Settings()
