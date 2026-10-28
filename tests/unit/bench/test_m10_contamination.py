"""Zero-cost contamination checks on finished M10 sessions (front F, L7)."""

from __future__ import annotations

import json

import pytest

from bench.m10_contamination import (
    ANSWER_N,
    LEAK_N,
    by_arm,
    check_session,
    delta_by_quarter,
    quarter,
    relative_to_cutoff,
)
from bench.m10_transcripts import parse_session
from tests.unit.bench.m10_transcript_helpers import transcript

REFERENCE = (
    "You can pass the end keyword argument to print so that it does not add "
    "a newline at the end of the output when you call it in a loop"
)


def _final(answer: str) -> str:
    return "Some reasoning.\n" + json.dumps({"answer": answer, "citations": []})


def test_a_no_docs_answer_that_copies_the_accepted_answer_is_flagged() -> None:
    s = parse_session("so-1", "no_docs", transcript([], _final(REFERENCE)))
    c = check_session(s, REFERENCE)
    assert c.answer_ref_overlap == 1.0
    assert c.answer_ref_only == 1.0  # nothing came from a tool
    assert not c.leaked


def test_answer_ngrams_seen_in_a_tool_result_are_not_memory() -> None:
    page = "Docs: " + REFERENCE
    s = parse_session(
        "so-1", "full_page", transcript([("fetch_page", page)], _final(REFERENCE))
    )
    c = check_session(s, REFERENCE)
    assert c.answer_ref_overlap == 1.0
    assert c.answer_ref_only == 0.0
    # the page holds the whole accepted answer: a leak by the 8-gram rule
    assert c.max_tool_ref_share == 1.0 and c.leaked


def test_a_stack_overflow_link_in_a_tool_result_is_a_leak() -> None:
    result = "See https://stackoverflow.com/questions/123/print-newline for more."
    s = parse_session("t", "webfetch_live", transcript([("WebFetch", result)], "x"))
    c = check_session(s, REFERENCE)
    assert c.so_url and c.leaked


def test_a_short_unrelated_answer_has_no_overlap() -> None:
    s = parse_session("t", "nina", transcript([("assemble_context", "abs")], "abs"))
    c = check_session(s, REFERENCE)
    assert c.answer_ref_overlap == 0.0 and not c.leaked
    assert ANSWER_N < LEAK_N


def test_by_arm_summarises_and_lists_leaked_sessions() -> None:
    a = check_session(
        parse_session("t1", "no_docs", transcript([], _final(REFERENCE))), REFERENCE
    )
    b = check_session(
        parse_session("t2", "no_docs", transcript([], _final("abs"))), REFERENCE
    )
    leak = parse_session(
        "t1", "webfetch_live", transcript([("WebFetch", "stackoverflow.com/a/9")], "x")
    )
    c = check_session(leak, REFERENCE)
    summary = by_arm([a, b, c])
    assert summary["no_docs"].sessions == 2
    assert summary["no_docs"].mean_ref_overlap == pytest.approx(0.5)
    assert summary["webfetch_live"].leaked == ["t1__webfetch_live"]


def test_quarter_and_cutoff() -> None:
    assert quarter("2023-02-08") == "2023Q1"
    assert quarter("2024-12-31") == "2024Q4"
    assert relative_to_cutoff("2023-02-08", None) == "unknown"
    assert relative_to_cutoff("2023-02-08", "2025-01-01") == "before"
    assert relative_to_cutoff("2025-03-01", "2025-01-01") == "after"


def test_delta_by_quarter_pairs_tasks_present_in_both_arms() -> None:
    rows = [
        {"task_id": "a", "strategy": "nina", "correct": "1"},
        {"task_id": "a", "strategy": "full_page", "correct": "0"},
        {"task_id": "b", "strategy": "nina", "correct": "1"},
        {"task_id": "b", "strategy": "full_page", "correct": "1"},
        {"task_id": "c", "strategy": "nina", "correct": "1"},  # no pair
        {"task_id": "d", "strategy": "nina", "correct": ""},  # excluded
        {"task_id": "d", "strategy": "full_page", "correct": "1"},
    ]
    created = {
        "a": "2023-01-05",
        "b": "2023-02-01",
        "c": "2023-04-01",
        "d": "2023-05-01",
    }
    assert delta_by_quarter(rows, created) == {"2023Q1": (2, 0.5)}
