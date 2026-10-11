"""``naive_rag`` baseline: trafilatura text -> fixed chunks -> BM25 (SPEC §10.3).

The classic retrieval baseline. For each task URL the snapshot HTML is extracted
to plain text (same as ``naive_text``), split into fixed 400-token chunks, and
all chunks across the task's URLs are ranked against the task question with
Okapi BM25 (``rank_bm25``). The top chunks are accumulated in rank order until
the token budget is reached; the kept chunks are then re-emitted in corpus order
(URL, then position) so the context reads coherently and is deterministic.

``rank_bm25`` is Apache-2.0 and pulls numpy (BSD) — recorded in DECISIONS.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.fetch.detect import parse_content_type
from app.index.tokens import count_tokens
from bench.corpus_store import CorpusStore
from bench.runners.base import ContextResult, truncate_to_budget
from bench.runners.naive_text import extract_text
from bench.tasks_schema import Task

#: Fixed chunk size, in cl100k_base tokens (SPEC §10.3 naive RAG).
_CHUNK_TOKENS = 400

#: Simple word tokenizer for BM25 scoring (lowercased alphanumeric runs).
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class _Chunk:
    """One fixed-size text chunk and its stable position in the corpus."""

    url: str
    order: int
    text: str


def _tokenize(text: str) -> list[str]:
    """Return BM25 word tokens for ``text`` (deterministic, lowercase)."""
    return _WORD_RE.findall(text.lower())


def _chunk_text(text: str) -> list[str]:
    """Split ``text`` into consecutive chunks of :data:`_CHUNK_TOKENS` tokens."""
    from app.index.tokens import _encoding

    encoding = _encoding()
    token_ids = encoding.encode(text)
    if not token_ids:
        return []
    chunks: list[str] = []
    for start in range(0, len(token_ids), _CHUNK_TOKENS):
        piece = token_ids[start : start + _CHUNK_TOKENS]
        chunks.append(encoding.decode(piece))
    return chunks


class NaiveRagRunner:
    """Chunk extracted text and keep the top BM25 chunks within the budget."""

    name = "naive_rag"

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Rank fixed chunks by BM25 against the question; keep top within budget."""
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

        tokenized = [_tokenize(chunk.text) for chunk in chunks]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(_tokenize(task.question))

        # Rank by score desc; break ties by corpus order (url, position) so the
        # ranking is deterministic even when BM25 returns equal scores.
        ranked = sorted(
            range(len(chunks)),
            key=lambda i: (-float(scores[i]), chunks[i].url, chunks[i].order),
        )

        kept: list[int] = []
        used_tokens = 0
        for index in ranked:
            chunk_tokens = count_tokens(chunks[index].text)
            if used_tokens + chunk_tokens > budget_tokens:
                continue
            kept.append(index)
            used_tokens += chunk_tokens
            if used_tokens >= budget_tokens:
                break

        # If no whole chunk fit the budget, keep the single top-ranked chunk and
        # let the final token truncation trim it, so a small budget still yields
        # the most relevant text rather than an empty context.
        if not kept:
            kept = [ranked[0]]

        # Re-emit kept chunks in corpus order for a coherent, stable context.
        kept.sort(key=lambda i: (chunks[i].url, chunks[i].order))
        used_urls: list[str] = []
        parts: list[str] = []
        for index in kept:
            chunk = chunks[index]
            if chunk.url not in used_urls:
                used_urls.append(chunk.url)
            parts.append(f"[URL] {chunk.url}\n{chunk.text}")

        joined = "\n\n".join(parts)
        truncated, tokens = truncate_to_budget(joined, budget_tokens)
        return ContextResult(text=truncated, context_tokens=tokens, used_urls=used_urls)
