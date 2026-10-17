"""Context assembly orchestrator (SPEC §6 steps 1-7, §8). No LLM anywhere.

``assemble_context`` is the heart of ``POST /v1/context/assemble``. It stitches
the M5 resolve path, the M6 indexer and the FEAT-002 retrieval/selection core
into one deterministic pipeline:

1. Resolve each URL through the M5 resolve path (24h cache) and ensure its
   current version is indexed (reusing ``index_version``). Each URL fails
   INDEPENDENTLY — a failure is recorded in ``sources[].status`` + ``warnings``
   and never aborts the others (SPEC §8).
2. Collect the current ``version_id`` of every OK URL.
3. Lexical top-50 + vector top-50 over those versions (ablation ``use_lexical`` /
   ``use_vector`` may drop a list).
4. Fuse the lists with RRF (``rrf_k``), keys carrying ``(document_id, ord)``.
5. Rerank the top-30 (``use_rerank``); value is the rerank score or, with rerank
   off, the normalized RRF score.
6. Greedy budget-constrained selection under ``max_tokens`` (SPEC §6 step 5), or
   — with ``selection == "truncate"`` — fill by value order until the budget is
   exhausted.
7. Order the survivors by document then ``ord`` and build fragments with full
   provenance (chunk/document/version id, source URL, heading path, md, tokens),
   per-source ranks + RRF + rerank scores, and literal evidence.

``evidence.quote`` is ALWAYS copied literally from the IR: it is the source
block's text when a single block backs the chunk, otherwise the sentence of the
chunk with the highest lexical overlap with the query, truncated to <=300
characters (SPEC §8). Coverage is a heuristic over the max rerank score (SPEC §6
step 7). The whole path is deterministic and never calls an LLM (CLAUDE.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.api.documents import resolve_document
from app.api.schemas import AssembleRequest, ResolveRequest
from app.index.indexer import index_version
from app.retrieve.lexical import lexical_search
from app.retrieve.rerank import rerank_candidates
from app.retrieve.rrf import rrf_fuse
from app.retrieve.select import Candidate, select
from app.retrieve.vector import vector_search

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session, sessionmaker

    from app.config import Settings
    from app.extract.ir import Document
    from app.fetch.client import Transport
    from app.fetch.guard import Resolver
    from app.fetch.robots import RobotsChecker
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.rerank.base import RerankProvider
    from app.store.blobs import BlobStore
    from app.store.models import Chunk

#: Fused ranking key: (document_id, ord) — stable, drives the RRF tie-break.
Key = tuple[str, int]

#: SPEC §8: an evidence quote is copied literally and truncated to <=300 chars.
_MAX_QUOTE_CHARS = 300

#: Sentence splitter for the fallback quote (end punctuation + whitespace).
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

#: Lowercase alphanumeric tokenizer for lexical-overlap scoring.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Evidence:
    """A literal quote from an IR block backing a fragment (SPEC §8)."""

    block_id: str
    page: int | None
    quote: str


@dataclass(frozen=True)
class FragmentScores:
    """Per-fragment retrieval scores surfaced on the API (SPEC §8)."""

    lexical_rank: int | None
    vector_rank: int | None
    rrf: float
    rerank: float | None


@dataclass(frozen=True)
class Fragment:
    """One selected chunk with full provenance + evidence (SPEC §8)."""

    chunk_id: str
    document_id: str
    version_id: str
    source_url: str
    heading_path: list[str]
    md: str
    tokens: int
    scores: FragmentScores
    evidence: list[Evidence]


@dataclass(frozen=True)
class SourceStatus:
    """Per-URL outcome reported in ``sources[]`` (SPEC §8)."""

    url: str
    document_id: str | None
    version_id: str | None
    fetched_at: str | None
    status: str


@dataclass
class AssembleResult:
    """Everything the ``/v1/context/assemble`` response needs (SPEC §8)."""

    query: str
    fragments: list[Fragment] = field(default_factory=list)
    sources: list[SourceStatus] = field(default_factory=list)
    output_tokens: int = 0
    budget_tokens: int = 0
    coverage: str = "insufficient"
    warnings: list[dict[str, object]] = field(default_factory=list)
    #: Relevant candidates (rerank >= ``coverage_low``) were left out for lack of
    #: budget; feeds the ``budget_limited`` hint (SPEC §8).
    budget_limited: bool = False


@dataclass
class _ResolvedUrl:
    """Internal bookkeeping for a successfully resolved + indexed URL."""

    url: str
    document_id: str
    version_id: str
    fetched_at: str


def _tokenize(text: str) -> list[str]:
    """Lowercase ``text`` and return its alphanumeric tokens (with repeats)."""
    return _TOKEN_RE.findall(text.lower())


def _resolve_and_index(
    req: AssembleRequest,
    *,
    session_factory: sessionmaker[Session],
    blobs: BlobStore,
    robots: RobotsChecker,
    resolver: Resolver | None,
    transport: Transport | None,
    embeddings: EmbeddingProvider,
    settings: Settings,
    now: datetime,
) -> tuple[list[_ResolvedUrl], list[SourceStatus], list[dict[str, object]]]:
    """Resolve each URL (independently) and ensure its version is indexed.

    Returns the resolved URLs, the per-URL ``SourceStatus`` list, and warnings
    for any URL that failed. A failure in one URL never aborts the others
    (SPEC §8).
    """
    resolved: list[_ResolvedUrl] = []
    sources: list[SourceStatus] = []
    warnings: list[dict[str, object]] = []

    for url in req.urls:
        try:
            response = resolve_document(
                ResolveRequest(url=url, formats=[], force_refresh=False),
                session_factory=session_factory,
                blobs=blobs,
                robots=robots,
                resolver=resolver,
                transport=transport,
                now=now,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001 — each URL fails independently.
            code = getattr(exc, "code", "fetch_failed")
            sources.append(
                SourceStatus(
                    url=url,
                    document_id=None,
                    version_id=None,
                    fetched_at=None,
                    status=code,
                )
            )
            warnings.append(
                {
                    "code": code,
                    "message": f"failed to resolve {url}: {code}",
                    "url": url,
                }
            )
            continue

        # Ensure the current version is indexed (idempotent delete-then-insert).
        with session_factory() as session:
            index_version(
                response.version_id,
                session=session,
                embeddings=embeddings,
                blob_store=blobs,
            )
            session.commit()

        fetched_at = response.source.fetched_at.isoformat()
        resolved.append(
            _ResolvedUrl(
                url=url,
                document_id=response.document_id,
                version_id=response.version_id,
                fetched_at=fetched_at,
            )
        )
        sources.append(
            SourceStatus(
                url=url,
                document_id=response.document_id,
                version_id=response.version_id,
                fetched_at=fetched_at,
                status="ok",
            )
        )

    return resolved, sources, warnings


def _best_quote(
    chunk: Chunk, ir_by_version: dict[str, Document], query: str
) -> Evidence:
    """Build the literal evidence quote for ``chunk`` (SPEC §8).

    When a single IR block backs the chunk, the quote is that block's text.
    Otherwise it is the sentence (across the backing blocks) with the highest
    lexical overlap with the query. The quote is ALWAYS copied literally from
    the IR and truncated to <=300 characters.
    """
    doc = ir_by_version.get(chunk.version_id)
    blocks_by_id = {b.block_id: b for b in doc.blocks} if doc is not None else {}
    backing = [blocks_by_id[bid] for bid in chunk.block_ids if bid in blocks_by_id]

    first_block_id = chunk.block_ids[0] if chunk.block_ids else ""
    page = backing[0].page if backing else None

    if len(backing) == 1:
        return Evidence(
            block_id=backing[0].block_id,
            page=backing[0].page,
            quote=backing[0].text[:_MAX_QUOTE_CHARS],
        )

    query_tokens = set(_tokenize(query))
    best_sentence = ""
    best_block_id = first_block_id
    best_page = page
    best_overlap = -1
    for block in backing:
        for sentence in _SENTENCE_RE.split(block.text):
            sentence = sentence.strip()
            if not sentence:
                continue
            overlap = len(query_tokens & set(_tokenize(sentence)))
            if overlap > best_overlap:
                best_overlap = overlap
                best_sentence = sentence
                best_block_id = block.block_id
                best_page = block.page

    if not best_sentence and backing:
        best_sentence = backing[0].text
        best_block_id = backing[0].block_id
        best_page = backing[0].page

    return Evidence(
        block_id=best_block_id,
        page=best_page,
        quote=best_sentence[:_MAX_QUOTE_CHARS],
    )


def _coverage(max_rerank: float | None, settings: Settings) -> str:
    """Classify coverage from the max rerank score (SPEC §6 step 7 heuristic)."""
    score = max_rerank if max_rerank is not None else 0.0
    if score < settings.coverage_low:
        return "insufficient"
    if score < settings.coverage_high:
        return "partial"
    return "sufficient"


def assemble_context(
    req: AssembleRequest,
    *,
    session_factory: sessionmaker[Session],
    blobs: BlobStore,
    robots: RobotsChecker,
    resolver: Resolver | None,
    transport: Transport | None,
    embeddings: EmbeddingProvider,
    reranker: RerankProvider,
    settings: Settings,
    now: datetime,
) -> AssembleResult:
    """Assemble an agent-ready context package for ``req`` (SPEC §6 steps 1-7)."""
    result = AssembleResult(
        query=req.query,
        budget_tokens=req.max_tokens,
        coverage="insufficient",
    )

    # (1-2) Resolve + index each URL independently; collect current versions.
    resolved, sources, warnings = _resolve_and_index(
        req,
        session_factory=session_factory,
        blobs=blobs,
        robots=robots,
        resolver=resolver,
        transport=transport,
        embeddings=embeddings,
        settings=settings,
        now=now,
    )
    result.sources = sources
    result.warnings = warnings

    version_ids = [r.version_id for r in resolved]
    doc_id_by_version = {r.version_id: r.document_id for r in resolved}
    url_by_version = {r.version_id: r.url for r in resolved}
    if not version_ids:
        return result

    # (3) Lexical + vector search over the current versions (ablation-aware).
    chunk_by_key: dict[Key, Chunk] = {}
    lexical_rank_by_key: dict[Key, int] = {}
    vector_rank_by_key: dict[Key, int] = {}

    with session_factory() as session:
        if req.use_lexical:
            for chunk, position in lexical_search(
                session, query=req.query, version_ids=version_ids
            ):
                key = _key(chunk, doc_id_by_version)
                chunk_by_key[key] = chunk
                lexical_rank_by_key[key] = position
        if req.use_vector:
            query_embedding = embeddings.embed([req.query])[0]
            for chunk, position in vector_search(
                session, query_embedding=query_embedding, version_ids=version_ids
            ):
                key = _key(chunk, doc_id_by_version)
                chunk_by_key[key] = chunk
                vector_rank_by_key[key] = position

    if not chunk_by_key:
        return result

    # (4) RRF fusion. Keys carry (document_id, ord) so the tie-break is stable.
    # The lexical list is weighted by settings.lexical_weight (M8 precision
    # knob; default 1.0 = identity) and the vector list by a fixed 1.0. Weights
    # are aligned to the lists ACTUALLY fused so an ablation that drops a list
    # (use_lexical/use_vector) still passes one weight per ranked list.
    lexical_ranked = _ranked_keys(lexical_rank_by_key)
    vector_ranked = _ranked_keys(vector_rank_by_key)
    ranked_lists: list[list[Key]] = [lexical_ranked, vector_ranked]
    weights: list[float] = [settings.lexical_weight, 1.0]
    fused = rrf_fuse(ranked_lists, k=settings.rrf_k, weights=weights)
    rrf_by_key = dict(fused)

    # (5) Rerank the top-30 and derive each candidate's selection value.
    documents = {key: chunk_by_key[key].text for key in rrf_by_key}
    reranked = rerank_candidates(
        req.query,
        fused,
        provider=reranker,
        use_rerank=req.use_rerank,
        documents=documents,
    )
    rerank_by_key = {key: score for key, _, score in reranked}

    candidates = [
        Candidate(
            chunk_id=chunk_by_key[key].id,
            document_id=key[0],
            ord=key[1],
            value=value,
            tokens=chunk_by_key[key].tokens,
            embedding=list(chunk_by_key[key].embedding),
        )
        for key, value, _ in reranked
    ]

    # (6) Selection: greedy under budget, or truncate-by-value ablation.
    if req.selection == "truncate":
        selected = _truncate_select(candidates, budget_tokens=req.max_tokens)
    else:
        selected = select(
            candidates,
            budget_tokens=req.max_tokens,
            lambda_=settings.selection_lambda,
            min_value=settings.min_value,
        )

    # (7) Build fragments (already ordered by (document_id, ord)). Evidence is
    # skipped when not asked for, except in the concise format, which needs it
    # for PDF pages (SPEC §8).
    with_evidence = req.include_evidence or req.response_format == "concise"
    ir_by_version = _load_ir(blobs, version_ids) if with_evidence else {}
    fragments: list[Fragment] = []
    for candidate in selected:
        frag_key: Key = (candidate.document_id, candidate.ord)
        chunk = chunk_by_key[frag_key]
        fragments.append(
            Fragment(
                chunk_id=str(chunk.id),
                document_id=candidate.document_id,
                version_id=chunk.version_id,
                source_url=url_by_version[chunk.version_id],
                heading_path=list(chunk.heading_path),
                md=chunk.md,
                tokens=chunk.tokens,
                scores=FragmentScores(
                    lexical_rank=lexical_rank_by_key.get(frag_key),
                    vector_rank=vector_rank_by_key.get(frag_key),
                    rrf=rrf_by_key.get(frag_key, 0.0),
                    rerank=rerank_by_key.get(frag_key),
                ),
                evidence=(
                    [_best_quote(chunk, ir_by_version, req.query)]
                    if with_evidence
                    else []
                ),
            )
        )

    result.fragments = fragments
    result.output_tokens = sum(f.tokens for f in fragments)

    rerank_scores = [s for s in rerank_by_key.values() if s is not None]
    max_rerank = max(rerank_scores) if rerank_scores else None
    result.coverage = _coverage(max_rerank, settings)
    chosen = {(c.document_id, c.ord) for c in selected}
    result.budget_limited = any(
        key not in chosen and score is not None and score >= settings.coverage_low
        for key, score in rerank_by_key.items()
    )
    return result


def _key(chunk: Chunk, doc_id_by_version: dict[str, str]) -> Key:
    """Build the ``(document_id, ord)`` fusion key for a chunk."""
    return (doc_id_by_version[chunk.version_id], chunk.ord)


def _ranked_keys(rank_by_key: dict[Key, int]) -> list[Key]:
    """Return keys ordered by their 1-based rank position (best first)."""
    return [key for key, _ in sorted(rank_by_key.items(), key=lambda kv: kv[1])]


def _truncate_select(
    candidates: list[Candidate], *, budget_tokens: int
) -> list[Candidate]:
    """Ablation: fill by value order until the budget is exhausted (SPEC §6)."""
    ordered = sorted(candidates, key=lambda c: (-c.value, c.document_id, c.ord))
    selected: list[Candidate] = []
    budget_left = budget_tokens
    for candidate in ordered:
        if 0 < candidate.tokens <= budget_left:
            selected.append(candidate)
            budget_left -= candidate.tokens
    selected.sort(key=lambda c: (c.document_id, c.ord))
    return selected


def _load_ir(blobs: BlobStore, version_ids: list[str]) -> dict[str, Document]:
    """Load the stored IR for each version (for literal evidence quotes)."""
    ir_by_version: dict[str, Document] = {}
    for version_id in version_ids:
        ir_by_version[version_id] = blobs.get_ir(version_id)
    return ir_by_version
