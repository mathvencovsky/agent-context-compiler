"""Baseline runners over the fixture corpus: bounded, deterministic, offline.

Proves the three baselines (``raw_html``, ``naive_text``, ``naive_rag``) build a
context that respects the token budget and is byte-identical across calls, and
that ``firecrawl`` is absent without a key (SPEC §10.3). No network is used.
"""

from __future__ import annotations

import pytest

from bench.corpus_store import CorpusStore
from bench.runners.base import available_runners, get_runner, truncate_to_budget
from bench.tasks_schema import Evidence, Task

_URL = "https://docs.python.org/3/library/functions.html#abs"


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


@pytest.mark.parametrize("strategy", ["raw_html", "naive_text", "naive_rag"])
def test_context_within_budget(strategy: str, store: CorpusStore) -> None:
    runner = get_runner(strategy)
    task = _task([_URL])
    result = runner.build_context(task, budget_tokens=50, store=store)
    assert result.context_tokens <= 50
    assert result.text  # non-empty for a real doc
    assert _URL in result.used_urls


@pytest.mark.parametrize("strategy", ["raw_html", "naive_text", "naive_rag"])
def test_context_is_deterministic(strategy: str, store: CorpusStore) -> None:
    runner = get_runner(strategy)
    task = _task([_URL])
    first = runner.build_context(task, budget_tokens=120, store=store)
    second = runner.build_context(task, budget_tokens=120, store=store)
    assert first == second


def test_naive_text_removes_html_tags(store: CorpusStore) -> None:
    runner = get_runner("naive_text")
    result = runner.build_context(_task([_URL]), budget_tokens=500, store=store)
    assert "<main>" not in result.text
    assert "absolute value" in result.text


def test_raw_html_keeps_markup(store: CorpusStore) -> None:
    runner = get_runner("raw_html")
    result = runner.build_context(_task([_URL]), budget_tokens=500, store=store)
    assert "<main>" in result.text


def test_naive_rag_ranks_relevant_url_first(store: CorpusStore) -> None:
    # Two URLs; the question is about abs, so the python doc must contribute.
    flask_url = "https://flask.palletsprojects.com/en/stable/quickstart/"
    runner = get_runner("naive_rag")
    result = runner.build_context(
        _task([_URL, flask_url]), budget_tokens=120, store=store
    )
    assert _URL in result.used_urls


def test_missing_url_is_ignored(store: CorpusStore) -> None:
    runner = get_runner("naive_text")
    result = runner.build_context(
        _task(["https://example.com/not-in-corpus"]), budget_tokens=100, store=store
    )
    assert result.text == ""
    assert result.used_urls == []


def test_truncate_to_budget_zero() -> None:
    assert truncate_to_budget("hello world", 0) == ("", 0)


def test_firecrawl_absent_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    # Force a fresh registry so the env change is observed.
    import bench.runners.base as base

    base._REGISTRY.clear()
    names = available_runners()
    assert "firecrawl" not in names
    assert {"raw_html", "naive_text", "naive_rag"} <= set(names)
    with pytest.raises(KeyError):
        get_runner("firecrawl")
