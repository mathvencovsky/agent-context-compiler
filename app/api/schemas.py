"""Pydantic request/response schemas for the resolve/get endpoints — M5 (SPEC §8).

These models define the public HTTP contract of ``POST /v1/documents/resolve`` and
``GET /v1/documents/{document_id}``. The IR models (``SourceInfo``, ``Metadata``,
``Block``, ``Warning``) are reused from :mod:`app.extract.ir` so the structured
payload the pipeline produces is surfaced verbatim — no second contract.

``markdown`` and ``blocks`` are optional on the wire: the handler fills them only
for the formats the request asked for (``formats``). ``trust`` is a fixed
``"untrusted_source"`` constant (SPEC §8 — every resolved document is untrusted
content). ``examples`` are attached so the generated OpenAPI shows the SPEC §8
request/response shapes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.extract.ir import Block, Metadata, SourceInfo, Warning

ResolveFormat = Literal["markdown", "blocks"]
RenderDecision = Literal["auto", "never", "always"]
SelectionStrategy = Literal["greedy", "truncate"]


class ResolveRequest(BaseModel):
    """Body of ``POST /v1/documents/resolve`` (SPEC §8)."""

    url: str = Field(examples=["https://docs.example.com/guide"])
    formats: list[ResolveFormat] = Field(default=["markdown", "blocks"])
    force_refresh: bool = False
    render: RenderDecision = "auto"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://docs.example.com/guide",
                    "formats": ["markdown", "blocks"],
                    "force_refresh": False,
                    "render": "auto",
                }
            ]
        }
    }


class Stats(BaseModel):
    """Counters returned alongside a resolved document (SPEC §8)."""

    blocks: int
    tokens_markdown: int
    tokens_raw_html: int
    cache_hit: bool


class ResolveResponse(BaseModel):
    """Response envelope of resolve/get-document (SPEC §8)."""

    document_id: str
    version_id: str
    source: SourceInfo
    metadata: Metadata
    markdown: str | None = None
    blocks: list[Block] | None = None
    stats: Stats
    warnings: list[Warning]
    trust: Literal["untrusted_source"] = "untrusted_source"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "document_id": "doc_0123456789abcdef",
                    "version_id": "ver_fedcba9876543210",
                    "source": {
                        "url": "https://docs.example.com/guide",
                        "final_url": "https://docs.example.com/guide",
                        "content_type": "text/html",
                        "fetched_at": "2026-10-11T00:00:00Z",
                        "etag": '"abc123"',
                        "last_modified": "Wed, 11 Oct 2026 00:00:00 GMT",
                        "content_hash": "e3b0c44298fc1c149afbf4c8996fb924",
                        "render_mode": "http",
                    },
                    "metadata": {
                        "title": "Guide",
                        "lang": "en",
                        "published_at": None,
                        "modified_at": None,
                        "jsonld": [],
                    },
                    "markdown": "# Guide\n\nText.",
                    "blocks": [],
                    "stats": {
                        "blocks": 1,
                        "tokens_markdown": 4,
                        "tokens_raw_html": 12,
                        "cache_hit": False,
                    },
                    "warnings": [],
                    "trust": "untrusted_source",
                }
            ]
        }
    }


class AssembleRequest(BaseModel):
    """Body of ``POST /v1/context/assemble`` (SPEC §8).

    ``urls`` holds 1-20 URLs and ``max_tokens`` is the output budget B (256-32000;
    SPEC §8). The ablation flags (``use_lexical`` / ``use_vector`` / ``use_rerank``
    / ``selection``) are accepted only when ``ENV != "production"`` (SPEC §6); the
    handler enforces that gate. They default to the full pipeline.
    """

    query: str = Field(examples=["Which header authenticates API requests?"])
    urls: list[str] = Field(min_length=1, max_length=20)
    max_tokens: int = Field(default=4000, ge=256, le=32000)
    include_evidence: bool = True
    # Ablation flags (SPEC §6; non-production only, enforced by the handler).
    use_lexical: bool = True
    use_vector: bool = True
    use_rerank: bool = True
    selection: SelectionStrategy = "greedy"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "Which header is required to authenticate API requests?",
                    "urls": [
                        "https://docs.example.com/auth",
                        "https://docs.example.com/errors",
                    ],
                    "max_tokens": 4000,
                    "include_evidence": True,
                }
            ]
        }
    }


class EvidenceItem(BaseModel):
    """One literal evidence quote backing a fragment (SPEC §8)."""

    block_id: str
    page: int | None = None
    quote: str


class FragmentScoresModel(BaseModel):
    """Per-fragment retrieval scores (SPEC §8)."""

    lexical_rank: int | None = None
    vector_rank: int | None = None
    rrf: float
    rerank: float | None = None


class FragmentModel(BaseModel):
    """One selected chunk with provenance + evidence (SPEC §8)."""

    chunk_id: str
    document_id: str
    version_id: str
    source_url: str
    heading_path: list[str]
    md: str
    tokens: int
    scores: FragmentScoresModel
    evidence: list[EvidenceItem]


class SourceStatusModel(BaseModel):
    """Per-URL outcome in ``sources[]`` (SPEC §8)."""

    url: str
    document_id: str | None = None
    version_id: str | None = None
    fetched_at: str | None = None
    status: str


class TokenUsage(BaseModel):
    """Token accounting for the assembled context (SPEC §8)."""

    output_tokens: int
    budget_tokens: int
    tokenizer: str = "cl100k_base (approximate)"


class AssembleResponse(BaseModel):
    """Response envelope of ``POST /v1/context/assemble`` (SPEC §8).

    ``coverage`` is a documented HEURISTIC over the max rerank score (SPEC §6
    step 7), calibrated in M8 — not a correctness guarantee.
    """

    query: str
    fragments: list[FragmentModel]
    sources: list[SourceStatusModel]
    token_usage: TokenUsage
    coverage: Literal["insufficient", "partial", "sufficient"]
    warnings: list[dict[str, object]]
    trust: Literal["untrusted_source"] = "untrusted_source"


class UsageByEndpoint(BaseModel):
    """Usage rolled up for one endpoint within a day (SPEC §8 GET /v1/usage)."""

    endpoint: str
    requests: int
    tokens_delivered: int
    renders: int
    latency_p50_ms: int
    latency_p95_ms: int


class UsageByDay(BaseModel):
    """One UTC day's usage, broken down per endpoint (SPEC §8 GET /v1/usage)."""

    day: str
    requests: int
    tokens_delivered: int
    renders: int
    latency_p50_ms: int
    latency_p95_ms: int
    endpoints: list[UsageByEndpoint]


