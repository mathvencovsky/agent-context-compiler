"""Tests for the per-site retention check (bench/extract_retention.py)."""

from __future__ import annotations

from bench.extract_retention import regressions, retention, summarize


def test_retention_ignores_chrome_and_scripts() -> None:
    raw = (
        b"<html><body><nav>Home Docs Blog</nav><script>var x = 1;</script>"
        b"<main><p>abcd efgh</p></main><footer>(c) 2026</footer></body></html>"
    )
    assert retention(raw, "abcd efgh") == 1.0
    assert retention(raw, "abcd") == 4 / 9


def test_regressions_flag_a_median_drop_and_more_pages_under_half() -> None:
    base = summarize({"a.test": [0.9, 0.95, 1.0], "b.test": [0.9, 0.4]})
    same = summarize({"a.test": [0.9, 0.94, 1.0], "b.test": [0.9, 0.45]})
    assert regressions(same, base) == []
    worse = summarize({"a.test": [0.5, 0.6, 1.0], "b.test": [0.3, 0.4]})
    assert regressions(worse, base) == [
        "a.test: median 0.95 -> 0.6",
        "b.test: median 0.65 -> 0.35",
        "b.test: pages under half 1 -> 2",
    ]
    assert regressions({}, base) == [
        "a.test: missing from the corpus",
        "b.test: missing from the corpus",
    ]
