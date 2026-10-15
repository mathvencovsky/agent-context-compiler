"""Tests for the M10a section recall metric (bench/section_recall.py)."""

from __future__ import annotations

from bench.section_recall import HIT, coverage, first_hit, normalize, units

_SECTION = (
    "LIMIT Clause\n"
    "The LIMIT clause consists of two independent sub-clauses. "
    "LIMIT ALL is the same as omitting the LIMIT clause.\n"
    "LIMIT ALL is the same as omitting the LIMIT clause."
)


def test_normalize_deletes_rendering_marks() -> None:
    assert normalize("## The **LIMIT** `clause`  ¶\n") == "the limit clause"
    assert normalize(r"sep\_x | value") == "sep_x value"


def test_units_split_sentences_drop_short_lines_and_dedupe() -> None:
    assert units(_SECTION) == [
        "the limit clause consists of two independent sub-clauses.",
        "limit all is the same as omitting the limit clause.",
    ]


def test_coverage_matches_markdown_renderings() -> None:
    context = (
        "## LIMIT Clause\n\n"
        "The **LIMIT** clause consists of two independent\nsub-clauses.\n"
    )
    assert coverage(units(_SECTION), context) == HIT
    assert coverage([], context) == 0.0


def test_first_hit_counts_url_fragments() -> None:
    context = (
        "[URL] https://d/1\nnothing relevant here\n\n"
        "[URL] https://d/2\nThe LIMIT clause consists of two independent sub-clauses."
    )
    assert first_hit(units(_SECTION), context) == 2
    assert first_hit(units(_SECTION), "[URL] https://d/1\nnope") is None