class UsageResponse(BaseModel):
    """Response envelope of ``GET /v1/usage`` (SPEC §8).

    ``by_day`` carries the per-day rollup (each with its per-endpoint
    breakdown); the top-level counters are the totals across every day. Latency
    percentiles are nearest-rank over the matching events' ``latency_ms``.
    """

    requests: int
    tokens_delivered: int
    renders: int
    latency_p50_ms: int
    latency_p95_ms: int
    by_day: list[UsageByDay]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "requests": 3,
                    "tokens_delivered": 1200,
                    "renders": 1,
                    "latency_p50_ms": 42,
                    "latency_p95_ms": 110,
                    "by_day": [
                        {
                            "day": "2026-10-14",
                            "requests": 3,
                            "tokens_delivered": 1200,
                            "renders": 1,
                            "latency_p50_ms": 42,
                            "latency_p95_ms": 110,
                            "endpoints": [
                                {
                                    "endpoint": "/v1/documents/resolve",
                                    "requests": 2,
                                    "tokens_delivered": 0,
                                    "renders": 1,
                                    "latency_p50_ms": 30,
                                    "latency_p95_ms": 55,
                                },
                                {
                                    "endpoint": "/v1/context/assemble",
                                    "requests": 1,
                                    "tokens_delivered": 1200,
                                    "renders": 0,
                                    "latency_p50_ms": 110,
                                    "latency_p95_ms": 110,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    }


class ErrorDetail(BaseModel):
    """The ``error`` object of the SPEC §8 error envelope."""

    code: str
    message: str
    details: dict[str, object]


class ErrorBody(BaseModel):
    """SPEC §8 error envelope: ``{"error": {code, message, details}}``."""

    error: ErrorDetail

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": {
                        "code": "blocked_destination",
                        "message": "destination IP is not a global unicast address",
                        "details": {},
                    }
                }
            ]
        }
    }
