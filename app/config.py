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
    # "fake": the deterministic test providers instead of OpenAI and Jina, so a
    # first assemble works with no paid key (demo only; refused in production;
    # DECISIONS.md 2026-10-17).
    providers: Literal["real", "fake"] = "real"

    # Postgres (discrete fields; database_url is computed below).
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "nina"

    # S3 object storage. Local dev uses SeaweedFS (S3-compatible) on :8333;
    # these credentials are ignored locally and only used against AWS (M11).
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

    # PDF extraction limits (audit A2, 2026-10-28). Generous for documentation
    # PDFs; when one truncates, extract_pdf adds a ``pdf_truncated`` warning.
    pdf_max_pages: int = 2000
    pdf_max_stream_bytes: int = 64 * 1024 * 1024
    pdf_max_decoded_bytes: int = 256 * 1024 * 1024

    # Markdown served by documentation sites (SPEC 3.5, M10b). OFF by default:
    # with it on, resolve of an HTML page that declares a same-origin
    # rel=alternate type=text/markdown fetches that alternate (guard + robots)
    # and uses it when its text agrees with the visible HTML text
    # (markdown_alternate_min_overlap = share of the alternate's word trigrams
    # found in the page; uncalibrated, DECISIONS.md 2026-10-29).
    # fetch_accept_markdown sends "Accept: text/markdown" first on every page
    # fetch; OFF because some servers then return agent-only content that the
    # agreement check cannot see (no HTML to compare with).
    markdown_alternate: bool = False
    markdown_alternate_min_overlap: float = 0.7
    fetch_accept_markdown: bool = False

    # Render decision (SPEC 3.3).
    render_min_text_chars: int = 500

    # Browser render wait cap (SPEC 3.4 — domcontentloaded + networkidle, 15s).
    render_networkidle_timeout_ms: int = 15000
    # Wall-clock deadline of a whole browser render (audit M2, 2026-10-28):
    # goto (30 s) + networkidle (15 s) + serialization fit inside it, so it only
    # cuts a render that would otherwise hang (a JS busy loop); Timeout (504).
    render_total_timeout_s: int = 60

    # Embeddings (M6, SPEC §5). text-embedding-3-small produces 1536-dim vectors
    # (human decision, DECISIONS.md 2026-10-12). embeddings_api_key is the
    # OpenAI key for the real adapter; it falls back to bench_answerer_api_key
    # so no new key is required (see app/providers/embeddings/openai.py).
    embedding_dim: int = 1536
    embedding_model: str = "text-embedding-3-small"
    embeddings_api_key: str | None = None

    # Reranker (M7, SPEC §6). reranker_api_key is the Jina key for the real
    # adapter (app/providers/rerank/jina.py); tests use FakeRerankProvider and
    # never read it. reranker_model defaults to Jina's multilingual v2 reranker.
    reranker_api_key: str | None = None
    reranker_model: str = "jina-reranker-v2-base-multilingual"

    # Retrieval / selection (SPEC 6). CALIBRATED and FROZEN in M8 Stage 1 on the
    # dev split ONLY (96 tasks, budget 4000, real providers); the test split was
    # NOT touched (DECISIONS.md 2026-10-13). selection_lambda/min_value are the
    # grid winner (λ=0.3, min_value=0.0 — in dev, selection does best with NO
    # min-value cut). coverage_low/high are the p33/p66 of the PER-TASK MAX
    # ABSOLUTE rerank-score distribution on dev (96 tasks) — the exact quantity
    # assemble._coverage classifies on, so threshold and observable are the same
    # grandeza (32/31/33 insufficient/partial/sufficient over the 96 dev tasks,
    # via _coverage at runtime with the rounded thresholds).
    # selection_lambda: M10a Phase 3 moved it 0.3 -> 0.0 (DECISIONS.md
    # 2026-10-16): on multi-URL docs tasks the diversity penalty ended the
    # selection with budget left; tuned on dev half A, confirmed on half B.
    selection_lambda: float = 0.0
    min_value: float = 0.0
    coverage_low: float = 0.6137
    coverage_high: float = 0.7746
    rrf_k: int = 60

    # lexical_weight is a NEW M8 retrieval-precision knob (not part of the Stage
    # 1 frozen set above). It is the per-list weight the WEIGHTED RRF applies to
    # the lexical (exact-term) ranking relative to the vector ranking (fixed at
    # 1.0), so specific-detail queries can lean on exact-term evidence:
    # score(d) = lexical_weight/(k+rank_lex) + 1.0/(k+rank_vec). The default 1.0
    # is the IDENTITY — it reproduces today's RRF byte-for-byte — and stays 1.0
    # until the honest DEV-ONLY calibration (bench.calibrate lexical_weight grid)
    # picks and freezes a value here, exactly like selection_lambda/min_value.
    lexical_weight: float = 1.0

    # Rate limit + render quota (M9, SPEC §8 'rate_limited'/'GET /v1/usage').
    # Process-local token bucket per API key: rate_limit_capacity is the burst
    # size (tokens), rate_limit_refill_per_sec the steady refill rate. Each /v1
    # request costs one token; exceeding the bucket -> 429 with Retry-After.
    # render_daily_quota caps browser renders per key per UTC day; exceeding it
    # -> 429 'rate_limited' with Retry-After until the next UTC midnight. All
    # three are overridable PER KEY via the api_keys.limits JSONB (keys
    # 'rate_capacity', 'rate_refill_per_sec', 'render_daily_quota').
    rate_limit_capacity: int = 60
    rate_limit_refill_per_sec: float = 1.0
    render_daily_quota: int = 200

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
