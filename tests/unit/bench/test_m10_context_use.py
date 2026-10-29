"""How much of the paid tool context reaches the answer (front F, L9; L10)."""

from __future__ import annotations

import json

import pytest

from bench.m10_context_use import USE_N, by_arm, session_use
from bench.m10_transcripts import parse_session
from tests.unit.bench.m10_transcript_helpers import transcript

_USED = "pass end equal to an empty string to print to drop the newline"
_UNUSED = " ".join(f"filler{i}" for i in range(40))


def _final(answer: str, quote: str = "") -> str:
    citations = [{"url": "https://d.test/p", "quote": quote}] if quote else []
    return "Reasoning.\n" + json.dumps({"answer": answer, "citations": citations})


def test_used_share_counts_tool_words_that_reach_the_final_message() -> None:
    page = f"{_USED} {_UNUSED}"
    s = parse_session(
        "t", "full_page", transcript([("fetch_page", page)], _final(_USED, _USED))
    )
    u = session_use(s)
    assert u.tool_words == 13 + 40
    assert u.used_words == 13
    assert u.tool_calls == 1 and not u.json_missing
    assert u.quoted_tokens > 0 and u.quoted_tokens < u.tool_tokens


def test_a_quote_not_in_any_tool_result_is_not_counted() -> None:
    s = parse_session(
        "t",
        "nina",
        transcript([("assemble_context", _UNUSED)], _final("x", "made up quote")),
    )
    u = session_use(s)
    assert u.quoted_tokens == 0 and u.used_words == 0


def test_a_final_message_without_json_is_reported() -> None:
    s = parse_session("t", "nina", transcript([], "just prose, no json"))
    u = session_use(s)
    assert u.json_missing and u.tool_words == 0
    assert USE_N == 5


def test_by_arm_weights_by_tokens_and_splits_by_correctness() -> None:
    used = parse_session(
        "t1", "nina", transcript([("assemble_context", _USED)], _final(_USED))
    )
    unused = parse_session(
        "t2", "nina", transcript([("assemble_context", _UNUSED)], "no json")
    )
    summary = by_arm(
        [session_use(used), session_use(unused)],
        correct={("t1", "nina"): True, ("t2", "nina"): False},
    )["nina"]
    assert summary.sessions == 2
    assert summary.used_share == pytest.approx(13 / 53)
    assert summary.json_missing_rate == 0.5
    assert summary.tool_calls_per_correct == 2.0
    assert summary.used_share_correct == 1.0
    assert summary.used_share_incorrect == 0.0
