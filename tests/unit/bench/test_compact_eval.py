"""``bench.compact_eval``: the complementary metric and the fixed rule (L7)."""

from __future__ import annotations

from typing import Any

from app.index.tokens import count_tokens
from app.retrieve.select import Candidate
from bench.compact_eval import (
    answer_hit,
    answer_units,
    compact_context,
    confirm,
    gate_half_a,
    per_1k,
)
from bench.section_recall import units

SECTION = (
    "Send the key in the X-Api-Key header on every single request.\nKeys are short.\n"
)


def test_answer_units_prefer_long_sentences() -> None:
    su = units(SECTION)
    assert answer_units(su) == [
        "send the key in the x-api-key header on every single request."
    ]
    assert answer_units(units("Keys are short ok.\n")) == ["keys are short ok."]


def test_answer_hit_survives_pruning_of_other_sentences() -> None:
    su = units(SECTION)
    ctx = (
        "[URL] u\n[…]\nSend the key in the `X-Api-Key` header on every single request."
    )
    assert answer_hit(su, ctx)
    assert not answer_hit(su, "Keys are short.")


def test_compact_context_emits_url_headers_and_saves_tokens() -> None:
    filler = " ".join(f"Filler sentence {i} about weather." for i in range(60))
    md = f"{filler} Send the key in the X-Api-Key header. {filler}"
    cand = Candidate(
        chunk_id=0, document_id="d", ord=0, value=0.9, tokens=900, embedding=[]
    )
    ctx = compact_context(
        "Which header carries the key?",
        [cand],
        {("d", 0): md},
        {"d": "https://x/a"},
        100,
    )
    assert ctx.startswith("[URL] https://x/a\n")
    assert "Send the key in the X-Api-Key header." in ctx
    assert count_tokens(ctx) < count_tokens(md)


def _cells(new_hits: list[bool], old_hits: list[bool]) -> dict[str, Any]:
    def cell(hits: list[bool], tokens: float) -> dict[str, list[Any]]:
        return {
            "answer_hit": hits,
            "coverage": [0.5 if h else 0.0 for h in hits],
            "tokens": [tokens] * len(hits),
            "precision": [0.4 if h else 0.1 for h in hits],
        }

    return {
        "compact": {b: cell(new_hits, 400.0) for b in (500, 1000, 2000)},
        "current": {b: cell(old_hits, 900.0) for b in (500, 1000, 2000)},
    }


def test_rule_gate_and_confirm() -> None:
    better = _cells([True] * 40 + [False] * 10, [True] * 20 + [False] * 30)
    assert gate_half_a(better)
    rows = confirm(better, strong_per_1k_at_1000=0.1)
    assert all(ok for _, ok, _ in rows), rows
    same = _cells([True] * 25 + [False] * 25, [True] * 25 + [False] * 25)
    assert not gate_half_a(same)
    assert not all(ok for _, ok, _ in confirm(same, strong_per_1k_at_1000=0.1))


def test_per_1k() -> None:
    assert per_1k({"coverage": [0.5, 0.5], "tokens": [500, 500]}) == 1.0
