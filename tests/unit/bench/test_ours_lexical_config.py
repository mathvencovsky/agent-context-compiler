"""The ``ours`` runner and the product share the M10b lexical stage.

The default ``LexicalConfig`` must give exactly the ranking ``overlap_ranks``
gave before M10b; the opt-in configs must reach the fusion. Offline (fake
providers, fixture corpus).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.retrieve.lexical import LexicalConfig, overlap_ranks
from bench.corpus_store import CorpusStore
from bench.runners.ours import OursRunner, _lexical_ranks, lexical_result
from bench.tasks_schema import Task, load_tasks

_FIXTURE_BENCH = Path(__file__).resolve().parents[2] / "fixtures" / "bench"


@pytest.fixture(scope="module")
def store() -> CorpusStore:
    return CorpusStore(_FIXTURE_BENCH / "corpus")


@pytest.fixture(scope="module")
def tasks() -> list[Task]:
    return load_tasks(_FIXTURE_BENCH / "tasks.yaml")


def _order(runner: OursRunner, task: Task, store: CorpusStore) -> list[tuple]:  # type: ignore[type-arg]
    prepared = runner.prepare_candidates(task, store=store)
    return [(c.document_id, c.ord, round(c.value, 9)) for c in prepared.candidates]


def test_default_lexical_stage_is_overlap_ranks(
    store: CorpusStore, tasks: list[Task]
) -> None:
    runner = OursRunner()
    for task in tasks:
        indexed, _ = runner._index_task(task, store)
        expected = overlap_ranks(
            ((item.key, item.chunk.text) for item in indexed), task.question, 50
        )
        assert _lexical_ranks(indexed, task.question) == expected
        assert lexical_result(indexed, task.question).page_rank == {}


def test_explicit_default_config_changes_nothing(
    store: CorpusStore, tasks: list[Task]
) -> None:
    for task in tasks:
        assert _order(OursRunner(), task, store) == _order(
            OursRunner(lexical=LexicalConfig()), task, store
        )


def test_lexical_config_reaches_the_shared_stage(
    store: CorpusStore, tasks: list[Task], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner hands its config and the task URLs to ``rank_lexical``.

    (The fixture pages have two chunks each, too few for the scorers to reorder;
    the scorers themselves are covered in ``tests/unit/retrieve``.)
    """
    import bench.runners.ours as ours

    seen: list[tuple[LexicalConfig, dict[str, str]]] = []
    real = ours.rank_lexical

    def spy(items, query, config=None, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((config, dict(kwargs["url_by_document"])))
        return real(items, query, config, **kwargs)

    monkeypatch.setattr(ours, "rank_lexical", spy)
    config = LexicalConfig(scorer="bm25f", identifiers=True, title_weight=5.0)
    task = tasks[0]
    OursRunner(lexical=config).prepare_candidates(task, store=store)
    assert seen[0][0] == config
    assert sorted(seen[0][1].values()) == sorted(task.urls)


def test_page_prior_rrf_reaches_the_fusion(
    store: CorpusStore, tasks: list[Task], monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.runners.ours as ours

    seen: list[tuple[dict[str, int], float]] = []
    real = ours.apply_page_rrf

    def spy(fused, page_rank, *, weight, k):  # type: ignore[no-untyped-def]
        seen.append((dict(page_rank), weight))
        return real(fused, page_rank, weight=weight, k=k)

    monkeypatch.setattr(ours, "apply_page_rrf", spy)
    task = tasks[0]
    OursRunner().prepare_candidates(task, store=store)
    OursRunner(
        lexical=LexicalConfig(page_prior="rrf", page_prior_weight=0.25)
    ).prepare_candidates(task, store=store)
    assert seen[0] == ({}, 0.5)
    assert seen[1][1] == 0.25
    assert list(seen[1][0].values()) == [1]  # the task's single page, ranked 1st


def test_runner_embeds_the_query_with_embed_query(
    store: CorpusStore, tasks: list[Task]
) -> None:
    class Spy(FakeEmbeddingProvider):
        queries: list[str] = []

        def embed_query(self, text: str) -> list[float]:
            self.queries.append(text)
            return super().embed_query(text)

    spy = Spy()
    OursRunner(embeddings=spy).prepare_candidates(tasks[0], store=store)
    assert spy.queries == [tasks[0].question]
