"""H1/H2 by task and by cluster (bench/m10_analysis.py)."""

from __future__ import annotations

import pytest

from bench.m10_analysis import (
    MARGIN,
    bootstrap,
    cpt_ratio,
    delta,
    percentile,
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
