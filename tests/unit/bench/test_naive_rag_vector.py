"""``naive_rag_vector`` baseline over the fixture corpus: bounded, deterministic.

Proves the competent VECTOR RAG baseline (hybrid BM25+vector by default, plus a
pure-cosine ``vector_only`` variant) respects the token budget, is byte-identical
across calls, builds a non-empty context in BOTH modes for a real doc, uses the
question's relevant URL, ignores a URL not in the corpus, and is registered.
Everything runs on the deterministic fake embeddings — zero network (the autouse
``_no_network`` guard would fail any real socket).
"""

from __future__ import annotations

import pytest

from bench.corpus_store import CorpusStore
from bench.runners.base import available_runners, get_runner
from bench.runners.naive_rag_vector import NaiveRagVectorRunner
from bench.tasks_schema import Evidence, Task

_URL = "https://docs.python.org/3/library/functions.html#abs"
_FLASK_URL = "https://flask.palletsprojects.com/en/stable/quickstart/"


def _task(urls: list[str]) -> Task:
    return Task(
        id="t1",
        category="api_reference",
        split="dev",
        urls=urls,
        question="What does abs return and what are its argument types?",
        answer_type="judge",
        expected=["the absolute value"],
        expected_evidence=[Evidence(url=urls[0], quote="absolute value")],
        author="human",
        validated=True,
    )


@pytest.fixture
def store(fixture_corpus_dir: str) -> CorpusStore:
    return CorpusStore(fixture_corpus_dir)


def test_context_within_budget(store: CorpusStore) -> None:
    runner = get_runner("naive_rag_vector")
    result = runner.build_context(_task([_URL]), budget_tokens=50, store=store)
    assert result.context_tokens <= 50
    assert result.text  # non-empty for a real doc
    assert _URL in result.used_urls


def test_context_is_deterministic(store: CorpusStore) -> None:
    runner = get_runner("naive_rag_vector")
    first = runner.build_context(_task([_URL]), budget_tokens=120, store=store)
    second = runner.build_context(_task([_URL]), budget_tokens=120, store=store)
    assert first == second


def test_hybrid_and_vector_only_both_non_empty(store: CorpusStore) -> None:
    hybrid = NaiveRagVectorRunner()  # default: hybrid BM25+vector
    vector_only = NaiveRagVectorRunner(name="naive_rag_vector_only", vector_only=True)
    task = _task([_URL])
    hybrid_result = hybrid.build_context(task, budget_tokens=200, store=store)
    vector_result = vector_only.build_context(task, budget_tokens=200, store=store)
    assert hybrid_result.text
    assert vector_result.text
    assert _URL in hybrid_result.used_urls
    assert _URL in vector_result.used_urls


def test_relevant_url_is_used(store: CorpusStore) -> None:
    # Two URLs; the question is about abs, so the python doc must contribute.
    runner = get_runner("naive_rag_vector")
    result = runner.build_context(
        _task([_URL, _FLASK_URL]), budget_tokens=200, store=store
    )
    assert _URL in result.used_urls


def test_missing_url_is_ignored(store: CorpusStore) -> None:
    runner = get_runner("naive_rag_vector")
    result = runner.build_context(
        _task(["https://example.com/not-in-corpus"]), budget_tokens=100, store=store
    )
    assert result.text == ""
    assert result.used_urls == []


def test_emits_url_provenance_header(store: CorpusStore) -> None:
    runner = get_runner("naive_rag_vector")
    result = runner.build_context(_task([_URL]), budget_tokens=200, store=store)
    assert result.text.startswith(f"[URL] {_URL}\n")


def test_runner_registered() -> None:
    assert "naive_rag_vector" in available_runners()
    assert isinstance(get_runner("naive_rag_vector"), NaiveRagVectorRunner)
