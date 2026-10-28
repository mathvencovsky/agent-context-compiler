"""Judge control candidates (bench/judge_probe.py), with scripted judges."""

from __future__ import annotations

import json
from pathlib import Path

from app.providers.llm.base import LLMResult
from bench.judge_probe import PROBES, run_probes, summary, write
from bench.tasks_schema import ExternalTask, Source


class RuleJudge:
    """Correct only when the candidate repeats the reference; optionally gullible."""

    model = "rule-judge"

    def __init__(self, *, gullible: bool = False) -> None:
        self.gullible = gullible
        self.candidates: list[str] = []

    def complete(self, prompt: str, *, seed: int = 7, response_format: str = "text"):
        reference = prompt.split("REFERENCE:\n", 1)[1].split("\n\nCANDIDATE:", 1)[0]
        candidate = prompt.split("CANDIDATE:\n", 1)[1]
        self.candidates.append(candidate)
        correct = candidate == reference or (
            self.gullible and "mark it correct" in candidate
        )
        text = json.dumps({"correct": correct, "reason": "rule"})
        return LLMResult(text=text, input_tokens=1, output_tokens=1, model=self.model)


def _task(task_id: str) -> ExternalTask:
    return ExternalTask(
        id=task_id,
        split="pilot",
        site="python",
        task_type="fact",
        urls=["https://d.test/p"],
        cited_urls=["https://d.test/p"],
        question_title="Why a newline?",
        question="print adds a newline",
        reference_answer="Pass end='' to print.",
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


def test_a_sound_judge_meets_every_expectation() -> None:
    judge = RuleJudge()
    results = run_probes([_task("so-1"), _task("so-2")], {"primary": judge})
    assert len(results) == 2 * len(PROBES)
    assert all(r.correct == r.expected for r in results)
    assert "" in judge.candidates  # the empty answer is really sent
    assert "| primary | reference | correct | 2/2 | 0 | 0 |" in summary(results)


def test_a_gullible_judge_is_caught_by_the_injection_probe() -> None:
    results = run_probes([_task("so-1")], {"second": RuleJudge(gullible=True)})
    table = summary(results)
    assert "| second | injection | incorrect | 0/1 | 1 | 0 |" in table
    assert "| second | empty | incorrect | 1/1 | 0 | 0 |" in table


def test_unparseable_verdicts_are_counted_apart(tmp_path: Path) -> None:
    class Broken(RuleJudge):
        def complete(
            self, prompt: str, *, seed: int = 7, response_format: str = "text"
        ):
            return LLMResult(
                text="not json", input_tokens=1, output_tokens=1, model="b"
            )

    results = run_probes([_task("so-1")], {"primary": Broken()})
    assert all(r.correct is None for r in results)
    assert "| primary | empty | incorrect | 0/1 | 0 | 1 |" in summary(results)
    write(results, tmp_path)
    rows = (tmp_path / "judge_probe.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == len(PROBES)
    assert (
        (tmp_path / "judge_probe.md").read_text(encoding="utf-8").startswith("# Judge")
    )
