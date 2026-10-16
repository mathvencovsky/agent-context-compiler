"""Tests for the M10a query-cleaning experiment (bench/m10a_query.py)."""

from __future__ import annotations

from bench.m10a_query import clean_body, lead


def test_clean_body_drops_code_lines_and_filler_keeps_prose() -> None:
    body = "\n".join(
        [
            "I want the tooltip to stay inside the container.",
            "enter image description here",
            "<Tooltip title='aaa' placement='bottom'>",
            "    return x",
            "const x = document.querySelector('#root');",
            "Thanks in advance.",
        ]
    )
    assert clean_body(body) == ("I want the tooltip to stay inside the container.\n.")


def test_clean_body_leaves_a_short_plain_query_alone() -> None:
    assert clean_body("bearer token header") == "bearer token header"


def test_lead_keeps_the_first_words() -> None:
    assert lead("a b c d", words=2) == "a b"
