"""Sample size for the M10 paired non-inferiority test (bench/PREREG.md §5).

The M10 primary hypothesis is that NINA's success rate is not worse than the
primary comparator's by more than a margin ``delta``, measured on the SAME tasks
(paired). With paired binary outcomes the per-task difference is -1, 0 or +1, so
when the true difference is zero its variance is the discordance rate ``psi``
(the share of tasks where exactly one of the two arms is correct), and the
number of tasks needed is

    n = ceil((z_{1-alpha} + z_{power})**2 * psi / delta**2)

with one-sided alpha 0.025 and power 0.80 by default. ``psi`` is read from any
``results.csv`` (one row per task x strategy, SPEC §10.5), so anyone can
recompute the PREREG numbers, and the pilot fixes ``n`` by the pre-registered
rule ``psi_plan = max(psi_pilot, PSI_FLOOR)``.

Usage:
    uv run python -m bench.power --results bench/results/<run>/results.csv \
        --a ours --b strong_rag --budget 4000
    uv run python -m bench.power --psi 0.25
"""

from __future__ import annotations

import argparse
import csv
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist

#: Lowest discordance the plan may assume (PREREG §5): the single-call M8 runs
#: measured 0.12-0.24 between ``ours`` and the RAG baselines, and agent loops are
#: expected to be noisier, so a lower pilot estimate is not trusted.
PSI_FLOOR = 0.25

#: Margins tabulated by default (PREREG uses 0.075).
_DEFAULT_MARGINS = (0.05, 0.075, 0.10)


@dataclass(frozen=True)
class Discordance:
    """Paired outcome counts for arms ``a`` and ``b`` over the same tasks."""

    n: int
    a_only: int
    b_only: int

    @property
    def psi(self) -> float:
        """Share of tasks where exactly one arm was correct."""
        return (self.a_only + self.b_only) / self.n if self.n else 0.0


def _z_sum_squared(alpha: float, power: float) -> float:
    z = NormalDist().inv_cdf
    return (z(1 - alpha) + z(power)) ** 2


def required_n(
    psi: float, delta: float, alpha: float = 0.025, power: float = 0.80
) -> int:
    """Tasks needed to show non-inferiority within ``delta`` (true diff 0)."""
    return math.ceil(_z_sum_squared(alpha, power) * psi / delta**2)


def achieved_delta(
    psi: float, n: int, alpha: float = 0.025, power: float = 0.80
) -> float:
    """Smallest margin ``n`` tasks can resolve: reported if the budget caps ``n``."""
    return math.sqrt(_z_sum_squared(alpha, power) * psi / n)


def discordance(
    rows: Iterable[Mapping[str, str]], a: str, b: str, budget: str | None = None
) -> Discordance:
    """Count paired outcomes of strategies ``a`` and ``b`` from results.csv rows.

    Rows are paired by ``task_id``; only tasks present for both arms count.
    ``budget`` filters single-call sweeps (several budgets per task); without it
    a task appearing twice for one strategy is ambiguous and raises.
    """
    correct: dict[str, dict[str, bool]] = {a: {}, b: {}}
    for row in rows:
        strategy = row["strategy"]
        if strategy not in correct:
            continue
        if budget is not None and row.get("budget") != budget:
            continue
        task = row["task_id"]
        if task in correct[strategy]:
            raise ValueError(
                f"task {task!r} appears twice for {strategy!r}: pass a budget"
            )
        correct[strategy][task] = row["correct"] == "1"
    tasks = correct[a].keys() & correct[b].keys()
    a_only = sum(1 for t in tasks if correct[a][t] and not correct[b][t])
    b_only = sum(1 for t in tasks if correct[b][t] and not correct[a][t])
    return Discordance(n=len(tasks), a_only=a_only, b_only=b_only)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", help="results.csv to estimate psi from")
    parser.add_argument("--a", default="ours")
    parser.add_argument("--b", default="strong_rag")
    parser.add_argument("--budget", help="budget filter for single-call sweeps")
    parser.add_argument("--psi", type=float, help="use this psi instead of a file")
    parser.add_argument("--margins", default=",".join(str(m) for m in _DEFAULT_MARGINS))
    args = parser.parse_args(argv)

    if args.psi is not None:
        psi_observed = args.psi
        print(f"psi given: {psi_observed:.3f}")
    elif args.results:
        with open(args.results, encoding="utf-8", newline="") as fh:
            d = discordance(csv.DictReader(fh), args.a, args.b, args.budget)
        psi_observed = d.psi
        print(
            f"{args.a} vs {args.b}: n={d.n} {args.a}-only={d.a_only} "
            f"{args.b}-only={d.b_only} psi={psi_observed:.3f}"
        )
    else:
        parser.error("pass --results or --psi")

    psi_plan = max(psi_observed, PSI_FLOOR)
    print(f"psi_plan = max(psi, {PSI_FLOOR}) = {psi_plan:.3f}\n")
    print("| margin | tasks needed |")
    print("|---|---|")
    for margin in (float(m) for m in args.margins.split(",")):
        print(f"| {margin * 100:.1f} pts | {required_n(psi_plan, margin)} |")


if __name__ == "__main__":
    main()
