"""Tests for the Amendment 4 page length (bench/page_length.py)."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.fetch.client import FetchResult
from app.fetch.render import RenderResult
from bench.corpus_store import CorpusStore
from bench.page_length import length_bin, page_markdown, task_page_tokens
from bench.snapshot import SiteConfig, snapshot
from bench.tasks_schema import ExternalTask, Source

_HTML = (
    "<html><body><nav>menu menu menu</nav><main><h1>print()</h1>"
    + "<p>The end parameter defaults to a newline character. </p>" * 20
    + "</main></body></html>"
)


def _store(tmp_path: Path) -> CorpusStore:
    def fake_fetch(url: str) -> FetchResult:
        plain = url.endswith(".txt")
        return FetchResult(
            final_url=url,
            status=200,
            headers=httpx.Headers(
                {"content-type": "text/plain" if plain else "text/html"}
            ),
            body=b"RFC text, line one.\n" if plain else _HTML.encode(),
            content_type="text/plain" if plain else "text/html",
            redirects=0,
            elapsed_ms=1,
        )

    def fake_render(url: str) -> RenderResult:
        return RenderResult(final_url=url, html="", status=200)

    urls = ["https://d.test/page", "https://d.test/rfc.txt"]
    site = SiteConfig(site="D", key="d", rich_categories=[], render="never", urls=urls)
    snapshot([site], tmp_path, fetch_fn=fake_fetch, render_fn=fake_render)
    return CorpusStore(tmp_path)


def _task(cited: list[str]) -> ExternalTask:
    return ExternalTask(
        id="so-1",
        split="holdout",
        site="python",
        task_type="fact",
        urls=cited,
        cited_urls=cited,
        question_title="t",
        question="q",
        reference_answer="a",
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


def test_page_markdown_extracts_main_text_and_keeps_plain_text(tmp_path: Path) -> None:
    store = _store(tmp_path)
    markdown = page_markdown(store, "https://d.test/page")
    assert "The end parameter defaults to a newline character." in markdown
    assert page_markdown(store, "https://d.test/rfc.txt") == "RFC text, line one.\n"


def test_task_page_tokens_sums_only_the_first_four_cited_pages() -> None:
    cache = {f"https://d.test/{n}": 10 * n for n in range(1, 6)}
    task = _task(list(cache))
    # The cache already holds every page, so no store is needed: 10+20+30+40.
    assert task_page_tokens(task, store=None, cache=cache) == 100  # type: ignore[arg-type]


def test_length_bin_boundaries() -> None:
    assert [length_bin(n) for n in (0, 3_999, 4_000, 15_999, 16_000)] == [
        "short",
        "short",
        "medium",
        "medium",
        "long",
    ]
