"""Brake, question mismatch and acceptance rule of bench/compact_stop_eval.py."""

from __future__ import annotations

from bench.compact_stop_eval import braked, checks, mismatched


def _score(values: list[float]):
    return braked(lambda texts: values, absolute=0.01)


def test_brake_zeroes_units_below_the_floor() -> None:
    assert _score([0.9, 0.005, 0.02])(["a", "b", "c"]) == [0.9, 0.0, 0.02]


def test_brake_always_keeps_the_best_unit_so_nothing_falls_back() -> None:
    assert _score([0.0001, 0.0002])(["a", "b"]) == [0.0, 0.0002]
    assert _score([0.0, 0.0])(["a", "b"]) == [1e-9, 0.0]


def test_relative_floor_follows_the_page_best_unit() -> None:
    score = braked(lambda texts: [0.5, 0.01, 0.1], relative=0.1)
    assert score(["a", "b", "c"]) == [0.5, 0.0, 0.1]


def test_mismatch_never_pairs_tasks_on_the_same_host() -> None:
    pairs = mismatched(
        {
            "a": ["https://x.test/1"],
            "b": ["https://x.test/2"],
            "c": ["https://y.test/1"],
        }
    )
    assert pairs == {"a": "c", "b": "c", "c": "a"}


def test_task_with_no_other_host_is_left_out() -> None:
    assert mismatched({"a": ["https://x.test/1"], "b": ["https://x.test/2"]}) == {}


def test_rule_needs_all_three_checks() -> None:
    base = {"answer_hit": 0.6, "tokens": 1000.0, "mismatch_tokens": 1000.0}
    good = {"answer_hit": 0.58, "tokens": 800.0, "mismatch_tokens": 500.0}
    assert all(passed for _, passed, _ in checks(base, good))
    for field, value in (
        ("answer_hit", 0.57),
        ("tokens", 801.0),
        ("mismatch_tokens", 501.0),
    ):
        failed = [
            c for c, passed, _ in checks(base, {**good, field: value}) if not passed
        ]
        assert len(failed) == 1
