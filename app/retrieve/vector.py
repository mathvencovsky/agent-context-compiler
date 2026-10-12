"""Vector search over the pgvector embedding column (SPEC §6 step 2, §5).

``vector_search`` ranks a version-restricted set of chunks by cosine distance
between each chunk's stored embedding and the query embedding, using pgvector's
``cosine_distance`` operator (``<=>``), which is backed by the HNSW
``vector_cosine_ops`` index (SPEC §5). The query embedding is produced upstream
by the injected ``EmbeddingProvider`` (the deterministic fake in tests).

Results are restricted to ``Chunk.version_id IN version_ids`` (SPEC §6 step 2),
ordered by cosine distance ascending (nearest first), capped at ``limit``
(top-50), and returned as ``(chunk, rank_position)`` with 1-based positions.
Ties on the distance are broken deterministically by ``(version_id, ord)`` so
the output is stable and the downstream RRF tie-break stays meaningful.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.store.models import Chunk

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: SPEC §6 step 2: top-50 by vector similarity.
_DEFAULT_LIMIT = 50


def vector_search(
    session: Session,
    *,
    query_embedding: list[float],
    version_ids: list[str],
    limit: int = _DEFAULT_LIMIT,
) -> list[tuple[Chunk, int]]:
    """Return the top-``limit`` chunks nearest to ``query_embedding`` by cosine.

    Restricted to ``version_ids``. Each result is ``(chunk, rank_position)`` with
    a 1-based position; ties on the cosine distance are broken by
    ``(version_id, ord)``. An empty ``version_ids`` yields ``[]``.
    """
    if not version_ids:
        return []

    distance = Chunk.embedding.cosine_distance(query_embedding)
    stmt = (
        select(Chunk)
        .where(Chunk.version_id.in_(version_ids))
        .order_by(distance.asc(), Chunk.version_id.asc(), Chunk.ord.asc())
        .limit(limit)
    )
    chunks = list(session.scalars(stmt))
    return [(chunk, position) for position, chunk in enumerate(chunks, start=1)]
