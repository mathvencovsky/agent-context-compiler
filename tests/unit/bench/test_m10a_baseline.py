"""Offline tests for the M10a systems comparator (bench/m10a_baseline.py)."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.fetch.client import FetchResult
from app.fetch.render import RenderResult
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, Row, measure, summarize
from bench.snapshot import SiteConfig, snapshot
from bench.tasks_schema import ExternalTask, Source

_ANSWER = "The LIMIT clause consists of two independent sub-clauses."
_PAGE = (
    "<html><body><main><h1>SELECT</h1>"
    + "<p>Filler sentence about something else entirely here.</p>" * 30
    + f"<h2 id='limit'>LIMIT Clause</h2><p>{_ANSWER}</p>"
    + "<p>More filler text that does not answer anything at all.</p>" * 30
    + "</main></body></html>"
)
_OTHER = (
    "<html><body><main><p>Unrelated page text with nothing useful.</p>"
    "</main></body></html>"
)


def _store(tmp_path: Path) -> CorpusStore:
    def fake_fetch(url: str) -> FetchResult:
        body = _PAGE if url.endswith("select") else _OTHER
        return FetchResult(
            final_url=url,
            status=200,
            headers=httpx.Headers({"content-type": "text/html"}),
            body=body.encode(),
            content_type="text/html",
            redirects=0,
            elapsed_ms=1,
        )

    def fake_render(url: str) -> RenderResult:
        return RenderResult(final_url=url, html="", status=200)

    urls = ["https://d.test/select", "https://d.test/other"]
    site = SiteConfig(site="D", key="d", rich_categories=[], render="never", urls=urls)
    snapshot([site], tmp_path, fetch_fn=fake_fetch, render_fn=fake_render)
    return CorpusStore(tmp_path)


def _task() -> ExternalTask:
    urls = ["https://d.test/select", "https://d.test/other"]
    return ExternalTask(
        id="so-1",
        split="dev",
        site="postgresql",
        task_type="fact",
        urls=urls,
        cited_urls=urls[:1],
        question_title="How does LIMIT work?",
        question="What does the LIMIT clause consist of?",
        reference_answer="two sub-clauses",
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


def test_measure_gives_every_system_a_row_and_finds_the_section(tmp_path: Path) -> None:
    key = {"url": "https://d.test/select", "section": f"LIMIT Clause\n{_ANSWER}"}
    rows = measure(_task(), key, _store(tmp_path))

    by = {(r.system, r.budget): r for r in rows}
    assert len(rows) == 3 + 3 * len(BUDGETS)
    assert by[("full_page", 0)].hit and by[("extract_nina", 0)].hit
    assert by[("extract_trafilatura", 0)].hit
    assert by[("full_page", 0)].first_hit == 1  # the select page comes first
    for system in ("nina", "strong_rag", "naive_rag"):
        assert all(by[(system, b)].tokens <= b for b in BUDGETS)
    assert by[("nina", 8_000)].hit  # a small page fits the largest budget


def test_summarize_reports_each_system_and_budget() -> None:
    rows = [
        Row("so-1", "x", "nina", 1_000, 500, 1.0, True, 1, 10.0),
        Row("so-2", "x", "nina", 1_000, 1_500, 0.0, False, None, 30.0),
        Row("so-1", "x", "full_page", 0, 9_000, 1.0, True, 2, 5.0),
    ]
    table = summarize(rows)
    assert "| nina | 1000 | 2 | 0.500 | 0.500 | 1,000 | 0.500 | 20 |" in table
    assert table.index("| nina |") < table.index("| full_page |")
    assert "| full_page | page | 1 |" in table
