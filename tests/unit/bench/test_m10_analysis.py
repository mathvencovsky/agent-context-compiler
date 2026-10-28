"""H1/H2 by task and by cluster (bench/m10_analysis.py)."""

from __future__ import annotations

import pytest

from bench.m10_analysis import (
    FULL_PAGE_FLOOR,
    MARGIN,
    MARGIN_FRACTION,
    band_mde,
    bootstrap,
    cpt_ratio,
    delta,
    paired_exact,
    percentile,
    retention,
    validity,
    verdicts,
)
from bench.roi import TaskCost


def _t(correct: bool, cost: float = 0.1) -> TaskCost:
    return TaskCost(cost_usd=cost, tool_tokens=0, correct=correct)


def test_delta_and_cpt_ratio() -> None:
    full = [_t(True, 0.2), _t(False, 0.2)]
    nina = [_t(True, 0.05), _t(True, 0.05)]
    assert delta(full, nina) == 0.5
    assert cpt_ratio(full, nina) == pytest.approx(0.4 / 0.05)


def test_cluster_bootstrap_keeps_tasks_of_a_cluster_together() -> None:
    full = [_t(False)] * 3 + [_t(True)] * 3
    nina = [_t(True)] * 3 + [_t(True)] * 3
    clusters = ["a", "a", "a", "b", "b", "b"]
    values = bootstrap(full, nina, delta, clusters=clusters, n_boot=200)
    # Each resample takes all of a or all of b twice: Δ is 0, 0.5 or 1.
    assert set(round(v, 6) for v in values) <= {0.0, 0.5, 1.0}


def test_clustering_widens_the_interval_when_clusters_agree_inside() -> None:
    # 20 clusters of 5 identical tasks: effectively 20 observations, not 100.
    full, nina, clusters = [], [], []
    for c in range(20):
        ok = c % 2 == 0
        full += [_t(ok)] * 5
        nina += [_t(not ok if c % 4 == 0 else ok)] * 5
        clusters += [f"p{c}"] * 5
    by_task = bootstrap(full, nina, delta, n_boot=2000)
    by_cluster = bootstrap(full, nina, delta, clusters=clusters, n_boot=2000)
    width = lambda v: percentile(v, 0.975) - percentile(v, 0.025)  # noqa: E731
    assert width(by_cluster) > 1.5 * width(by_task)


def test_verdicts_report_whether_clustering_changes_a_verdict() -> None:
    full = [_t(True, 0.2)] * 30 + [_t(False, 0.2)] * 10
    nina = [_t(True, 0.05)] * 30 + [_t(False, 0.05)] * 10
    v = verdicts(full, nina, [f"p{i}" for i in range(40)], n_boot=300)
    assert v.delta == 0.0 and v.h1 and v.h2
    assert v.delta_lo_task > -MARGIN
    assert v.cluster_changes_a_verdict is False  # singleton clusters = tasks


def test_bootstrap_rejects_mismatched_inputs() -> None:
    with pytest.raises(ValueError):
        bootstrap([_t(True)], [], delta)
    with pytest.raises(ValueError):
        bootstrap([_t(True)], [_t(True)], delta, clusters=["a", "b"])


# --- Validity of H1 (assay sensitivity, F_avaliacao L1) and paired extras (L10)


def test_retention_is_nina_success_over_full_page_success() -> None:
    full = [_t(True)] * 4 + [_t(False)] * 6
    nina = [_t(True)] * 3 + [_t(False)] * 7
    assert retention(full, nina) == pytest.approx(0.75)
    assert retention([_t(False)], [_t(True)]) == float("inf")
    assert retention([_t(False)], [_t(False)]) == 1.0  # nothing to retain


