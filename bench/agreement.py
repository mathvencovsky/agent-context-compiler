"""Agreement between M10 raters (PREREG §9): Cohen's kappa and the judge gate.

The kappa covers the two human graders, the primary judge against the settled
human labels, and the two judges. If the primary judge's kappa against the
humans is below :data:`KAPPA_GATE`, the headline must come from the human-
labelled sample only and the judge-based results are marked unreliable.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: PREREG §9: minimum kappa between the primary judge and the settled labels.
KAPPA_GATE = 0.6


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float:
    """Cohen's kappa for two binary raters on the same items.

    Returns NaN when chance agreement is total (both raters gave one label to
    every item), where kappa is undefined.
    """
    if len(a) != len(b) or not a:
        raise ValueError("need two non-empty label lists of the same length")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return math.nan if chance == 1 else (observed - chance) / (1 - chance)


def judge_is_reliable(kappa: float) -> bool:
    """The PREREG §9 gate (an undefined kappa does not pass)."""
    return kappa >= KAPPA_GATE


def disagreement_by_length(
    rows: Sequence[tuple[str, bool, bool]], bins: int = 3
) -> list[dict[str, float]]:
    """Judge-vs-human disagreement per answer-length bin (judge bias check).

    Each row is ``(answer, judge_correct, human_correct)``. Rows are sorted by
    answer length in characters and cut into ``bins`` bins of near-equal size.
    Per bin: the longest answer, n, and the share of answers the judge passed
    and the humans failed (``judge_lenient``) or the reverse (``judge_strict``).
    A ``judge_lenient`` share that grows with length is the verbosity bias
    LLM judges are known for (Zheng et al., 2023); the rubric tells the judge
    to ignore length, and this checks it did. Description only: the PREREG
    gate stays the kappa.
    """
    ordered = sorted(rows, key=lambda r: len(r[0]))
    out = []
    for i in range(bins):
        part = ordered[i * len(ordered) // bins : (i + 1) * len(ordered) // bins]
        if not part:
            continue
        n = len(part)
        out.append(
            {
                "max_chars": float(len(part[-1][0])),
                "n": float(n),
                "judge_lenient": sum(j and not h for _a, j, h in part) / n,
                "judge_strict": sum(h and not j for _a, j, h in part) / n,
            }
        )
    return out


# --- Judge validity in the population (research front F, lesson L2) ----------
#
# The human-label sample has fixed quotas per arm x judge-verdict stratum
# (Amendment 5), so half of it is "judge said correct" whatever the real share.
# Kappa depends on prevalence, so the §9 kappa above describes that artificial
# mix. The functions below weight each labelled answer by N_stratum / n_stratum
# (N from ``answers.jsonl``) to describe the judge on the population it rules
# on, per arm, which is what can bias Δ: differential judge error between arms.
#
# The corrected success of an arm is the stratified estimator
# Σ_h (N_h / N) · mean(human | stratum h). With strata defined by the judge's
# verdict it equals the prediction-powered (PPI) estimate: the judge's mean
# plus the stratum-weighted mean of (human − judge). It is a sensitivity
# analysis, never a decision; with so few labels its interval is wide (Dorner,
# Nastl and Hardt, 2025). Intervals: stratified bootstrap of the labelled
# answers (each stratum resampled within itself), seed ``20261015``.

#: (arm, judge verdict, settled human label) of one labelled answer.
Labelled = tuple[str, bool, bool]
#: (arm, judge verdict) -> number of population answers in that stratum.
Population = Mapping[tuple[str, bool], int]

_SEED = 20261015
_N_BOOT = 10_000


@dataclass(frozen=True)
class WeightedAgreement:
    """Judge-vs-human agreement with each label weighted to its stratum size."""

    observed: float
    kappa: float
    n: int


def _weights(rows: Sequence[Labelled], population: Population) -> list[float]:
    sampled = Counter((arm, j) for arm, j, _h in rows)
    out = []
    for arm, j, _h in rows:
        if population.get((arm, j), 0) <= 0:
            raise ValueError(f"stratum {(arm, j)} is not in the population counts")
        out.append(population[(arm, j)] / sampled[(arm, j)])
    return out


def weighted_agreement(
    rows: Sequence[Labelled], population: Population
) -> WeightedAgreement:
    """Agreement and Cohen's kappa on the stratum-weighted 2x2 table."""
    if not rows:
        raise ValueError("no labelled answers")
    w = _weights(rows, population)
    total = sum(w)
    observed = sum(wi for wi, (_a, j, h) in zip(w, rows, strict=True) if j == h)
    pj = sum(wi for wi, (_a, j, _h) in zip(w, rows, strict=True) if j) / total
    ph = sum(wi for wi, (_a, _j, h) in zip(w, rows, strict=True) if h) / total
    chance = pj * ph + (1 - pj) * (1 - ph)
    po = observed / total
    kappa = math.nan if chance == 1 else (po - chance) / (1 - chance)
    return WeightedAgreement(observed=po, kappa=kappa, n=len(rows))


