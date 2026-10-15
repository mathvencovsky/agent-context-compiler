"""``strong_rag`` baseline: the best RAG a good production engineer ships today.

This is a STRONG, INDEPENDENT baseline for the NINA bench — "a non-naive RAG" —
with NONE of NINA's proprietary pipeline. It exists to answer honestly whether
NINA's advantage (efficiency + traceable evidence at quality parity) survives a
competent, modern opponent rather than the deliberately weak ``naive_rag``.

Pipeline (assembled independently; only generic pieces are reused):

1. Sliding-window chunking WITH OVERLAP over trafilatura-extracted text —
   ~400-token windows with ~25% (~100-token) overlap (step 300). This is a
   decent, standard chunking choice, NOT NINA's structural ``chunk_document`` and
   NOT the blind, non-overlapping fixed chunker ``naive_rag`` uses.
2. HYBRID retrieval: a BM25 ranking (``rank_bm25``, same tokenizer as
   ``naive_rag``) and a dense-vector cosine ranking over an injected
   :class:`~app.providers.embeddings.base.EmbeddingProvider`, fused with
   :func:`app.retrieve.rrf.rrf_fuse` at ``k=60`` (plain RRF — ``lexical_weight``
   is a NINA knob and is deliberately NOT used here).
3. Cross-encoder RERANK of the top-30 fused candidates via
   :func:`app.retrieve.rerank.rerank_candidates` with an injected
   :class:`~app.providers.rerank.base.RerankProvider`. The reranker is what makes
   this baseline strong — on the paid sweep it is the real Jina cross-encoder.
4. SELECTION: simple greedy top-k by DESCENDING rerank score, accumulating chunks
   until the token budget fits (standard RAG). This deliberately does NOT use
   NINA's MMR-under-budget selection (:func:`app.retrieve.select.select`) — that
   is the ``ours`` differential being isolated, so borrowing it would make the
   comparison meaningless.
5. Provenance: each kept fragment is emitted as ``[URL] <url>\n<text>``,
   byte-identical to ``naive_rag`` / ``naive_rag_vector`` / ``ours`` so citation
   precision is measured fairly, then truncated to the budget.

What ``strong_rag`` deliberately does NOT have (so the boundary is explicit): no
query rewriting / LLM-based context compression (that would break the "no LLM in
retrieval/selection" rule, CLAUDE.md); no NINA MMR-under-budget selection (the
isolated variable); no NINA structural chunking (overlap sliding window instead).

The default providers are the deterministic fakes
(:class:`FakeEmbeddingProvider` / :class:`FakeRerankProvider`), so every test is
offline and byte-stable; the paid sweep injects the disk-cached OpenAI embeddings
+ real Jina reranker via :func:`bench.runners.base.register_ours`.

``rank_bm25`` is Apache-2.0 and pulls numpy (BSD) — both already in use and
recorded in DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rank_bm25 import BM25Okapi

from app.fetch.detect import parse_content_type
from app.index.tokens import count_tokens
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.rerank.fake import FakeRerankProvider
from app.retrieve.rerank import rerank_candidates
from app.retrieve.rrf import rrf_fuse
from app.retrieve.select import cosine
from bench.corpus_store import CorpusStore
from bench.runners.base import ContextResult, truncate_to_budget
from bench.runners.naive_rag import _tokenize
from bench.runners.naive_text import extract_text
from bench.tasks_schema import Task

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.rerank.base import RerankProvider

#: RRF smoothing constant for the hybrid fusion (SPEC §6, same k the product uses).
_RRF_K = 60

#: Sliding-window chunk size, in cl100k_base tokens (~400-token windows).
_CHUNK_TOKENS = 400

#: Sliding-window overlap, in cl100k_base tokens (~25% of the window -> step 300).
_CHUNK_OVERLAP = 100

#: SPEC §6 step 4 default: rerank the 30 best of the fused ranking.
_RERANK_TOP_N = 30

#: Fusion key: a chunk's stable corpus position ``(url, order)`` — deterministic
#: RRF tie-break, identical to the re-emission order.
_Key = tuple[str, int]


@dataclass(frozen=True)
class _Chunk:
    """One sliding-window text chunk and its stable position in the corpus."""

    url: str
    order: int
    text: str


def _sliding_window_chunks(text: str) -> list[str]:
    """Split ``text`` into overlapping ~400-token windows (step 300).

    Encodes with the shared cl100k_base encoder, then emits
    ``token_ids[start : start + _CHUNK_TOKENS]`` stepping by
    ``_CHUNK_TOKENS - _CHUNK_OVERLAP`` (= 300), so consecutive windows share the
    last :data:`_CHUNK_OVERLAP` tokens of one with the first of the next. The
    final window is the tail remainder (no padding). Empty / whitespace-only
    windows are dropped. Fully deterministic — the ONLY chunking difference from
    ``naive_rag_vector`` is the overlap (vs blind, non-overlapping chunks).
    """
    from app.index.tokens import _encoding

    encoding = _encoding()
    token_ids = encoding.encode(text)
    if not token_ids:
        return []
    step = _CHUNK_TOKENS - _CHUNK_OVERLAP
    chunks: list[str] = []
    for start in range(0, len(token_ids), step):
        piece = encoding.decode(token_ids[start : start + _CHUNK_TOKENS])
        if piece.strip():
            chunks.append(piece)
        if start + _CHUNK_TOKENS >= len(token_ids):
            break
    return chunks


@dataclass(frozen=True)
class StrongRagRunner:
    """Strong, independent RAG baseline: overlap chunking + hybrid + rerank top-k.

    ``embeddings`` and ``reranker`` default to ``None``, in which case the
    deterministic fakes are built per call (so every test stays offline); the
    paid sweep injects the real disk-cached OpenAI provider + the real Jina
    cross-encoder. Selection is greedy top-k by DESCENDING rerank score under
    budget — NOT NINA's MMR selection, which is the ``ours`` differential.
    """

    name: str = "strong_rag"
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

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Hybrid-retrieve, rerank the top-30, keep top-k by score within budget."""
        chunks: list[_Chunk] = []
        for url in task.urls:
            try:
                raw = store.raw_bytes(url)
            except (KeyError, ValueError):
                continue
            _media, charset = parse_content_type(store.content_type(url))
            html = raw.decode(charset or "utf-8", errors="replace")
            text = extract_text(html)
            if not text:
                continue
            for order, piece in enumerate(_sliding_window_chunks(text)):
                chunks.append(_Chunk(url=url, order=order, text=piece))

        if not chunks:
            return ContextResult(text="", context_tokens=0, used_urls=[])

        keys: list[_Key] = [(chunk.url, chunk.order) for chunk in chunks]
        chunk_by_key: dict[_Key, _Chunk] = dict(zip(keys, chunks, strict=True))

        # BM25 ranking (same tokenizer as naive_rag), best first, key tie-break.
        tokenized = [_tokenize(chunk.text) for chunk in chunks]
        bm25 = BM25Okapi(tokenized)
        bm25_scores = bm25.get_scores(_tokenize(task.question))
        bm25_scored = [(float(bm25_scores[i]), key) for i, key in enumerate(keys)]
        bm25_scored.sort(key=lambda s: (-s[0], s[1]))
        bm25_ranked = [key for _, key in bm25_scored]

        # Vector ranking: cosine between the query embedding and each chunk
        # embedding, best first, deterministic key tie-break ((url, order) asc).
        embeddings = self._embeddings()
        vectors = embeddings.embed([chunk.text for chunk in chunks])
        query_embedding = embeddings.embed([task.question])[0]
        vector_scored = [
            (cosine(query_embedding, vector), key)
            for vector, key in zip(vectors, keys, strict=True)
        ]
        vector_scored.sort(key=lambda s: (-s[0], s[1]))
        vector_ranked = [key for _, key in vector_scored]

        # HYBRID fuse with plain RRF (k=60). No weights: lexical_weight is a NINA
        # knob and is deliberately not used by this independent baseline.
        fused = rrf_fuse([bm25_ranked, vector_ranked], k=_RRF_K)

        # Cross-encoder RERANK of the top-30 fused candidates. The ABSOLUTE score
        # (3rd element) is the standard cross-encoder relevance; the 2nd element
        # (``value``) is a min-max selection artifact that is NOT used here.
        documents = {key: chunk_by_key[key].text for key, _ in fused}
        reranked = rerank_candidates(
            task.question,
            fused,
            provider=self._reranker(),
            use_rerank=True,
            documents=documents,
            top_n=_RERANK_TOP_N,
        )

        # SELECTION: simple greedy top-k by DESCENDING absolute rerank score under
        # the budget (standard RAG). A ``None`` score is impossible here
        # (use_rerank=True), but is treated as -inf defensively. Tie-break by key
        # ascending so the order is deterministic. This is NOT NINA's MMR.
        ranked_by_score = sorted(
            reranked,
            key=lambda item: (
                -(item[2] if item[2] is not None else float("-inf")),
                item[0],
            ),
        )

        kept: list[_Key] = []
        used_tokens = 0
        for key, _value, _score in ranked_by_score:
            chunk_tokens = count_tokens(chunk_by_key[key].text)
            if used_tokens + chunk_tokens > budget_tokens:
                continue
            kept.append(key)
            used_tokens += chunk_tokens
            if used_tokens >= budget_tokens:
                break

        # If no whole chunk fit the budget, keep the single top-reranked chunk and
        # let the final token truncation trim it, so a tiny budget still yields
        # the most relevant text (mirrors the other baselines' fallback).
        if not kept:
            kept = [ranked_by_score[0][0]]

        # Re-emit kept chunks in corpus order for a coherent, stable context;
        # used_urls is in first-seen order. Provenance header is IDENTICAL to
        # naive_rag / naive_rag_vector / ours so citation is measured fairly.
        kept.sort()
        used_urls: list[str] = []
        parts: list[str] = []
        for key in kept:
            chunk = chunk_by_key[key]
            if chunk.url not in used_urls:
                used_urls.append(chunk.url)
            parts.append(f"[URL] {chunk.url}\n{chunk.text}")

        joined = "\n\n".join(parts)
        truncated, tokens = truncate_to_budget(joined, budget_tokens)
        return ContextResult(text=truncated, context_tokens=tokens, used_urls=used_urls)
