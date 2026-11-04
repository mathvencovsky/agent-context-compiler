"""The answer check, sample, blinding and verdict of bench/context7_compare.py."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from bench.context7_compare import (
    adjusted,
    blind_sheet,
    sample,
    scrub,
    soft_hit,
    sweep_passes,
    verdict,
)

UNIT = "you can loop over the file object for reading lines from a file"


def test_soft_hit_accepts_reformatted_text() -> None:
    reply = (
        "For reading lines from a file, you can\nloop over the file object:\n\n```\n"
    )
    assert soft_hit([UNIT], reply)


def test_soft_hit_refuses_words_scattered_across_the_reply() -> None:
    far = "\n".join(
        ["loop file", "x", "x", "x", "object reading", "x", "x", "x", "lines"]
    )
    assert not soft_hit([UNIT], far)


def test_soft_hit_is_false_on_an_empty_reply() -> None:
    assert not soft_hit([UNIT], "")


def test_sample_is_fixed_and_independent_of_input_order() -> None:
    ids = [f"t{i}" for i in range(200)]
    assert sample(ids) == sample(list(reversed(ids)))
    assert len(sample(ids)) == 80


@pytest.mark.parametrize(
    ("d", "lo", "hi", "share", "expected"),
    [
        (0.20, 0.05, 0.35, 1.10, "NINA VENCE"),
        (-0.03, -0.15, 0.09, 0.70, "NINA VENCE"),
        (-0.20, -0.35, -0.05, 0.50, "NINA PERDE"),
        (0.03, -0.09, 0.15, 1.20, "NINA PERDE"),
        (0.20, 0.05, 0.35, 1.30, "SEM VANTAGEM"),
        (0.00, -0.12, 0.12, 1.00, "SEM VANTAGEM"),
    ],
)
def test_verdict_rule(
    d: float, lo: float, hi: float, share: float, expected: str
) -> None:
    assert verdict(d, lo, hi, share) == expected


def test_sweep_rule_needs_both_tokens_and_answers() -> None:
    assert sweep_passes(560, 0.45)
    assert not sweep_passes(561, 0.60)
    assert not sweep_passes(400, 0.44)


def test_scrub_keeps_page_text_when_the_notice_names_the_notes_tag() -> None:
    # This exact shape once erased every reply of one tool.
    reply = (
        "[untrusted_source] NINA's own notes are only inside <nina_notes>.\n"
        '<document source="u" chunks="1">\nThe page text.\n</document>\n'
        "<nina_notes>\n- budget_limited: more.\n</nina_notes>\n"
    )
    assert scrub(reply) == "The page text."


def test_scrub_removes_tool_names_and_says_when_nothing_came_back() -> None:
    assert scrub("Context7 and NINA") == "TOOL and TOOL"
    assert scrub("") == "(the tool returned nothing)"


def test_blind_sheet_skips_a_rival_a_task_did_not_run() -> None:
    row = _row()
    row["hit"] = {"nina": True, "c7_fetch": False}
    sheet, _ = blind_sheet([row])
    assert len(sheet) == 1


def _row() -> dict[str, Any]:
    return {
        "task": "t1",
        "question": "q",
        "reference": "r",
        "reply": {"nina": "from nina", "c7_fetch": "from c7", "c7_docs": "same"},
        "hit": {"nina": True, "c7_fetch": False, "c7_docs": True},
    }


def test_blind_sheet_holds_only_disagreements_and_hides_the_tools() -> None:
    sheet, key = blind_sheet([_row()])
    assert len(sheet) == 1
    assert set(sheet[0]) == {"case", "question", "reference_answer", "A", "B"}
    assert "t1" not in str(sheet) and "c7_fetch" not in str(sheet)
    assert set(key["t1|c7_fetch"].values()) == {"nina", "c7_fetch"}


def test_blind_reader_replaces_the_soft_hit_of_a_disagreement() -> None:
    row = _row()
    _, key = blind_sheet([row])
    code = hashlib.sha256(b"t1|c7_fetch").hexdigest()[:12]
    nina_label = next(k for k, v in key["t1|c7_fetch"].items() if v == "nina")
    other = "B" if nina_label == "A" else "A"
    decisions = {code: {nina_label: False, other: True}}
    assert adjusted(row, "c7_fetch", decisions, key) == (False, True)


def test_agreement_is_not_sent_to_the_blind_reader() -> None:
    assert adjusted(_row(), "c7_docs", {}, {}) == (True, True)
