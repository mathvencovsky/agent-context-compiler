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

**Validity of H1** (research front F, lesson L1; proposed for an amendment).
A non-inferiority test only means something if the comparator worked: two
arms that both fail have Δ ≈ 0 and "pass" (ICH E10's *assay sensitivity*; FDA
non-inferiority guidance, 2016). :func:`validity` checks two conditions on the
task bootstrap, and :attr:`Verdicts.headline` needs both on top of H1:

* **floor:** the 2.5th percentile of success(``full_page``) is at least
  :data:`FULL_PAGE_FLOOR`;
* **margin rule:** the margin is at most :data:`MARGIN_FRACTION` of the
  documentation effect's lower bound, the 2.5th percentile of
  success(``full_page``) − success(``no_docs``). That is the FDA's usual
  "M2 = 50% of M1": a NINA inside the margin keeps at least half of what the
  documentation adds. Without a ``no_docs`` re-run on the primary population,
  its success there is 0 by construction of the §2.2 screen, which overstates
  the effect (regression to the mean); the result says which case applies.

It also reports the retention S(``nina``)/S(``full_page``) with its interval,
because a 7.5-point margin is a 19% relative loss when ``full_page`` is at 40%.

:func:`paired_exact` adds, as description only (L10), the exact McNemar p and
Newcombe's hybrid score interval for the paired difference (method 10,
Newcombe 1998), which covers better than the percentile bootstrap when there
are few discordant tasks.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from bench.power import PSI_FLOOR, achieved_delta
from bench.roi import TaskCost

SEED = 20261015
N_BOOT = 10_000
#: PREREG §1/§6: the non-inferiority margin on Δ.
MARGIN = 0.075
#: Proposed validity floor: lower 95% bound of success(full_page).
FULL_PAGE_FLOOR = 0.30
#: Proposed margin rule: MARGIN <= this share of the documentation effect.
MARGIN_FRACTION = 0.5
#: Two-sided 95% normal quantile for the score intervals.
_Z95 = 1.959963984540054


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
    values = [
        stat([full[i] for i in idx], [nina[i] for i in idx])
        for idx in _resamples(len(full), clusters, n_boot, seed)
    ]
    values.sort()
    return values


