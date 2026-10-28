"""Agreement between M10 raters (PREREG §9): Cohen's kappa and the judge gate.

The kappa covers the two human graders, the primary judge against the settled
human labels, and the two judges. If the primary judge's kappa against the
humans is below :data:`KAPPA_GATE`, the headline must come from the human-
labelled sample only and the judge-based results are marked unreliable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

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
