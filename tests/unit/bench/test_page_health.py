"""Soft 404s, bot challenges, consent walls and empty pages (PREREG Amendment 16)."""

from __future__ import annotations

import json
from pathlib import Path

from bench.page_health import (
    SHORT_CHARS,
    blocked_sessions,
    classify,
    report_blocked,
    session_fetch_cases,
    unhealthy_tasks,
)

_BODY = "The function returns the absolute value of a number. " * 10


def test_a_documentation_page_is_healthy() -> None:
    assert classify("# abs(x)\n\n" + _BODY) is None


def test_empty_and_whitespace_pages() -> None:
    assert classify("") == "empty"
    assert classify("  \n Loading... \n") == "empty"


def test_bot_challenge() -> None:
    page = (
        "# Just a moment...\n\nChecking your browser before accessing the site. "
        + _BODY
    )
    assert classify(page) == "challenge"


def test_soft_404_needs_the_marker_near_the_top() -> None:
    assert classify("# Page not found\n\nSorry. " + _BODY) == "soft_404"
    # A short page that only mentions a 404 deep in the text is documentation.
    assert classify("# Errors\n\n" + _BODY + " The server answers 404.") is None


def test_consent_wall() -> None:
    page = (
        "# Before you continue\n\nWe use cookies to improve your experience. " + _BODY
    )
    assert classify(page) == "consent_wall"


def test_markers_do_not_count_on_long_pages() -> None:
    page = "# 404 Not Found\n\n" + "x" * SHORT_CHARS
    assert classify(page) is None


def test_unhealthy_tasks_reads_each_page_once() -> None:
    calls: list[str] = []
    pages = {"a": "# Page not found\n\n" + _BODY, "b": "# ok\n\n" + _BODY}

    def markdown_of(url: str) -> str:
        calls.append(url)
        return pages.get(url, "")

    bad = unhealthy_tasks(
        [("t1", ["a", "b"]), ("t2", ["b"]), ("t3", ["a", "c"])], markdown_of
    )
    assert bad == {"t1": {"a": "soft_404"}, "t3": {"a": "soft_404", "c": "empty"}}
    assert sorted(calls) == ["a", "b", "c"]


def _session(*results: tuple[str, str, bool]) -> list[str]:
    """Transcript lines: one tool call and its result per ``(tool, text, is_error)``."""
    lines = [json.dumps({"type": "SystemMessage", "subtype": "init", "data": {}})]
    for i, (tool, text, is_error) in enumerate(results):
        lines.append(
            json.dumps(
                {
                    "type": "AssistantMessage",
                    "content": [{"id": f"t{i}", "name": tool, "input": {}}],
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "UserMessage",
                    "content": [
                        {
                            "tool_use_id": f"t{i}",
                            "content": [{"type": "text", "text": text}],
                            "is_error": is_error,
                        }
                    ],
                }
            )
        )
    return lines


def test_session_fetch_cases_counts_fetch_tools_only() -> None:
    n, cases = session_fetch_cases(
        _session(
            ("WebFetch", "Just a moment... verify you are human. " + _BODY, False),
            ("WebFetch", "Request failed with status code 403", True),
            ("mcp__docs__fetch_page", "# abs\n\n" + _BODY, False),
            ("mcp__docs__assemble_context", "Page not found", False),
        )
    )
    assert n == 3
    assert dict(cases) == {"challenge": 1, "fetch_error": 1}


def test_a_short_live_summary_is_not_counted_as_empty() -> None:
    _, cases = session_fetch_cases(_session(("WebFetch", "abs(x) returns |x|.", False)))
    assert not cases


def test_blocked_sessions_uses_the_last_attempt(tmp_path: Path) -> None:
    blocked = _session(("WebFetch", "Attention Required! | Cloudflare " + _BODY, False))
    fine = _session(("WebFetch", "# abs\n\n" + _BODY, False))
    (tmp_path / "so-1__webfetch_live__0.jsonl").write_text("\n".join(blocked))
    (tmp_path / "so-1__webfetch_live__1.jsonl").write_text("\n".join(fine))
    (tmp_path / "so-2__webfetch_live__0.jsonl").write_text("\n".join(blocked))
    (tmp_path / "so-2__full_page__0.jsonl").write_text("\n".join(fine))
    arms = blocked_sessions(tmp_path)
    assert arms["webfetch_live"].results == 2
    assert arms["webfetch_live"].sessions == {"so-2__webfetch_live"}
    assert arms["full_page"].sessions == set()
    table = report_blocked(arms)
    assert "| webfetch_live | 2 | 1 | 0 | 0 | 1 |" in table