def _resamples(
    n: int, clusters: Sequence[str] | None, n_boot: int, seed: int
) -> Iterator[list[int]]:
    """Task indices of each resample, by task or by cluster."""
    rng = random.Random(seed)
    if clusters is None:
        groups = [[i] for i in range(n)]
    else:
        if len(clusters) != n:
            raise ValueError("one cluster per task")
        by: dict[str, list[int]] = {}
        for i, c in enumerate(clusters):
            by.setdefault(c, []).append(i)
        groups = [by[c] for c in sorted(by)]
    for _ in range(n_boot):
        yield [i for _ in groups for i in groups[rng.randrange(len(groups))]]


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q`` percentile (0-1) of sorted ``values``, nearest-rank."""
    return values[min(len(values) - 1, max(0, round(q * (len(values) - 1))))]


def success(tasks: Sequence[TaskCost]) -> float:
    """Share of ``tasks`` the primary judge marked correct."""
    return sum(t.correct for t in tasks) / len(tasks)


def retention(full: Sequence[TaskCost], nina: Sequence[TaskCost]) -> float:
    """S(nina) / S(full_page).

    Infinite if ``full_page`` has no correct task and ``nina`` has some; 1.0 if
    neither has any (nothing to retain, nothing lost).
    """
    s_full, s_nina = success(full), success(nina)
    if s_full == 0:
        return math.inf if s_nina > 0 else 1.0
    return s_nina / s_full


@dataclass(frozen=True)
class Validity:
    """Whether H1 can be read at all (L1): the comparator must have worked."""

    full_success: float
    full_lo: float
    no_docs_success: float
    #: False: no ``no_docs`` re-run, its success is the screen's 0.
    no_docs_rerun: bool
    doc_effect: float
    doc_effect_lo: float
    retention: float
    retention_lo: float
    retention_hi: float

    @property
    def floor_met(self) -> bool:
        return self.full_lo >= FULL_PAGE_FLOOR

    @property
    def margin_preserved(self) -> bool:
        return MARGIN <= MARGIN_FRACTION * self.doc_effect_lo

    @property
    def preserved_fraction(self) -> float:
        """Share of the documentation effect (lower bound) the margin keeps."""
        if self.doc_effect_lo <= 0:
            return -math.inf
        return 1 - MARGIN / self.doc_effect_lo

    @property
    def interpretable(self) -> bool:
        return self.floor_met and self.margin_preserved


def validity(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    *,
    no_docs: Sequence[bool] | None = None,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> Validity:
    """The L1 validity condition and the retention, on the §6 task bootstrap.

    ``no_docs`` is a ``no_docs`` re-run on the same tasks, in the same order;
    without it, ``no_docs`` success is 0 (every primary task failed the screen).
    """
    if len(full) != len(nina) or not full:
        raise ValueError("need the same non-empty task list for both arms")
    base = list(no_docs) if no_docs is not None else [False] * len(full)
    if len(base) != len(full):
        raise ValueError("no_docs must be paired with the tasks")
    full_s, effect, keep = [], [], []
    for idx in _resamples(len(full), None, n_boot, seed):
        f = [full[i] for i in idx]
        s = success(f)
        full_s.append(s)
        effect.append(s - sum(base[i] for i in idx) / len(idx))
        keep.append(retention(f, [nina[i] for i in idx]))
    full_s.sort()
    effect.sort()
    keep.sort()
    no_docs_s = sum(base) / len(base)
    return Validity(
        full_success=success(full),
        full_lo=percentile(full_s, 0.025),
        no_docs_success=no_docs_s,
        no_docs_rerun=no_docs is not None,
        doc_effect=success(full) - no_docs_s,
        doc_effect_lo=percentile(effect, 0.025),
        retention=retention(full, nina),
        retention_lo=percentile(keep, 0.025),
        retention_hi=percentile(keep, 0.975),
    )


def _wilson(k: int, n: int, z: float = _Z95) -> tuple[float, float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


@dataclass(frozen=True)
class PairedExact:
    """Descriptive paired statistics for Δ (L10; PREREG §6 McNemar)."""

    only_nina: int
    only_full: int
    mcnemar_p: float
    newcombe_lo: float
    newcombe_hi: float


def paired_exact(full: Sequence[TaskCost], nina: Sequence[TaskCost]) -> PairedExact:
    """Exact two-sided McNemar p and Newcombe's method-10 95% interval of Δ.

    With no discordant task the two arms are perfectly correlated and method
    10 collapses to [0, 0]; the bootstrap interval is the one to read then.
    """
    if len(full) != len(nina) or not full:
        raise ValueError("need the same non-empty task list for both arms")
    n = len(full)
    both = sum(f.correct and m.correct for f, m in zip(full, nina, strict=True))
    b = sum(m.correct and not f.correct for f, m in zip(full, nina, strict=True))
    c = sum(f.correct and not m.correct for f, m in zip(full, nina, strict=True))
    d = n - both - b - c
    m = b + c
    tail = sum(math.comb(m, i) for i in range(min(b, c) + 1)) / 2**m if m else 1.0
    # p1 = success(nina), p2 = success(full_page), Δ = p1 - p2
    p1, p2 = (both + b) / n, (both + c) / n
    l1, u1 = _wilson(both + b, n)
    l2, u2 = _wilson(both + c, n)
    denom = (both + b) * (c + d) * (both + c) * (b + d)
    phi = (both * d - b * c) / math.sqrt(denom) if denom else 0.0
    dl = math.sqrt(
        max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2)
    )
    du = math.sqrt(
        max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2)
    )
    diff = p1 - p2
    return PairedExact(
        only_nina=b,
        only_full=c,
        mcnemar_p=min(1.0, 2 * tail),
        newcombe_lo=max(-1.0, diff - dl),
        newcombe_hi=min(1.0, diff + du),
    )


def band_mde(n_by_band: Mapping[str, int], psi: float = PSI_FLOOR) -> dict[str, float]:
    """Smallest Δ each page-length band can resolve (Amendment 4 bins, L10)."""
    return {band: achieved_delta(psi, n) for band, n in n_by_band.items() if n}


@dataclass(frozen=True)
class Verdicts:
    """H1/H2 point estimates, bounds and verdicts, by task and by cluster."""

    delta: float
    delta_lo_task: float
    delta_lo_cluster: float
    ratio: float
    ratio_lo_task: float
    ratio_lo_cluster: float
    validity: Validity
    paired: PairedExact

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

    @property
    def h1_interpretable(self) -> bool:
        """The L1 validity condition (proposed amendment)."""
        return self.validity.interpretable

    @property
    def headline(self) -> bool:
        """§7's efficiency headline: H1 holds and H1 can be read."""
        return self.h1 and self.h1_interpretable

    @property
    def newcombe_changes_h1(self) -> bool:
        return (self.paired.newcombe_lo > -MARGIN) != self.h1


def verdicts(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    clusters: Sequence[str],
    *,
    no_docs: Sequence[bool] | None = None,
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
        validity=validity(full, nina, no_docs=no_docs, n_boot=n_boot),
        paired=paired_exact(full, nina),
    )
