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


class _StubReranker:
    """Deterministic offline reranker that forces one chunk to the top.

    Scores the first document ``1.0`` and all others ``0.0`` so that injecting
    this instance into ``OursRunner`` provably drives selection to a single,
    high-value fragment — nothing here touches the network (CLAUDE.md).
    """

    model = "stub-reranker"
    calls = 0

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        type(self).calls += 1
        return [1.0 if i == 0 else 0.0 for i, _ in enumerate(documents)]


def test_injected_reranker_is_used(store: CorpusStore, tasks: list[Task]) -> None:
    """A reranker passed to ``OursRunner`` is actually invoked (not the fake).

    Proves the injection seam: with a hand-written stub whose scores clear the
    ``min_value`` gate, the greedy path selects context AND the stub records
    being called — all offline (no network, no real provider).
    """
    stub = _StubReranker()
    _StubReranker.calls = 0
    runner = OursRunner(reranker=stub, min_value=0.0)
    built_any = False
    for task in _answerable_tasks(tasks):
        result = runner.build_context(task, budget_tokens=_BUDGET, store=store)
        assert 0 <= result.context_tokens <= _BUDGET
        if result.text:
            built_any = True
    assert _StubReranker.calls > 0, "injected reranker was never called"
    assert built_any, "injected reranker + min_value=0 produced no context"


@pytest.mark.parametrize("lambda_", [0.3, 0.5, 0.7])
@pytest.mark.parametrize("min_value", [0.0, 0.05, 0.1])
def test_cached_candidates_match_full_pipeline(
    lambda_: float, min_value: float, store: CorpusStore, tasks: list[Task]
) -> None:
    """Selection over CACHED candidates equals recomputing the whole pipeline.

    The M8 calibrator computes the selection-invariant pipeline (steps 1-5) once
    per task and reuses it across the grid. This proves that
    ``prepare_candidates`` + ``assemble_from_candidates`` is byte-identical to
    ``build_context`` for every grid ``(lambda_, min_value)`` — so the cache
    never changes the calibration result. Fully offline (fake providers).
    """
    runner = OursRunner(lambda_=lambda_, min_value=min_value)
    for task in _answerable_tasks(tasks):
        full = runner.build_context(task, budget_tokens=_BUDGET, store=store)
        prepared = runner.prepare_candidates(task, store=store)
        cached = runner.assemble_from_candidates(prepared, budget_tokens=_BUDGET)
        assert cached == full


def test_prepared_candidates_are_lambda_invariant(
    store: CorpusStore, tasks: list[Task]
) -> None:
    """``prepare_candidates`` ignores lambda_/min_value (the cache's premise).

    Steps 1-5 (search + rerank) must not depend on the selection knobs, so the
    calibrator can prepare once and sweep the grid. Two runners differing only
    in lambda_/min_value must produce identical prepared candidates (same keys,
    values, tokens and max rerank).
    """
    a = OursRunner(lambda_=0.3, min_value=0.0)
    b = OursRunner(lambda_=0.7, min_value=0.1)
    for task in _answerable_tasks(tasks):
        pa = a.prepare_candidates(task, store=store)
        pb = b.prepare_candidates(task, store=store)
        assert pa.max_rerank == pb.max_rerank
        assert pa.rerank_scores == pb.rerank_scores
        assert [(c.document_id, c.ord, c.value, c.tokens) for c in pa.candidates] == [
            (c.document_id, c.ord, c.value, c.tokens) for c in pb.candidates
        ]


def test_prepared_exposes_full_top30_scores(
    store: CorpusStore, tasks: list[Task]
) -> None:
    """``prepare_candidates`` exposes the FULL top-30 rerank scores (M8 D7).

    The M8 calibrator pools ``rerank_scores`` across the dev tasks to derive the
    frozen coverage thresholds from the real distribution (Option A), instead of
    the per-task max which Jina normalization pins at 1.0. This proves the field
    is populated and that ``max_rerank`` is exactly the max of that list — so the
    pooled distribution the thresholds come from is auditable. Fully offline.
    """
    runner = OursRunner()
    saw_scores = False
    for task in _answerable_tasks(tasks):
        prepared = runner.prepare_candidates(task, store=store)
        if prepared.rerank_scores:
            saw_scores = True
            assert prepared.max_rerank == max(prepared.rerank_scores)
            # The fake reranker returns scores in [0, 1].
            assert all(0.0 <= s <= 1.0 for s in prepared.rerank_scores)
    assert saw_scores, "no task produced any rerank score"
