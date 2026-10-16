"""Tests for the M10a neighbor-expansion experiment (bench/m10a_expand.py)."""

from __future__ import annotations

from app.index.chunker import Chunk
from app.retrieve.select import Candidate, select
from bench.m10a_expand import neighbors_by_ord, select_expand
from bench.runners.ours import _IndexedChunk


def _item(ord_: int, heading: str, tokens: int = 100) -> _IndexedChunk:
    chunk = Chunk(version_id="v", block_ids=["b"], heading_path=[heading],
                  text="t", md=f"md{ord_}", tokens=tokens, ord=ord_)  # fmt: skip
    return _IndexedChunk(key=("d", ord_), document_id="d", ord=ord_, chunk=chunk,
                         embedding=[1.0])  # fmt: skip


# ords 0-1 under heading A, 2-4 under B
_INDEX = [_item(0, "A"), _item(1, "A"), _item(2, "B"), _item(3, "B"), _item(4, "B")]


def _cand(ord_: int, value: float) -> Candidate:
    return Candidate(chunk_id=0, document_id="d", ord=ord_, value=value, tokens=100,
                     embedding=[1.0])  # fmt: skip


def _ords(got: list[Candidate]) -> list[int]:
    return [c.ord for c in got]


def test_neighbors_come_nearest_first_and_stop_at_a_heading_change() -> None:
    pick = _cand(2, 1.0)
    assert _ords(neighbors_by_ord(_INDEX, 2, True)(pick)) == [3, 4]
    assert _ords(neighbors_by_ord(_INDEX, 1, False)(pick)) == [3, 1]
    assert _ords(neighbors_by_ord(_INDEX, 0, True)(pick)) == []


def test_window_zero_is_the_app_selector() -> None:
    pool = [_cand(2, 1.0), _cand(0, 0.5)]
    no_expansion = neighbors_by_ord(_INDEX, 0, True)
    for budget in (100, 250, 1_000):
        assert select_expand(pool, budget_tokens=budget, lambda_=0.0, min_value=0.0,
                             neighbors=no_expansion) == select(
            pool, budget_tokens=budget, lambda_=0.0, min_value=0.0)  # fmt: skip


def test_each_pick_pulls_neighbors_that_fit_without_repeats() -> None:
    pool = [_cand(2, 1.0), _cand(3, 0.9), _cand(0, 0.5)]
    expand = neighbors_by_ord(_INDEX, 1, True)
    got = select_expand(pool, budget_tokens=300, lambda_=0.0, min_value=0.0,
                        neighbors=expand)  # fmt: skip
    assert _ords(got) == [0, 2, 3]  # 2 pulls 3 (same heading); 1 is under A
    got = select_expand(pool, budget_tokens=1_000, lambda_=0.0, min_value=0.0,
                        neighbors=expand)  # fmt: skip
    assert _ords(got) == [0, 1, 2, 3]  # neighbors do not chain: 4 stays out


def test_only_the_first_picks_expand_when_asked() -> None:
    pool = [_cand(3, 1.0), _cand(0, 0.5)]
    expand = neighbors_by_ord(_INDEX, 1, False)
    for picks, want in ((1, [0, 2, 3, 4]), (None, [0, 1, 2, 3, 4])):
        got = select_expand(pool, budget_tokens=1_000, lambda_=0.0, min_value=0.0,
                            neighbors=expand, expand_picks=picks)  # fmt: skip
        assert _ords(got) == want  # with top-1, pick 0 does not pull 1
