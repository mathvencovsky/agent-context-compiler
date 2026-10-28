"""Expand-to-section ceiling helpers (bench/m10a_section_expand.py)."""

from __future__ import annotations

from app.index.chunker import Chunk
from app.retrieve.select import Candidate
from bench.m10a_section_expand import (
    best_candidate,
    expand,
    rescue_share,
    section_chunks,
    summarize,
)


def _chunk(ord_: int, path: list[str], md: str) -> Chunk:
    return Chunk(version_id="v", block_ids=["b"], heading_path=path, text=md,
                 md=md, tokens=3, ord=ord_)  # fmt: skip


def _cand(ord_: int, value: float) -> Candidate:
    return Candidate(chunk_id=ord_, document_id="d", ord=ord_, value=value,
                     tokens=3, embedding=[1.0])  # fmt: skip


CHUNKS = [
    _chunk(0, ["Intro"], "intro text"),
    _chunk(1, ["API", "open"], "open part one"),
    _chunk(2, ["API", "open"], "open part two"),
    _chunk(3, ["API", "open", "Flags"], "flag details"),
    _chunk(4, ["API", "close"], "close text"),
]


def test_best_candidate_is_the_highest_value() -> None:
    assert best_candidate([_cand(2, 0.3), _cand(1, 0.9)]) == _cand(1, 0.9)
    assert best_candidate([]) is None


def test_section_and_subtree_chunks() -> None:
    path = ["API", "open"]
    assert [c.ord for c in section_chunks(CHUNKS, path, "section")] == [1, 2]
    assert [c.ord for c in section_chunks(CHUNKS, path, "subtree")] == [1, 2, 3]


def test_expand_appends_only_missing_chunks_in_page_order() -> None:
    section = section_chunks(CHUNKS, ["API", "open"], "subtree")
    out = expand("[URL] u\nopen part one", "u", section, already={1})
    assert out == (
        "[URL] u\nopen part one\n\n[URL] u\nopen part two\n\n[URL] u\nflag details"
    )
    assert expand("ctx", "u", section, already={1, 2, 3}) == "ctx"


def test_rescue_share_and_summary() -> None:
    base = [True, False, False, False]
    assert rescue_share(base, [True, True, False, True]) == 2 / 3
    cells = {
        "nina": {4000: {"hit": base, "tokens": [100, 100, 100, 100]}},
        "section": {
            4000: {"hit": [True, True, False, True], "tokens": [150, 150, 150, 150]}
        },  # fmt: skip
    }
    table = "\n".join(summarize(cells))
    assert "| section | 4000 | 0.750 | 150 | 2/3 (0.667) |" in table
