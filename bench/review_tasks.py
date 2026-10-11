"""Human review of AI-drafted tasks (SPEC §10.2).

Draft tasks arrive ``validated=False`` (``bench/draft_tasks.py``). A human runs
:func:`review` to walk each unvalidated task and accept / edit / discard it:

* **accept** (``a``) — keep the task as-is and mark it ``validated=True``;
* **edit** (``e``) — replace the question (and optionally the expected answer),
  mark it ``validated=True`` and set ``author="human"`` (a human now owns it);
* **discard** (``d``) — drop the task from the set.

The reviewed file is written back with :func:`bench.tasks_schema.dump_tasks`
(stable field order). I/O is injected via ``input_fn``/``output_fn`` so the
tests drive the whole flow without a TTY; :func:`main` wires the real
``input``/``print``. Already-validated tasks are shown but left untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from bench.corpus_store import CorpusStore
from bench.tasks_schema import Evidence, Task, dump_tasks, load_tasks

#: Injected console seams. ``input_fn(prompt)`` reads one line; ``output_fn``
#: writes one line (both default to the builtins in :func:`main`).
InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

#: Characters of surrounding snapshot context shown around a located quote.
_CONTEXT_CHARS = 120


def _quote_context(store: CorpusStore, evidence: list[Evidence]) -> str:
    """Return a short snippet of snapshot context around the first quote.

    Best-effort: if the quote cannot be located (or the corpus lacks the URL),
    the quote text itself is returned so the reviewer still sees something.
    """
    if not evidence:
        return "(no evidence quote)"
    first = evidence[0]
    try:
        raw = store.raw_bytes(first.url)
    except (KeyError, ValueError):
        return first.quote
    text = raw.decode("utf-8", errors="replace")
    idx = text.find(first.quote)
    if idx < 0:
        return first.quote
    start = max(0, idx - _CONTEXT_CHARS)
    end = min(len(text), idx + len(first.quote) + _CONTEXT_CHARS)
    return text[start:end]


def _review_one(
    task: Task,
    store: CorpusStore,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> Task | None:
    """Review a single unvalidated task; return the kept task or ``None``.

    Returning ``None`` means the reviewer discarded the task.
    """
    output_fn(f"\nTask {task.id} [{task.category}/{task.split}]")
    output_fn(f"  Q: {task.question}")
    output_fn(f"  expected: {task.expected}")
    output_fn(f"  context: {_quote_context(store, task.expected_evidence)}")

    choice = input_fn("accept / edit / discard [a/e/d]? ").strip().lower()
    if choice.startswith("d"):
        return None
    if choice.startswith("e"):
        new_question = input_fn("new question (blank keeps current): ").strip()
        new_expected = input_fn("new expected (comma-sep, blank keeps): ").strip()
        question = new_question or task.question
        expected = (
            [part.strip() for part in new_expected.split(",") if part.strip()]
            if new_expected
            else task.expected
        )
        return task.model_copy(
            update={
                "question": question,
                "expected": expected,
                "author": "human",
                "validated": True,
            }
        )
    # Default / "accept": keep as-is, validated.
    return task.model_copy(update={"validated": True})


def review(
    tasks_path: str | Path,
    store: CorpusStore,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> list[Task]:
    """Review every unvalidated task in ``tasks_path`` and rewrite the file.

    Validated tasks are preserved untouched. For each ``validated=False`` task
    the reviewer accepts (``validated=True``), edits (``validated=True`` +
    ``author="human"``) or discards it. The surviving tasks are written back via
    :func:`dump_tasks` and returned.
    """
    tasks = load_tasks(tasks_path)
    kept: list[Task] = []
    for task in tasks:
        if task.validated:
            kept.append(task)
            continue
        reviewed = _review_one(task, store, input_fn=input_fn, output_fn=output_fn)
        if reviewed is not None:
            kept.append(reviewed)

    dump_tasks(kept, tasks_path)
    return kept


def main() -> None:  # pragma: no cover - human entry point (real TTY + corpus).
    """Human entry point: review ``bench/tasks/tasks.yaml`` against the corpus."""
    import argparse

    parser = argparse.ArgumentParser(description="Review drafted benchmark tasks.")
    parser.add_argument("tasks", help="path to tasks.yaml")
    parser.add_argument("corpus", help="path to the frozen corpus directory")
    args = parser.parse_args()

    store = CorpusStore(args.corpus)
    review(
        args.tasks,
        store,
        input_fn=input,
        output_fn=print,
    )


if __name__ == "__main__":
    main()
