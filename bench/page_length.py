"""Page length for the M10 secondary analysis (PREREG Amendment 4).

A task's page length is the cl100k token count (``count_tokens``) of its cited
pages, at most 4, as whole-page Markdown extracted from the frozen snapshots
with trafilatura. :func:`page_markdown` is that extraction, and the
``full_page`` arm is meant to serve the same text, so the bins describe what
that arm reads. Rendered HTML is used when the snapshot has it; a plain-text
page (an RFC ``.txt``) is already text and is used as is.

Bins (Amendment 4): short < 4,000 tokens, medium 4,000-15,999, long >= 16,000.

Usage (reads the frozen corpus only; no network):
    uv run python -m bench.page_length
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import trafilatura

from app.index.tokens import count_tokens
from bench.corpus_store import CorpusStore
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import ExternalTask, load_external_tasks

#: PREREG §2.1: at most this many cited pages count toward a task.
MAX_CITED = 4
#: Amendment 4 bins: (exclusive upper bound, name); anything above is "long".
_BINS = ((4_000, "short"), (16_000, "medium"))


def page_markdown(store: CorpusStore, url: str) -> str:
    """The frozen page at ``url`` as trafilatura Markdown (``""`` if none)."""
    html = store.rendered_html(url)
    if html is None:
        raw = store.raw_bytes(url).decode("utf-8", errors="replace")
        if store.content_type(url) == "text/plain":
            return raw
        html = raw
    return trafilatura.extract(html, output_format="markdown") or ""


def task_page_tokens(
    task: ExternalTask, store: CorpusStore, cache: dict[str, int] | None = None
) -> int:
    """Tokens of the task's first ``MAX_CITED`` cited pages; ``cache`` is per URL."""
    cache = {} if cache is None else cache
    total = 0
    for url in task.cited_urls[:MAX_CITED]:
        if url not in cache:
            cache[url] = count_tokens(page_markdown(store, url))
        total += cache[url]
    return total


def length_bin(tokens: int) -> str:
    """The Amendment 4 bin of a page length."""
    for upper, name in _BINS:
        if tokens < upper:
            return name
    return "long"


def main() -> None:  # pragma: no cover - reads the local frozen corpus
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = load_external_tasks(Path("bench/tasks/external.yaml"))
    out = Path("bench/results/m10/page_lengths.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    cache: dict[str, int] = {}
    bins: Counter[str] = Counter()
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["task_id", "split", "site", "page_tokens", "bin"])
        for task in tasks:
            tokens = task_page_tokens(task, store, cache)
            bins[length_bin(tokens)] += 1
            writer.writerow(
                [task.id, task.split, task.site, tokens, length_bin(tokens)]
            )
    print(json.dumps({"tasks": len(tasks), "by_bin": bins}, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
