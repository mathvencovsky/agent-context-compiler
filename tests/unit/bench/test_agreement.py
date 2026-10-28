"""Tests for Cohen's kappa and the PREREG §9 gate (bench/agreement.py)."""

from __future__ import annotations

import math

import pytest

from bench.agreement import (
    cohen_kappa,
    corrected_delta,
    disagreement_by_length,
    judge_errors_by_arm,
    judge_is_reliable,
    weighted_agreement,
)

# Arm "x": the judge passed 90 answers and failed 10; 10 of each were labelled.
# Humans agree on 9 of the 10 passes and on 5 of the 10 fails.
_POP = {("x", True): 90, ("x", False): 10}
_ROWS = (
    [("x", True, True)] * 9
    + [("x", True, False)]
    + [("x", False, True)] * 5
    + [("x", False, False)] * 5
)


def test_weighted_agreement_reweights_strata_to_the_population() -> None:
    w = weighted_agreement(_ROWS, _POP)
    # unweighted it would be 14/20 = 0.70; the passes weigh 9x, the fails 1x
    assert w.observed == pytest.approx(0.86)
    # weighted table 81 / 9 / 5 / 5: chance 0.9*0.86 + 0.1*0.14 = 0.788
    assert w.kappa == pytest.approx((0.86 - 0.788) / (1 - 0.788))
    assert w.n == 20


def test_weighted_agreement_refuses_a_stratum_missing_from_the_population() -> None:
    with pytest.raises(ValueError, match="population"):
        weighted_agreement([("y", True, True)], _POP)


def test_judge_errors_by_arm_give_population_sensitivity_and_specificity() -> None:
    e = judge_errors_by_arm(_ROWS, _POP, n_boot=300)["x"]
    assert e.judge_success == pytest.approx(0.9)
    assert e.corrected_success == pytest.approx(0.86)
    assert e.bias == pytest.approx(0.04)
    assert e.sensitivity == pytest.approx(81 / 86)
    assert e.specificity == pytest.approx(5 / 14)
    assert e.sensitivity_lo <= e.sensitivity <= e.sensitivity_hi
    assert e.corrected_lo <= e.corrected_success <= e.corrected_hi
    assert e.n_labelled == 20


def test_an_unlabelled_stratum_leaves_the_arm_unestimated() -> None:
    e = judge_errors_by_arm([("x", True, True)], _POP, n_boot=50)["x"]
    assert math.isnan(e.corrected_success) and math.isnan(e.specificity)


def test_corrected_delta_exposes_differential_judge_error() -> None:
    # Same judge success (0.9) in both arms, but the judge is lenient only with
    # "a": the human-corrected Δ is negative and the judge's Δ hides it.
    pop = {
        ("a", True): 90,
        ("a", False): 10,
        ("b", True): 90,
        ("b", False): 10,
    }
    rows = (
        [("a", True, True)] * 6
        + [("a", True, False)] * 4
        + [("a", False, False)] * 10
        + [("b", True, True)] * 10
        + [("b", False, False)] * 10
    )
    d = corrected_delta(rows, pop, a="a", b="b", n_boot=500)
    assert d.judge_delta == pytest.approx(0.0)
    assert d.corrected == pytest.approx(0.54 - 0.90)
    assert d.bias == pytest.approx(0.36)
    assert d.lo <= d.corrected <= d.hi
    assert d.bias_lo > 0  # the differential error is detectable here


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
