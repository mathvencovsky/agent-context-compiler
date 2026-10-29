"""Blind human labels on M10 agent answers (PREREG §9, Amendment 5).

Input: ``bench/results/m10/answers.jsonl`` from the Phase 3 main run, one JSON
object per (task, arm) session: ``{"task_id", "arm", "answer",
"judge_correct"}``. ``judge_correct`` is the primary judge's verdict, and
``null`` marks an excluded session. Only primary-population answers go in; the
synthetic bridge slice keeps its own rubric and is never labelled.

1. ``sample`` draws 100 answers with equal quotas per arm x verdict stratum (10
   each for the five main-run arms). A short stratum's shortfall is shared
   among the others; the PREREG seed fixes the draw and the shuffled order. It
   is written once, to ``sample.jsonl``.
2. ``label --grader A`` (then ``B``) shows the rubric, the question, the
   accepted answer and the agent's answer, never the arm or the judge's
   verdict. Labels are saved as they are given, so a session can stop and
   resume.
3. ``settle`` shows only the items where A and B disagree, for the two graders
   to decide together.
4. ``report`` prints Cohen's kappa between the graders and between the primary
   judge and the settled labels, and the PREREG §9 gate. With ``answers.jsonl``
   present it adds the population view (research front F, L2): agreement
   weighted by stratum size, the judge's sensitivity and specificity per arm,
   and a human-corrected Δ as a sensitivity analysis.

I/O is injected like :mod:`bench.review_tasks`, so tests need no TTY.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.agreement import (
    KAPPA_GATE,
    CorrectedDelta,
    cohen_kappa,
    corrected_delta,
    judge_errors_by_arm,
    judge_is_reliable,
    weighted_agreement,
)
from bench.external_tasks import SEED
from bench.judge_external import JUDGE_ONLY_LINES, judge_prompt
from bench.tasks_schema import ExternalTask, load_external_tasks

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

#: PREREG §9: at least 100 human-labelled answers.
SAMPLE_SIZE = 100


def draw_sample(
    rows: Sequence[Mapping[str, Any]],
    *,
    size: int = SAMPLE_SIZE,
    seed: int = SEED,
    quotas: Mapping[tuple[str, bool], int] | None = None,
) -> list[dict[str, Any]]:
    """Equal quotas per (arm, verdict) stratum, shuffled and numbered from 1.

    ``quotas`` replaces the equal split with explicit per-stratum counts (no
    sharing of shortfalls), for a design that puts the labels where the
    headline depends on them (research front F, L2). The population report
    weights each stratum by its size either way.
    """
    rng = random.Random(seed)
    strata: dict[tuple[str, bool], list[Mapping[str, Any]]] = {}
    usable = (r for r in rows if r["judge_correct"] is not None)
    for row in sorted(usable, key=lambda r: (r["task_id"], r["arm"])):
        strata.setdefault((row["arm"], row["judge_correct"]), []).append(row)
    if not strata:
        return []
    keys = sorted(strata)
    for key in keys:
        rng.shuffle(strata[key])
    if quotas is not None:
        take = {k: min(quotas.get(k, 0), len(strata[k])) for k in keys}
    else:
        take = {k: min(size // len(keys), len(strata[k])) for k in keys}
    # A short stratum's shortfall goes to the others, one item at a time in turn.
    while (
        quotas is None
        and sum(take.values()) < size
        and any(take[k] < len(strata[k]) for k in keys)
    ):
        for k in keys:
            if sum(take.values()) < size and take[k] < len(strata[k]):
                take[k] += 1
    picked = [dict(r) for k in keys for r in strata[k][: take[k]]]
    rng.shuffle(picked)
    return [{"item": i, **r} for i, r in enumerate(picked, 1)]


def _read(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {int(r["item"]): r["label"] for r in csv.DictReader(fh)}


def _write(path: Path, labels: Mapping[int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["item", "label"])
        writer.writerows(sorted(labels.items()))


def _show(row: Mapping[str, Any], task: ExternalTask, output_fn: OutputFn) -> None:
    output_fn(f"\n=== item {row['item']} ===")
    output_fn(judge_prompt(task, row["answer"]).replace(JUDGE_ONLY_LINES, ""))


def _ask(input_fn: InputFn) -> str | None:
    """``correct``, ``incorrect``, ``quit``, or ``None`` to skip."""
    choice = input_fn("correct / incorrect / skip / quit [c/i/s/q]? ").strip().lower()
    for word in ("correct", "incorrect", "quit"):
        if choice[:1] == word[0]:
            return word
    return None


def _collect(
    rows: Sequence[Mapping[str, Any]],
    tasks: Mapping[str, ExternalTask],
    path: Path,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
    note: Callable[[int], str] | None = None,
) -> dict[int, str]:
    """Ask about each row not yet in ``path``, saving after every answer."""
    labels = _read(path)
    for row in rows:
        if row["item"] in labels:
            continue
        _show(row, tasks[row["task_id"]], output_fn)
        if note is not None:
            output_fn(note(row["item"]))
        answer = _ask(input_fn)
        if answer == "quit":
            break
        if answer is not None:
            labels[row["item"]] = answer
            _write(path, labels)
    return labels


def label(
    sample: Sequence[Mapping[str, Any]],
    tasks: Mapping[str, ExternalTask],
    path: Path,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> dict[int, str]:
    """One grader's blind labels; resumes from ``path``."""
    labels = _collect(sample, tasks, path, input_fn=input_fn, output_fn=output_fn)
    output_fn(f"\nlabelled {len(labels)}/{len(sample)}")
    return labels


