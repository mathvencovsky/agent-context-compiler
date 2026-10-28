"""The full CPT with NINA's embeddings and rerank (bench/full_cpt.py, front F L3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bench.full_cpt import (
    ArmCost,
    full_ratio,
    retrieval_usd,
    session_retrieval_usd,
    usage_by_session,
)
from bench.roi import TaskCost

_PRICES = {
    "retrieval": {
        "emb": {"usd_per_1m_tokens": 0.02},
        "rr": {"usd_per_1m_tokens": 0.05},
    }
}
_EMBED = {"kind": "embed", "model": "emb", "texts": 2, "tokens": 1_000_000}
_RERANK = {
    "kind": "rerank",
    "model": "rr",
    "documents": 10,
    "query_tokens": 10,
    "document_tokens": 999_900,
}


def test_retrieval_usd_prices_embeddings_and_rerank_pairs() -> None:
    # rerank: each document is billed with the query, 10 x 10 + 999,900 = 1M
    assert retrieval_usd([_EMBED, _RERANK], _PRICES) == pytest.approx(0.02 + 0.05)
    assert retrieval_usd([], _PRICES) == 0.0


def test_a_todo_or_missing_price_is_refused_not_zero() -> None:
    todo = {"retrieval": {"emb": {"usd_per_1m_tokens": "TODO"}}}
    with pytest.raises(ValueError, match="TODO"):
        retrieval_usd([_EMBED], todo)
    with pytest.raises(ValueError, match="no price"):
        retrieval_usd([_RERANK], _PRICES | {"retrieval": {}})


def test_the_shipped_price_list_refuses_until_the_human_fills_it() -> None:
    prices = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "bench/prices.yaml").read_text()
    )
    row = dict(_EMBED, model="text-embedding-3-small")
    entry = prices["retrieval"]["text-embedding-3-small"]
    if not isinstance(entry["usd_per_1m_tokens"], (int, float)):
        with pytest.raises(ValueError, match="TODO"):
            retrieval_usd([row], prices)
    assert "jina-reranker-v2-base-multilingual" in prices["retrieval"]


def test_usage_files_by_session_keep_the_last_attempt(tmp_path: Path) -> None:
    for name, rows in {
        "so-1__nina__0": [_EMBED],
        "so-1__nina__1": [_EMBED, _RERANK],
        "so-2__strong_rag__0": [_RERANK],
    }.items():
        (tmp_path / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
    usd = usage_by_session(tmp_path, _PRICES)
    assert usd[("so-1", "nina")] == pytest.approx(0.07)
    assert usd[("so-2", "strong_rag")] == pytest.approx(0.05)
    assert session_retrieval_usd(tmp_path / "missing.jsonl", _PRICES) == 0.0


def test_arm_cost_gives_cpt_full_cpt_and_usd_per_thousand() -> None:
    arm = ArmCost(n=4, correct=2, cost_usd=0.40, retrieval_usd=0.04)
    assert arm.cpt == pytest.approx(0.20)
    assert arm.full_cpt == pytest.approx(0.22)
    assert arm.usd_per_1000_tasks == pytest.approx(100.0)
    assert arm.full_usd_per_1000_tasks == pytest.approx(110.0)
    assert arm.full_usd_per_1000_correct == pytest.approx(220.0)
    assert ArmCost(n=1, correct=0, cost_usd=1.0, retrieval_usd=0.0).cpt == float("inf")


def test_full_ratio_adds_retrieval_cost_to_nina_only_where_it_was_paid() -> None:
    full = [TaskCost(cost_usd=0.2, tool_tokens=0, correct=True)] * 10
    nina = [TaskCost(cost_usd=0.05, tool_tokens=0, correct=True)] * 10
    ratio, lo = full_ratio(full, nina, [0.0] * 10, [0.05] * 10, n_boot=200)
    assert ratio == pytest.approx(2.0)  # 0.2 / (0.05 + 0.05)
    assert lo == pytest.approx(2.0)
    with pytest.raises(ValueError):
        full_ratio(full, nina, [0.0], [0.0] * 10, n_boot=10)
