"""Lexical search over the generated tsvector column (SPEC §6 step 2, §5).

``lexical_search`` ranks a version-restricted set of chunks against the query
using Postgres full-text search. It builds the query with
``websearch_to_tsquery('simple', :query)`` — the ``'simple'`` configuration
matches the GENERATED ``tsv`` column (``to_tsvector('simple', text)``; SPEC §5)
so the index (GIN on ``tsv``) is usable — and ranks by ``ts_rank_cd`` descending.

Results are restricted to ``Chunk.version_id IN version_ids`` (the current
versions of the requested URLs; SPEC §6 step 2), capped at ``limit`` (top-50),
and returned as ``(chunk, rank_position)`` with 1-based positions. Ties on the
rank score are broken deterministically by ``(version_id, ord)`` so the output
is stable and the downstream RRF tie-break (keyed by ``(document_id, ord)``) is
meaningful. An empty query (no lexemes) yields an empty tsquery and therefore an
empty result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.store.models import Chunk

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: SPEC §6 step 2: top-50 by lexical rank.
_DEFAULT_LIMIT = 50


def lexical_search(
    session: Session,
    *,
    query: str,
    version_ids: list[str],
    limit: int = _DEFAULT_LIMIT,
) -> list[tuple[Chunk, int]]:
    """Return the top-``limit`` chunks for ``query`` ranked by ``ts_rank_cd``.

    Restricted to ``version_ids``. Each result is ``(chunk, rank_position)`` with
    a 1-based position; ties are broken by ``(version_id, ord)``. An empty
    ``version_ids`` or an empty tsquery yields ``[]``.
    """
    if not version_ids:
        return []

    tsquery = func.websearch_to_tsquery("simple", query)
    rank = func.ts_rank_cd(Chunk.tsv, tsquery)
    stmt = (
        select(Chunk)
        .where(
            Chunk.version_id.in_(version_ids),
            # Only rows whose tsvector actually matches the query contribute;
            # an empty tsquery matches nothing, so the result is empty.
            Chunk.tsv.op("@@")(tsquery),
        )
        .order_by(rank.desc(), Chunk.version_id.asc(), Chunk.ord.asc())
        .limit(limit)
    )
    chunks = list(session.scalars(stmt))
    return [(chunk, position) for position, chunk in enumerate(chunks, start=1)]
