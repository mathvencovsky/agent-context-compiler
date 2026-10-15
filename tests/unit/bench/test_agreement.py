"""Tests for Cohen's kappa and the PREREG §9 gate (bench/agreement.py)."""

from __future__ import annotations

import math

import pytest

from bench.agreement import cohen_kappa, judge_is_reliable


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
