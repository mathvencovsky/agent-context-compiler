"""Pure parts of ``bench.m10b_retrieval``: variants, metrics, acceptance rules.

The rules are the ones written in DECISIONS 2026-10-29 before any measurement;
these tests pin them so a later edit cannot loosen them silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.retrieve.lexical import LexicalConfig
from bench import m10b_retrieval as m
from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider


def _tally(
    chunk: Sequence[bool], pool_set: Sequence[bool] | None = None
) -> m.PoolTally:
    t = m.PoolTally()
    for c, s in zip(chunk, pool_set if pool_set is not None else chunk, strict=True):
        t.add(c, s)
    return t


def _hits(n_true: int, n: int = 10) -> list[bool]:
    return [True] * n_true + [False] * (n - n_true)


def _paired(a: Sequence[bool], b: Sequence[bool]) -> dict[str, float]:
    diff = (sum(a) - sum(b)) / len(a)
    return {"diff": diff, "lo": diff - 0.05, "hi": diff + 0.05}


# --------------------------------------------------------------------------- #
# Variants
# --------------------------------------------------------------------------- #
def test_variant_list_is_fixed() -> None:
    names = m.lexical_variants()
    assert len(names) == 21 == len(set(names))
    assert names[:3] == ["current", "bm25", "ident"]
    for name in names:
        m.variant_config(name)  # every name parses


def test_variant_configs() -> None:
    assert m.variant_config("current") == LexicalConfig()
    assert m.variant_config("ident") == LexicalConfig(identifiers=True)
    assert m.variant_config("bm25f_t5_ident_sym5_cov") == LexicalConfig(
        scorer="bm25f",
        title_weight=5.0,
        identifiers=True,
        symbol_bonus=5.0,
        coverage=True,
    )
    with pytest.raises(ValueError):
        m.variant_config("bm25f_t3_magic")
    with pytest.raises(ValueError):
        m.variant_config("tfidf")


def test_page_variants() -> None:
    base = m.variant_config("bm25f_t3")
    rrf = m.with_page_prior(base, "rrf_w0.25", 16)
    assert (rrf.page_prior, rrf.page_prior_weight, rrf.page_prior_query_words) == (
        "rrf",
        0.25,
        16,
    )
    mul = m.with_page_prior(base, "mul_a1", 32)
    assert (mul.page_prior, mul.page_prior_alpha) == ("multiply", 1.0)
    assert mul.scorer == "bm25f"
    with pytest.raises(ValueError):
        m.with_page_prior(base, "max_x1", 32)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_first_hit_rank_and_diagnostics() -> None:
    assert m.first_hit_rank(["a", "b", "c"], {"c", "b"}) == 2
    assert m.first_hit_rank(["a"], {"z"}) is None
    d = m.rank_diagnostics([1, 4, 20, None])
    assert d["n"] == 4
    assert d["recall@10"] == 0.5
    assert d["recall@50"] == 0.75
    assert d["mrr"] == pytest.approx((1 + 1 / 4 + 1 / 20) / 4)
    assert m.rank_diagnostics([])["mrr"] == 0.0


def test_per_thousand_and_shares() -> None:
    assert m.per_thousand([0.5, 0.7], [1000, 3000]) == pytest.approx(0.3)
    assert m.per_thousand([], []) == 0.0
    assert m.coverage_shares([0.1, 0.5, 0.9], 0.3, 0.7) == {
        "insufficient": 0.333,
        "partial": 0.333,
        "sufficient": 0.333,
    }


# --------------------------------------------------------------------------- #
# Half-A rules
# --------------------------------------------------------------------------- #
def test_l1_thresholds_use_the_research_numbers_only_at_n_139() -> None:
    at_139 = _tally(_hits(80, 139), _hits(100, 139))
    assert m.l1_thresholds(at_139) == (91, 105)
    strong = _tally(_hits(95, 139), _hits(110, 139))
    assert m.l1_thresholds(strong) == (99, 110)
    other_n = _tally(_hits(80, 140), _hits(100, 140))
    assert m.l1_thresholds(other_n) == (84, 100)


def test_step_needs_chunk_set_and_no_loss_on_plain_tasks() -> None:
    base = _tally([True, False, False, True])
    cand = _tally([False, True, True, True])
    assert m.step_passes(cand, base, min_chunk=3, min_set=3)
    assert not m.step_passes(cand, base, min_chunk=4, min_set=3)
    # Task 0 has no symbol and the candidate loses it there.
    assert not m.step_passes(
        cand, base, min_chunk=3, min_set=3, plain=[True, False, False, False]
    )


def test_best_title_weight_prefers_chunk_then_set_then_lower_weight() -> None:
    tallies = {
        "bm25f_t1": _tally(_hits(5), _hits(6)),
        "bm25f_t3": _tally(_hits(5), _hits(6)),
        "bm25f_t5": _tally(_hits(5), _hits(5)),
    }
    assert m.best_title_weight(tallies) == "bm25f_t1"
    tallies["bm25f_t5"] = _tally(_hits(6), _hits(5))
    assert m.best_title_weight(tallies) == "bm25f_t5"


def _all_variants(default: m.PoolTally) -> dict[str, m.PoolTally]:
    return {name: default for name in m.lexical_variants()}


def test_decide_lexical_full_chain() -> None:
    n = 50
    tallies = _all_variants(_tally(_hits(20, n)))
    tallies["bm25f_t3"] = _tally(_hits(25, n))
    tallies["bm25f_t3_ident"] = _tally(_hits(29, n))
    tallies["bm25f_t3_ident_sym5"] = _tally(_hits(30, n))  # +1: fails
    tallies["bm25f_t3_ident_cov"] = _tally(_hits(33, n))
    chosen, log = m.decide_lexical(tallies, plain=[False] * n)
    assert chosen == "bm25f_t3_ident_cov"
    assert [line.split(":")[0] for line in log] == [
        "L1 PASS",
        "L3a PASS",
        "L3b FAIL",
        "L4 PASS",
    ]


def test_decide_lexical_without_l1() -> None:
    n = 50
    tallies = _all_variants(_tally(_hits(20, n)))
    tallies["bm25f_t5"] = _tally(_hits(23, n))  # +3 only
    tallies["ident"] = _tally(_hits(24, n))
    chosen, log = m.decide_lexical(tallies, plain=[False] * n)
    assert chosen == "ident"
    assert log[0].startswith("L1 FAIL")
    assert log[1].startswith("L3a PASS")
    assert log[2].startswith("L3b SKIP") and log[3].startswith("L4 SKIP")


def test_page_gate_is_strictly_above_40_percent() -> None:
    assert m.page_gate(_hits(4, 10)) == (False, 0.4)
    assert m.page_gate(_hits(5, 10))[0]
    assert m.page_gate([]) == (False, 0.0)


# --------------------------------------------------------------------------- #
# Half-B rules
# --------------------------------------------------------------------------- #
def test_confirm_general_passes_and_fails_for_each_condition() -> None:
    base = {b: _hits(50, 100) for b in m.BUDGETS}
    cand = {b: _hits(50, 100) for b in m.BUDGETS}
    cand[4000] = _hits(60, 100)
    good = {b: 0.2 for b in m.BUDGETS}
    weak = {b: 0.1 for b in m.BUDGETS}
    kwargs = {"cand_ms": 100.0, "base_ms": 80.0, "paired": _paired}
    ok, _ = m.confirm_general(
        cand, base, cand_per_1k=good, strong_per_1k=weak, **kwargs
    )
    assert ok
    # Efficiency below strong_rag at one budget.
    ok, _ = m.confirm_general(
        cand, base, cand_per_1k={**good, 8000: 0.05}, strong_per_1k=weak, **kwargs
    )
    assert not ok
    # A 3-point drop at 1k.
    dropped = {**cand, 1000: _hits(47, 100)}
    assert not m.confirm_general(
        dropped, base, cand_per_1k=good, strong_per_1k=weak, **kwargs
    )[0]
    # Latency above x1.5.
    slow = {"cand_ms": 200.0, "base_ms": 100.0, "paired": _paired}
    assert not m.confirm_general(
        cand, base, cand_per_1k=good, strong_per_1k=weak, **slow
    )[0]
    # CI at 4k not above zero.
    flat = {b: _hits(50, 100) for b in m.BUDGETS}
    assert not m.confirm_general(
        flat, base, cand_per_1k=good, strong_per_1k=weak, **kwargs
    )[0]


def test_rerank_rule_against_jina_and_none() -> None:
    jina = {b: _hits(50, 100) for b in m.BUDGETS}
    same = {b: _hits(50, 100) for b in m.BUDGETS}
    assert m.rerank_rule(same, jina, half="A", comparator="jina", paired=_paired)[0]
    # Fallback against no rerank needs +2 pts at 4k on half A.
    assert not m.rerank_rule(same, jina, half="A", comparator="none", paired=_paired)[0]
    better = {**same, 4000: _hits(52, 100)}
    assert m.rerank_rule(better, jina, half="A", comparator="none", paired=_paired)[0]
    # Half B vs Jina: non-inferiority, CI lower bound > -0.02 (the stub's
    # interval is the estimate +/- 0.05).
    ahead = {**same, 4000: _hits(54, 100)}
    assert m.rerank_rule(ahead, jina, half="B", comparator="jina", paired=_paired)[0]
    assert not m.rerank_rule(same, jina, half="B", comparator="jina", paired=_paired)[0]
    worse_1k = {**ahead, 1000: _hits(47, 100)}
    assert not m.rerank_rule(
        worse_1k, jina, half="B", comparator="jina", paired=_paired
    )[0]
    # Half B vs none: CI above zero.
    assert not m.rerank_rule(same, jina, half="B", comparator="none", paired=_paired)[0]


def test_embed_rule() -> None:
    openai = _tally(_hits(20, 50))
    assert m.embed_rule(_tally(_hits(24, 50)), openai, half="A", paired=_paired)[0]
    assert not m.embed_rule(_tally(_hits(23, 50)), openai, half="A", paired=_paired)[0]
    assert m.embed_rule(_tally(_hits(24, 50)), openai, half="B", paired=_paired)[0]
    assert not m.embed_rule(_tally(_hits(21, 50)), openai, half="B", paired=_paired)[0]


def test_one_look_per_change(tmp_path: Path) -> None:
    looks = tmp_path / "looks.jsonl"
    m.check_new_look("lexical:bm25f_t3", looks)
    m.record_look("lexical:bm25f_t3", True, looks)
    m.check_new_look("lexical:bm25f_t5", looks)
    with pytest.raises(SystemExit, match="already looked"):
        m.check_new_look("lexical:bm25f_t3", looks)


# --------------------------------------------------------------------------- #
# Zero cost
# --------------------------------------------------------------------------- #
def test_cache_misses_refuse_instead_of_paying(tmp_path: Path) -> None:
    embeddings = CachingEmbeddingProvider(
        m._RefusingEmbeddings("text-embedding-3-small", 1536), cache_dir=tmp_path
    )
    with pytest.raises(m.PaidCallRefused):
        embeddings.embed(["never embedded"])
    reranker = CachingRerankProvider(
        m._RefusingRerank("jina-reranker-v2-base-multilingual"), cache_dir=tmp_path
    )
    with pytest.raises(m.PaidCallRefused):
        reranker.rerank("q", ["d"])
    assert embeddings.api_calls == reranker.api_calls == 0


def test_cached_providers_use_the_frozen_model_names() -> None:
    assert m.cached_openai().model == "text-embedding-3-small"
    assert m.cached_jina().model == "jina-reranker-v2-base-multilingual"


def test_paid_line_counts_only_local_calls() -> None:
    class P:
        api_calls = 7
        _local = True

    class Q:
        api_calls = 3

    assert m.paid_line(P(), Q(), None).startswith("paid calls: 0")
    assert m.paid_line(P(), Q(), None).endswith("local-server calls: 7")


def test_parser_defaults() -> None:
    args = m._parser().parse_args(["lexical"])
    assert (args.half, args.vector, args.reranker, args.comparator) == (
        "A",
        "cache",
        "local",
        "jina",
    )
