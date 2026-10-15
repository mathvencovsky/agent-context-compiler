"""Per-session output files of the M10 runs (PREREG §6, §11).

``results.csv`` gets one row per (task, arm) session, with the arm id in the
``strategy`` column so ``bench.power`` reads it unchanged. Rows are appended as
sessions finish, so a long run can stop and resume: :func:`done_pairs` lists the
sessions already recorded. Booleans are written ``1``/``0``; an excluded
session (an infrastructure failure, PREREG §10) has an empty ``correct``.

``answers.jsonl`` gets one line per primary-population session with the
agent's final answer and the primary judge's verdict, the input of the blind
human labels (:mod:`bench.label_answers`).
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class SessionRecord:
    """One (task, arm) session: outcome, billed usage and effort (PREREG §6)."""

    task_id: str
    strategy: str  # the arm id
    split: str
    site: str
    task_type: str
    correct: bool | None = None  # primary judge; None = excluded session
    correct_judge2: bool | None = None
    citation_correct: bool | None = None
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    turns: int = 0
    tool_calls: int = 0
    tool_result_tokens: int = 0
    wall_ms: int = 0
    hit_max_turns: bool = False
    infra_error: bool = False
    git_commit: str = ""


FIELDS = [f.name for f in fields(SessionRecord)]


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.8f}"
    return str(value)


def append_record(path: Path, record: SessionRecord) -> None:
    """Append ``record`` to ``results.csv``, writing the header the first time."""
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({k: _cell(v) for k, v in asdict(record).items()})


def done_pairs(path: Path) -> set[tuple[str, str]]:
    """The (task_id, arm) sessions already in ``results.csv``."""
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as fh:
        return {(row["task_id"], row["strategy"]) for row in csv.DictReader(fh)}


def append_answer(
    path: Path, *, task_id: str, arm: str, answer: str, judge_correct: bool | None
) -> None:
    """Append one session's final answer for the blind labels."""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "task_id": task_id,
        "arm": arm,
        "answer": answer,
        "judge_correct": judge_correct,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
