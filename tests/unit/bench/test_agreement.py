"""Tests for Cohen's kappa and the PREREG §9 gate (bench/agreement.py)."""

from __future__ import annotations

import math

import pytest

from bench.agreement import cohen_kappa, disagreement_by_length, judge_is_reliable


def test_cohen_kappa_known_values() -> None:
    # observed 0.75, chance 0.5 -> kappa 0.5
    assert cohen_kappa([True, True, False, False], [True, False, False, False]) == 0.5
    assert cohen_kappa([True, False, True], [True, False, True]) == 1.0
    assert cohen_kappa([True, False], [False, True]) == -1.0


def test_cohen_kappa_undefined_and_invalid_input() -> None:
    assert math.isnan(cohen_kappa([True, True], [True, True]))
    with pytest.raises(ValueError):
        cohen_kappa([True], [True, False])


def test_gate_needs_kappa_of_at_least_0_6() -> None:
    assert judge_is_reliable(0.6)
    assert not judge_is_reliable(0.59)
    assert not judge_is_reliable(math.nan)


def test_disagreement_by_length_exposes_a_lenient_judge_on_long_answers() -> None:
    rows = [
        ("a", True, True),
        ("bb", False, False),
        ("ccc" * 10, True, False),  # long and wrongly passed
        ("ddd" * 10, True, False),
        ("e" * 5, False, True),  # mid-length and wrongly failed
        ("f" * 6, True, True),
    ]
    bins = disagreement_by_length(rows)
    assert [b["n"] for b in bins] == [2.0, 2.0, 2.0]
    assert [b["judge_lenient"] for b in bins] == [0.0, 0.0, 1.0]
    assert [b["judge_strict"] for b in bins] == [0.0, 0.5, 0.0]
    assert bins[-1]["max_chars"] == 30.0


def test_disagreement_by_length_skips_empty_bins() -> None:
    assert len(disagreement_by_length([("x", True, True)], bins=3)) == 1
