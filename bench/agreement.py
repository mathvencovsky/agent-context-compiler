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
