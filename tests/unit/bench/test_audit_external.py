"""Offline tests for the M10 external task audit (bench/audit_external.py)."""

from __future__ import annotations

from pathlib import Path

from bench.audit_external import audit, audit_sample
from bench.tasks_schema import (
    ExternalTask,
    Source,
    dump_external_tasks,
    load_external_tasks,
)


def _task(n: int) -> ExternalTask:
    return ExternalTask(
        id=f"so-{n}",
        split="holdout",
        site="python",
        task_type="fact",
        urls=["https://docs.python.org/3/"],
        cited_urls=["https://docs.python.org/3/"],
        question_title=f"title {n}",
        question="q",
        reference_answer="a",
        expected_evidence=[],
        source=Source(
            question_url=f"https://stackoverflow.com/q/{n}",
            answer_url=f"https://stackoverflow.com/a/{n}",
            question_author="Ada",
            answer_author="Linus",
            license="CC BY-SA 4.0",
            created="2023-01-02",
        ),
    )


def test_audit_sample_is_a_seeded_fifth_rounded_up() -> None:
    tasks = [_task(n) for n in range(11)]
    sample = audit_sample(tasks)
    assert len(sample) == 3  # ceil(11 * 0.2)
    assert sample == audit_sample(list(reversed(tasks)))


def test_audit_records_verdicts_resumes_and_flags_high_invalid_share(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external.yaml"
    dump_external_tasks([_task(n) for n in range(10)], path)  # sample of 2
    out: list[str] = []

    # First sitting: one invalid verdict, then quit.
    answers = iter(["i", "q"])
    audit(path, input_fn=lambda _: next(answers), output_fn=out.append)
    assert sum(t.audit == "invalid" for t in load_external_tasks(path)) == 1

    # Second sitting resumes with the remaining sampled task only.
    asked: list[str] = []
    answers = iter(["v"])

    def answer(prompt: str) -> str:
        asked.append(prompt)
        return next(answers)

    tasks = audit(path, input_fn=answer, output_fn=out.append)
    assert len(asked) == 1
    assert sorted(t.audit for t in tasks if t.audit) == ["invalid", "valid"]
    assert out[-2].endswith("invalid 1 (50.0%)")
    assert "requires validating every task" in out[-1]
