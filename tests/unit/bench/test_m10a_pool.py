"""Tests for the M10a pool experiment (bench/m10a_pool.py)."""

from __future__ import annotations

from app.index.chunker import Chunk
from bench.m10a_pool import bm25_ranks, pool_hits
from bench.runners.ours import _IndexedChunk, _lexical_ranks


def _item(ord_: int, text: str) -> _IndexedChunk:
    chunk = Chunk(
        version_id="v", block_ids=[], heading_path=[], text=text, md=text,
        tokens=len(text.split()), ord=ord_,
    )  # fmt: skip
    return _IndexedChunk(key=("d", ord_), document_id="d", ord=ord_, chunk=chunk,
                         embedding=[1.0])  # fmt: skip


_FILLER = " ".join(["the value is the thing that is"] * 20)
_CHUNKS = [
    _item(0, _FILLER),  # long, only common words
    _item(1, "functools cache decorator memoizes the function"),
    _item(2, "nothing in common here"),
    # common words are common: in most chunks
    *(_item(n, f"the value is set here {n}") for n in range(3, 9)),
]


def test_bm25_prefers_the_rare_word_over_a_long_chunk_of_common_ones() -> None:
    query = "how is the functools cache the value"
    assert _lexical_ranks(_CHUNKS, query)[0] == ("d", 0)  # overlap rewards length
    ranks = bm25_ranks(_CHUNKS, query)
    assert ranks[0] == ("d", 1) and ("d", 2) not in ranks  # no match: dropped
    assert bm25_ranks(_CHUNKS, "") == [] and bm25_ranks([], "x") == []


def test_pool_hits_separates_one_chunk_from_the_pool_together() -> None:
    md = {("d", 0): "Alpha beta gamma delta.", ("d", 1): "Epsilon zeta eta theta."}
    section = [
        "alpha beta gamma delta.",
        "epsilon zeta eta theta.",
        "iota kappa lambda mu.",
    ]
    assert pool_hits([("d", 0), ("d", 1)], md, section) == (False, True)
    assert pool_hits([("d", 0)], md, section[:1]) == (True, True)
