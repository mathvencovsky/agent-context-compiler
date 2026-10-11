"""Application configuration — pydantic-settings Settings loaded from .env (M0)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration read from environment variables / .env.

    Field names map 1:1 to upper-cased env var names (pydantic-settings is
    case-insensitive by default). See .env.example for the full list.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "production"] = "dev"

    # Postgres (discrete fields; database_url is computed below).
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "nina"

    # S3 object storage. Local dev uses SeaweedFS (S3-compatible) on :8333;
    # these credentials are ignored locally and only used against AWS (M10).
    s3_endpoint_url: str = "http://localhost:8333"
    s3_access_key: str = "nina-dev-access-key"
    s3_secret_key: str = "nina-dev-secret-key"
    s3_bucket: str = "nina-blobs"
    s3_region: str = "us-east-1"

    # Fetcher identity (SPEC 3.1).
    fetcher_user_agent: str = "AgentContextBot/0.1 (+<contact>)"
    fetcher_contact_url: str = "https://example.com/contact"

    # Fetcher limits (SPEC 3.1).
    max_body_bytes: int = 15 * 1024 * 1024
    total_timeout: int = 20
    connect_timeout: int = 5
    max_redirects: int = 5

    # Render decision (SPEC 3.3).
    render_min_text_chars: int = 500

    # Embeddings — provisional until M6.
    embedding_dim: int = 1024

    # Retrieval / selection (SPEC 6).
    selection_lambda: float = 0.5
    min_value: float = 0.05
    coverage_low: float = 0.2
    coverage_high: float = 0.5
    rrf_k: int = 60

    @property
    def database_url(self) -> str:
        """SQLAlchemy/psycopg DSN built from the discrete Postgres fields."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()
