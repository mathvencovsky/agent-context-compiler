"""``naive_rag_vector`` baseline: a competent VECTOR RAG for a fair fight (SPEC §10.3).

The ``naive_rag`` baseline is BM25-only — a weak strawman next to a 2026 RAG.
This baseline keeps everything about ``naive_rag`` EXCEPT the retrieval method,
so it is a clean ablation of lexical vs vector/hybrid retrieval: it reuses the
same trafilatura text extraction (:func:`bench.runners.naive_text.extract_text`)
and the same fixed 400-token chunking (:func:`bench.runners.naive_rag._chunk_text`),
and emits the IDENTICAL ``[URL] <url>\n<text>`` provenance so citation is fair.

The default is the stronger, fairer HYBRID: a BM25 ranking and a dense-vector
(cosine) ranking are fused with :func:`app.retrieve.rrf.rrf_fuse` at ``k=60``
(the product's fusion). ``vector_only=True`` yields the pure-cosine variant (the
minimum competent vector baseline). Embeddings come from an injected
:class:`~app.providers.embeddings.base.EmbeddingProvider`; the default is the
deterministic :class:`~app.providers.embeddings.fake.FakeEmbeddingProvider`, so
tests are offline and byte-stable. The paid sweep injects the disk-cached OpenAI
provider via :func:`bench.runners.base.register_ours`.

``rank_bm25`` is Apache-2.0 and pulls numpy (BSD); the embedding provider seam is
the same one the product uses (recorded in DECISIONS.md / CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rank_bm25 import BM25Okapi

from app.fetch.detect import parse_content_type
from app.index.tokens import count_tokens
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.retrieve.rrf import rrf_fuse
from app.retrieve.select import cosine
from bench.corpus_store import CorpusStore
from bench.runners.base import ContextResult, truncate_to_budget
from bench.runners.naive_rag import _Chunk, _chunk_text, _tokenize
from bench.runners.naive_text import extract_text
from bench.tasks_schema import Task

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider

#: RRF smoothing constant for the hybrid fusion (SPEC §6, same k the product uses).
_RRF_K = 60

#: Fusion key: a chunk's stable corpus position ``(url, order)`` — deterministic
#: RRF tie-break, identical to the re-emission order.
_Key = tuple[str, int]


@dataclass(frozen=True)
class NaiveRagVectorRunner:
    """Vector/hybrid RAG baseline: naive_rag's extraction+chunking, vector retrieval.

    ``vector_only`` (default ``False``) selects the pure-cosine variant; the
    default is the stronger HYBRID that fuses BM25 and cosine with RRF. The ONLY
    difference from ``naive_rag`` is the retrieval method, so a comparison is a
    clean lexical-vs-vector ablation. ``embeddings`` defaults to ``None``, in
    which case the deterministic :class:`FakeEmbeddingProvider` is built per call
    (every test stays offline); the paid sweep injects the real disk-cached
    OpenAI provider.
    """

    name: str = "naive_rag_vector"
    vector_only: bool = False
    embeddings: EmbeddingProvider | None = field(default=None)

    def _embeddings(self) -> EmbeddingProvider:
        """Return the injected embeddings provider, or the deterministic fake."""
        if self.embeddings is not None:
            return self.embeddings
        return FakeEmbeddingProvider()

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Rank fixed chunks by cosine (and BM25 when hybrid); keep top in budget."""
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
            for order, piece in enumerate(_chunk_text(text)):
                chunks.append(_Chunk(url=url, order=order, text=piece))

        if not chunks:
            return ContextResult(text="", context_tokens=0, used_urls=[])

        keys: list[_Key] = [(chunk.url, chunk.order) for chunk in chunks]

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

        if self.vector_only:
            ranked = vector_ranked
        else:
            # Hybrid: also rank by BM25 (same tokenizer as naive_rag) and fuse
            # the two ranked lists with RRF (k=60). The fused order is already
            # deterministic (RRF ties break by key asc = (url, order)).
            tokenized = [_tokenize(chunk.text) for chunk in chunks]
            bm25 = BM25Okapi(tokenized)
            bm25_scores = bm25.get_scores(_tokenize(task.question))
            bm25_scored = [(float(bm25_scores[i]), key) for i, key in enumerate(keys)]
            bm25_scored.sort(key=lambda s: (-s[0], s[1]))
            bm25_ranked = [key for _, key in bm25_scored]
            fused = rrf_fuse([bm25_ranked, vector_ranked], k=_RRF_K)
            ranked = [key for key, _ in fused]

        chunk_by_key: dict[_Key, _Chunk] = dict(zip(keys, chunks, strict=True))

        kept: list[_Key] = []
        used_tokens = 0
        for key in ranked:
            chunk_tokens = count_tokens(chunk_by_key[key].text)
            if used_tokens + chunk_tokens > budget_tokens:
                continue
            kept.append(key)
            used_tokens += chunk_tokens
            if used_tokens >= budget_tokens:
                break

        # If no whole chunk fit the budget, keep the single top-ranked chunk and
        # let the final token truncation trim it (mirrors naive_rag's fallback).
        if not kept:
            kept = [ranked[0]]

        # Re-emit kept chunks in corpus order for a coherent, stable context.
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
