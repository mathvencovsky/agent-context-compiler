"""FakeRerankProvider: interface, range, length, determinism and overlap."""

from __future__ import annotations

from app.providers.rerank.base import RerankProvider
from app.providers.rerank.fake import FakeRerankProvider


def test_satisfies_protocol() -> None:
    assert isinstance(FakeRerankProvider(), RerankProvider)


def test_scores_aligned_to_documents() -> None:
    provider = FakeRerankProvider()
    scores = provider.rerank("default port", ["one", "two", "three"])
    assert len(scores) == 3


def test_scores_in_unit_range() -> None:
    provider = FakeRerankProvider()
    docs = ["the default port is 8080", "unrelated text", "", "port port port"]
    for score in provider.rerank("what is the default port", docs):
        assert 0.0 <= score <= 1.0


def test_deterministic_across_calls() -> None:
    provider = FakeRerankProvider()
    query = "configure the database connection"
    docs = ["database connection string", "something else entirely"]
    assert provider.rerank(query, docs) == provider.rerank(query, docs)


def test_disjoint_query_and_doc_score_zero() -> None:
    provider = FakeRerankProvider()
    (score,) = provider.rerank("alpha beta", ["gamma delta"])
    assert score == 0.0


def test_identical_tokens_score_one() -> None:
    provider = FakeRerankProvider()
    (score,) = provider.rerank("the default port", ["the default port"])
    assert score == 1.0


def test_high_overlap_outranks_low_overlap() -> None:
    provider = FakeRerankProvider()
    query = "default listening port"
    high, low = provider.rerank(
        query, ["the default listening port", "entirely unrelated content"]
    )
    assert high > low


def test_model_name_surfaced() -> None:
    assert FakeRerankProvider(model="fake-xyz").model == "fake-xyz"
