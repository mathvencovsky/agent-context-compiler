"""Human audit of about 20% of the M10 primary population (PREREG §2.1, Amendment 2).

The audit runs after the doc-dependence screen, on the tasks that failed it
(the primary population), passed in as a file of task ids. A task is in the
sample when a seeded hash of its id falls in the lowest 20%, so the selection
stays the same when replacement tasks join the population.

For each sampled task not yet audited, the auditor reads the question, the
accepted answer and the cited pages, then marks it valid (``v``) or invalid
(``i``) against the PREREG Appendix B checklist; ``s`` skips it for now and
``q`` stops. Each verdict is written to ``bench/tasks/external.yaml`` at once,
so an audit can stop and resume. If more than 10% of the audited tasks are
invalid, every task in the primary population must be validated before the
main run.

I/O is injected like :mod:`bench.review_tasks`, so tests need no TTY.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from bench.external_tasks import SEED
from bench.tasks_schema import ExternalTask, dump_external_tasks, load_external_tasks

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

#: PREREG Amendment 2: share of tasks audited, and the invalid share above which
#: every task in the primary population must be validated.
AUDIT_FRACTION = 0.20
INVALID_LIMIT = 0.10

#: PREREG Appendix B: a task is invalid if any of these holds.
CHECKLIST = (
    "needs an image, a private repo, or a linked page other than the docs",
    "the accepted answer is wrong or obsolete for the current cited page",
    "the cited pages lack the information the accepted answer relies on",
    "it duplicates another task",
)


def in_audit_sample(task_id: str, seed: int = SEED) -> bool:
    """Whether the seeded hash of ``task_id`` falls in the lowest 20%."""
    digest = hashlib.sha256(f"{seed}:{task_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") < AUDIT_FRACTION * 2**64


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
    tasks_path: str | Path,
    population: set[str],
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> list[ExternalTask]:
    """Ask for a verdict on each unaudited sampled task; return all tasks."""
    tasks = load_external_tasks(tasks_path)
    sample = {t.id for t in tasks if t.id in population and in_audit_sample(t.id)}
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

    audited = [t for t in tasks if t.id in sample and t.audit is not None]
    invalid = sum(1 for t in audited if t.audit == "invalid")
    share = invalid / len(audited) if audited else 0.0
    output_fn(
        f"\naudited {len(audited)}/{len(sample)}; invalid {invalid} ({share:.1%})"
    )
    if share > INVALID_LIMIT:
        output_fn(
            "More than 10% invalid: validate every task in the primary population."
        )
    return tasks


def main() -> None:  # pragma: no cover - human entry point (real TTY).
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "population", help="file with one primary-population id per line"
    )
    parser.add_argument("--tasks", default="bench/tasks/external.yaml")
    args = parser.parse_args()
    ids = Path(args.population).read_text(encoding="utf-8").split()
    audit(args.tasks, set(ids), input_fn=input, output_fn=print)


if __name__ == "__main__":
    main()
