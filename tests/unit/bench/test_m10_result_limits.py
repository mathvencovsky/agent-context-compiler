"""Tool-result size limits on the M10 pages (bench/m10_result_limits.py)."""

from __future__ import annotations

from bench.m10_result_limits import count, report

SIZES = {"a": 10_000, "b": 60_000, "c": 600_000, "d": 40_000}


def test_count_pages_and_tasks_over_each_limit() -> None:
    tasks = [
        (["a", "b"], ["a"]),
        (["c", "d"], ["d"]),  # an uncited distractor over the ceiling
        (["c"], ["c"]),
    ]
    c = count(tasks, SIZES.__getitem__)
    assert (c.pages, c.pages_over_default, c.pages_over_ceiling) == (4, 2, 1)
    assert (c.tasks, c.tasks_any_url_over_ceiling, c.tasks_cited_over_ceiling) == (
        3,
        2,
        1,
    )
    assert "over 500,000 (saved to file even with it): 1" in report(c)


def test_each_page_is_measured_once() -> None:
    calls: list[str] = []

    def chars_of(url: str) -> int:
        calls.append(url)
        return 1

    count([(["a", "b"], ["a"]), (["a"], ["a"])], chars_of)
    assert sorted(calls) == ["a", "b"]


def test_over_ceiling_lists_tasks_with_any_listed_page_over_the_cap() -> None:
    from bench.m10_result_limits import over_ceiling

    tasks = [("t1", ["a", "b"]), ("t2", ["d", "c"]), ("t3", ["c"]), ("t4", [])]
    assert over_ceiling(tasks, SIZES.__getitem__) == ["t2", "t3"]
