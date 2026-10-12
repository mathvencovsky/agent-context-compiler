"""Coverage heuristic — thresholds and observable are the same grandeza (M8).

``assemble._coverage`` classifies a task by its MAX absolute rerank score against
``coverage_low``/``coverage_high`` (frozen in M8 Stage 1 as the p33/p66 of the
per-task-max distribution on dev). These tests prove the heuristic is NOT
degenerate: with the frozen thresholds the three buckets
(insufficient/partial/sufficient) are all reachable, and over a realistic spread
of per-task-max absolute scores the derivation yields distinct, ordered
thresholds strictly inside the range (so coverage does not collapse to a single
bucket for every task). Pure and network-free.
"""

from __future__ import annotations

from app.config import Settings
from app.retrieve.assemble import _coverage
from bench.calibrate import coverage_from_scores


def test_frozen_thresholds_make_all_three_buckets_reachable() -> None:
    """With the frozen config thresholds, each bucket is reachable."""
    s = Settings()
    assert s.coverage_low < s.coverage_high  # non-degenerate band

    below = s.coverage_low / 2
    middle = (s.coverage_low + s.coverage_high) / 2
    above = min(1.0, s.coverage_high + (1.0 - s.coverage_high) / 2)

    assert _coverage(below, s) == "insufficient"
    assert _coverage(middle, s) == "partial"
    assert _coverage(above, s) == "sufficient"


def test_coverage_does_not_collapse_over_spread_distribution() -> None:
    """A realistic per-task-max distribution yields non-degenerate thresholds.

    Models the kind of absolute Jina maxima observed on dev (values spread
    across the unit interval, not pinned at 1.0). The derived thresholds must be
    strictly ordered and strictly inside the observed range, and classifying the
    distribution's own min/median/max must span more than one bucket — i.e.
    coverage does NOT collapse to "sufficient" for every task.
    """
    # A spread of per-task-max absolute scores (no degenerate all-1.0 pile-up).
    maxima = [0.03, 0.05, 0.08, 0.12, 0.18, 0.22, 0.26, 0.34, 0.5, 0.84]
    low, high = coverage_from_scores(maxima)
    assert low < high
    assert maxima[0] <= low <= high <= maxima[-1]

    s = Settings(coverage_low=low, coverage_high=high)
    buckets = {_coverage(m, s) for m in maxima}
    # More than one bucket is reached across the distribution (not all one label).
    assert len(buckets) >= 2
    assert "sufficient" in buckets  # the top maxima are sufficient
    assert buckets != {"sufficient"}  # ...but NOT every task is sufficient
