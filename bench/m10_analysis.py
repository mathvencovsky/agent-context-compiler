"""H1 and H2 of the M10 pre-registration, by task and by cluster (PREREG §6, Am. 13).

Inputs are the paired primary-population tasks of two arms, as
:class:`bench.roi.TaskCost` (session USD, tool tokens, primary-judge verdict),
in the same task order, plus each task's cluster: its first cited page.

* **H1:** Δ = success(``nina``) − success(``full_page``). Supported if the
  lower bound of the one-sided 97.5% interval (the 2.5th percentile) is above
  −0.075.
* **H2:** R = CPT(``full_page``) / CPT(``nina``), CPT = Σ USD / Σ correct.
  Supported if the lower bound of the 95% interval is above 1.

§6 resamples tasks. Amendment 13 adds the same intervals resampling clusters
of tasks that share their first cited page, as a sensitivity analysis: tasks
about one page are not independent draws, so the task interval can be too
narrow (Anthropic, "Adding error bars to evals"). The verdicts stay §6's; the
report says whether the clustered interval would change them.

10,000 resamples, seed ``20261015``, percentile intervals.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from bench.roi import TaskCost

SEED = 20261015
N_BOOT = 10_000
#: PREREG §1/§6: the non-inferiority margin on Δ.
MARGIN = 0.075


def delta(full: Sequence[TaskCost], nina: Sequence[TaskCost]) -> float:
    """Success rate of ``nina`` minus that of ``full_page``."""
    n = len(full)
    return (sum(t.correct for t in nina) - sum(t.correct for t in full)) / n


def cpt_ratio(full: Sequence[TaskCost], nina: Sequence[TaskCost]) -> float:
    """CPT(full_page) / CPT(nina); infinite if ``nina`` has no correct task."""

    def cpt(tasks: Sequence[TaskCost]) -> float:
        correct = sum(t.correct for t in tasks)
        cost = sum(t.cost_usd for t in tasks)
        return cost / correct if correct else float("inf")

    return cpt(full) / cpt(nina)


Stat = Callable[[Sequence[TaskCost], Sequence[TaskCost]], float]


def bootstrap(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    stat: Stat,
    *,
    clusters: Sequence[str] | None = None,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> list[float]:
    """Sorted resampled values of ``stat``, by task or by cluster.

    With ``clusters``, each resample draws clusters with replacement and keeps
    every task of a drawn cluster, both arms together (the pairing holds).
    """
    if len(full) != len(nina) or not full:
        raise ValueError("need the same non-empty task list for both arms")
    rng = random.Random(seed)
    if clusters is None:
        groups = [[i] for i in range(len(full))]
    else:
        if len(clusters) != len(full):
            raise ValueError("one cluster per task")
        by: dict[str, list[int]] = {}
        for i, c in enumerate(clusters):
            by.setdefault(c, []).append(i)
        groups = [by[c] for c in sorted(by)]
    values = []
    for _ in range(n_boot):
        idx = [i for _ in groups for i in groups[rng.randrange(len(groups))]]
        values.append(stat([full[i] for i in idx], [nina[i] for i in idx]))
    values.sort()
    return values


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q`` percentile (0-1) of sorted ``values``, nearest-rank."""
    return values[min(len(values) - 1, max(0, round(q * (len(values) - 1))))]


@dataclass(frozen=True)
class Verdicts:
    """H1/H2 point estimates, bounds and verdicts, by task and by cluster."""

    delta: float
    delta_lo_task: float
    delta_lo_cluster: float
    ratio: float
    ratio_lo_task: float
    ratio_lo_cluster: float

    @property
    def h1(self) -> bool:
        return self.delta_lo_task > -MARGIN

    @property
    def h2(self) -> bool:
        return self.ratio_lo_task > 1

    @property
    def cluster_changes_a_verdict(self) -> bool:
        return (self.delta_lo_cluster > -MARGIN) != self.h1 or (
            self.ratio_lo_cluster > 1
        ) != self.h2


def verdicts(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    clusters: Sequence[str],
    *,
    n_boot: int = N_BOOT,
) -> Verdicts:
    """H1 and H2 with the §6 task bootstrap and the Amendment 13 cluster one."""
    kw = {"n_boot": n_boot}
    d_task = bootstrap(full, nina, delta, **kw)  # type: ignore[arg-type]
    d_clus = bootstrap(full, nina, delta, clusters=clusters, **kw)  # type: ignore[arg-type]
    r_task = bootstrap(full, nina, cpt_ratio, **kw)  # type: ignore[arg-type]
    r_clus = bootstrap(full, nina, cpt_ratio, clusters=clusters, **kw)  # type: ignore[arg-type]
    return Verdicts(
        delta=delta(full, nina),
        delta_lo_task=percentile(d_task, 0.025),
        delta_lo_cluster=percentile(d_clus, 0.025),
        ratio=cpt_ratio(full, nina),
        ratio_lo_task=percentile(r_task, 0.025),
        ratio_lo_cluster=percentile(r_clus, 0.025),
    )
