"""Typed response models for the NINA SDK — M9.

These dataclasses mirror the Pydantic response schemas in ``app/api/schemas.py``
(``ResolveResponse`` / ``AssembleResponse``) so a caller gets attribute access
with types instead of raw dicts. They are deliberately TOLERANT: fields the
server may add later are ignored (``from_dict`` reads only what it knows), and
the raw decoded body is preserved on ``raw`` so nothing is lost. The SDK does not
depend on Pydantic — it keeps the dependency surface to ``httpx`` alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceInfo:
    """Provenance of a resolved document (mirrors ``app.extract.ir.SourceInfo``)."""

    url: str
    final_url: str
    content_type: str
    fetched_at: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    render_mode: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceInfo:
        return cls(
            url=str(data.get("url", "")),
            final_url=str(data.get("final_url", "")),
            content_type=str(data.get("content_type", "")),
            fetched_at=data.get("fetched_at"),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            content_hash=data.get("content_hash"),
            render_mode=data.get("render_mode"),
        )


@dataclass(frozen=True)
class Stats:
    """Counters returned alongside a resolved document (SPEC §8)."""

    blocks: int = 0
    tokens_markdown: int = 0
    tokens_raw_html: int = 0
    cache_hit: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Stats:
        return cls(
            blocks=int(data.get("blocks", 0)),
            tokens_markdown=int(data.get("tokens_markdown", 0)),
            tokens_raw_html=int(data.get("tokens_raw_html", 0)),
            cache_hit=bool(data.get("cache_hit", False)),
        )


@dataclass(frozen=True)
class ResolveResponse:
    """Response of ``POST /v1/documents/resolve`` / ``GET /v1/documents/{id}``.

    ``blocks`` and ``metadata`` are kept as raw dict/list structures (the IR
    block tree is large and only surfaced verbatim); ``raw`` holds the complete
    decoded body so no field is ever lost.
    """

    document_id: str
    version_id: str
    source: SourceInfo
    stats: Stats
    markdown: str | None = None
    blocks: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    trust: str = "untrusted_source"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolveResponse:
        return cls(
            document_id=str(data.get("document_id", "")),
            version_id=str(data.get("version_id", "")),
            source=SourceInfo.from_dict(data.get("source", {})),
            stats=Stats.from_dict(data.get("stats", {})),
            markdown=data.get("markdown"),
            blocks=data.get("blocks"),
            metadata=data.get("metadata", {}),
            warnings=data.get("warnings", []),
            trust=str(data.get("trust", "untrusted_source")),
            raw=data,
        )


@dataclass(frozen=True)
class EvidenceItem:
    """One literal evidence quote backing a fragment (SPEC §8)."""

    block_id: str
    quote: str
    page: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        return cls(
            block_id=str(data.get("block_id", "")),
            quote=str(data.get("quote", "")),
            page=data.get("page"),
        )


@dataclass(frozen=True)
class FragmentScores:
    """Per-fragment retrieval scores (SPEC §8)."""

    rrf: float = 0.0
    lexical_rank: int | None = None
    vector_rank: int | None = None
    rerank: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FragmentScores:
        return cls(
            rrf=float(data.get("rrf", 0.0)),
            lexical_rank=data.get("lexical_rank"),
            vector_rank=data.get("vector_rank"),
            rerank=data.get("rerank"),
        )


@dataclass(frozen=True)
class Fragment:
    """One selected chunk with provenance + evidence (SPEC §8)."""

    chunk_id: str
    document_id: str
    version_id: str
    source_url: str
    heading_path: list[str]
    md: str
    tokens: int
    scores: FragmentScores
    evidence: list[EvidenceItem]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fragment:
        return cls(
            chunk_id=str(data.get("chunk_id", "")),
            document_id=str(data.get("document_id", "")),
            version_id=str(data.get("version_id", "")),
            source_url=str(data.get("source_url", "")),
            heading_path=list(data.get("heading_path", [])),
            md=str(data.get("md", "")),
            tokens=int(data.get("tokens", 0)),
            scores=FragmentScores.from_dict(data.get("scores", {})),
            evidence=[EvidenceItem.from_dict(e) for e in data.get("evidence", [])],
        )


@dataclass(frozen=True)
class SourceStatus:
    """Per-URL outcome in ``sources[]`` (SPEC §8)."""

    url: str
    status: str
    document_id: str | None = None
    version_id: str | None = None
    fetched_at: str | None = None
    page_tokens: int | None = None
    date: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceStatus:
        return cls(
            url=str(data.get("url", "")),
            status=str(data.get("status", "")),
            document_id=data.get("document_id"),
            version_id=data.get("version_id"),
            fetched_at=data.get("fetched_at"),
            page_tokens=data.get("page_tokens"),
            date=data.get("date"),
        )


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting for the assembled context (SPEC §8)."""

    output_tokens: int = 0
    budget_tokens: int = 0
    tokenizer: str = "cl100k_base (approximate)"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenUsage:
        return cls(
            output_tokens=int(data.get("output_tokens", 0)),
            budget_tokens=int(data.get("budget_tokens", 0)),
            tokenizer=str(data.get("tokenizer", "cl100k_base (approximate)")),
        )


@dataclass(frozen=True)
class ConciseAssembleResponse:
    """``POST /v1/context/assemble`` with ``response_format="concise"`` (SPEC §8).

    ``context`` is the selected fragments as Markdown for an agent; scores and
    evidence quotes are only in :class:`AssembleResponse`.
    """

    context: str
    sources: list[SourceStatus]
    token_usage: TokenUsage
    coverage: str
    warnings: list[dict[str, Any]] = field(default_factory=list)
    #: NINA's actionable notes (``code``, ``message``, ``details``); never page text.
    hints: list[dict[str, Any]] = field(default_factory=list)
    trust: str = "untrusted_source"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConciseAssembleResponse:
        return cls(
            context=str(data.get("context", "")),
            sources=[SourceStatus.from_dict(s) for s in data.get("sources", [])],
            token_usage=TokenUsage.from_dict(data.get("token_usage", {})),
            coverage=str(data.get("coverage", "")),
            warnings=data.get("warnings", []),
            hints=data.get("hints", []),
            trust=str(data.get("trust", "untrusted_source")),
            raw=data,
        )


@dataclass(frozen=True)
class AssembleResponse:
    """Response of ``POST /v1/context/assemble`` (SPEC §8).

    ``raw`` preserves the complete decoded body; the typed fields cover the
    documented contract (fragments with provenance + evidence, per-URL status,
    token accounting and the coverage heuristic).
    """

    query: str
    fragments: list[Fragment]
    sources: list[SourceStatus]
    token_usage: TokenUsage
    coverage: str
    warnings: list[dict[str, Any]] = field(default_factory=list)
    #: NINA's actionable notes (``code``, ``message``, ``details``); never page text.
    hints: list[dict[str, Any]] = field(default_factory=list)
    trust: str = "untrusted_source"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssembleResponse:
        return cls(
            query=str(data.get("query", "")),
            fragments=[Fragment.from_dict(f) for f in data.get("fragments", [])],
            sources=[SourceStatus.from_dict(s) for s in data.get("sources", [])],
            token_usage=TokenUsage.from_dict(data.get("token_usage", {})),
            coverage=str(data.get("coverage", "")),
            warnings=data.get("warnings", []),
            hints=data.get("hints", []),
            trust=str(data.get("trust", "untrusted_source")),
            raw=data,
        )
