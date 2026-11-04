"""Arms and acceptance rule of bench/compact_select_eval.py."""

from __future__ import annotations

from collections.abc import Sequence

from app.retrieve.compact import _units
from bench.compact_select_eval import (
    ARMS,
    Arm,
    adopted,
    advances,
    arm_of,
    combined,
    scored,
)

MD = "# Install\n\nRun pip install nina. It is quick.\n\nDocs.\n"


def _units_of(md: str = MD) -> list:
    return _units(md, 0)


def _by_length(texts: Sequence[str]) -> list[float]:
    return [float(len(t)) for t in texts]


def test_base_scores_the_units_and_leaves_headings_at_zero() -> None:
    units = _units_of()
    scores = scored(Arm(), _by_length, [MD], units)
    assert [u.kind for u in units][0] == "heading"
    assert scores[0] == 0.0
    assert scores[1:] == [21.0, 12.0, 5.0]


def test_short_units_score_zero_of_their_own() -> None:
    units = _units_of()
    scores = scored(ARMS["short5"], _by_length, [MD], units)
    assert scores[-1] == 0.0  # "Docs."
    assert scores[1] == 21.0


def test_context_arm_reads_the_unit_under_its_heading() -> None:
    seen: list[str] = []

    def spy(texts: Sequence[str]) -> list[float]:
        seen.extend(texts)
        return [1.0 for _ in texts]

    scored(ARMS["context"], spy, [MD], _units_of())
    assert seen[0] == "Install\nRun pip install nina."


def test_heading_arm_adds_a_quarter_of_the_heading_score() -> None:
    scores = scored(ARMS["heading"], _by_length, [MD], _units_of())
    assert scores[1] == 21.0 + 0.25 * len("Install")


def test_paragraph_arm_mixes_the_whole_line() -> None:
    scores = scored(ARMS["paragraph"], _by_length, [MD], _units_of())
    whole = len("Run pip install nina. It is quick.")
    assert scores[1] == 0.5 * 21.0 + 0.5 * whole
    assert scores[2] == 0.5 * 12.0 + 0.5 * whole


def test_combination_applies_each_change() -> None:
    merged = combined("short5", "context")
    assert merged.short_min == 5 and merged.context
    assert arm_of("short5+context") == merged
    assert arm_of("base") == Arm()


def test_tuning_rule_needs_three_points_within_the_budget() -> None:
    assert advances(0.63, 0.60, 560)
    assert not advances(0.62, 0.60, 560)
    assert not advances(0.70, 0.60, 561)


def test_confirmation_rule_needs_the_gain_and_the_blind_reader() -> None:
    assert adopted(0.62, 0.60, 4, 4)
    assert not adopted(0.61, 0.60, 4, 0)
    assert not adopted(0.70, 0.60, 3, 4)
