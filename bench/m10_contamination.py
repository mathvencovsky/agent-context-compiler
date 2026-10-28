"""Zero-cost contamination checks on finished M10 sessions (front F, L7).

Exploratory and descriptive only; they decide nothing and run after the run.
The §2.2 screen removes what the model answers from memory, but two leaks get
past it:

1. **Leak through a tool.** A live arm can reach a page that quotes the
   accepted answer (a Stack Overflow mirror, a blog). Flagged per session when
   a tool result has a stackoverflow.com / stackexchange.com link, or holds at
   least :data:`LEAK_SHARE` of the accepted answer's word :data:`LEAK_N`-grams
   (and at least :data:`LEAK_MIN` of them). Frozen documentation pages can
   share code with the accepted answer legitimately, so the frozen arms give
   the baseline rate the live arms are read against.
2. **Memory.** ``answer_ref_overlap`` is the share of the judged answer's word
   :data:`ANSWER_N`-grams found in the accepted answer; ``answer_ref_only``
   keeps only those absent from every tool result of the session, i.e. text
   the agent could not have read in the session (Caspari et al., TMLR 2025,
   compare answers with the accepted answer the same way). In ``no_docs`` both
   are the same.

Dates: tasks carry their question's creation date (``source.created``), so
:func:`delta_by_quarter` gives Δ per quarter and :func:`relative_to_cutoff`
places a question before or after a model's training cutoff when one is given.
A post-cutoff drop is not proof of contamination (it depends on how questions
are built), and the report must say so.

Usage::

    uv run python -m bench.m10_contamination --out bench/results/m10 \
        [--cutoff YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import csv
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from bench.judge_external import final_answer
from bench.m10_transcripts import Session, last_sessions, ngrams, words

#: Word n-gram length for answer overlap (answers are short).
ANSWER_N = 5
#: Word n-gram length and thresholds for a leak through a tool (fixed now).
LEAK_N = 8
LEAK_SHARE = 0.25
LEAK_MIN = 3
_SO_URL = re.compile(r"\b(?:[\w-]+\.)?(?:stackoverflow|stackexchange)\.com\b", re.I)


@dataclass(frozen=True)
class SessionCheck:
    """The contamination signals of one (task, arm) session."""

    task_id: str
    arm: str
    answer_ref_overlap: float
    answer_ref_only: float
    so_url: bool
    max_tool_ref_share: float

    @property
    def leaked(self) -> bool:
        return self.so_url or self.max_tool_ref_share >= LEAK_SHARE


def _share(part: set[tuple[str, ...]], whole: set[tuple[str, ...]]) -> float:
    return len(part) / len(whole) if whole else 0.0


def check_session(session: Session, reference: str) -> SessionCheck:
    """Overlap of the judged answer and of each tool result with ``reference``."""
    answer = ngrams(words(final_answer(session.final_text).answer), ANSWER_N)
    ref = ngrams(words(reference), ANSWER_N)
    seen: set[tuple[str, ...]] = set()
    for _name, text in session.tool_results:
        seen |= ngrams(words(text), ANSWER_N)
    in_ref = answer & ref
    ref_leak = ngrams(words(reference), LEAK_N)
    best = 0.0
    so_url = False
    for _name, text in session.tool_results:
        so_url = so_url or bool(_SO_URL.search(text))
        shared = ref_leak & ngrams(words(text), LEAK_N)
        if len(shared) >= LEAK_MIN:
            best = max(best, _share(shared, ref_leak))
    return SessionCheck(
        task_id=session.task_id,
        arm=session.arm,
        answer_ref_overlap=_share(in_ref, answer),
        answer_ref_only=_share(in_ref - seen, answer),
        so_url=so_url,
        max_tool_ref_share=best,
    )


@dataclass
class ArmContamination:
    """Per-arm means and the leaked sessions."""

    sessions: int = 0
    mean_ref_overlap: float = 0.0
    mean_ref_only: float = 0.0
    leaked: list[str] = field(default_factory=list)


def by_arm(checks: Iterable[SessionCheck]) -> dict[str, ArmContamination]:
    groups: dict[str, list[SessionCheck]] = {}
    for c in checks:
        groups.setdefault(c.arm, []).append(c)
    out = {}
    for arm, cs in sorted(groups.items()):
        out[arm] = ArmContamination(
            sessions=len(cs),
            mean_ref_overlap=sum(c.answer_ref_overlap for c in cs) / len(cs),
            mean_ref_only=sum(c.answer_ref_only for c in cs) / len(cs),
            leaked=sorted(f"{c.task_id}__{c.arm}" for c in cs if c.leaked),
        )
    return out


def quarter(created: str) -> str:
    """``YYYY-MM-DD`` -> ``YYYYQn``."""
    year, month = created[:4], int(created[5:7])
    return f"{year}Q{(month - 1) // 3 + 1}"


def relative_to_cutoff(created: str, cutoff: str | None) -> str:
    """``before``/``after`` a model's training cutoff, ``unknown`` without one."""
    if cutoff is None:
        return "unknown"
    return "after" if created[:10] > cutoff[:10] else "before"


def delta_by_quarter(
    rows: Sequence[Mapping[str, str]],
    created: Mapping[str, str],
    *,
    a: str = "nina",
    b: str = "full_page",
) -> dict[str, tuple[int, float]]:
    """Quarter -> (paired tasks, success(a) − success(b)); exploratory."""
    verdicts: dict[str, dict[str, bool]] = {a: {}, b: {}}
    for r in rows:
        if r["strategy"] in verdicts and r["correct"] != "":
            verdicts[r["strategy"]][r["task_id"]] = r["correct"] == "1"
    per: dict[str, list[int]] = {}
    for task in sorted(verdicts[a].keys() & verdicts[b].keys()):
        diff = int(verdicts[a][task]) - int(verdicts[b][task])
        per.setdefault(quarter(created[task]), []).append(diff)
    return {q: (len(d), sum(d) / len(d)) for q, d in sorted(per.items())}


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - run output
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("bench/results/m10"))
    parser.add_argument("--tasks", default="bench/tasks/external.yaml")
    parser.add_argument("--cutoff", help="the agent model's training cutoff")
    args = parser.parse_args(argv)

    tasks = {t.id: t for t in load_external_tasks(args.tasks)}
    checks = [
        check_session(s, tasks[s.task_id].reference_answer)
        for s in last_sessions(args.out / "transcripts")
        if s.task_id in tasks
    ]
    print("| arm | sessions | answer∩accepted | not seen in tools | leaked |")
    print("|---|---|---|---|---|")
    for arm, c in by_arm(checks).items():
        print(
            f"| {arm} | {c.sessions} | {c.mean_ref_overlap:.3f} | "
            f"{c.mean_ref_only:.3f} | {len(c.leaked)} |"
        )
    with (args.out / "results.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    created = {t: task.source.created for t, task in tasks.items()}
    print("\n| quarter | tasks | Δ nina − full_page | vs cutoff |")
    print("|---|---|---|---|")
    for q, (n, d) in delta_by_quarter(rows, created).items():
        dates = [c for c in created.values() if quarter(c) == q]
        where = {relative_to_cutoff(c, args.cutoff) for c in dates}
        print(f"| {q} | {n} | {d:+.3f} | {'/'.join(sorted(where))} |")
    print("\nExploratory: a difference after the cutoff does not prove contamination.")


if __name__ == "__main__":
    main()
