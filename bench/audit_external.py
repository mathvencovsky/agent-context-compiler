"""Human audit of a random 20% of the M10 external tasks (PREREG §2.1).

The sample is drawn with the PREREG seed, so every run asks about the same
tasks. For each sampled task not yet audited, the auditor reads the question,
the accepted answer and the cited pages, then marks it valid (``v``) or invalid
(``i``) against the PREREG Appendix B checklist; ``s`` skips it for now and
``q`` stops. Each verdict is written to ``bench/tasks/external.yaml`` at once,
so an audit can stop and resume. If more than 10% of the audited tasks are
invalid, PREREG §2.1 requires validating every task before the main run.

I/O is injected like :mod:`bench.review_tasks`, so tests need no TTY.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from pathlib import Path

from bench.external_tasks import SEED
from bench.tasks_schema import ExternalTask, dump_external_tasks, load_external_tasks

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

#: PREREG §2.1: share of tasks the human audits, and the invalid share above
#: which every task must be validated.
AUDIT_FRACTION = 0.20
INVALID_LIMIT = 0.10

#: PREREG Appendix B: a task is invalid if any of these holds.
CHECKLIST = (
    "needs an image, a private repo, or a linked page other than the docs",
    "the accepted answer is wrong or obsolete for the current cited page",
    "the cited pages lack the information the accepted answer relies on",
    "it duplicates another task",
)


def audit_sample(tasks: Sequence[ExternalTask], seed: int = SEED) -> set[str]:
    """Ids of the seeded random 20% of ``tasks`` (rounded up)."""
    ids = sorted(task.id for task in tasks)
    return set(random.Random(seed).sample(ids, math.ceil(len(ids) * AUDIT_FRACTION)))


def _show(task: ExternalTask, output_fn: OutputFn) -> None:
    output_fn(
        f"\n=== {task.id} [{task.site}/{task.task_type}] {task.source.question_url}"
    )
    output_fn(f"Q: {task.question_title}\n{task.question}")
    output_fn(f"\nACCEPTED ANSWER:\n{task.reference_answer}")
    output_fn("\nCITED PAGES: " + ", ".join(task.cited_urls))
    for evidence in task.expected_evidence:
        output_fn(f"  quoted from {evidence.url}: {evidence.quote}")
    output_fn("INVALID IF: " + "; ".join(CHECKLIST))


def audit(
    tasks_path: str | Path, *, input_fn: InputFn, output_fn: OutputFn
) -> list[ExternalTask]:
    """Ask for a verdict on each unaudited sampled task; return all tasks."""
    tasks = load_external_tasks(tasks_path)
    sample = audit_sample(tasks)
    for i, task in enumerate(tasks):
        if task.id not in sample or task.audit is not None:
            continue
        _show(task, output_fn)
        choice = input_fn("valid / invalid / skip / quit [v/i/s/q]? ").strip().lower()
        if choice.startswith("q"):
            break
        if choice[:1] in ("v", "i"):
            verdict = "valid" if choice.startswith("v") else "invalid"
            tasks[i] = task.model_copy(update={"audit": verdict})
            dump_external_tasks(tasks, tasks_path)

    audited = [t for t in tasks if t.audit is not None]
    invalid = sum(1 for t in audited if t.audit == "invalid")
    share = invalid / len(audited) if audited else 0.0
    output_fn(
        f"\naudited {len(audited)}/{len(sample)}; invalid {invalid} ({share:.1%})"
    )
    if share > INVALID_LIMIT:
        output_fn("More than 10% invalid: PREREG §2.1 requires validating every task.")
    return tasks


def main() -> None:  # pragma: no cover - human entry point (real TTY).
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "tasks", nargs="?", default="bench/tasks/external.yaml", help="external.yaml"
    )
    args = parser.parse_args()
    audit(args.tasks, input_fn=input, output_fn=print)


if __name__ == "__main__":
    main()