def _arm_rates(
    n_pass: int, n_fail: int, p_pass: float, p_fail: float
) -> tuple[float, float, float, float]:
    """Judge success, corrected success, sensitivity and specificity of an arm.

    ``p_pass``/``p_fail``: share the humans called correct among the judge's
    passes/fails. NaN where a needed stratum has no labels.
    """
    n = n_pass + n_fail
    human_ok = n_pass * p_pass + n_fail * p_fail
    human_bad = n_pass * (1 - p_pass) + n_fail * (1 - p_fail)
    corrected = human_ok / n
    sens = n_pass * p_pass / human_ok if human_ok else math.nan
    spec = n_fail * (1 - p_fail) / human_bad if human_bad else math.nan
    return n_pass / n, corrected, sens, spec


@dataclass(frozen=True)
class JudgeErrors:
    """The primary judge against the humans on one arm's population."""

    n_labelled: int
    judge_success: float
    corrected_success: float
    corrected_lo: float
    corrected_hi: float
    sensitivity: float
    sensitivity_lo: float
    sensitivity_hi: float
    specificity: float
    specificity_lo: float
    specificity_hi: float

    @property
    def bias(self) -> float:
        """Judge success minus human-corrected success (positive: lenient)."""
        return self.judge_success - self.corrected_success


def _labels_by_stratum(rows: Sequence[Labelled]) -> dict[tuple[str, bool], list[bool]]:
    out: dict[tuple[str, bool], list[bool]] = {}
    for arm, j, h in rows:
        out.setdefault((arm, j), []).append(h)
    return out


def _share(labels: Sequence[bool]) -> float:
    return sum(labels) / len(labels) if labels else math.nan


def _resampled_shares(
    by: Mapping[tuple[str, bool], list[bool]], rng: random.Random
) -> dict[tuple[str, bool], float]:
    return {
        k: _share([v[rng.randrange(len(v))] for _ in v]) for k, v in sorted(by.items())
    }


def _rates_of(
    arm: str, population: Population, shares: Mapping[tuple[str, bool], float]
) -> tuple[float, float, float, float]:
    n_pass, n_fail = population.get((arm, True), 0), population.get((arm, False), 0)
    # A stratum with no population answers needs no label.
    p_pass = shares.get((arm, True), math.nan) if n_pass else 0.0
    p_fail = shares.get((arm, False), math.nan) if n_fail else 0.0
    return _arm_rates(n_pass, n_fail, p_pass, p_fail)


def _interval(values: Sequence[float]) -> tuple[float, float]:
    finite = sorted(v for v in values if not math.isnan(v))
    if not finite:
        return math.nan, math.nan
    pick = lambda q: finite[min(len(finite) - 1, round(q * (len(finite) - 1)))]  # noqa: E731
    return pick(0.025), pick(0.975)


def judge_errors_by_arm(
    rows: Sequence[Labelled],
    population: Population,
    *,
    n_boot: int = _N_BOOT,
    seed: int = _SEED,
) -> dict[str, JudgeErrors]:
    """Per arm: judge and corrected success, sensitivity and specificity, CIs."""
    by = _labels_by_stratum(rows)
    point = {k: _share(v) for k, v in by.items()}
    rng = random.Random(seed)
    boots = [_resampled_shares(by, rng) for _ in range(n_boot)]
    out = {}
    for arm in sorted({a for a, _j in population}):
        judge_s, corrected, sens, spec = _rates_of(arm, population, point)
        draws = [_rates_of(arm, population, b) for b in boots]
        c_lo, c_hi = _interval([d[1] for d in draws])
        se_lo, se_hi = _interval([d[2] for d in draws])
        sp_lo, sp_hi = _interval([d[3] for d in draws])
        out[arm] = JudgeErrors(
            n_labelled=sum(len(v) for (a, _j), v in by.items() if a == arm),
            judge_success=judge_s,
            corrected_success=corrected,
            corrected_lo=c_lo,
            corrected_hi=c_hi,
            sensitivity=sens,
            sensitivity_lo=se_lo,
            sensitivity_hi=se_hi,
            specificity=spec,
            specificity_lo=sp_lo,
            specificity_hi=sp_hi,
        )
    return out


@dataclass(frozen=True)
class CorrectedDelta:
    """Δ = success(a) − success(b), by the judge and corrected by the humans."""

    judge_delta: float
    corrected: float
    lo: float
    hi: float
    #: judge Δ − corrected Δ: what differential judge error adds to Δ.
    bias: float
    bias_lo: float
    bias_hi: float


def corrected_delta(
    rows: Sequence[Labelled],
    population: Population,
    *,
    a: str = "nina",
    b: str = "full_page",
    n_boot: int = _N_BOOT,
    seed: int = _SEED,
) -> CorrectedDelta:
    """Human-corrected Δ and the judge's bias on Δ (sensitivity only, L2)."""
    by = _labels_by_stratum(rows)

    def deltas(shares: Mapping[tuple[str, bool], float]) -> tuple[float, float]:
        ja, ca, _sa, _pa = _rates_of(a, population, shares)
        jb, cb, _sb, _pb = _rates_of(b, population, shares)
        return ca - cb, (ja - jb) - (ca - cb)

    point = deltas({k: _share(v) for k, v in by.items()})
    rng = random.Random(seed)
    draws = [deltas(_resampled_shares(by, rng)) for _ in range(n_boot)]
    lo, hi = _interval([d[0] for d in draws])
    b_lo, b_hi = _interval([d[1] for d in draws])
    judge_delta = point[0] + point[1]
    return CorrectedDelta(
        judge_delta=judge_delta,
        corrected=point[0],
        lo=lo,
        hi=hi,
        bias=point[1],
        bias_lo=b_lo,
        bias_hi=b_hi,
    )
