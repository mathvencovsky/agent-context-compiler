"""Document order and near-duplicate selection variants (bench/m10a_order_dedup.py)."""

from __future__ import annotations

from app.retrieve.select import Candidate, select
from bench.m10a_order_dedup import (
    confirm_dedup,
    confirm_order,
    jaccard,
    near_dup_tokens,
    order_by_document_value,
    select_dedup,
    shingles,
    variant_select,
)

_BASE = "the timeout option sets how many seconds the client waits for a reply"
TEXTS = {
    ("d1", 0): _BASE,
    ("d1", 1): _BASE + " today",  # near duplicate of ("d1", 0)
    ("d2", 0): "flask routes map a url rule to a view function in the app",
    ("d3", 0): "postgres limit clause restricts the number of rows a query returns",
}


def _c(doc: str, ord_: int, value: float, tokens: int = 10) -> Candidate:
    return Candidate(
        chunk_id=hash((doc, ord_)) & 0xFFFF,
        document_id=doc,
        ord=ord_,
        value=value,
        tokens=tokens,
        embedding=[1.0, 0.0],
    )


def _text(c: Candidate) -> str:
    return TEXTS[(c.document_id, c.ord)]


def test_shingles_and_jaccard_flag_near_duplicates_only() -> None:
    a, b = shingles(TEXTS[("d1", 0)]), shingles(TEXTS[("d1", 1)])
    assert jaccard(a, b) >= 0.8
    assert jaccard(a, shingles(TEXTS[("d2", 0)])) == 0.0
    assert shingles("two words") == frozenset({("two", "words")})
    assert jaccard(frozenset(), a) == 0.0


def test_near_dup_tokens_counts_the_later_copy() -> None:
    picked = [_c("d1", 0, 0.9, 10), _c("d2", 0, 0.5, 7), _c("d1", 1, 0.8, 12)]
    assert near_dup_tokens(picked, _text) == 12


def test_select_dedup_spends_the_budget_on_new_text() -> None:
    cands = [_c("d1", 0, 0.9), _c("d1", 1, 0.85), _c("d2", 0, 0.5), _c("d3", 0, 0.4)]
    plain = select(cands, budget_tokens=20, lambda_=0.0, min_value=0.0)
    deduped = select_dedup(
        cands, budget_tokens=20, lambda_=0.0, min_value=0.0, text_of=_text
    )
    assert [(c.document_id, c.ord) for c in plain] == [("d1", 0), ("d1", 1)]
    assert [(c.document_id, c.ord) for c in deduped] == [("d1", 0), ("d2", 0)]


def test_select_dedup_matches_select_without_duplicates() -> None:
    cands = [_c("d2", 0, 0.5), _c("d3", 0, 0.4), _c("d1", 0, 0.9)]
    for budget in (5, 10, 20, 30):
        assert select_dedup(
            cands, budget_tokens=budget, lambda_=0.0, min_value=0.0, text_of=_text
        ) == select(cands, budget_tokens=budget, lambda_=0.0, min_value=0.0)


def test_order_puts_the_best_document_first_and_keeps_page_order() -> None:
    picked = [_c("a", 0, 0.2), _c("a", 3, 0.3), _c("b", 1, 0.9), _c("b", 0, 0.1)]
    ordered = order_by_document_value(picked)
    assert [(c.document_id, c.ord) for c in ordered] == [
        ("b", 0),
        ("b", 1),
        ("a", 0),
        ("a", 3),
    ]


def test_variant_select_records_each_variant_picks() -> None:
    cands = [_c("d3", 0, 0.9), _c("d2", 0, 0.1)]
    sink: list[list[Candidate]] = []
    out = variant_select("order", _text, sink)(
        cands, budget_tokens=20, lambda_=0.0, min_value=0.0
    )
    assert [c.document_id for c in out] == ["d3", "d2"]  # current gives d2, d3
    assert sink == [out]


def _cells(hits: list[bool], first: list[int | None], cov: list[float]) -> dict:
    return {
        "hit": hits,
        "first_hit": first,
        "coverage": cov,
        "precision": [0.1] * len(hits),
    }


def test_confirm_order_needs_the_answer_to_move_first() -> None:
    n = 40
    current = {1000: _cells([True] * n, [3] * n, [0.6] * n)}
    better = {1000: _cells([True] * n, [1] * n, [0.6] * n)}
    assert all(ok for _, ok, _ in confirm_order(better, current))
    assert not all(ok for _, ok, _ in confirm_order(current, current))


def test_confirm_dedup_rejects_a_coverage_loss() -> None:
    current = {4000: _cells([True, True], [1, 1], [0.8, 0.8])}
    worse = {4000: _cells([True, False], [1, None], [0.8, 0.4])}
    worse[4000]["precision"] = [0.5, 0.5]
    checks = confirm_dedup(worse, current, {4000: 0.01}, {4000: [100.0, 100.0]})
    failed = [what for what, ok, _ in checks if not ok]
    assert failed == [
        "coverage at 4000: no drop over 0.5 pt",
        "hit at 4000: no drop over 1 pt",
    ]
