"""How many M10 tasks hit Claude Code's tool-result size limits (no cost).

Claude Code saves an MCP text result to a file once it is longer than 50,000
characters, unless the tool declares ``anthropic/maxResultSizeChars``; the
declared value is capped at 500,000 (Claude Code MCP docs, "MCP output
limits"). The M10 arms have no tool that reads such a file. ``bench/m10_tools``
now declares the cap, so a whole page reaches the agent up to 500,000
characters; this script counts what the cap still leaves out, on the pages the
``full_page`` arm can fetch (every URL a task lists) and on the cited ones.

Usage (needs the corpus)::

    uv run python -m bench.m10_result_limits [--splits pilot,holdout]
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass

DEFAULT_LIMIT = 50_000
CEILING = 500_000


@dataclass(frozen=True)
class LimitCounts:
    """Pages and tasks over each limit."""

    pages: int
    pages_over_default: int
    pages_over_ceiling: int
    tasks: int
    tasks_any_url_over_ceiling: int
    tasks_cited_over_ceiling: int


def count(
    tasks: Iterable[tuple[list[str], list[str]]], chars_of: Callable[[str], int]
) -> LimitCounts:
    """``tasks`` as ``(urls, cited_urls)``; ``chars_of`` gives a page's length."""
    cache: dict[str, int] = {}

    def size(url: str) -> int:
        if url not in cache:
            cache[url] = chars_of(url)
        return cache[url]

    n_tasks = any_over = cited_over = 0
    for urls, cited in tasks:
        n_tasks += 1
        any_over += any(size(u) > CEILING for u in urls)
        cited_over += any(size(u) > CEILING for u in cited)
    return LimitCounts(
        pages=len(cache),
        pages_over_default=sum(v > DEFAULT_LIMIT for v in cache.values()),
        pages_over_ceiling=sum(v > CEILING for v in cache.values()),
        tasks=n_tasks,
        tasks_any_url_over_ceiling=any_over,
        tasks_cited_over_ceiling=cited_over,
    )


def over_ceiling(
    tasks: Iterable[tuple[str, list[str]]], chars_of: Callable[[str], int]
) -> list[str]:
    """Ids of the tasks, given as ``(id, urls)``, with a listed page over the cap.

    PREREG Amendment 14 excludes them from every arm: the ``full_page`` arm
    could not read such a page, and keeping them would favor NINA.
    """
    cache: dict[str, int] = {}
    out = []
    for task_id, urls in tasks:
        for url in urls:
            if url not in cache:
                cache[url] = chars_of(url)
            if cache[url] > CEILING:
                out.append(task_id)
                break
    return out


def report(c: LimitCounts) -> str:
    return (
        f"pages: {c.pages}; over 50,000 chars (saved to file without the "
        f"annotation): {c.pages_over_default}; over 500,000 (saved to file "
        f"even with it): {c.pages_over_ceiling}\n"
        f"tasks: {c.tasks}; with any listed URL over 500,000: "
        f"{c.tasks_any_url_over_ceiling}; with a cited URL over 500,000: "
        f"{c.tasks_cited_over_ceiling}\n"
    )


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - corpus
    from bench.corpus_store import CorpusStore
    from bench.page_length import page_markdown
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--splits", default="pilot,holdout")
    args = parser.parse_args(argv)
    splits = set(args.splits.split(","))
    store = CorpusStore(DEFAULT_CORPUS_DIR)

    def chars_of(url: str) -> int:
        try:
            return len(page_markdown(store, url))
        except (KeyError, ValueError):
            return 0

    tasks = [
        (t.urls, t.cited_urls)
        for t in load_external_tasks("bench/tasks/external.yaml")
        if t.split in splits
    ]
    print(report(count(tasks, chars_of)), end="")


if __name__ == "__main__":
    main()
