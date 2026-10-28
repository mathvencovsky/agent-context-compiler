"""Tests for the M10a section recall metric (bench/section_recall.py)."""

from __future__ import annotations

from app.index.tokens import count_tokens
from bench.section_recall import (
    HIT,
    coverage,
    first_hit,
    normalize,
    token_precision,
    units,
)

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


def test_token_precision_counts_only_section_sentences_once() -> None:
    section = "The timeout is set in seconds. Use the retry flag to try again."
    context = (
        "[URL] https://d.test/p\n"
        "The timeout is set in seconds.\n"
        "Unrelated text about other matters entirely.\n"
        "The timeout is set in seconds.\n"
    )
    tp = token_precision(section, context)
    one = count_tokens("The timeout is set in seconds.")
    assert tp.on_section == one  # the repeat is waste, not answer
    assert tp.context == count_tokens(context)
    assert tp.precision == one / tp.context
    assert tp.iou == one / (tp.context + count_tokens(section) - one)


def test_token_precision_is_zero_for_an_empty_context() -> None:
    tp = token_precision("Some section text here today.", "")
    assert (tp.on_section, tp.context, tp.precision, tp.iou) == (0, 0, 0.0, 0.0)


def test_token_precision_is_one_when_the_context_is_the_section() -> None:
    section = "Pass end equal to an empty string to print."
    assert token_precision(section, section).precision == 1.0
