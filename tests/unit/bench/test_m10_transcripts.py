"""Reading finished M10 transcripts (bench/m10_transcripts.py)."""

from __future__ import annotations

from pathlib import Path

from bench.m10_transcripts import last_sessions, ngrams, parse_session, words
from tests.unit.bench.m10_transcript_helpers import transcript


def test_parse_session_pairs_tool_results_with_their_tool_names() -> None:
    lines = transcript(
        [("mcp__docs__fetch_page", "page one"), ("WebFetch", "page two")], "done"
    )
    s = parse_session("so-1", "full_page", lines)
    assert s.tool_results == [
        ("mcp__docs__fetch_page", "page one"),
        ("WebFetch", "page two"),
    ]
    assert s.final_text == "done"


def test_a_session_without_a_result_message_has_no_final_text() -> None:
    assert parse_session("t", "a", transcript([], None)).final_text == ""


def test_last_sessions_keeps_the_last_attempt(tmp_path: Path) -> None:
    (tmp_path / "so-1__nina__0.jsonl").write_text("\n".join(transcript([], "first")))
    (tmp_path / "so-1__nina__1.jsonl").write_text("\n".join(transcript([], "second")))
    (tmp_path / "so-2__nina__0.jsonl").write_text("\n".join(transcript([], "other")))
    got = {(s.task_id, s.final_text) for s in last_sessions(tmp_path)}
    assert got == {("so-1", "second"), ("so-2", "other")}


def test_words_and_ngrams() -> None:
    assert words("Use `end=''` in print().") == ["use", "end", "in", "print"]
    assert ngrams(["a", "b", "c"], 2) == {("a", "b"), ("b", "c")}
    assert ngrams(["a"], 2) == set()
