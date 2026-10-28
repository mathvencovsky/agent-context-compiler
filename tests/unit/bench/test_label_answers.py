"""Offline tests for blind human labelling (bench/label_answers.py)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from bench.label_answers import _read, draw_sample, label, report, settle
from bench.tasks_schema import ExternalTask, Source

_ARMS = ["full_page", "strong_rag", "nina", "webfetch_live", "context7_live"]


def _task(task_id: str) -> ExternalTask:
    return ExternalTask(
        id=task_id,
        split="holdout",
        site="python",
        task_type="fact",
        urls=["https://d.test/p"],
        cited_urls=["https://d.test/p"],
        question_title="Why a newline?",
        question="print adds a newline",
        reference_answer="Pass end=''.",
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


def _row(task: str, arm: str, verdict: bool | None, item: int = 0) -> dict:
    return {
        "item": item,
        "task_id": task,
        "arm": arm,
        "answer": f"answer from {arm}",
        "judge_correct": verdict,
    }


def test_draw_sample_shares_a_short_stratum_and_drops_excluded() -> None:
    rows = [
        {k: v for k, v in _row(f"so-{n}", arm, v).items() if k != "item"}
        for arm in _ARMS
        for v in (True, False)
        for n in range(3 if (arm, v) == ("nina", False) else 30)
    ]
    rows.append({k: v for k, v in _row("so-x", "nina", None).items() if k != "item"})
    sample = draw_sample(rows)

    assert [r["item"] for r in sample] == list(range(1, 101))
    strata = Counter((r["arm"], r["judge_correct"]) for r in sample)
    assert strata[("nina", False)] == 3
    assert all(n >= 10 for key, n in strata.items() if key != ("nina", False))
    assert all(r["judge_correct"] is not None for r in sample)
    assert draw_sample(list(reversed(rows))) == sample


def test_label_hides_arm_and_verdict_and_resumes(tmp_path: Path) -> None:
    sample = [_row(f"so-{i}", "nina", True, item=i) for i in (1, 2, 3)]
    tasks = {r["task_id"]: _task(r["task_id"]) for r in sample}
    path = tmp_path / "labels_A.csv"
    shown: list[str] = []

    answers = iter(["c", "q"])
    label(sample, tasks, path, input_fn=lambda _: next(answers), output_fn=shown.append)
    answers = iter(["i", "s"])
    label(sample, tasks, path, input_fn=lambda _: next(answers), output_fn=shown.append)

    assert _read(path) == {1: "correct", 2: "incorrect"}
    screen = "\n".join(shown)
    # The candidate is shown, but nothing names its arm or the judge's verdict.
    assert "answer from [tool]" in screen and "nina" not in screen.lower()
    assert "arm" not in screen and "judge_correct" not in screen
    assert "Return a single JSON object" not in screen
    # Amendment 13: humans apply the same rubric without the judge-only lines.
    assert "several alternative core answers" in screen
    assert "abstained" not in screen and "Write your reasoning first" not in screen


def test_settle_asks_only_disagreements_and_report_applies_the_gate(
    tmp_path: Path,
) -> None:
    verdicts = [True, True, True, False]
    sample = [_row(f"so-{i}", "nina", v, item=i) for i, v in enumerate(verdicts, 1)]
    tasks = {r["task_id"]: _task(r["task_id"]) for r in sample}
    a = {1: "correct", 2: "incorrect", 3: "correct", 4: "incorrect"}
    b = {1: "correct", 2: "correct", 3: "correct", 4: "incorrect"}
    asked: list[str] = []

    def decide(prompt: str) -> str:
        asked.append(prompt)
        return "i"

    settled = settle(
        sample,
        tasks,
        a,
        b,
        tmp_path / "s.csv",
        input_fn=decide,
        output_fn=lambda _: None,
    )
    assert settled == {2: "incorrect"} and len(asked) == 1

    out: list[str] = []
    k_graders, k_judge = report(sample, a, b, settled, output_fn=out.append)
    assert (k_graders, k_judge) == (0.5, 0.5)
    assert out[-1].startswith("GATE: kappa below 0.6")
