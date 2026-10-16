"""Tests for the M10a extraction experiment (bench/m10a_extract.py)."""

from __future__ import annotations

from typing import Any

from bench.m10a_extract import confirm_extraction


def _cell(hits: list[bool], tokens: int, ms: float) -> dict[str, list[Any]]:
    return {
        "hit": hits,
        "coverage": [1.0 if h else 0.0 for h in hits],
        "tokens": [tokens] * len(hits),
        "ms": [ms] * len(hits),
    }


def _ok(checks: list[tuple[str, bool, str]]) -> list[bool]:
    return [ok for _, ok, _ in checks]


def test_extraction_rule_checks_every_task_and_the_postgresql_mean() -> None:
    hits = [True, False, True, False]
    current = {8_000: _cell(hits, 5_000, 100)}
    strong = {8_000: _cell([False] * 4, 8_000, 0)}
    sites = ["postgresql", "postgresql", "python", "mdn"]
    better = {
        "current": [0.0, 1.0, 0.5, 0.5],
        "container_fallback": [1.0, 1.0, 0.5, 0.5],
    }
    assert all(_ok(confirm_extraction(current, current, strong, better, sites)))

    worse = {
        "current": [0.0, 1.0, 0.5, 0.5],
        "container_fallback": [1.0, 1.0, 0.4, 0.5],
    }
    assert _ok(confirm_extraction(current, current, strong, worse, sites))[0] is False
    flat = {"current": [0.5] * 4, "container_fallback": [0.5] * 4}
    assert _ok(confirm_extraction(current, current, strong, flat, sites))[1] is False
    dropped = {8_000: _cell([True, False, False, False], 5_000, 100)}
    assert _ok(confirm_extraction(dropped, current, strong, better, sites))[2] is False
