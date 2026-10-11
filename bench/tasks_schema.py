"""Task/Evidence model and the ``tasks.yaml`` loader/dumper (SPEC §10.2).

SPEC §10.2 fixes the shape of each benchmark task: a closed enum of five task
categories (``api_reference | guide | params_table | changelog | pdf``), a
``dev``/``test`` split, an answer-matching mode (``exact | contains_all |
judge``), the expected answer(s), the literal evidence quotes, and provenance
(``author`` + ``validated``). :class:`Task` is the Pydantic v2 model of that
contract; invalid rows fail loudly naming the offending ``id`` and field.

:func:`load_tasks` / :func:`dump_tasks` are a stable YAML round-trip: fields are
written in a deterministic order so a reviewer (``bench/review_tasks.py``) can
rewrite the file without spurious diffs (SPEC §10 determinism).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ValidationError

#: Closed enum of benchmark task categories (SPEC §10.2). The rich labels in
#: ``bench/sites.yaml`` are descriptive metadata and are mapped onto these five
#: by ``bench/draft_tasks.py``; only these five ever appear in ``tasks.yaml``.
Category = Literal["api_reference", "guide", "params_table", "changelog", "pdf"]

#: dev/test split for the task (SPEC §10.2).
Split = Literal["dev", "test"]

#: Answer-matching mode (SPEC §10.2): deterministic ``exact``/``contains_all``
#: or model-judged ``judge``.
AnswerType = Literal["exact", "contains_all", "judge"]

#: Who authored the task and whether a human has validated it (SPEC §10.2).
Author = Literal["human", "ai_draft"]


class Evidence(BaseModel):
    """A supporting quote located at a specific URL in the corpus (SPEC §10.2).

    ``quote`` is copied LITERALLY from the snapshot document at ``url`` so the
    grader can verify a citation against the frozen corpus (SPEC §10.4).
    """

    url: str
    quote: str


class Task(BaseModel):
    """One benchmark task — the exact SPEC §10.2 contract.

    ``expected`` holds the acceptable answer string(s); for an unanswerable task
    it is ``["INSUFFICIENT"]``. ``expected_evidence`` lists the literal quotes
    that support the expected answer. ``author``/``validated`` carry provenance:
    an ``ai_draft`` task is ``validated=False`` until a human accepts it.
    """

    id: str
    category: Category
    split: Split
    urls: list[str]
    question: str
    answer_type: AnswerType
    expected: list[str]
    expected_evidence: list[Evidence]
    author: Author
    validated: bool


#: Deterministic field order for the YAML round-trip (keeps review diffs clean).
_FIELD_ORDER = (
    "id",
    "category",
    "split",
    "urls",
    "question",
    "answer_type",
    "expected",
    "expected_evidence",
    "author",
    "validated",
)


def _task_to_ordered_dict(task: Task) -> dict[str, Any]:
    """Serialize ``task`` to a plain dict with a deterministic key order."""
    data = task.model_dump()
    return {key: data[key] for key in _FIELD_ORDER}


def load_tasks(path: str | Path) -> list[Task]:
    """Load and validate ``tasks.yaml`` into a list of :class:`Task`.

    The file must be a YAML sequence of task mappings. A row that violates the
    SPEC §10.2 contract raises :class:`ValueError` with a message naming the
    task ``id`` (or its index when the ``id`` itself is missing) and the field
    at fault, so a reviewer can fix exactly one line.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"{path}: expected a YAML sequence of tasks, got {type(raw).__name__}"
        )

    tasks: list[Task] = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(
                f"{path}: task #{index} is not a mapping (got {type(row).__name__})"
            )
        row_id = row.get("id", f"<index {index}>")
        try:
            tasks.append(Task.model_validate(row))
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(str(part) for part in error["loc"]) for error in exc.errors()
            )
            raise ValueError(
                f"{path}: task {row_id!r} is invalid at field(s): {fields}"
            ) from exc
    return tasks


def dump_tasks(tasks: list[Task], path: str | Path) -> None:
    """Write ``tasks`` to ``path`` as YAML with a stable, review-friendly layout.

    Keys follow :data:`_FIELD_ORDER` for every task and the output is block-style
    (``default_flow_style=False``) with keys kept in insertion order
    (``sort_keys=False``) so re-dumping an unchanged list is byte-stable.
    """
    path = Path(path)
    payload = [_task_to_ordered_dict(task) for task in tasks]
    text = yaml.safe_dump(
        payload,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    path.write_text(text, encoding="utf-8")
