"""``ours`` runner + ablations: the full NINA pipeline over the snapshot (SPEC §6).

Unlike the baselines, ``ours`` runs NINA's real retrieval/selection core
(:mod:`app.retrieve`) against the frozen corpus — WITHOUT Postgres. For each
task URL it reads the snapshot bytes from the :class:`~bench.corpus_store.
CorpusStore`, extracts the IR (HTML or PDF), chunks it with
:func:`app.index.chunker.chunk_document`, embeds every chunk with the
deterministic :class:`FakeEmbeddingProvider`, then reproduces the DB search in
memory:

* lexical ranks from a deterministic token-overlap scorer (the same lowercase
  alphanumeric tokenization the fake reranker uses), top-50;
* vector ranks from cosine similarity between the query embedding and each
  chunk embedding, top-50.

The two rankings are fused with :func:`app.retrieve.rrf.rrf_fuse` (keys carry
``(document_id, ord)`` so the tie-break is deterministic), the top-30 are
reranked with the deterministic :class:`FakeRerankProvider`
(:func:`app.retrieve.rerank.rerank_candidates`), and the survivors are chosen
under the token budget by :func:`app.retrieve.select.select`. The emitted
context is the selected chunks' Markdown in document order (``(document_id,
ord)``), truncated for safety.

The ablations flip exactly one knob each (SPEC §6):

* ``ours_no_vector`` — drop the vector list (lexical-only fusion);
* ``ours_no_lexical`` — drop the lexical list (vector-only fusion);
* ``ours_no_rerank`` — skip the reranker (value = normalized RRF);
* ``ours_fixed_chunks`` — fixed 400-token chunks instead of structural chunking;
* ``ours_truncate`` — fill by value order until the budget (no greedy diversity).

Everything is deterministic and offline: the fake providers never touch the
network and two runs over the same corpus produce byte-identical context
(CLAUDE.md / SPEC §10 determinism).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.extract.html import extract_html
from app.extract.ir import (
    Document,
    SourceInfo,
    make_document_id,
    make_version_id,
)
from app.extract.pdf import extract_pdf
from app.fetch.detect import parse_content_type
from app.index.chunker import Chunk, chunk_document
from app.index.tokens import count_tokens
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.rerank.fake import FakeRerankProvider
from app.retrieve.rerank import rerank_candidates
from app.retrieve.rrf import rrf_fuse
from app.retrieve.select import Candidate, cosine, select
from bench.corpus_store import CorpusStore
from bench.runners.base import ContextResult, truncate_to_budget
from bench.tasks_schema import Task

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.rerank.base import RerankProvider

#: SPEC §6: lexical/vector lists are each capped at the top-50 before fusion.
_SEARCH_TOP_K = 50

#: Fused ranking key: (document_id, ord) — deterministic RRF tie-break.
_Key = tuple[str, int]

#: Lowercase alphanumeric tokenizer for the in-memory lexical scorer.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Fixed chunk size (in cl100k_base tokens) for the ``ours_fixed_chunks`` ablation.
_FIXED_CHUNK_TOKENS = 400

#: RRF smoothing constant and selection knobs (mirror app.config defaults;
#: SPEC §6). The bench runner is self-contained and never reads Settings.
_RRF_K = 60
_SELECTION_LAMBDA = 0.5
_MIN_VALUE = 0.05

_PDF_CONTENT_TYPE = "application/pdf"


def _tokenize(text: str) -> list[str]:
    """Return lowercase alphanumeric tokens for ``text``."""
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class _IndexedChunk:
    """A chunk plus its fusion key, embedding and ordering position."""

    key: _Key
    document_id: str
    ord: int
    chunk: Chunk
    embedding: list[float]


@dataclass(frozen=True)
class PreparedCandidates:
    """The selection-invariant result of the pipeline steps (1)-(5).

    Holds the reranked :class:`Candidate` list plus the minimal maps needed to
    emit the context (``md_by_key``, ``url_by_document``) and the max rerank
    score (for coverage derivation). Depends only on the task + search/rerank —
    NOT on ``lambda_``/``min_value`` — so it can be computed once per task and
    reused across the calibration grid.
    """

    candidates: list[Candidate]
    md_by_key: dict[_Key, str]
    url_by_document: dict[str, str]
    max_rerank: float | None


def _source_for(url: str, content_type: str) -> tuple[SourceInfo, str, str]:
    """Build a synthetic :class:`SourceInfo` + ids for a snapshot URL.

    The ids are derived deterministically from ``url`` so a chunk's provenance
    is stable across runs; no network or DB is involved.
    """
    document_id = make_document_id(url)
    version_id = make_version_id(document_id)
    render_mode = "pdf" if content_type == _PDF_CONTENT_TYPE else "http"
    source = SourceInfo(
        url=url,
        final_url=url,
        content_type=content_type,
        fetched_at=datetime(1970, 1, 1, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash=version_id,
        render_mode=render_mode,
    )
    return source, document_id, version_id


def _extract_ir(raw: bytes, content_type: str, source: SourceInfo) -> Document:
    """Extract the IR for a snapshot body (PDF or HTML branch)."""
    if content_type == _PDF_CONTENT_TYPE:
        return extract_pdf(raw, source)
    _media, charset = parse_content_type(content_type)
    decoded = raw.decode(charset or "utf-8", errors="replace")
    return extract_html(decoded, source)


def _fixed_chunks(doc: Document) -> list[Chunk]:
    """Fixed 400-token chunks over the document text (``ours_fixed_chunks``)."""
    from app.index.tokens import _encoding

    encoding = _encoding()
    text = "\n\n".join(block.text for block in doc.blocks if block.text)
    token_ids = encoding.encode(text)
    chunks: list[Chunk] = []
    for ord_, start in enumerate(range(0, len(token_ids), _FIXED_CHUNK_TOKENS)):
        piece = encoding.decode(token_ids[start : start + _FIXED_CHUNK_TOKENS])
        if not piece.strip():
            continue
        block_ids = [b.block_id for b in doc.blocks] or ["block_0"]
        chunks.append(
            Chunk(
                version_id=doc.version_id,
                block_ids=block_ids,
                heading_path=[],
                text=piece,
                md=piece,
                tokens=count_tokens(piece),
                ord=ord_,
            )
        )
    return chunks


def _lexical_ranks(indexed: list[_IndexedChunk], query: str) -> list[_Key]:
    """Deterministic in-memory lexical ranking (top-50) by token overlap.

    Scores each chunk by the count of query tokens that occur in the chunk
    (``ts``-like overlap), drops zero-overlap chunks, and breaks ties on the key
    so the order is reproducible — the offline analogue of ``lexical_search``.
    """
    query_tokens = set(_tokenize(query))
    scored: list[tuple[float, _Key]] = []
    for item in indexed:
        chunk_tokens = _tokenize(item.chunk.text)
        overlap = sum(1 for t in chunk_tokens if t in query_tokens)
        if overlap > 0:
            scored.append((float(overlap), item.key))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [key for _, key in scored[:_SEARCH_TOP_K]]


def _vector_ranks(
    indexed: list[_IndexedChunk], query_embedding: list[float]
) -> list[_Key]:
    """In-memory vector ranking (top-50) by cosine to the query embedding.

    Mirrors ``vector_search``: higher cosine first, deterministic key tie-break.
    """
    scored = [(cosine(query_embedding, item.embedding), item.key) for item in indexed]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [key for _, key in scored[:_SEARCH_TOP_K]]


@dataclass(frozen=True)
class OursRunner:
    """NINA's full retrieve/select pipeline over the frozen snapshot (SPEC §6).

    The ablation flags configure the single named variants; the default instance
    is the full pipeline registered under ``ours``.

    ``lambda_`` and ``min_value`` are the SPEC §6 selection knobs; they default
    to the module constants (identical to today's behavior) and are injected by
    the M8 calibrator to sweep the grid. ``embeddings`` and ``reranker`` default
    to ``None``, in which case the deterministic fakes are constructed per call
    (so every test stays offline); the calibrator injects the REAL
    ``OpenAIEmbeddingProvider`` / ``JinaRerankProvider`` for the dev run.
    """

    name: str = "ours"
    use_lexical: bool = True
    use_vector: bool = True
    use_rerank: bool = True
    structural_chunking: bool = True
    selection: str = "greedy"
    lambda_: float = _SELECTION_LAMBDA
    min_value: float = _MIN_VALUE
    embeddings: EmbeddingProvider | None = field(default=None)
    reranker: RerankProvider | None = field(default=None)

    def _embeddings(self) -> EmbeddingProvider:
        """Return the injected embeddings provider, or the deterministic fake."""
        if self.embeddings is not None:
            return self.embeddings
        return FakeEmbeddingProvider()

    def _reranker(self) -> RerankProvider:
        """Return the injected reranker provider, or the deterministic fake."""
        if self.reranker is not None:
            return self.reranker
        return FakeRerankProvider()

    def prepare_candidates(
        self, task: Task, *, store: CorpusStore
    ) -> PreparedCandidates:
        """Run the EXPENSIVE, selection-invariant part of the pipeline (1)-(5).

        Extract/chunk/embed, lexical+vector search, RRF fusion and rerank the
        top-30 — everything that does NOT depend on ``lambda_``/``min_value``.
        The result (ordered :class:`Candidate` list + the md/url maps needed to
        emit + the max rerank score) is deterministic for a given task, so the
        M8 calibrator can compute it ONCE per task and reuse it across the 9 grid
        combinations instead of re-calling the real reranker/embeddings 9×.
        """
        indexed, url_by_document = self._index_task(task, store)
        if not indexed:
            return PreparedCandidates(
                candidates=[], md_by_key={}, url_by_document={}, max_rerank=None
            )

        embeddings = self._embeddings()
        chunk_by_key = {item.key: item for item in indexed}

        # (3) in-memory lexical + vector search (ablation-aware).
        lexical_ranked = (
            _lexical_ranks(indexed, task.question) if self.use_lexical else []
        )
        if self.use_vector:
            query_embedding = embeddings.embed([task.question])[0]
            vector_ranked = _vector_ranks(indexed, query_embedding)
        else:
            vector_ranked = []

        # (4) RRF fusion over the two rankings.
        fused = rrf_fuse([lexical_ranked, vector_ranked], k=_RRF_K)
        if not fused:
            return PreparedCandidates(
                candidates=[], md_by_key={}, url_by_document={}, max_rerank=None
            )

        # (5) rerank the top-30 (injected provider or fake) -> selection value.
        documents = {key: chunk_by_key[key].chunk.text for key, _ in fused}
        reranked = rerank_candidates(
            task.question,
            fused,
            provider=self._reranker(),
            use_rerank=self.use_rerank,
            documents=documents,
        )

        candidates = [
            Candidate(
                chunk_id=0,
                document_id=key[0],
                ord=key[1],
                value=value,
                tokens=chunk_by_key[key].chunk.tokens,
                embedding=chunk_by_key[key].embedding,
            )
            for key, value, _ in reranked
        ]
        md_by_key = {key: chunk_by_key[key].chunk.md for key, _, _ in reranked}
        rerank_scores = [score for _, _, score in reranked if score is not None]
        max_rerank = max(rerank_scores) if rerank_scores else None

        return PreparedCandidates(
            candidates=candidates,
            md_by_key=md_by_key,
            url_by_document=url_by_document,
            max_rerank=max_rerank,
        )

    def assemble_from_candidates(
        self, prepared: PreparedCandidates, *, budget_tokens: int
    ) -> ContextResult:
        """Apply ONLY the (pure) selection + emit (steps 6-7) to ``prepared``.

        No network and no provider call: ``select`` is pure and local, so this
        is the per-combination work the calibrator repeats for each
        ``(lambda_, min_value)`` over the cached candidates. The output is
        byte-identical to running the whole pipeline with the same params
        (``select`` sees the same candidates either way).
        """
        if not prepared.candidates:
            return ContextResult(text="", context_tokens=0, used_urls=[])

        # (6) selection: greedy under budget, or truncate-by-value ablation.
        if self.selection == "truncate":
            selected = _truncate_select(
                prepared.candidates, budget_tokens=budget_tokens
            )
        else:
            selected = select(
                prepared.candidates,
                budget_tokens=budget_tokens,
                lambda_=self.lambda_,
                min_value=self.min_value,
            )

        # (7) emit the selected chunks' Markdown in document order.
        used_urls: list[str] = []
        parts: list[str] = []
        for candidate in selected:
            key: _Key = (candidate.document_id, candidate.ord)
            url = prepared.url_by_document.get(candidate.document_id)
            if url is not None and url not in used_urls:
                used_urls.append(url)
            parts.append(prepared.md_by_key[key])

        joined = "\n\n".join(parts)
        truncated, tokens = truncate_to_budget(joined, budget_tokens)
        return ContextResult(text=truncated, context_tokens=tokens, used_urls=used_urls)

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Build an agent-ready context for ``task`` within ``budget_tokens``."""
        prepared = self.prepare_candidates(task, store=store)
        return self.assemble_from_candidates(prepared, budget_tokens=budget_tokens)

    def max_rerank_score(self, task: Task, *, store: CorpusStore) -> float | None:
        """Return the MAX rerank score over ``task``'s fused top-30, or ``None``.

        Runs steps (1)-(5) of the pipeline (extract/chunk/embed/fuse/rerank) and
        returns the largest rerank score the configured reranker assigned to any
        candidate — the exact single observable ``assemble._coverage`` thresholds
        on. Used by the M8 calibrator (D7) to derive ``coverage_low``/
        ``coverage_high`` from the observed dev distribution. ``None`` when the
        task yields no candidates or when rerank is off (no score to report).
        """
        if not self.use_rerank:
            return None
        return self.prepare_candidates(task, store=store).max_rerank

    def _index_task(
        self, task: Task, store: CorpusStore
    ) -> tuple[list[_IndexedChunk], dict[str, str]]:
        """Extract + chunk + embed every task URL into fused-ready chunks.

        Returns the indexed chunks and a ``document_id -> url`` map (used to
        report ``used_urls``).
        """
        embeddings = self._embeddings()
        url_by_document: dict[str, str] = {}
        indexed: list[_IndexedChunk] = []

        for url in task.urls:
            try:
                raw = store.raw_bytes(url)
            except (KeyError, ValueError):
                continue
            content_type = store.content_type(url)
            source, document_id, _version_id = _source_for(url, content_type)
            url_by_document[document_id] = url
            try:
                doc = _extract_ir(raw, content_type, source)
            except Exception:  # noqa: BLE001 — a bad URL must not abort the task.
                continue

            chunks = (
                chunk_document(doc) if self.structural_chunking else _fixed_chunks(doc)
            )
            if not chunks:
                continue
            vectors = embeddings.embed([chunk.text for chunk in chunks])
            for chunk, vector in zip(chunks, vectors, strict=True):
                key: _Key = (document_id, chunk.ord)
                indexed.append(
                    _IndexedChunk(
                        key=key,
                        document_id=document_id,
                        ord=chunk.ord,
                        chunk=chunk,
                        embedding=vector,
                    )
                )
        return indexed, url_by_document


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


def make_ours_runners() -> list[OursRunner]:
    """Return the ``ours`` runner and its five ablation variants (SPEC §6)."""
    return [
        OursRunner(),
        OursRunner(name="ours_no_vector", use_vector=False),
        OursRunner(name="ours_no_lexical", use_lexical=False),
        OursRunner(name="ours_no_rerank", use_rerank=False),
        OursRunner(name="ours_fixed_chunks", structural_chunking=False),
        OursRunner(name="ours_truncate", selection="truncate"),
    ]
