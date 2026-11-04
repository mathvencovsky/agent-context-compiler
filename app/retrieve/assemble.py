"""Context assembly orchestrator (SPEC §6 steps 1-7, §8). No LLM anywhere.

``assemble_context`` is the heart of ``POST /v1/context/assemble``. It stitches
the M5 resolve path, the M6 indexer and the FEAT-002 retrieval/selection core
into one deterministic pipeline:

1. Resolve each URL through the M5 resolve path (24h cache) and ensure its
   current version is indexed (reusing ``index_version``). Each URL fails
   INDEPENDENTLY — a failure is recorded in ``sources[].status`` + ``warnings``
   and never aborts the others (SPEC §8). The resolves run CONCURRENTLY on a
   small thread pool (``settings.assemble_fetch_concurrency``, at most
   ``settings.assemble_per_host_concurrency`` per host); URLs that may name
   the same document run one after the other, in request order, so they never
   race on its rows. Indexing stays in request order on the calling thread, so
   chunk rows, ``sources[]``, ``warnings`` and everything after are exactly
   what the one-by-one loop produced.
2. Collect the current ``version_id`` of every OK URL.
3. Lexical top-50 + vector top-50 over those versions (ablation ``use_lexical`` /
   ``use_vector`` may drop a list). The lexical scorer and the page prior come
   from ``LexicalConfig.from_settings`` (defaults: today's overlap, no prior).
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

import contextvars
import re
import threading
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date as calendar_date
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.api.documents import resolve_document
from app.api.schemas import AssembleRequest, ResolveRequest, ResolveResponse
from app.index.indexer import index_version
from app.providers.embeddings.base import embed_query
from app.retrieve.lexical import LexicalConfig, lexical_search_ranked
from app.retrieve.page_prior import apply_page_rrf
from app.retrieve.rerank import rerank_candidates
from app.retrieve.rrf import rrf_fuse
from app.retrieve.select import Candidate, select
from app.retrieve.vector import vector_search

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session, sessionmaker

    from app.config import Settings
    from app.extract.ir import Document, Metadata
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
    #: cl100k tokens of the whole page as Markdown (resolve ``stats``): what
    #: ``resolve_url`` would cost; lets the concise form say it is an excerpt.
    page_tokens: int | None = None
    #: The page's own date, from extracted metadata (:func:`page_date`).
    date: str | None = None


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
    #: The release the caller asked for (``version``); feeds the
    #: ``version_mismatch`` hint. Set by the API layer, never by selection.
    requested_version: str | None = None
    #: Low-budget mode outcome (:mod:`app.retrieve.compact`): ``None`` when
    #: ``compact`` was not asked, else ``"pruned"`` or ``"whole"``.
    compact: str | None = None


@dataclass
class _ResolvedUrl:
    """Internal bookkeeping for a successfully resolved + indexed URL."""

    url: str
    document_id: str
    version_id: str
    fetched_at: str


#: A leading ISO 8601 calendar date (``2024-05-01``, ``2024-05-01T10:00Z``).
_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def page_date(metadata: Metadata) -> str | None:
    """The page's date as ``YYYY-MM-DD``, or ``None`` when it has none.

    Uses only what extraction already captured (``modified_at``, then
    ``published_at``: SPEC §4 ``Metadata``). A value counts only when it starts
    with a valid ISO calendar date; free text is never guessed, and the result
    holds digits and dashes only, so no page text reaches the output.
    """
    for raw in (metadata.modified_at, metadata.published_at):
        match = _ISO_DATE.match(raw or "")
        if match is None:
            continue
        try:
            return calendar_date.fromisoformat(match.group(1)).isoformat()
        except ValueError:
            continue
    return None


def _tokenize(text: str) -> list[str]:
    """Lowercase ``text`` and return its alphanumeric tokens (with repeats)."""
    return _TOKEN_RE.findall(text.lower())


@dataclass
class _UrlOutcome:
    """What one URL contributes to ``sources[]``, ``warnings`` and the search."""

    source: SourceStatus
    warning: dict[str, object] | None = None
    resolved: _ResolvedUrl | None = None


def _failed(url: str, exc: Exception) -> _UrlOutcome:
    """The outcome of a URL whose resolve raised (SPEC §8: it fails alone)."""
    code = getattr(exc, "code", "fetch_failed")
    upstream = getattr(exc, "details", {}).get("upstream_status")
    reason = f"{code} (HTTP {upstream})" if upstream else code
    return _UrlOutcome(
        source=SourceStatus(
            url=url,
            document_id=None,
            version_id=None,
            fetched_at=None,
            status=code,
        ),
        warning={
            "code": code,
            "message": f"failed to resolve {url}: {reason}",
            "url": url,
        },
    )


def _resolved(url: str, response: ResolveResponse) -> _UrlOutcome:
    """The outcome of a URL that resolved (and whose version is indexed)."""
    warning: dict[str, object] | None = None
    if response.stats.blocks == 0:
        # Explicit failure (CLAUDE.md): the page resolved but holds no text,
        # so it can contribute no fragment. Say so, with the document's own
        # warning codes (NINA's codes, never page text).
        codes = sorted({w.code for w in response.warnings})
        reason = f" ({', '.join(codes)})" if codes else ""
        warning = {
            "code": "empty_content",
            "message": f"{url} resolved but has no extractable text{reason}",
            "url": url,
        }

    fetched_at = response.source.fetched_at.isoformat()
    return _UrlOutcome(
        source=SourceStatus(
            url=url,
            document_id=response.document_id,
            version_id=response.version_id,
            fetched_at=fetched_at,
            status="ok",
            page_tokens=response.stats.tokens_markdown,
            date=page_date(response.metadata),
        ),
        warning=warning,
        resolved=_ResolvedUrl(
            url=url,
            document_id=response.document_id,
            version_id=response.version_id,
            fetched_at=fetched_at,
        ),
    )


def _url_keys(url: str) -> tuple[str, str]:
    """``(host, document group)`` scheduling keys of a requested URL.

    The group over-approximates the guard's normalization (host lowercased,
    one trailing dot dropped, IDNA; fragment dropped; scheme and port ignored):
    any two URLs the guard could normalize to the same document share a group,
    so they are resolved one after the other and never race on the same rows.
    Over-grouping only costs parallelism. A URL that cannot be split keys on
    itself; the guard rejects it before any database access anyway.
    """
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".")
        if not host.isascii():
            host = host.encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return url, url
    return host, f"{host}|{parts.path}|{parts.query}"


def _in_request_order[T](
    urls: list[str],
    resolve: Callable[[str], T],
    settings: Settings,
) -> Generator[Future[T]]:
    """Run ``resolve`` over ``urls`` on a bounded pool; yield futures in order.

    Same-group URLs run sequentially, in request order, inside one task; at
    most ``assemble_per_host_concurrency`` URLs of one host run at once. Each
    task runs in a copy of the caller's context, so request-scoped log context
    follows it into the worker thread. With one worker (one group, or the
    concurrency setting at 1) everything runs inline, one URL at a time, like
    the original loop. Tasks not started when the consumer stops are dropped.
    """
    groups: dict[str, list[int]] = {}
    host_of: list[str] = []
    for index, url in enumerate(urls):
        host, group = _url_keys(url)
        groups.setdefault(group, []).append(index)
        host_of.append(host)
    results: list[Future[T]] = [Future() for _ in urls]

    def run(index: int) -> None:
        try:
            results[index].set_result(resolve(urls[index]))
        except BaseException as exc:  # noqa: BLE001 — handed to the consumer.
            results[index].set_exception(exc)

    workers = min(max(1, settings.assemble_fetch_concurrency), len(groups))
    if workers <= 1:
        for index in range(len(urls)):
            run(index)
            yield results[index]
        return

    per_host = max(1, settings.assemble_per_host_concurrency)
    host_slots = {host: threading.BoundedSemaphore(per_host) for host in host_of}

    def run_group(indices: list[int]) -> None:
        for index in indices:
            with host_slots[host_of[index]]:
                run(index)

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="assemble")
    try:
        for indices in groups.values():
            pool.submit(contextvars.copy_context().run, run_group, indices)
        yield from results
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


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
    (SPEC §8). The resolves (fetch, render, extract, store) run concurrently
    (:func:`_in_request_order`); indexing then runs here, one URL at a time
    and in request order, as each resolve completes, so chunk rows are written
    in the same order as by the one-by-one loop and every list comes back in
    request order.
    """

    def resolve(url: str) -> ResolveResponse | Exception:
        try:
            return resolve_document(
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
            return exc

    outcomes: list[_UrlOutcome] = []
    with closing(_in_request_order(list(req.urls), resolve, settings)) as futures:
        for url, future in zip(req.urls, futures, strict=True):
            response = future.result()
            if isinstance(response, Exception):
                outcomes.append(_failed(url, response))
                continue
            # Ensure the current version is indexed (idempotent
            # delete-then-insert).
            with session_factory() as session:
                index_version(
                    response.version_id,
                    session=session,
                    embeddings=embeddings,
                    blob_store=blobs,
                )
                session.commit()
            outcomes.append(_resolved(url, response))

    resolved = [o.resolved for o in outcomes if o.resolved is not None]
    sources = [o.source for o in outcomes]
    warnings = [o.warning for o in outcomes if o.warning is not None]
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
    """Classify coverage from the max rerank score (SPEC §6 step 7 heuristic).

    The thresholds were calibrated on Jina scores: with a local reranker
    (``reranker_base_url``) the scale differs and the label is ``"unknown"``.
    """
    if settings.reranker_base_url:
        return "unknown"
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

    lexical_config = LexicalConfig.from_settings(settings)
    page_rank: dict[str, int] = {}
    with session_factory() as session:
        if req.use_lexical:
            lexical, page_rank = lexical_search_ranked(
                session,
                query=req.query,
                version_ids=version_ids,
                config=lexical_config,
                url_by_document={r.document_id: r.url for r in resolved},
            )
            for chunk, position in lexical:
                key = _key(chunk, doc_id_by_version)
                chunk_by_key[key] = chunk
                lexical_rank_by_key[key] = position
        if req.use_vector:
            query_embedding = embed_query(embeddings, req.query)
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
    # Page prior as a third list (M10b, off by default): every candidate gains
    # the RRF term of its page's rank; a no-op when page_rank is empty.
    fused = apply_page_rrf(
        fused, page_rank, weight=lexical_config.page_prior_weight, k=settings.rrf_k
    )
    # Fragments the caller already has leave before the rerank, so the next
    # candidates take their places in the top-30 (SPEC §8 exclude_chunk_ids).
    if req.exclude_chunk_ids:
        excluded = set(req.exclude_chunk_ids)
        fused = [(k, v) for k, v in fused if str(chunk_by_key[k].id) not in excluded]
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
    reranked = _drop_copies(reranked, {k: c.text for k, c in chunk_by_key.items()})
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
    if not settings.reranker_base_url:
        result.budget_limited = any(
            key not in chosen and score is not None and score >= settings.coverage_low
            for key, score in rerank_by_key.items()
        )
    elif not req.compact:
        # Uncalibrated scores: no threshold; any unchosen candidate counts. In
        # compact mode, compact_result decides from the text it dropped.
        result.budget_limited = any(key not in chosen for key in rerank_by_key)
    return result


def _drop_copies[T](
    ranked: list[tuple[Key, float, T]], text_by_key: dict[Key, str]
) -> list[tuple[Key, float, T]]:
    """Drop later entries whose text equals an earlier one (whitespace-normalized).

    ``ranked`` is best first, so the highest-ranked copy stays. Exact copies only.
    """
    seen: set[str] = set()
    kept = []
    for entry in ranked:
        text = " ".join(text_by_key[entry[0]].split())
        if text not in seen:
            seen.add(text)
            kept.append(entry)
    return kept


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
