"""Task schema: valid rows load, invalid fields fail naming them, stable round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bench.tasks_schema import Evidence, Task, dump_tasks, load_tasks

_VALID_ROW = {
    "id": "t1",
    "category": "api_reference",
    "split": "dev",
    "urls": ["https://example.test/a"],
    "question": "What does abs return?",
    "answer_type": "contains_all",
    "expected": ["absolute value"],
    "expected_evidence": [
        {"url": "https://example.test/a", "quote": "the absolute value"}
    ],
    "author": "human",
    "validated": True,
}


def _write_yaml(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")


def test_valid_tasks_load(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    _write_yaml(path, [_VALID_ROW])
    tasks = load_tasks(path)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.id == "t1"
    assert task.category == "api_reference"
    assert task.expected_evidence[0].quote == "the absolute value"


def test_empty_file_loads_empty(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    path.write_text("", encoding="utf-8")
    assert load_tasks(path) == []


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("category", "reference"),  # not in the 5-value enum
        ("split", "train"),  # not dev|test
        ("answer_type", "regex"),  # not exact|contains_all|judge
        ("author", "robot"),  # not human|ai_draft
    ],
)
def test_invalid_enum_fails_naming_field(
    tmp_path: Path, field: str, bad_value: str
) -> None:
    row = dict(_VALID_ROW)
    row[field] = bad_value
    path = tmp_path / "tasks.yaml"
    _write_yaml(path, [row])

    with pytest.raises(ValueError) as excinfo:
        load_tasks(path)
    message = str(excinfo.value)
    assert "'t1'" in message
    assert field in message


def test_missing_required_field_fails_naming_field(tmp_path: Path) -> None:
    row = dict(_VALID_ROW)
    del row["question"]
    path = tmp_path / "tasks.yaml"
    _write_yaml(path, [row])

    with pytest.raises(ValueError) as excinfo:
        load_tasks(path)
    assert "question" in str(excinfo.value)


def test_round_trip_is_stable(tmp_path: Path) -> None:
    task = Task(
        id="t1",
        category="guide",
        split="test",
        urls=["https://example.test/g"],
        question="How do I start?",
        answer_type="judge",
        expected=["INSUFFICIENT"],
        expected_evidence=[Evidence(url="https://example.test/g", quote="start here")],
        author="ai_draft",
        validated=False,
    )
    path = tmp_path / "tasks.yaml"
    dump_tasks([task], path)
    first = path.read_text(encoding="utf-8")

    reloaded = load_tasks(path)
    assert reloaded == [task]

    dump_tasks(reloaded, path)
    assert path.read_text(encoding="utf-8") == first
