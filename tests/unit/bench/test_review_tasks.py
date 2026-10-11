"""Review flow with injected I/O: accept, edit, discard — written back to the file."""

from __future__ import annotations

from pathlib import Path

from bench.corpus_store import CorpusStore
from bench.review_tasks import review
from bench.tasks_schema import Evidence, Task, dump_tasks, load_tasks

_API_URL = "https://docs.python.org/3/library/functions.html"


def _draft(task_id: str) -> Task:
    return Task(
        id=task_id,
        category="api_reference",
        split="dev",
        urls=[_API_URL],
        question=f"question {task_id}?",
        answer_type="judge",
        expected=["something"],
        expected_evidence=[Evidence(url=_API_URL, quote="absolute value")],
        author="ai_draft",
        validated=False,
    )


def _scripted_input(answers: list[str]) -> object:
    it = iter(answers)

    def _input(_prompt: str) -> str:
        return next(it)

    return _input


def test_accept_marks_validated(store: CorpusStore, tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    dump_tasks([_draft("t1")], path)

    outputs: list[str] = []
    kept = review(
        path,
        store,
        input_fn=_scripted_input(["a"]),  # accept
        output_fn=outputs.append,
    )

    assert len(kept) == 1
    assert kept[0].validated is True
    assert kept[0].author == "ai_draft"  # accept does not change authorship
    reloaded = load_tasks(path)
    assert reloaded[0].validated is True


def test_edit_sets_author_human_and_validated(
    store: CorpusStore, tmp_path: Path
) -> None:
    path = tmp_path / "tasks.yaml"
    dump_tasks([_draft("t1")], path)

    kept = review(
        path,
        store,
        input_fn=_scripted_input(["e", "a sharper question?", "the absolute value"]),
        output_fn=lambda _s: None,
    )

    assert kept[0].validated is True
    assert kept[0].author == "human"
    assert kept[0].question == "a sharper question?"
    assert kept[0].expected == ["the absolute value"]
    assert load_tasks(path)[0].author == "human"


def test_discard_removes_task(store: CorpusStore, tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    dump_tasks([_draft("t1"), _draft("t2")], path)

    kept = review(
        path,
        store,
        input_fn=_scripted_input(["d", "a"]),  # discard t1, accept t2
        output_fn=lambda _s: None,
    )

    assert [t.id for t in kept] == ["t2"]
    assert [t.id for t in load_tasks(path)] == ["t2"]


def test_already_validated_untouched(store: CorpusStore, tmp_path: Path) -> None:
    validated = _draft("t1").model_copy(update={"validated": True, "author": "human"})
    path = tmp_path / "tasks.yaml"
    dump_tasks([validated], path)

    def _input(_prompt: str) -> str:
        raise AssertionError("validated tasks must not prompt the reviewer")

    kept = review(path, store, input_fn=_input, output_fn=lambda _s: None)
    assert kept == [validated]
