"""Tests for the A2 code-aware selection experiment (bench/m10a_code.py)."""

from __future__ import annotations

from typing import Any

from app.index.chunker import Chunk
from app.retrieve.select import Candidate
from bench.m10a_code import accept, boosted, is_code_query, whole_code_keys
from bench.runners.ours import _IndexedChunk


def test_code_query_detector() -> None:
    assert is_code_query("Why does os.path.join() return a TypeError here?")
    assert is_code_query("useState setter callback with async function")
    assert not is_code_query("What year was the standard published?")


def _item(ord_: int, md: str, blocks: list[str]) -> _IndexedChunk:
    chunk = Chunk(version_id="v", block_ids=blocks, heading_path=[], text=md, md=md,
                  tokens=10, ord=ord_)  # fmt: skip
    return _IndexedChunk(key=("d", ord_), document_id="d", ord=ord_, chunk=chunk,
                         embedding=[1.0])  # fmt: skip


def test_whole_code_needs_a_full_fence_from_an_unsplit_block() -> None:
    indexed = [
        _item(0, "Intro\n\n```python\nx = 1\n```", ["b1", "b2"]),
        _item(1, "```\npart one\n```", ["b3"]),  # b3 split across two chunks
        _item(2, "```\npart two\n```", ["b3"]),
        _item(3, "No code here.", ["b4"]),
    ]
    assert whole_code_keys(indexed) == {("d", 0)}


def test_boost_touches_only_the_given_keys() -> None:
    cands = [Candidate(0, "d", n, 0.5, 10, [1.0]) for n in range(2)]
    out = boosted(cands, {("d", 1)}, 0.2)
    assert [c.value for c in out] == [0.5, 0.6]


def _cell(covs: list[float]) -> dict[str, list[Any]]:
    return {"hit": [c >= 0.5 for c in covs], "coverage": covs,
            "tokens": [100] * len(covs)}  # fmt: skip


def test_accept_needs_a_code_gain_without_hurting_the_rest() -> None:
    mask = [True, True, False, False]
    budgets = (1000, 4000)
    cur = {b: _cell([0.4, 0.4, 0.8, 0.8]) for b in budgets}
    strong = {b: _cell([0.0] * 4) for b in budgets}
    better = {b: _cell([0.5, 0.4, 0.8, 0.79]) for b in budgets}
    assert all(ok for _, ok, _ in accept(better, cur, strong, mask))
    flat = {b: _cell([0.4, 0.4, 0.8, 0.8]) for b in budgets}
    assert not all(ok for _, ok, _ in accept(flat, cur, strong, mask))
    hurts = {b: _cell([0.6, 0.6, 0.5, 0.5]) for b in budgets}
    assert not all(ok for _, ok, _ in accept(hurts, cur, strong, mask))
