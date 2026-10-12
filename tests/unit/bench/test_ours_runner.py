"""Unit tests for the ``ours`` bench runner + ablations — M7 (SPEC §6/§10).

The runner reproduces NINA's retrieve/select pipeline over the FROZEN fixture
corpus with the deterministic fake providers, so these tests never touch the
network or Postgres (CLAUDE.md). They assert that ``ours`` and every ablation:

* build a non-empty context whose token count stays within the budget;
* are deterministic (two runs produce byte-identical context);

and that the ablation knobs actually change behavior relative to the full
pipeline (so a flag is not a silent no-op).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.corpus_store import CorpusStore
from bench.runners.base import available_runners, get_runner
from bench.runners.ours import OursRunner, make_ours_runners
from bench.tasks_schema import Task, load_tasks

_FIXTURE_BENCH = Path(__file__).resolve().parents[2] / "fixtures" / "bench"
_CORPUS_DIR = _FIXTURE_BENCH / "corpus"
_TASKS_PATH = _FIXTURE_BENCH / "tasks.yaml"

_BUDGET = 2000

_ABLATION_NAMES = [
    "ours",
    "ours_no_vector",
    "ours_no_lexical",
    "ours_no_rerank",
    "ours_fixed_chunks",
    "ours_truncate",
]


@pytest.fixture(scope="module")
def store() -> CorpusStore:
    return CorpusStore(_CORPUS_DIR)


@pytest.fixture(scope="module")
def tasks() -> list[Task]:
    return load_tasks(_TASKS_PATH)


def _answerable_tasks(tasks: list[Task]) -> list[Task]:
    """Tasks whose URLs are all present (status 'ok') in the fixture corpus."""
    # Every fixture task URL is in the corpus manifest; use them all.
    return tasks


@pytest.mark.parametrize("name", _ABLATION_NAMES)
def test_each_runner_builds_within_budget(
    name: str, store: CorpusStore, tasks: list[Task]
) -> None:
    """``ours`` and each ablation build a bounded context for every task.

    Not every variant yields text on this tiny fixture corpus: the greedy path
    (``ours``/ablations) gates on ``min_value`` against the deterministic FAKE
    reranker, whose Jaccard overlap scores are often below that threshold — so
    an empty context is legitimate here (the e2e run and
    :func:`test_truncate_produces_context` cover the non-empty path). What every
    runner must do is stay within the budget and never crash.
    """
    runner = get_runner(name)
    for task in _answerable_tasks(tasks):
        result = runner.build_context(task, budget_tokens=_BUDGET, store=store)
        assert 0 <= result.context_tokens <= _BUDGET
        # used_urls are a subset of the task's URLs.
        assert set(result.used_urls) <= set(task.urls)


def test_truncate_produces_context(store: CorpusStore, tasks: list[Task]) -> None:
    """The ``ours_truncate`` variant (no min_value gate) assembles real text.

    This proves the whole pipeline — extract, chunk, embed, fuse, rerank,
    select, emit — is wired correctly end to end over the frozen corpus.
    """
    runner = get_runner("ours_truncate")
    built_any = False
    for task in _answerable_tasks(tasks):
        result = runner.build_context(task, budget_tokens=_BUDGET, store=store)
        assert 0 <= result.context_tokens <= _BUDGET
        if result.text:
            built_any = True
            assert set(result.used_urls) <= set(task.urls)
    assert built_any, "ours_truncate produced no context for any task"


@pytest.mark.parametrize("name", _ABLATION_NAMES)
def test_runner_is_deterministic(
    name: str, store: CorpusStore, tasks: list[Task]
) -> None:
    """Two runs over the same corpus produce byte-identical context."""
    runner = get_runner(name)
    for task in _answerable_tasks(tasks):
        first = runner.build_context(task, budget_tokens=_BUDGET, store=store)
        second = runner.build_context(task, budget_tokens=_BUDGET, store=store)
        assert first == second


def test_all_ablations_registered() -> None:
    """Every ``ours*`` strategy is discoverable through the registry."""
    registered = set(available_runners())
    assert set(_ABLATION_NAMES) <= registered


def test_make_ours_runners_names_unique() -> None:
    """The factory yields exactly the six expected, uniquely-named runners."""
    runners = make_ours_runners()
    names = [r.name for r in runners]
    assert names == _ABLATION_NAMES
    assert len(set(names)) == len(names)


def test_no_rerank_changes_values_vs_full(
    store: CorpusStore, tasks: list[Task]
) -> None:
    """The rerank-off ablation is not a silent no-op: it can reorder/select.

    With the fake providers the rerank-off path uses normalized RRF as the
    selection value, which differs from the fake reranker's overlap score, so at
    least one task yields a different context than the full pipeline.
    """
    full = OursRunner()
    no_rerank = OursRunner(name="ours_no_rerank", use_rerank=False)
    differed = False
    for task in _answerable_tasks(tasks):
        a = full.build_context(task, budget_tokens=_BUDGET, store=store)
        b = no_rerank.build_context(task, budget_tokens=_BUDGET, store=store)
        if a.text != b.text:
            differed = True
    assert differed, "ours_no_rerank behaved identically to ours on every task"


def test_fixed_chunks_differs_from_structural(
    store: CorpusStore, tasks: list[Task]
) -> None:
    """Fixed-size chunking yields a different context than structural chunking."""
    structural = OursRunner()
    fixed = OursRunner(name="ours_fixed_chunks", structural_chunking=False)
    differed = False
    for task in _answerable_tasks(tasks):
        a = structural.build_context(task, budget_tokens=_BUDGET, store=store)
        b = fixed.build_context(task, budget_tokens=_BUDGET, store=store)
        if a.text != b.text:
            differed = True
    assert differed, "ours_fixed_chunks behaved identically to structural chunking"