def test_validity_passes_when_full_page_works_and_beats_no_docs() -> None:
    full = [_t(True)] * 60 + [_t(False)] * 40
    nina = [_t(True)] * 58 + [_t(False)] * 42
    v = validity(full, nina, n_boot=500)
    assert v.no_docs_rerun is False and v.no_docs_success == 0.0
    assert v.full_success == pytest.approx(0.6)
    assert v.full_lo > FULL_PAGE_FLOOR and v.floor_met
    assert v.doc_effect_lo >= MARGIN / MARGIN_FRACTION and v.margin_preserved
    assert v.interpretable
    assert v.retention == pytest.approx(58 / 60)
    assert v.retention_lo <= v.retention <= v.retention_hi
    # a margin of 7.5 points out of an effect of at least doc_effect_lo
    assert v.preserved_fraction == pytest.approx(1 - MARGIN / v.doc_effect_lo)


def test_validity_fails_when_both_arms_fail_together() -> None:
    # Both arms near the bottom of the scale: Δ ≈ 0 and H1 passes, meaninglessly.
    full = [_t(True)] * 10 + [_t(False)] * 90
    nina = [_t(True)] * 10 + [_t(False)] * 90
    v = verdicts(full, nina, [f"p{i}" for i in range(100)], n_boot=500)
    assert v.h1
    assert not v.validity.floor_met
    assert not v.h1_interpretable and not v.headline


def test_a_no_docs_rerun_close_to_full_page_breaks_assay_sensitivity() -> None:
    full = [_t(True)] * 50 + [_t(False)] * 50
    nina = [_t(True)] * 50 + [_t(False)] * 50
    no_docs = [True] * 45 + [False] * 55  # regression to the mean after the screen
    v = validity(full, nina, no_docs=no_docs, n_boot=500)
    assert v.no_docs_rerun and v.no_docs_success == pytest.approx(0.45)
    assert v.floor_met
    assert v.doc_effect == pytest.approx(0.05)
    assert not v.margin_preserved and not v.interpretable


def test_validity_needs_paired_no_docs() -> None:
    with pytest.raises(ValueError):
        validity([_t(True)], [_t(True)], no_docs=[True, False], n_boot=10)


def test_exact_mcnemar_and_newcombe_interval() -> None:
    # 12 both right, full_page alone 9, nina alone 2, 21 both wrong
    full = [_t(True)] * 21 + [_t(False)] * 23
    nina = [_t(True)] * 12 + [_t(False)] * 9 + [_t(True)] * 2 + [_t(False)] * 21
    p = paired_exact(full, nina)
    assert (p.only_nina, p.only_full) == (2, 9)
    # two-sided exact binomial on 11 discordant pairs, k = 2
    assert p.mcnemar_p == pytest.approx(2 * (1 + 11 + 55) / 2**11)
    d = delta(full, nina)
    assert p.newcombe_lo < d < p.newcombe_hi
    assert -1 <= p.newcombe_lo and p.newcombe_hi <= 1
    # swapping the arms mirrors the interval
    q = paired_exact(nina, full)
    assert q.newcombe_lo == pytest.approx(-p.newcombe_hi)
    assert q.newcombe_hi == pytest.approx(-p.newcombe_lo)


def test_newcombe_collapses_without_discordant_tasks() -> None:
    # Method 10's known limit (perfect correlation): the docstring says so.
    full = [_t(True)] * 5 + [_t(False)] * 5
    p = paired_exact(full, full)
    assert p.mcnemar_p == 1.0
    assert (p.newcombe_lo, p.newcombe_hi) == (0.0, 0.0)
    # one discordant task is enough to open it
    q = paired_exact(full, [_t(False)] + full[1:])
    assert q.newcombe_lo < -0.1 < q.newcombe_hi


def test_verdicts_carry_validity_and_paired_extras() -> None:
    full = [_t(True, 0.2)] * 30 + [_t(False, 0.2)] * 10
    nina = [_t(True, 0.05)] * 30 + [_t(False, 0.05)] * 10
    v = verdicts(full, nina, [f"p{i}" for i in range(40)], n_boot=300)
    assert v.validity.floor_met and v.h1_interpretable and v.headline
    assert v.paired.mcnemar_p == 1.0
    assert v.newcombe_changes_h1 is False


def test_band_mde_uses_the_power_formula() -> None:
    from bench.power import achieved_delta

    mde = band_mde({"short": 200, "long": 40})
    assert mde["long"] == pytest.approx(achieved_delta(0.25, 40))
    assert mde["long"] > mde["short"]
