"""Menu service settings, loaded from environment variables (and the same optional `.env` file as the agent)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MenuSettings(BaseSettings):
    """Configuration of the Menu service process."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    menu_service_host: str = "127.0.0.1"
    menu_service_port: int = 8081

    kb_sql_path: Path = Path("knowledge_base/knowledge_base.sql")
    menu_db_path: Path = Path("data/menu.db")
    vector_db_path: Path = Path("data/lancedb")

    openai_api_key: str | None = None
    embedding_model_id: str = "text-embedding-3-small"
    # Max cosine distance for a semantic-only flavor match to be proposed to the customer.
    semantic_max_distance: float = Field(default=0.75, gt=0, le=2)

    # When set, PUT/DELETE /menu/items require the `X-Admin-Token` header with this value.
    menu_admin_token: str | None = None

    log_level: str = "INFO"


@lru_cache
def get_settings() -> MenuSettings:
    """Return the process-wide Menu service settings, loaded once."""
    return MenuSettings()
