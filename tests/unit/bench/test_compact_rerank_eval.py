"""The rule of bench/compact_rerank_eval.py and the scorer hook of compact."""

from __future__ import annotations

from app.retrieve.compact import compact_pieces
from bench.compact_rerank_eval import accepted, cached_scorer, floored


def _cell(hit: float, tokens: float) -> dict[str, list[float]]:
    return {"answer_hit": [hit], "coverage": [0.0], "tokens": [tokens]}


def _cells(arm_500: tuple[float, float], arm_1000: tuple[float, float]) -> dict:
    return {
        "bm25": {500: _cell(0.48, 495), 1000: _cell(0.60, 1000)},
        "qwen_0.1": {500: _cell(*arm_500), 1000: _cell(*arm_1000)},
    }


def test_rule_accepts_a_gain_at_1000_or_the_same_answers_at_500() -> None:
    assert [r[1] for r in accepted(_cells((0.50, 490), (0.65, 990)), 5.0)] == ["a"]
    assert [r[1] for r in accepted(_cells((0.59, 490), (0.61, 990)), 5.0)] == ["b"]


def test_rule_rejects_small_gains_more_tokens_and_slow_scoring() -> None:
    assert accepted(_cells((0.50, 490), (0.64, 990)), 5.0) == []
    assert accepted(_cells((0.50, 490), (0.70, 1001)), 5.0) == []
    assert accepted(_cells((0.59, 490), (0.70, 990)), 10.1) == []


def test_floor_zeroes_low_scores_and_each_text_is_scored_once() -> None:
    calls: list[list[str]] = []

    def rerank(_query: str, docs: list[str]) -> list[float]:
        calls.append(docs)
        return [0.9 if "key" in d else 0.05 for d in docs]

    seconds: list[float] = []
    score = floored(cached_scorer(rerank, "q", seconds), 0.1)
    assert score(["the key", "other"]) == [0.9, 0.0]
    assert score(["the key"]) == [0.9]
    assert calls == [["the key", "other"]]
    assert len(seconds) == 1


def test_unit_scorer_replaces_bm25_and_leaves_headings_out() -> None:
    text = "## Setup\n\nInstale com pip.\n\nUnrelated weather sentence here."
    scored: list[str] = []

    def scorer(units: list[str]) -> list[float]:
        scored.extend(units)
        return [1.0 if "pip" in u else 0.0 for u in units]

    out = compact_pieces("how to install", [text], [0.9], 500, unit_scorer=scorer)
    kept = out.texts[0] or ""
    assert "Instale com pip." in kept
    assert "weather" not in kept
    assert "## Setup" in kept
    assert not any(u.startswith("##") for u in scored)
