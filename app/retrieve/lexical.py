"""Lexical search by query-word overlap (SPEC §6 step 2, §5).

``lexical_search`` loads the chunks of the requested versions and ranks them
with ``overlap_ranks``: each chunk scores the number of its tokens that are
query words (lowercase ``[a-z0-9]+`` tokens), zero-score chunks are dropped,
and ties break on the ``(document_id, ord)`` key — the same key the RRF fusion
uses. Any query word counts, so a long natural-language question still matches
(``websearch_to_tsquery`` required every word; DECISIONS 2026-10-16).

``overlap_ranks`` is shared with the benchmark runner (``bench/runners/ours``),
so the product ranks exactly as the benchmark measures. Results are capped at
``limit`` (top-50) and returned as ``(chunk, rank_position)`` with 1-based
positions. A query with no tokens yields an empty result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.store.models import Chunk, DocumentVersion, Source

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: SPEC §6 step 2: top-50 by lexical rank.
_DEFAULT_LIMIT = 50

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: (document_id, ord), the fusion key.
Key = tuple[str, int]


def tokenize(text: str) -> list[str]:
    """Return lowercase alphanumeric tokens for ``text``."""
    return _TOKEN_RE.findall(text.lower())


def overlap_ranks(
    items: Iterable[tuple[Key, str]], query: str, limit: int = _DEFAULT_LIMIT
) -> list[Key]:
    """Rank ``(key, text)`` items by how many of their tokens are query words."""
    terms = set(tokenize(query))
    scored: list[tuple[int, Key]] = []
    for key, text in items:
        overlap = sum(1 for t in tokenize(text) if t in terms)
        if overlap > 0:
            scored.append((overlap, key))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [key for _, key in scored[:limit]]


def lexical_search(
    session: Session,
    *,
    query: str,
    version_ids: list[str],
    limit: int = _DEFAULT_LIMIT,
) -> list[tuple[Chunk, int]]:
    """Return the top-``limit`` chunks of ``version_ids`` by ``overlap_ranks``.

    Each result is ``(chunk, rank_position)`` with a 1-based position. An empty
    ``version_ids`` or a query without tokens yields ``[]``.
    """
    if not version_ids:
        return []

    # shortcut: scores every chunk of the requested versions in Python, no index;
    # fine for a few URLs per request, revisit if requests grow to many large docs.
    rows = session.execute(
        select(Chunk, Source.id)
        .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
        .join(Source, Source.id == DocumentVersion.source_id)
        .where(Chunk.version_id.in_(version_ids))
    ).all()
    chunk_by_key: dict[Key, Chunk] = {
        (document_id, chunk.ord): chunk for chunk, document_id in rows
    }
    ranked = overlap_ranks(
        ((key, chunk.text) for key, chunk in chunk_by_key.items()), query, limit
    )
    return [(chunk_by_key[key], pos) for pos, key in enumerate(ranked, start=1)]
