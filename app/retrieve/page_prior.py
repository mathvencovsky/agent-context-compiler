"""Page prior: score whole pages against the start of the query (M10b, off).

Documentation search engines rank the page as well as the section: DocSearch
puts the page and top-section titles first, MDN matches title and URL slug, Read
the Docs weights page titles (research round 4, front H, L2). NINA ranks chunks
only. This module scores each page of a request — the tokens of its URL path
plus every distinct heading of its chunks (the page title is usually the first
heading) — with BM25 against the first ``page_prior_query_words`` words of the
query (the product has no separate question title), IDF over the request's
pages. Callers use the scores one of two ways (``LexicalConfig.page_prior``):

* ``"multiply"``: the lexical score of each chunk is multiplied by
  ``1 + alpha · page_norm`` (min-max normalized page score);
* ``"rrf"``: after the fusion, each fused candidate gains
  ``weight / (k + page_rank)``, the RRF term of a list where every chunk
  inherits its page's position (:func:`apply_page_rrf`).

Deterministic, no I/O. Pages with a zero score get no rank and no boost.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.retrieve.lexical import BM25_B, BM25_K1, bm25_idf, tokenize

if TYPE_CHECKING:
    from app.retrieve.lexical import Key, LexicalConfig, LexicalItem

#: URL path tokens that name a file type, not a topic.
_PATH_NOISE = frozenset({"html", "htm", "php", "aspx", "md", "index"})


def page_query(query: str, words: int) -> str:
    """The first ``words`` whitespace-separated words of ``query``."""
    return " ".join(query.split()[:words])


def url_slug_text(url: str) -> str:
    """The URL path as words (``/docs/3/library/os.path.html`` -> its parts)."""
    path = urlsplit(url).path
    return " ".join(t for t in tokenize(path) if t not in _PATH_NOISE)


def page_texts(
    items: Sequence[LexicalItem], url_by_document: Mapping[str, str]
) -> dict[str, str]:
    """Per document: URL path words plus each distinct heading, in page order."""
    headings: dict[str, dict[str, None]] = {}
    for item in sorted(items, key=lambda i: i.key):
        seen = headings.setdefault(item.key[0], {})
        for heading in item.heading_path:
            seen.setdefault(heading, None)
    return {
        doc: "\n".join([url_slug_text(url_by_document.get(doc, "")), *seen])
        for doc, seen in headings.items()
    }


def page_scores(
    items: Sequence[LexicalItem],
    query: str,
    config: LexicalConfig,
    url_by_document: Mapping[str, str],
) -> dict[str, float]:
    """BM25 score of each page of ``items`` against the query's first words."""
    tok = config.tokenizer()
    terms = sorted(set(tok(page_query(query, config.page_prior_query_words))))
    texts = page_texts(items, url_by_document)
    if not texts or not terms:
        return {doc: 0.0 for doc in texts}
    counts = {doc: Counter(tok(text)) for doc, text in sorted(texts.items())}
    n = len(counts)
    avgdl = sum(sum(c.values()) for c in counts.values()) / n or 1.0
    df = Counter(t for c in counts.values() for t in terms if c[t])
    scores: dict[str, float] = {}
    for doc, tf in counts.items():
        dl = sum(tf.values())
        norm = BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)
        scores[doc] = sum(
            bm25_idf(n, df[t]) * tf[t] * (BM25_K1 + 1) / (tf[t] + norm)
            for t in terms
            if tf[t]
        )
    return scores


def page_ranks(scores: Mapping[str, float]) -> dict[str, int]:
    """1-based rank of each page with a positive score (ties: document id)."""
    ordered = sorted(
        ((s, doc) for doc, s in scores.items() if s > 0), key=lambda x: (-x[0], x[1])
    )
    return {doc: pos for pos, (_, doc) in enumerate(ordered, start=1)}


def page_norm(scores: Mapping[str, float]) -> dict[str, float]:
    """Page scores min-max normalized to ``[0, 1]``; all equal -> all ``0``."""
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if math.isclose(hi, lo):
        return {doc: 0.0 for doc in scores}
    return {doc: (s - lo) / (hi - lo) for doc, s in scores.items()}


def apply_page_rrf(
    fused: list[tuple[Key, float]],
    page_rank: Mapping[str, int],
    *,
    weight: float,
    k: int,
) -> list[tuple[Key, float]]:
    """Add the page list's RRF term to each fused candidate and re-sort.

    Each candidate ``(document_id, ord)`` gains ``weight / (k + rank)`` of its
    page; pages without a rank add nothing. With an empty ``page_rank`` the
    input comes back unchanged. Ties break on the key, as in ``rrf_fuse``.
    """
    if not page_rank:
        return fused
    rescored = [
        (key, score + (weight / (k + page_rank[key[0]]) if key[0] in page_rank else 0))
        for key, score in fused
    ]
    return sorted(rescored, key=lambda item: (-item[1], item[0]))
