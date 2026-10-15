"""Offline tests for the M10 external task audit (bench/audit_external.py)."""

from __future__ import annotations

from pathlib import Path

from bench.audit_external import audit, in_audit_sample
from bench.tasks_schema import (
    ExternalTask,
    Source,
    dump_external_tasks,
    load_external_tasks,
)


def _task(task_id: str) -> ExternalTask:
    return ExternalTask(
        id=task_id,
        split="holdout",
        site="python",
        task_type="fact",
        urls=["https://docs.python.org/3/"],
        cited_urls=["https://docs.python.org/3/"],
        question_title=f"title {task_id}",
        question="q",
        reference_answer="a",
        expected_evidence=[],
        source=Source(
            question_url="https://stackoverflow.com/q/1",
            answer_url="https://stackoverflow.com/a/2",
            question_author="Ada",
            answer_author="Linus",
            license="CC BY-SA 4.0",
            created="2023-01-02",
        ),
    )


def test_in_audit_sample_is_per_task_seeded_and_about_a_fifth() -> None:
    ids = [f"so-{n}" for n in range(2000)]
    share = sum(in_audit_sample(i) for i in ids) / len(ids)
    assert 0.17 < share < 0.23
    assert [in_audit_sample(i) for i in ids] == [in_audit_sample(i) for i in ids]
    assert [in_audit_sample(i, seed=1) for i in ids] != [
        in_audit_sample(i) for i in ids
    ]


def test_audit_asks_only_sampled_population_tasks_and_resumes(tmp_path: Path) -> None:
    ids = [f"so-{n}" for n in range(100)]
    sampled = [i for i in ids if in_audit_sample(i)][:3]
    unsampled = [i for i in ids if not in_audit_sample(i)][:2]
    population = set(sampled[:2] + unsampled)  # sampled[2] failed no screen
    path = tmp_path / "external.yaml"
    dump_external_tasks([_task(i) for i in sampled + unsampled], path)
    out: list[str] = []

    # First sitting: one invalid verdict, then quit.
    answers = iter(["i", "q"])
    audit(path, population, input_fn=lambda _: next(answers), output_fn=out.append)
    assert [t.audit for t in load_external_tasks(path)].count("invalid") == 1

    # Second sitting resumes with the one remaining sampled population task.
    asked: list[str] = []

    def answer(prompt: str) -> str:
        asked.append(prompt)
        return "v"

    tasks = audit(path, population, input_fn=answer, output_fn=out.append)
    assert len(asked) == 1
    assert {t.id: t.audit for t in tasks if t.audit} == {
        sampled[0]: "invalid",
        sampled[1]: "valid",
    }
    assert out[-2].endswith("audited 2/2; invalid 1 (50.0%)")
    assert "validate every task" in out[-1]
