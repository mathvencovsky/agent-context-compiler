"""Prefer the named documentation release (bench/m10a_version.py)."""

from __future__ import annotations

import pytest

from app.retrieve.select import Candidate
from bench.m10a_version import BETA, adjust, match, named_release, releases_in

URLS = {
    "d311": "https://docs.python.org/3.11/library/re.html",
    "d312": "https://docs.python.org/3.12/library/re.html",
    "d3": "https://docs.python.org/3/library/re.html",
    "dcur": "https://www.postgresql.org/docs/current/sql.html",
    "dmdn": "https://developer.mozilla.org/en-US/docs/Web/API",
}


def _c(doc: str, value: float = 1.0) -> Candidate:
    return Candidate(chunk_id=1, document_id=doc, ord=0, value=value, tokens=5,
                     embedding=[1.0])  # fmt: skip


def test_named_release_reads_product_and_number() -> None:
    assert named_release("Why does re differ in Python 3.11?") == "3.11"
    assert named_release("Upgrading to React 18 broke effects") == "18"
    assert named_release("PostgreSQL 16 MERGE syntax") == "16"
    assert named_release("How do I sort a list?") is None
    assert named_release("my array has 3.11 values") is None  # no product named


def test_match_classes() -> None:
    assert match("3.11", "3.11") == "exact"
    assert match("2.3.x", "2.3") == "exact"
    assert match("3.12", "3.11") == "other"
    assert match("3", "3.11") == "neutral"  # one page for many minors
    assert match("current", "16") == "neutral"
    assert match(None, "16") == "neutral"
    assert match("v1.30", "1.30") == "exact"


def test_adjust_moves_values_only_when_a_release_is_named() -> None:
    cands = [_c("d311"), _c("d312"), _c("d3"), _c("dmdn")]
    moved = adjust(cands, URLS, "3.11")
    assert [c.value for c in moved] == pytest.approx([1 + BETA, 1 - BETA, 1.0, 1.0])
    assert adjust(cands, URLS, None) == cands


def test_releases_in_counts_numbered_labels_only() -> None:
    assert releases_in([_c("d311"), _c("d312"), _c("d3"), _c("dcur")], URLS) == 3
    assert releases_in([_c("dmdn"), _c("dcur")], URLS) == 0