def settle(
    sample: Sequence[Mapping[str, Any]],
    tasks: Mapping[str, ExternalTask],
    a: Mapping[int, str],
    b: Mapping[int, str],
    path: Path,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> dict[int, str]:
    """The two graders' joint decision on each item where they disagreed."""
    split = [
        r
        for r in sample
        if r["item"] in a and r["item"] in b and a[r["item"]] != b[r["item"]]
    ]
    return _collect(
        split,
        tasks,
        path,
        input_fn=input_fn,
        output_fn=output_fn,
        note=lambda item: f"A said {a[item]}; B said {b[item]}. Decide together.",
    )


def settled_labels(
    sample: Sequence[Mapping[str, Any]],
    a: Mapping[int, str],
    b: Mapping[int, str],
    settled: Mapping[int, str],
) -> dict[int, bool]:
    """Item -> the humans' final label: agreed by A and B, or settled."""
    final: dict[int, bool] = {}
    for r in sample:
        item = r["item"]
        if item not in a or item not in b:
            continue
        agreed = a[item] if a[item] == b[item] else settled.get(item)
        if agreed is not None:
            final[item] = agreed == "correct"
    return final


def report(
    sample: Sequence[Mapping[str, Any]],
    a: Mapping[int, str],
    b: Mapping[int, str],
    settled: Mapping[int, str],
    *,
    output_fn: OutputFn,
) -> tuple[float, float]:
    """Kappa between the graders and between the primary judge and the humans."""
    both = [r for r in sample if r["item"] in a and r["item"] in b]
    final = settled_labels(sample, a, b, settled)
    k_graders = math.nan
    if both:
        k_graders = cohen_kappa(
            [a[r["item"]] == "correct" for r in both],
            [b[r["item"]] == "correct" for r in both],
        )
    judged = [r for r in both if r["item"] in final]
    k_judge = math.nan
    if judged:
        k_judge = cohen_kappa(
            [r["judge_correct"] for r in judged], [final[r["item"]] for r in judged]
        )
    output_fn(f"graders A vs B: kappa {k_graders:.3f} on {len(both)} items")
    output_fn(
        f"primary judge vs settled labels: kappa {k_judge:.3f} on {len(judged)} "
        f"items ({len(both) - len(judged)} disagreements not settled yet)"
    )
    if not judge_is_reliable(k_judge):
        output_fn(
            f"GATE: kappa below {KAPPA_GATE}. The headline must use the "
            "human-labelled sample only; judge-based results are unreliable."
        )
    return k_graders, k_judge


def _ci(lo: float, hi: float) -> str:
    return f"[{lo:.3f}, {hi:.3f}]"


def population_report(
    sample: Sequence[Mapping[str, Any]],
    a: Mapping[int, str],
    b: Mapping[int, str],
    settled: Mapping[int, str],
    answers: Sequence[Mapping[str, Any]],
    *,
    output_fn: OutputFn,
) -> CorrectedDelta:
    """The judge on the population (L2): weighted agreement, errors per arm, Δ.

    ``answers`` are the ``answers.jsonl`` rows; their counts per arm x verdict
    stratum weight the labels. Description and sensitivity only: the §9 gate
    and §6 are unchanged.
    """
    population = Counter(
        (r["arm"], r["judge_correct"])
        for r in answers
        if r["judge_correct"] is not None
    )
    final = settled_labels(sample, a, b, settled)
    rows = [
        (r["arm"], bool(r["judge_correct"]), final[r["item"]])
        for r in sample
        if r["item"] in final
    ]
    w = weighted_agreement(rows, population)
    output_fn(
        f"primary judge vs settled labels, weighted by stratum size: agreement "
        f"{w.observed:.3f}, kappa {w.kappa:.3f} on {w.n} items"
    )
    output_fn(
        "| arm | labelled | judge success | corrected success [95% CI] | "
        "judge bias | sensitivity [95% CI] | specificity [95% CI] |"
    )
    output_fn("|---|---|---|---|---|---|---|")
    for arm, e in judge_errors_by_arm(rows, population).items():
        output_fn(
            f"| {arm} | {e.n_labelled} | {e.judge_success:.3f} | "
            f"{e.corrected_success:.3f} {_ci(e.corrected_lo, e.corrected_hi)} | "
            f"{e.bias:+.3f} | {e.sensitivity:.3f} "
            f"{_ci(e.sensitivity_lo, e.sensitivity_hi)} | {e.specificity:.3f} "
            f"{_ci(e.specificity_lo, e.specificity_hi)} |"
        )
    d = corrected_delta(rows, population)
    output_fn(
        f"Δ nina - full_page: judge {d.judge_delta:+.3f}; human-corrected "
        f"{d.corrected:+.3f} {_ci(d.lo, d.hi)}; judge bias on Δ {d.bias:+.3f} "
        f"{_ci(d.bias_lo, d.bias_hi)} (sensitivity only, not a decision)"
    )
    return d


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real TTY
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["sample", "label", "settle", "report"])
    parser.add_argument("--grader", choices=["A", "B"])
    parser.add_argument("--answers", default="bench/results/m10/answers.jsonl")
    parser.add_argument("--tasks", default="bench/tasks/external.yaml")
    parser.add_argument("--dir", default="bench/results/m10/labels")
    args = parser.parse_args(argv)

    folder = Path(args.dir)
    sample_path = folder / "sample.jsonl"
    if args.command == "sample":
        if sample_path.exists():
            parser.error(f"{sample_path} exists; the sample is drawn once")
        lines = Path(args.answers).read_text(encoding="utf-8").splitlines()
        sample = draw_sample([json.loads(line) for line in lines])
        folder.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(
            "".join(json.dumps(r) + "\n" for r in sample), encoding="utf-8"
        )
        print(f"drew {len(sample)} answers into {sample_path}")
        return

    lines = sample_path.read_text(encoding="utf-8").splitlines()
    sample = [json.loads(line) for line in lines]
    a, b = _read(folder / "labels_A.csv"), _read(folder / "labels_B.csv")
    if args.command == "report":
        settled = _read(folder / "settled.csv")
        report(sample, a, b, settled, output_fn=print)
        answers_path = Path(args.answers)
        if answers_path.exists():
            rows = answers_path.read_text(encoding="utf-8").splitlines()
            answers = [json.loads(line) for line in rows]
            population_report(sample, a, b, settled, answers, output_fn=print)
        return
    tasks = {t.id: t for t in load_external_tasks(args.tasks)}
    if args.command == "label":
        if args.grader is None:
            parser.error("label needs --grader A or B")
        path = folder / f"labels_{args.grader}.csv"
        label(sample, tasks, path, input_fn=input, output_fn=print)
    else:
        settled = folder / "settled.csv"
        settle(sample, tasks, a, b, settled, input_fn=input, output_fn=print)


if __name__ == "__main__":
    main()
