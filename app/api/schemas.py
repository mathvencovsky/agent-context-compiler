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
#: ``detailed``: fragments with ids, scores and evidence (JSON); ``concise``: the
#: same fragments rendered as Markdown for agents (SPEC §8; DECISIONS 2026-10-16).
ResponseFormat = Literal["detailed", "concise"]


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
    response_format: ResponseFormat = "detailed"
    #: Chunk ids the caller already has; they are left out before the rerank, so
    #: the budget goes to new fragments. Unknown ids are ignored (SPEC §8).
    exclude_chunk_ids: list[str] = Field(default_factory=list, max_length=1000)
    #: The documentation release the caller needs ("3.12", "v1.30", "react@18").
    #: Selection ignores it; a ``version_mismatch`` hint names delivered
    #: fragments from another release (SPEC §8; DECISIONS 2026-10-29).
    version: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9@._+-]+$"
    )
    #: Low-budget mode: keep only the sentences of the selected chunks that
    #: overlap the query, verbatim, with an elision marker (SPEC §8).
    compact: bool = False
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
    #: Tokens of the whole page as Markdown (what ``resolve`` would return).
    page_tokens: int | None = None
    #: The page's own date (``YYYY-MM-DD``) from its metadata, if it states one.
    date: str | None = None


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
    hints: list[HintModel] = Field(default_factory=list)
    trust: Literal["untrusted_source"] = "untrusted_source"


class HintModel(BaseModel):
    """One actionable hint: NINA's metadata, never page text (SPEC §8)."""

    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class ConciseAssembleResponse(BaseModel):
    """``POST /v1/context/assemble`` with ``response_format="concise"`` (SPEC §8).

    ``context`` holds the selected fragments as Markdown
    (:func:`app.retrieve.render.render_concise`): document URL and ids once per
    document, ``chunk_id`` (and PDF page) per fragment. Scores and evidence
    quotes are only in the detailed format.
    """

    context: str
    sources: list[SourceStatusModel]
    token_usage: TokenUsage
    coverage: Literal["insufficient", "partial", "sufficient"]
    warnings: list[dict[str, object]]
    hints: list[HintModel] = Field(default_factory=list)
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
    # Same value as the X-Request-ID response header; quote it when reporting
    # a failure (it ties the response to the server logs).
    request_id: str | None = None


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
                        "request_id": "4f0c2a9e8b7d4c1fa3e2d1c0b9a8f7e6",
                    }
                }
            ]
        }
    }


class SiteIndexRequest(BaseModel):
    """Body of ``POST /v1/sites/index`` (SPEC §8; research J3)."""

    url: str = Field(examples=["https://docs.example.com/guide/start"])
    #: Cap on the rendered index (``context``), in cl100k tokens.
    max_tokens: int = Field(default=2000, ge=256, le=8000)


class SiteIndexEntry(BaseModel):
    """One link of the site's ``llms.txt``: untrusted text from the site."""

    section: str
    title: str
    url: str
    note: str


class SiteIndexResponse(BaseModel):
    """The site's ``llms.txt`` as a link list (SPEC §8, ``/v1/sites/index``).

    ``index_url`` is the ``llms.txt`` that was read (``None`` when none was
    found). ``entries`` are the links that fit ``max_tokens``, in file order;
    ``entries_total`` counts them all. ``context`` renders the same entries for
    an agent inside NINA's tags. Every text field comes from the site.
    """

    index_url: str | None
    title: str
    summary: str
    entries: list[SiteIndexEntry]
    entries_total: int
    context: str
    warnings: list[dict[str, object]]
    trust: Literal["untrusted_source"] = "untrusted_source"
