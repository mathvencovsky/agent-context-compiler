"""Application configuration — pydantic-settings Settings loaded from .env (M0)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Fetcher destination policy (SPEC 3.1). Ports outside this allow-list are
    # rejected with blocked_destination. Accept a comma-separated env string
    # (e.g. ALLOWED_PORTS="80,443,8443") and normalize to a tuple of ints.
    allowed_ports: Annotated[tuple[int, ...], NoDecode] = (80, 443)

    # robots.txt per-domain cache TTL in seconds (SPEC 3.2 — 24h default).
    robots_cache_ttl_seconds: int = 86400

    # resolve cache TTL in seconds (SPEC §5 — a stored version younger than this
    # is served from storage when force_refresh is False; 24h default).
    resolve_cache_ttl_seconds: int = 86400

    # Render decision (SPEC 3.3).
    render_min_text_chars: int = 500

    # Browser render wait cap (SPEC 3.4 — domcontentloaded + networkidle, 15s).
    render_networkidle_timeout_ms: int = 15000

    # Embeddings — provisional until M6.
    embedding_dim: int = 1024

    # Retrieval / selection (SPEC 6).
    selection_lambda: float = 0.5
    min_value: float = 0.05
    coverage_low: float = 0.2
    coverage_high: float = 0.5
    rrf_k: int = 60

    # Benchmark LLM keys (M2, SPEC §10). Optional: the deterministic fake
    # provider ignores them and every test runs without network. Only the real
    # OpenAI answerer / Anthropic judge adapters read these, in the human run.
    bench_answerer_api_key: str | None = None
    bench_judge_api_key: str | None = None

    @field_validator("allowed_ports", mode="before")
    @classmethod
    def _parse_allowed_ports(cls, value: object) -> object:
        """Accept a comma-separated string from the environment.

        pydantic-settings hands tuple fields the raw env string; parse
        ``"80,443,8443"`` into ``(80, 443, 8443)``. Non-string values (the
        default tuple, a list) are passed through for pydantic to coerce.
        """
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return tuple(int(p) for p in parts)
        return value

    def user_agent(self) -> str:
        """Effective User-Agent string, SPEC §3.1 form.

        ``AgentContextBot/0.1 (+<contact_url>)`` built from
        ``fetcher_contact_url`` so the bot identity and the contact URL stay in
        sync regardless of the stored ``fetcher_user_agent`` literal.
        """
        return f"AgentContextBot/0.1 (+{self.fetcher_contact_url})"

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
